"""
"""
__version__ = "0.1.0"

import os
import re
import warnings
from argparse import ArgumentParser
from copy import copy
from io import BytesIO
from typing import Any, Collection, Dict, Iterator

import dataset
import jinja2
import pandas as pd
import toml
import zulip
from bs4 import BeautifulSoup
from elog import Logbook
from html2text import html2text
from loguru import logger as log

MD_LINE_WIDTH = 350

# TODO split large tables
# TODO split large quotes
# TODO insert images in text when placeholders are present
# TODO use config header or logbook name or zulip stream as db table?


def split_string(string: str, maxchar: int = 9999) -> Iterator[str]:
    next_block = ''

    for line in string.splitlines(keepends=True):
        # TODO handle case where line is > maxchar
        if len(next_block + line) > maxchar:
            yield next_block
            next_block = line
        else:
            next_block += line
    if next_block:
        yield next_block


def assemble_strings(strings: Collection[str], maxchar: int =9999) -> Iterator[str]:
    """Assemble consecutive strings up to maxchar.
    """
    assembled = ''
    for string in strings:
        # TODO handle len(string) > maxchar
        if len(assembled + string) > maxchar:
            if assembled:
                yield assembled
            assembled = string
        else:
            assembled += os.linesep + string
    if assembled:
        yield assembled


def get_sub_tables(table, depth=1):
    """Get all sub tables at level `depth`.
    """
    current_depth = len(table.find_parents("table"))
    for sub_table in table.find_all("table"):
        if (len(sub_table.find_parents("table")) - current_depth) == depth:
            yield sub_table


def table_to_md(table):
    """Convert tables in html to markdown format.
    
    Tables here can be quoted elog entries or actual tables.
    """
    table = copy(table)
    sub_tables = []
    for st in get_sub_tables(table):
        sub_tables.append(copy(st))
        st.replace_with(BeautifulSoup('<p>{}</p>', 'lxml').p)

    html = table.prettify()
    try:
        df = pd.read_html(html, header=0)[0]
    except ValueError:
        # failed finding a table
        return f"```quote\n{html2text(html, bodywidth=MD_LINE_WIDTH)}\n```\n"
    
    if df.columns.size == 1 and re.match(r'^.*? wrote:$', df.columns[0]):
        # this table contains quote(s)
        # we manually parse the table, as pandas does not retain cells formatting
        author, text = table.find_all('td')[:2]
        author = html2text(str(author), bodywidth=MD_LINE_WIDTH)
        text = html2text(str(text), bodywidth=MD_LINE_WIDTH)
        ret = f"```quote\n**{author.strip()}**\n{text}\n```\n"
        ret = ret.format(*[table_to_md(st) for st in sub_tables])
        return ret
    else:
        df.dropna(how='all', inplace=True)
        df.fillna('', inplace=True)
        return f"\n{df.to_markdown(index=False)}\n"


def format_text(text, maxchar=9999):
    soup = BeautifulSoup(text, 'lxml')
    
    # split message in parts:
    #   - separate tables from the messages to be rendered with pandas
    #   - split text in multiple messages if it is too long
    parts, remain = [], ''

    def _add_part(_part):
        for p in split_string(html2text(_part, bodywidth=MD_LINE_WIDTH), maxchar=maxchar):
            if not p.strip():
                continue
            parts.append(p)

    remain = text
    for table in get_sub_tables(soup, depth=0):
        part, _, remain = str(soup).partition(str(table))
        _add_part(part)
        parts.append(table_to_md(table))
    if remain:
        _add_part(remain)

    # reassemble parts
    for p in assemble_strings(parts, maxchar=maxchar):
        yield p


