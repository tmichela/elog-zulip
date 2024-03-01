import os
import re
from base64 import b64decode
from copy import copy
from functools import partial, wraps
from io import BytesIO
from time import sleep
from typing import Collection, Iterator
from uuid import uuid4

import pandas as pd
from bs4 import BeautifulSoup
from pypandoc import convert_text

MD_LINE_WIDTH = 350
MSG_MAX_CHAR = 10_000


def html_to_md(html, columns=MD_LINE_WIDTH):
    # remove [span, div] tags
    soup = BeautifulSoup(html, 'lxml')
    for tag in soup.find_all(['span', 'div']):
        tag.unwrap()
    html = str(soup)

    # convert html to markdown
    md = convert_text(html, to='gfm', format='html', extra_args=[f'--columns={columns}'])

    # do not escape '-' at begining of lines (likely bullet points)
    md = re.sub(r'^(\s*)\\-', '\g<1>-', md, flags=re.MULTILINE)
    # do not escape "[]*~<.()"
    md = re.sub(r'\\([\[\]\*\~\<\.\(\)])', '\g<1>', md)
    # do not excape ">#" except at start of line (interpreted as quote)
    md = re.sub(r'(?<!^)\\([\>\#])', '\g<1>', md, flags=re.MULTILINE)
    # -[]*>#()
    # \`_{}+.!
    return md


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
        tb_id = str(uuid4())
        sub_tables.append((copy(st), tb_id))
        st.replace_with(BeautifulSoup(f'<p>{{{tb_id}}}</p>', 'lxml').p)

    html = table.prettify()
    try:
        df = pd.read_html(html, header=0)[0]
    except (IndexError, ValueError):
        # failed finding a table
        return f"```quote\n{html_to_md(html)}\n```\n"

    if df.columns.size == 1 and re.match(r'^.*? wrote:$', df.columns[0]):
        # this table contains quote(s)
        # we manually parse the table, as pandas does not retain cells formatting
        author, text = table.find_all('td')[:2]
        author = html_to_md(str(author))
        text = html_to_md(str(text))
        ret = f"```quote\n**{author.strip()}**\n{text}\n```\n"
        if sub_tables:
            def _format(**kwargs):
                try:
                    placeholders = {id_: table_to_md(st) for st, id_ in sub_tables}
                    placeholders.update(kwargs)
                    return ret.format(**placeholders)
                except KeyError as kerr:
                    key = kerr.args[0]
                    return _format(**{key: f'{{{key}}}', **kwargs})

            ret = _format()
        return ret
    else:
        df.dropna(how='all', inplace=True)
        df.fillna('', inplace=True)

        # split table if not in a quote
        if len(table.find_parents('table')) == 0:
            return split_md_table(df)

        return f"\n{df.to_markdown(index=False)}\n"


def extract_embedded_images(html: str | BeautifulSoup):
    """extract embedded images from an html string

    Returns:
        Tuple[BeautifulSoup, List[str, BytesIO]]: trimmed html and list of images
    """
    if isinstance(html, str):
        soup = BeautifulSoup(html, 'lxml')
    else:
        soup = html

    images = []
    for idx, img in enumerate(soup.find_all('img')):
        if not (src := img.attrs.get('src')):
            continue
        metadata, _, data = src.partition(',')
        if metadata == 'data:image/png;base64':
            f = BytesIO()
            f.write(b64decode(data))
            f.name = img.attrs.get('alt', None) or f'image_{idx}.png'
            f.seek(0)
            img.replace_with(f'{{image_{idx}}}')
            images.append((f'image_{idx}', f))
        else:
            print('Embedded image in elog entry:')
            print(img.attrs)

    return soup, images


def format_text(text, maxchar=MSG_MAX_CHAR):
    soup = BeautifulSoup(text, 'lxml')

    # split message in parts:
    #   - separate tables from the messages to be rendered with pandas
    #   - split text in multiple messages if it is too long
    parts = []
    def _add_part(_part):
        _part, images = extract_embedded_images(_part)
        for p in split_string(html_to_md(str(_part)), maxchar=maxchar):
            if not p.strip():
                continue
            part_images = [im for im in images if im[0] in p]
            parts.append((p, part_images))

    remain = text
    for table in get_sub_tables(soup, depth=0):
        part, _, remain = str(BeautifulSoup(remain, 'lxml')).partition(str(table))
        _add_part(part)

        table, table_images = extract_embedded_images(table)
        md_table = table_to_md(table)

        if isinstance(md_table, str):
            parts.append((md_table, table_images))
        else:
            for t in md_table:
                t_images = [im for im in table_images if im[0] in t]
                parts.append((t, t_images))
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
