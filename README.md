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
elog-credentials = ['', '']
elog-url = ''
zulip-stream = ''
zulip-topic = ''
db-table = ''

[proposal001234]
# credential to acces the elog page, formated ['username', 'password']
elog-credentials = ['John', '1234']
elog-url = 'https://elog.institute.eu/proposal001234'
zulip-stream = 'proposal001234'
db-table = 'proposal001234'
# the following (optional) variables can be formatted using jinja2 syntax and use the elog entry variable as input (+ the elog 'EntryUrl')
zulip-topic = '{{ Type }}'
elog-subject = '# :note: **[{{ Author }} wrote]({{ EntryUrl }}): {{ Subject }}**\n'
elog-prefix = '{{ Group }} - {{ location }} - {{ Component }}'
# whether the top level elog entry is quoted on zulip or not (default, True)
quote = false
# whether to show elog entry attributes
show-header = false
```
