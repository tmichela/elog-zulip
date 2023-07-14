"""
"""
__version__ = "0.2.0"

import warnings
from argparse import ArgumentParser
from io import BytesIO
from time import sleep
from typing import Dict, List, Tuple

import dataset
import jinja2
import toml
import zulip
from elog import Logbook, LogbookMessageRejected, LogbookServerProblem
from loguru import logger as log

from .mock import FakeDB, FakeZulip
from .utils import format_text, retry

# TODO split large quotes
# TODO insert images in text when placeholders are present
# TODO use config header or logbook name or zulip stream as db table?


__all__ = ['Elog']


def _handle_z_error(caller, *args):
    """Handles Zulip errors.
    """
    res = caller(*args)
    if res['result'] == 'success':
        if param := res.get('ignored_parameters_unsupported'):
            log.warning(f'Ignored unsupported parameters: {param}')
        return res

    code = res.get('code')
    if code == 'RATE_LIMIT_HIT':
        # wait for requested timeout (+1s) and resend the request
        wait = 1 + res["retry-after"]
        log.info(f'Zulip: {res["msg"]}, waiting {wait}')
        sleep(wait)
        return _handle_z_error(caller, *args)
    raise Exception(res.get('msg', res))


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

    @retry(attempts=5, delay=1, exc=(LogbookServerProblem, LogbookMessageRejected))
    def _read_entry(self, entry_id: int) -> Tuple[str, Dict[str, str], List[str]]:
        return self.logbook.read(entry_id)

    def new_entries(self):
        entries = self.logbook.get_message_ids()
        new_entries = sorted(set(entries).difference(self._saved_entries()))
        log.info(f'New entries {new_entries}')

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for entry in new_entries:
                yield self._read_entry(entry)

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
        res = _handle_z_error(self.zulip.upload_file, file_)
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
        res = _handle_z_error(self.zulip.send_message, request)
        r = self.zulip.send_message(request)
        return res

    def _publish(self, text, attributes, attachments):
        attributes['EntryUrl'] = self.entry_url(attributes)
        subject = self.config.get('elog-subject', self._default_subject(attributes))
        prefix = self.config.get('elog-prefix', '')
        topic = self.config.get('zulip-topic', '')
        quote = self.config.get('quote', False)
        # format subject, prefix and topic using jinja2
        env = jinja2.Environment()
        subject = env.from_string(subject).render(attributes)
        prefix = env.from_string(prefix).render(attributes)
        topic = env.from_string(topic).render(attributes) or 'no topic'

        r = self._send_message(f'{subject}\n{prefix}', topic)
        for content in format_text(text):
            if quote:
                content = f'```quote plain\n{content}\n```'
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
