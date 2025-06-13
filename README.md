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
from elog_zulip import Elog
elog = Elog('~/configuration.toml')
elog.publish()
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
# If set to true will send the message to zulip using the user that sent the message in the elog
use-elog-user = false
# A csv file with at least two fields: elog_user and 'email'.
# In the elog, users are specified using a free strings. For this reason automatic mapping
# is error prone. So a table with matches between authors and zulip emails must be provided
# The software will match elog users exactly (the match is case-sensitive and elog strings
# are not modified in any way e.g. spaces before/after are not trimmed).
users-map = '/path/to/user_map.csv'
# User that will be used as a default users if the impersonated user is not found.
# Useful if the user specified in the zuliprc file ends with a domain that is not
# authorized to be impersonated (the domain check is exact e.g. allowing example.com
# users to be impersonated will not allow users in the subdomain test.example.com
# to be impersonated. This is true even if the allow-subdomain option is specified)
impersonator-email='user@institute.eu'
# If set to true will send the message to zulip using the date/time specified in the original elog message
use-elog-datetime = false
# If set to true will map (when possible) the links to other elog messages. This ability only works within the same elog. And is limited to links pointing to previous messages.
rewrite-elog-links = false
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