class Elog:
    def __init__(self, config, dry_run=False):
        user, pswd = config.get('elog-credentials', (None, ''))
        url = config['elog-url']
        self.logbook = Logbook(url, user=user, password=pswd)

        self.table = config['db-table']
        self.stream = config['zulip-stream']
        self.config = config

        self.dry_run = dry_run
        if dry_run:
            class FakeDB:
                def insert(self, data, columns=None):
                    log.info(f'Inserting {data}')
                def find_one(self, entry_id):
                    return None
                def find(self, *args, **kwargs):
                    return None
                def __len__(self):
                    return 0

            class FakeZulip:
                def send_message(self, message):
                    log.info(f'Sending {message}')
                    return {'result': 'success'}
                def upload_file(self, file):
                    return {'result': 'success', 'uri': 'https://example.com'}

            self.entry = FakeDB()
            self.zulip = FakeZulip()

        else:
            # zulip client
            self.zulip = zulip.Client(config_file=config['zulip-rc'])
            # database connection
            self._db = dataset.connect(config['database'])
            self.entry = self._db[self.table]

    def _saved_entries(self):
        return [e['entry_id'] for e in self.entry.find(order_by=['entry_id']) or ()]

    def new_entries(self):
        entries = self.logbook.get_message_ids()
        for entry in sorted(set(entries).difference(self._saved_entries())):

            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                yield self.logbook.read(entry)

    def upload(self, attachment):
        # download attachment from logbook
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            data = self.logbook.download_attachment(attachment)
        file_ = BytesIO()
        file_.write(data)
        file_.name = attachment.rpartition('/')[-1]
        file_.seek(0)

        # upload document to zulip
        if (res := self.zulip.upload_file(file_))['result'] != 'success':
            log.warning(f'Failed uploading {attachment} to zulip:\n{res["reason"]}')
            return
        return f'[{file_.name}]({res["uri"]})'

    def entry_url(self, attributes):
        return '/'.join([self.logbook._url, attributes['$@MID@$']])

    def _default_subject(self, attrs):
        subject = attrs.get('Subject', 'no subject')
        return f'[{subject}]({self.entry_url(attrs)}):'

    def _send_message(self, message, topic):
        log.info(message)
        log.info(f'sending to #{self.stream}>>{topic}')
        request = {
            # "type": "private",
            # "to": [306218],
            "type": "stream",
            "to": self.stream,
            "topic": topic,
            "content": message,
        }
        return self.zulip.send_message(request)

    def _publish(self, text, attributes, attachments):
        attributes['EntryUrl'] = self.entry_url(attributes)
        subject = self.config.get('elog-subject', self._default_subject(attributes))
        prefix = self.config.get('elog-prefix', '')
        topic = self.config.get('zulip-topic', '')
        quote = self.config.get('quote', True)
        # format subject, prefix and topic using jinja2
        env = jinja2.Environment()
        subject = env.from_string(subject).render(attributes)
        prefix = env.from_string(prefix).render(attributes)
        topic = env.from_string(topic).render(attributes) or 'no topic'

        for n, content in enumerate(format_text(text)):
            content = f'```quote plain\n{content}\n```' if quote else f'{content}'
            if n == 0:
                content = f'{subject}\n{prefix}\n{content}'

            r = self._send_message(content, topic)
            log.info(f'New publication: {self.entry_url(attributes)} - {r}')

        # upload attachments
        attachments_text = ''
        for attachment in attachments:
            log.info(f'New attachment: {attachment}')
            if uri := self.upload(attachment):
                attachments_text += f'\n{uri}'
        if attachments_text:
            r = self._send_message(attachments_text, topic)
            log.info(f'New publication: {self.entry_url(attributes)} - {r}')

        # add entry to db
        data = {'entry_id': int(attributes["$@MID@$"]),
                'entry_date': str(attributes['Date']),
                'entry_author': str(attributes['Author'])}
        self.entry.insert(data, ['entry_id'])

    def publish(self):
        for content, attributes, attachments in self.new_entries():
            self._publish(content, attributes, attachments)


def main(argv=None):
    ap = ArgumentParser('elog-zulip-publisher',
                        description='Publish ELog entries to Zulip')
    ap.add_argument('config', help='toml configuration file')
    ap.add_argument("--dry-run", action="store_true",
                    help="Connect to elog, but mock the database and Zulip.")
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

    meta = config.pop("META")

    for elog, conf in config.items():
        conf.update(meta)
        Elog(conf, args.dry_run).publish()


if __name__ == '__main__':
    import sys
    main(sys.argv[1:])
