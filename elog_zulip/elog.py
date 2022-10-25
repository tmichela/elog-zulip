from argparse import ArgumentParser
import http.cookiejar as cookielib
import re
from typing import Tuple

from bs4 import BeautifulSoup
from chardet import detect
import dataset
from loguru import logger as log
import mechanize
import pandas as pd
import pypandoc
import toml
import zulip


def trim_lines(text, maxchar=80):
    """Split lines to maxchar in text
    """
    def split(lines):
        for line in lines.splitlines():
            if not line:
                yield '\n'
                continue
        
            if line.startswith('|') and line.endswith('|'):
                # this is hopefully a table
                yield line
                continue

            s = ''
            for word in line.split():
                if len(s) + len(word) > maxchar:
                    yield s
                    s = word
                else:
                    s = ' '.join((s, word)) if s else word
            if s:
                yield s
    return '\n'.join(split(text))


class Elog(mechanize.Browser):
    def __init__(self, config, dry_run=False):
        super().__init__()

        user, pswd = config.get('elog-credentials', (None, ''))
        self.user = user
        self.pswd = fr'{pswd}'
        self.url = config['elog-url']
        self.stream = config['zulip-stream']
        self.topic = config.get('zulip-topic', "Uncategorized")
        self.table = config['db-table']

        self._logged = False
        if self.user is None:
            self._logged = True

        # cookie jar
        self.set_cookiejar(cookielib.LWPCookieJar())

        # Browser options
        self.set_handle_equiv(True)
        self.set_handle_gzip(True)
        self.set_handle_redirect(True)
        self.set_handle_referer(True)
        self.set_handle_robots(False)
        self.set_handle_refresh(mechanize._http.HTTPRefreshProcessor(), max_time=1)

        self.addheaders = [('User-agent', 'Chrome')]

        self.dry_run = dry_run
        if dry_run:
            class FakeDB:
                def insert(self, data, columns=None):
                    log.info(f'Inserting {data}')
                def find_one(self, entry_id):
                    return None

            class FakeZulip:
                def send_message(self, message):
                    log.info(f'Sending {message}')
                    return {'result': 'success'}
                def call_endpoint(self, endpoint, method, files):
                    return {'result': 'success', 'uri': 'https://example.com'}

            self.entry = FakeDB()
            self.zulip = FakeZulip()

        else:
            # zulip client
            self.zulip = zulip.Client(config_file=config['zulip-rc'])
            # database connection
            self._db = dataset.connect(config['database'])
            self.entry = self._db[self.table]

    @property
    def logged_in(self):
        return self._logged

    def login(self):
        if self.logged_in:
            return

        self.open(self.url)
        self.select_form(nr=0)
        self.form['uname'] = self.user
        self.form['upassword'] = self.pswd
        self.submit()
        self._logged = True

    def _read_page(self, url):
        if not self.logged_in:
            self.login()

        page = self.open(url).read()
        encoding = detect(page)['encoding']
        return page.decode(encoding)

    def get_entries(self, page=None):
        text = self._read_page(f'{self.url}{page or ""}')
        soup = BeautifulSoup(text, 'html.parser')
        table = soup.find('table', class_='listframe')
        df = pd.read_html(str(table))[0]
        return df

    def get_entry(self, entry_id: int):
        text = self._read_page(f'{self.url}{entry_id}')
        soup = BeautifulSoup(text, 'html.parser')

        # elog entry text
        text = soup.find('td', class_='messageframe')
        md_text = pypandoc.convert_text(text, to='gfm', format='html')
        text = trim_lines(md_text)
        text = re.sub(r'\\(.)', r'\1', text)

        # elog entry attachment
        attachments = []
        for att in soup.find_all(class_='attachmentframe') or ():
            attachment = att.find('img')
            if attachment:
                attachments.append(attachment.attrs)

        return text, attachments

    def upload(self, attachment):
        base, _, src_name = attachment["src"].rpartition("/")
        fname = f'{base}/{attachment["alt"]}'

        tempfile = f'/tmp/{fname.replace("/", "_")}'
        self.retrieve(f'{self.url}{fname}', filename=tempfile)

        with open(tempfile, 'rb') as f:
            # upload image to zulip
            result = self.zulip.call_endpoint(
                'user_uploads',
                method='POST',
                files=[f],
            )

        if result['result'] != 'success':
            raise Exception(result)

        return f'[{attachment["title"]}]({result["uri"]})'

    def _publish(self, entry, text, subject=None, attachments=(),
                 topic=None, quote=True):
        topic = topic if topic is not None else self.topic

        content = subject or f"[{entry.Subject}]({self.url}{entry.ID}):"
        content += f"\n```quote plain\n{text}\n```" if quote else f"\n{text}"

        for attachment in attachments:
            log.info(f'New attachment: {attachment}')
            content += f'\n{self.upload(attachment)}'

        request = {
            #"type": "private",
            #"to": [306218],
            "type": "stream",
            "to": self.stream,
            "topic": topic,
            "content": content,
        }
        r = self.zulip.send_message(request)

        log.info(f'New publication: {self.url}{entry.ID} - {r["result"]}')

        # add entry to db
        data = {'entry_id': int(entry.ID),
                'entry_date': entry.Date,
                'entry_author': entry.Author}
        self.entry.insert(data, ['entry_id'])

    def publish(self):
        for post in self._new_posts() or ():
            self._publish(*post)

    def _new_posts(self):
        raise NotImplementedError


