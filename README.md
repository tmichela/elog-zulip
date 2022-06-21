# ELog-Zulip publisher

Simple Zulip client application for ELog integration. This client application reads elog
pages and publish new entries on Zulip.

# Installation

to install the package:

```bash
$ git clone https://github.com/tmichela/elog-zulip.git
$ python3 -m pip install ./elog-zulip
```

# Usage

```python
from elog_zulip import Publisher
publisher = Publisher('~/configuration.toml')
publisher.publish()
```

or from the command line:

```console
elog-zulip-publisher ~/config.toml
```

# Configuration

```toml
[META]
# Path to sqlite database
database = 'sqlite:////home/user/elog.db'
# path to sulip botrc file
zulip-rc = '/home/user/.zulip/.zuliprc'

[XO]
elog-url = ''
zulip-stream = ''
zulip-topic = ''
db-table = ''

[OP]
elog-url = ''
zulip-stream = ''
zulip-topic = ''
db-table = ''

[DOC]
# credential to acces the elog page, formated ['username', 'password']
credentials = ['', '']
elog-url = ''
zulip-stream = ''
zulip-topic = ''
db-table = ''
```
