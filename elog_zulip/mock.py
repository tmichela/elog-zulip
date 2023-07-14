from loguru import logger as log


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
