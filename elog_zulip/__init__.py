"""
"""
__version__ = "0.2.0"

import csv
import datetime
import os
import re
import sys
import warnings
from argparse import ArgumentParser
from io import BytesIO
from pathlib import Path
from time import sleep
from typing import Dict, List, Tuple
from urllib.parse import quote

import dataset
import jinja2
import toml
import zulip
from elog import Logbook, LogbookMessageRejected, LogbookServerProblem
from loguru import logger as log

from .mock import FakeDB, FakeZulip
from .utils import format_text, retry, _log_error

# TODO split large quotes
# TODO insert images in text when placeholders are present
# TODO use config header or logbook name or zulip stream as db table?


__all__ = ['Elog']


def _handle_z_error(caller, *args, **kwargs):
    """Handles Zulip errors.
    """
    res = caller(*args, **kwargs)
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
        return _handle_z_error(caller, *args, **kwargs)
    raise Exception(res.get('msg', res))


class Elog:
    def __init__(self, config, dry_run=False):
        user, pswd = config.get('elog-credentials', (None, ''))
        url = config['elog-url']

        self.logbook = Logbook(url, user=user, password=pswd)

        self.table = config['db-table']
        self.stream = config['zulip-stream']
        self.impersonate = config.get('use-elog-user', False)
        users_map_path = config.get('users-map')
        self.rewrite_datetime = config.get('use-elog-datetime', False)
        self.can_create_users = config.get('can-create-users', dry_run)
        self._fallback_user = {
            'email': 'me@example.com',
            'user_id': None
        }
        self.config = config

        self.dry_run = dry_run
        if dry_run:
            self.entry = FakeDB()
            self.zulip = FakeZulip()
        else:
            # zulip client
            self.zulip = zulip.Client(
                    config_file=config['zulip-rc'],
                    # Option to make the software work even if the HTTPS certificate is not valid
                    insecure=config.get('allow-insecure-zulip', False),
                    # Only when passing specific client names the server allows the client to impersonate users
                    client='jabber_mirror' if self.impersonate else None
                    )
            # database connection
            self._db = dataset.connect(config['database'])
            self.entry = self._db[self.table]

            fallback_user_email = config.get('impersonator-email', None)

            if fallback_user_email is not None:
                user_info = self._get_user_by_email(fallback_user_email)

                if not user_info:
                    log.error(f'The impersonation default user "{fallback_user_email}" does not exist on the server.')
                    sys.exit(1)

                self._fallback_user = user_info
            else:
                self._fallback_user = _handle_z_error(self.zulip.get_profile)


        self._users_map = {}
        if self.impersonate:
            if not users_map_path:
                log.error('Cannot proceed without a users map')
                sys.exit(1)
            self._load_elog_user_map()


    def _get_user_by_email(self, user):
        response = _handle_z_error(self.zulip.call_endpoint,
            url=f"users/{user}",
            method="GET"
        )

        return (response['user'] if response['result'] == 'success' else {})



    def _load_elog_user_map(self):
        existing_users = {}

        if not self.can_create_users:
            response = _handle_z_error(self.zulip.get_members)
            if response['result'] == 'success':
                existing_users = { m['email'] : m for m in response['members'] }
            else:
                log.error(f'Error while querying the users: {response["msg"]}')
                sys.exit(1)

        with open(self.config.get('users-map'), newline='') as f:
            data = list(csv.reader(f))

        if len(data) < 2:
            log.error(f'The provided user map is empty. Please provide a valid one.')
            sys.exit(1)

        header = data[0]

        if not ('elog_user' in header and 'email' in header):
            log.error(
                f'The users map needs to have an header with two fields:'
                '"elog_user" (name of the user in the elog) and "email" (email of '
                 'the user in the destination zulip system)')
            sys.exit(1)

        for d in data[1:]:
            user_dict = dict(zip(header, d))
            server_user = existing_users.get(user_dict['email'], {
                'email': user_dict['email'],
                'user_id': None
            })

            if not (self.can_create_users or server_user):
                log.info(f'User {user_dict["email"]} is not present in the server skipping.')
                continue

            self._users_map[user_dict['elog_user']] = {
                'map_info': user_dict,
                'server_info': server_user
            }

        if len(self._users_map) == 0:
            log.error(f'No user found in the user map. Please check that you are using the correct user map for this elog.')
            sys.exit(1)

    def _saved_entries(self):
        return {int(e['entry_id']) for e in self.entry.find(order_by=['entry_id']) or ()}

    @retry(attempts=5, delay=1, exc=(LogbookServerProblem, LogbookMessageRejected))
    def _read_entry(self, entry_id: int) -> Tuple[str, Dict[str, str], List[str]]:
        return self.logbook.read(entry_id)

    def new_entries(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
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
        return file_.name, res["uri"]

    def entry_url(self, attributes):
        return '/'.join([self.logbook._url.rstrip('/'), attributes['$@MID@$']])

    def _default_subject(self, attrs):
        subject = attrs.get('Subject', 'no subject')
        return f'[{subject}]({self.entry_url(attrs)}):'

    def _default_header(self, attrs):
        attrs = attrs.copy()
        elog_url = self.entry_url(attrs)

        attrs.pop('Subject')
        attrs.pop('$@MID@$')
        attrs.pop('Author')
        attrs.pop('Encoding')
        ret = '```quote\n'
        ret += f'Date: **`{attrs.pop("Date")}`**\n'
        ret += f'elog: {elog_url}\n\n'
        for key, value in sorted(attrs.items()):
            ret += f'{key}: **{value}**\n' if value else f'{key}:\n'
        ret += '```\n'
        return ret

    def _send_message(self, message, topic, sender = None, date = None):
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
        if sender is not None:
            request['sender'] = sender

        if date is not None:
            request['forged'] = True
            request['time'] = date.timestamp()

        res = _handle_z_error(self.zulip.send_message, request)
        return res

    def _publish(self, text, attributes, attachments, maxchar=10_000):
        header = self._default_header(attributes)

        sender = {
            'email': None,
            'user_id': None
        }
        if self.impersonate and self._users_map:
            a = attributes['Author']
            sender = self._fallback_user
            if a in self._users_map:
                sender = self._users_map[a]['server_info']

        is_user_new = sender['user_id'] is None

        # TODO the way to fix this would be to create a dummy message and then delete it. There is no proper way to fix this,
        #      so this would be a hack. This seems to be rare enough that is not worth implementing for now.
        if self.impersonate and len(attachments) > 0 and is_user_new:
            log.warning(f'Creating users on the fly is currently not implemented falling back to the default user')
            sender = self._fallback_user

        try:
            date = datetime.datetime.strptime(attributes['Date'], '%a, %d %b %Y %H:%M:%S %z') if self.rewrite_datetime else None
        except ValueError as e:
            log.warning(f'Ignoring invalid date {attributes["Date"]}')
            date = None

        attributes['EntryUrl'] = self.entry_url(attributes)
        attributes['EntryID'] = attributes['$@MID@$']
        ext = {k.replace(' ', '_'): v for k, v in attributes.items() if ' ' in k}
        attributes.update(ext)
        subject = self.config.get('elog-subject', self._default_subject(attributes))
        prefix = self.config.get('elog-prefix', '')
        topic = self.config.get('zulip-topic', '')
        show_header = self.config.get('show-header', True)
        # format subject, prefix and topic using jinja2
        env = jinja2.Environment()
        subject = env.from_string(subject).render(attributes)
        prefix = env.from_string(prefix).render(attributes)
        topic = env.from_string(topic).render(attributes) or 'no topic'

        parts = [(f'{subject}\n{header if show_header else ""}{prefix}', [])]
        parts.extend(format_text(text, attachments))

        # upload attachments
        attachments_text = ''
        zulip_attachments = []
        for idx, attachment in enumerate(attachments, start=1):
            log.info(f'New attachment: {attachment}')
            # replace special characters in url string
            attachment = quote(attachment, safe='/:')
            log.debug(f'Attachment url parsed: {attachment}')
            fname, uri = self.upload(attachment)
            zulip_attachments.append((fname, uri))
            attachments_text += f'\n[{idx}] [{fname}]({uri})'
        if attachments_text:
            parts.append((attachments_text, []))

        def _upload_embedded_images(txt, imgs):
            placeholders = {}
            for placeholder, img in imgs:
                if isinstance(img, int):
                    # reference to an attachement
                    uri = zulip_attachments[img][1]
                elif isinstance(img, str):
                    # download attachment from elog
                    url = Path(img)
                    while len(url.suffixes) > 1:
                        url = url.with_suffix('')
                    try:
                        _, uri = self.upload(self.logbook._url + str(url))
                    except LogbookMessageRejected:
                        # image url is not something saved in the elog
                        _log_error(f'Could not download attachment: {img}')
                        uri = None
                else:
                    uri = _handle_z_error(self.zulip.upload_file, img)['uri']
                placeholders[placeholder] = f'[]({uri})' if uri is not None else ''
            return txt.format(**placeholders)

        def _replace_attachments_in_table(txt):
            pattern = r'^.*\|\s*(\[.*\]\((\/user_uploads\/[^)]+)\))\s*\|.*$'

            output = []
            for line in txt.splitlines():
                if (match := re.match(pattern, line)) is not None:
                    att = match.group(2)
                    for idx, aline in enumerate(attachments_text.splitlines()):
                        if att in aline:
                            line = line.replace(match.group(1), f'[{idx}]')
                            break
                output.append(line)
            return '\n'.join(output)

        def _send_message(txt):
            txt = _replace_attachments_in_table(txt)
            r = self._send_message(txt, topic, sender['email'], date)
            log.info(f'New publication: {self.entry_url(attributes)} - {r}')

        # combine parts and send to zulip
        message = ''
        for part, part_images in parts:
            # TODO handle len(part) > maxchar
            if part_images:
                part = _upload_embedded_images(part, part_images)
            if (len(message) + len(part)) > maxchar:
                if message:
                    _send_message(message)
                message = part
            else:
                message += part + os.linesep
        if message:
            _send_message(message)
        if self.impersonate and is_user_new and self.can_create_users:
            user_info = self._get_user_by_email(sender['email'])

            if not user_info:
                log.warning(f'User "{sender["email"]}" was not created for unknown reasons.')
            else:
                log.info(f'User "{sender["email"]}" was created and added to the user_map.')
                self._users_map[sender['email']] = user_info
        # add entry to db
        data = {'entry_id': int(attributes["$@MID@$"]),
                'entry_date': str(attributes['Date']),
                'entry_author': str(attributes['Author'])}
        self.entry.insert(data, ['entry_id'])

    def publish(self, ids: list[int] = []):

        if ids:
            saved_entries = self._saved_entries()
            for id in ids:
                if id not in saved_entries:
                    self._publish(*self._read_entry(id))
                else:
                    log.warning(f'Message {id} is already present in the db. Skipping.')
        else:
            for content, attributes, attachments in self.new_entries():
                self._publish(content, attributes, attachments)


def main(argv=None):
    ap = ArgumentParser('elog-zulip-publisher',
                        description='Publish ELog entries to Zulip')
    ap.add_argument('config', help='toml configuration file')
    ap.add_argument("--dry-run", action="store_true",
                    help="Connect to elog, but mock the database and Zulip.")
    ap.add_argument("--elog-ids", default='',
                    help="The ids of the messages to import from the elog.")
    args = ap.parse_args()
    config = toml.load(args.config)

    try:
        ids = list({int(e) for e in args.elog_ids.split(',') if e.strip()})
    except ValueError:
        log.error(f'The list of ids is not valid: "{args.elog_ids}". The list must be a comma separated list of integers')
        sys.exit(1)


    # set logger
    if 'log-file' in config['META']:
        log.add(
            config['META']['log-file'],
            level=config['META'].get('log-level', 'DEBUG'),
            rotation=config['META'].get('log-rotation'),
            retention=config['META'].get('log-retention')
        )

    meta = config.pop("META")

    if len(config) > 1 and args.elog_ids:
        log.error('The --elog-ids option only works if a single import configuration is present in the configuration file.')
        sys.exit(1)




    for elog, conf in config.items():
        conf.update(meta)
        Elog(conf, args.dry_run).publish(ids)


if __name__ == '__main__':
    main(sys.argv[1:])