class ElogXO(Elog):
    def _new_posts(self):
        log.info(f'[{type(self).__name__}] Checking for new posts')
        entries = self.get_entries()
        newest_entry = int(pd.to_numeric(entries.ID, errors='coerce').max())
        entry = entries.loc[entries.ID == newest_entry].squeeze()
        log.info(entry)
        if entry.ID.dtype.kind != 'i':
            # draft entry
            return []
        if self.entry.find_one(entry_id=int(entry.ID)):
            # entry is already published
            return []

        text, _ = self.get_entry(entry.ID)
        return [(entry, text)]


class ElogOperation(Elog):
    def _new_posts(self):
        log.info(f'[{type(self).__name__}] Checking for new posts')
        entries = self.get_entries()

        for idx, entry in entries.iloc[::-1].iterrows():
            try:
                int(entry.ID)
            except ValueError:
                log.info(f'entry {idx} is a draft')
                # draft entry
                continue

            if self.entry.find_one(entry_id=int(entry.ID)):
                # entry is already published
                continue

            text, attachments = self.get_entry(entry.ID)
            group = f' ({entry.Group})' if entry.Group else ''
            subject = f"[{entry.Author}{group}: {entry.Subject}]({self.url}{entry.ID}):"

            yield entry, text, subject, attachments


class ElogDoc(Elog):
    def _new_posts(self):
        log.info(f'[{type(self).__name__}] Checking for new posts')
        entries = self.get_entries()
        entries.ID = entries.ID.apply(pd.to_numeric, errors='coerce')
        newest_entry = int(entries.ID.max())
        entry = entries.loc[entries.ID == newest_entry].squeeze()
        log.info(entry)

        if self.entry.find_one(entry_id=int(entry.ID)):
            # entry is already published
            return []

        text, attachments = self.get_entry(entry.ID)
        shifters = f'{entry["DOC Shift Leader"]}, {entry["DOC Shift Deputy"]}'
        subject = f'[{shifters} (DRC: {entry.DRC}): {entry.Subject}]({self.url}{entry.ID})'
        return [(entry, text, subject, attachments)]


def main(argv=None):
    import os
    os.environ.setdefault('PYPANDOC_PANDOC', '/usr/bin/pandoc')

    ap = ArgumentParser('elog-zulip-publisher',
                        description='Publish ELog entries to Zulip')
    ap.add_argument('config', help='toml configuration file')
    args = ap.parse_args()
    config = toml.load(args.config)

    # set logger
    if 'log-file' in config['META']:
        log.add(
            config['META']['log-file'],
            level=config['META'].get('log-level', 'DEBUG'),
            rotation=config['META'].get('log-rotation'),
            retention=config['META'].get('log-retention')
        )

    for elog in ('XO', 'Operation', 'Doc'):
        if elog in config:
            conf = config[elog].copy()
            conf.update(config['META'])
            globals()[f'Elog{elog}'](conf).publish()


if __name__ == '__main__':
    import sys
    main(sys.argv[1:])
