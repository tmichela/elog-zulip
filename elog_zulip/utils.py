import os
import re
from copy import copy
from functools import partial, wraps
from time import sleep
from typing import Collection, Iterator

import pandas as pd
from bs4 import BeautifulSoup
from html2text import html2text

MD_LINE_WIDTH = 350
MSG_MAX_CHAR = 10_000


def split_string(string: str, maxchar: int = MSG_MAX_CHAR) -> Iterator[str]:
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


def assemble_strings(strings: Collection[str], maxchar: int =MSG_MAX_CHAR) -> Iterator[str]:
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


def split_md_table(table: pd.DataFrame, maxchar=MSG_MAX_CHAR - 4):
    tables, start, stop = [], 0, 0
    while True:
        if stop == 0:
            md_table = table.iloc[start:].to_markdown(index=False)
        else:
            md_table = table.iloc[start:stop].to_markdown(index=False)

        if len(md_table) > maxchar:
            stop -= 1
        else:
            tables.append(f'\n{md_table}\n')
            if stop == 0:
                break
            start, stop = stop, 0
    return tables


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
    except (IndexError, ValueError):
        # failed finding a table
        return f"```quote\n{html2text(html, bodywidth=MD_LINE_WIDTH)}\n```\n"

    if df.columns.size == 1 and re.match(r'^.*? wrote:$', df.columns[0]):
        # this table contains quote(s)
        # we manually parse the table, as pandas does not retain cells formatting
        author, text = table.find_all('td')[:2]
        author = html2text(str(author), bodywidth=MD_LINE_WIDTH)
        text = html2text(str(text), bodywidth=MD_LINE_WIDTH)
        ret = f"```quote\n**{author.strip()}**\n{text}\n```\n"
        if sub_tables:
            ret = ret.format(*[table_to_md(st) for st in sub_tables])
        return ret
    else:
        df.dropna(how='all', inplace=True)
        df.fillna('', inplace=True)

        # split table if not in a quote
        if len(table.find_parents('table')) == 0:
            return split_md_table(df)

        return f"\n{df.to_markdown(index=False)}\n"


def format_text(text, maxchar=MSG_MAX_CHAR):
    soup = BeautifulSoup(text, 'lxml')

    # split message in parts:
    #   - separate tables from the messages to be rendered with pandas
    #   - split text in multiple messages if it is too long
    parts = []
    def _add_part(_part):
        for p in split_string(html2text(_part, bodywidth=MD_LINE_WIDTH), maxchar=maxchar):
            if not p.strip():
                continue
            parts.append(p)

    remain = text
    for table in get_sub_tables(soup, depth=0):
        part, _, remain = str(BeautifulSoup(remain, 'lxml')).partition(str(table))
        _add_part(part)

        md_table = table_to_md(table)
        parts.extend([md_table] if isinstance(md_table, str) else md_table)
    if remain:
        _add_part(remain)

    # # reassemble parts
    # for p in assemble_strings(parts, maxchar=maxchar):
    #     yield p
    return parts


def retry(func=None, *, attempts=1, delay=0, exc=(Exception,)):
    """Re-execute decorated function.

    :attemps int: number of tries, default 1
    :delay float: timeout between each tries in seconds, default 0
    :exc tuple: collection of exceptions to be caugth
    """
    if func is None:
        return partial(retry, attempts=attempts, delay=delay, exc=exc)

    @wraps(func)
    def retried(*args, **kwargs):
        retry._tries[func.__name__] = 0
        for i in reversed(range(attempts)):
            retry._tries[func.__name__] += 1
            try:
                ret = func(*args, *kwargs)
            except exc:
                if i <= 0:
                    raise
                sleep(delay)
                continue
            else:
                break
        return ret

    retry._tries = {}
    return retried
