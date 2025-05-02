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
        return {'result': 'success', 'id': -1}
    def call_endpoint(self, url, method = "POST", *args, **kwargs):
        if url.startswith('user_uploads') and method == "POST":
            return {'result': 'success', 'uri': 'https://example.com'}
        if url.startswith('users/') and method == "GET":
            return {'result': 'success', 'user': {'email': 'me@example.com', 'user_id': -1}}

        raise Exception(f'Call to url: {url} with method {method} is not implemented')

