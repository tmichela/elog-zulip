import os
import re
from base64 import b64decode
from copy import copy
from datetime import datetime
from functools import partial, wraps
from io import BytesIO
from time import sleep
from typing import Collection, Iterator, Tuple, List
from uuid import uuid4

import pandas as pd
import requests
from bs4 import BeautifulSoup
from pypandoc import convert_text

MD_LINE_WIDTH = 350
MSG_MAX_CHAR = 10_000

# map of elog emoji names to zulip emoji names
EMOJI_MAP = {
    'angel_smile': ':angel:',
    'angry_smile': ':angry:',
    'confused_smile': ':face_with_diagonal_mouth:',
    'cry_smile': ':cry:',
    'embarrassed_smile': ':blushing:',
    'envelope': ':envelope:',
    'heart': ':heart:',
    "laugh": ":laughing:",
    'lightbulb': ':light_bulb:',
    'omg_smile': ':surprise:',
    'regular_smile': ':smile:',
    'sad_smile': ':sad:',
    'shades_smile': ':nerd:',
    'slight_smile': ':smile:',
    'teeth_smile': ':grinning_face_with_smiling_eyes:',
    'thumbs_down': ':thumbs_down:',
    'thumbs_up': ':thumbs_up:',
    'tongue_smile': ':stuck_out_tongue:',
    'whatchutalkingabout_smile': ':neutral:',
    'wink_smile': ':wink:'
}


def _log_error(error):
    with open('./elog-zulip-error.log', 'a') as f:
        f.write(f'{datetime.now().isoformat(timespec="seconds")}:{error}\n')


def is_html(text: str) -> bool:
    return bool(BeautifulSoup(text, "html.parser").find())


def html_to_md(html: str, columns: int = MD_LINE_WIDTH) -> str:
    soup = BeautifulSoup(html, "lxml")
    # remove [span, div, u, sup, sub] tags
    for tag in soup.find_all(["span", "div", "u", "sup", "sub"]):
        tag.unwrap()

    for tag in soup.find_all(["a"]):
        href = tag.get('href', '')

        if href == '':
            # removes links with empty href
            tag.unwrap()
        else:
            # remove attributes from a tags as it prevents proper conversion to markdown links
            tag.attrs = {'href': href}

    html = str(soup)


    # convert html to markdown
    md = convert_text(
        html, to="gfm", format="html", extra_args=[f"--columns={columns}"]
    )

    # do not escape '-' at begining of lines (likely bullet points)
    md = re.sub(r"^(\s*)\\-", r"\g<1>-", md, flags=re.MULTILINE)
    # do not escape "[]*~<.()_|"
    md = re.sub(r"\\([\[\]\*\~\<\.\(\)\_\|])", r"\g<1>", md)
    # do not excape ">#" except at start of line (interpreted as quote)
    md = re.sub(r"(?<!^)\\([\>\#])", r"\g<1>", md, flags=re.MULTILINE)
    # -[]*>#().|
    # \`_{}+!

    # remove < > from bare links that is part of github markdown but not of zulip markdown
    md = re.sub(r"\<(https?\:\/\/[^\>]+)\>", r"\g<1>", md, flags=re.MULTILINE)

    # remove multiple < > from emails
    md = re.sub(r"\<+([^@\s]+\@[^\>\s]+)\>+", r"<\g<1>>", md, flags=re.MULTILINE)

    # remove empty HTML <!-- --> comments that are added from the conversion to github markdown
    md = re.sub(r"^\s*\<\!--\s*--\>\s*$", "", md, flags=re.MULTILINE)

    # Adds new line entities where there are lines with single spaces. The number of entities added
    # is equal to the matches plus one because the first that is added doesn't seem to do anything.
    # The &NewLine; entity is better compared to &nbsp; because it doesn't produce any character when copied
    # TODO: check whether this needs to be extended to empty lines
    md = re.sub(r"^\s$", "&NewLine;", md, flags=re.MULTILINE)
    # Removes empty lines between lines containing &NewLines; entities
    md = re.sub(r"(^&NewLine;$\n)(?:^$\n)+(?=\1)", r"\g<1>", md, flags=re.MULTILINE)
    # Adds one &NewLine; entity more at the end because one single entity has no effect in zulip
    md = re.sub(r"(?:^&NewLine;$\n)+", r"\g<0>&NewLine;\n", md, flags=re.MULTILINE)

    return md


def split_string(string: str, maxchar: int = MSG_MAX_CHAR) -> Iterator[str]:
    next_block = ""

    for line in string.splitlines(keepends=True):
        # TODO handle case where line is > maxchar
        if len(next_block + line) > maxchar:
            yield next_block
            next_block = line
        else:
            next_block += line
    if next_block:
        yield next_block


def assemble_strings(
    strings: Collection[str], maxchar: int = MSG_MAX_CHAR
) -> Iterator[str]:
    """Assemble consecutive strings up to maxchar."""
    assembled = ""
    for string in strings:
        # TODO handle len(string) > maxchar
        if (len(assembled) + len(string)) > maxchar:
            if assembled:
                yield assembled
            assembled = string
        else:
            assembled += string + os.linesep
    if assembled:
        yield assembled


def escape_curly_brackets(text: str):
    return text.replace("{", "{{").replace("}", "}}")


def get_sub_tables(table, depth=1):
    """Get all sub tables at level `depth`."""
    current_depth = len(table.find_parents("table"))
    for sub_table in table.find_all("table"):
        if (len(sub_table.find_parents("table")) - current_depth) == depth:
            yield sub_table


def split_md_table(table: pd.DataFrame, maxchar: int = MSG_MAX_CHAR - 4):
    # TODO handle  where a single table row contains more than maxchar
    tables, start, stop = [], 0, 0
    while True:
        if stop == 0:
            md_table = table.iloc[start:].to_markdown(index=False)
        else:
            md_table = table.iloc[start:stop].to_markdown(index=False)
        # shorten header separator
        md_table = re.sub(r":(\-)\1{2,}", ":---", md_table)

        if len(md_table) > maxchar:
            stop -= 1
        else:
            tables.append(f"\n{md_table}\n")
            if stop == 0:
                break
            start, stop = stop, 0
    return tables


def table_to_md(table: BeautifulSoup) -> str:
    """Convert tables in html to markdown format.

    Tables here can be quoted elog entries or actual tables.
    """
    table = copy(table)

    sub_tables = []
    for st in get_sub_tables(table):
        tb_id = str(uuid4())
        escaped_st = BeautifulSoup(escape_curly_brackets(str(st)), "lxml")
        sub_tables.append((escaped_st, f"table_{tb_id}"))
        st.replace_with(f"{{table_{tb_id}}}")

    html = table.prettify()
    try:
        df = pd.read_html(html, header=0)[0]
        # clean empty fields in header
        header = []
        for elem in df.columns.values:
            if re.match(r"^Unnamed: \d+$", elem):
                header.append('')
            else:
                header.append(elem)
        df.columns = header
    except (IndexError, ValueError):
        # failed finding a table
        return f"```quote\n{html_to_md(html)}\n```\n"

    if df.columns.size == 1 and re.match(r"^.*? wrote:$", df.columns[0]):
        # this table contains quote(s)
        # we manually parse the table, as pandas does not retain cells formatting
        author, text = table.find_all("td")[:2]
        author = html_to_md(str(author))
        text = html_to_md(str(text))
        ret = f"```quote\n**{author.strip()}**\n{text}\n```\n"
        if sub_tables:
            ph = {}
            for st, id_ in sub_tables:
                tb = table_to_md(st)
                if isinstance(tb, list):
                    tb = "\n".join(tb)
                ph[id_] = tb

            def _format(txt, **kwargs):
                try:
                    return txt.format(**kwargs)
                except KeyError as kerr:
                    kwargs[kerr.args[0]] = f'{{{kerr.args[0]}}}'
                    return _format(txt, **kwargs)
            ret = _format(ret, **ph)
        return ret
    else:
        df.dropna(how="all", inplace=True)
        df.fillna("", inplace=True)

        # split table if not in a quote
        if len(table.find_parents("table")) == 0:
            return split_md_table(df)

        return f"\n{df.to_markdown(index=False)}\n"


def extract_embedded_images(html, attachments) -> BeautifulSoup:
    """extract embedded images from an html string

    Returns:
        Tuple[BeautifulSoup, List[str, BytesIO]]: trimmed html and list of images
    """
    if not isinstance(html, str):
        html = str(html)

    # Escape curly braces in html content
    html = escape_curly_brackets(html)

    soup = BeautifulSoup(html, "lxml")
    images = []

    def _buffer(img_data, name):
        f = BytesIO()
        f.write(img_data)
        f.name = name
        f.seek(0)
        return f

    def _add_image(image, image_id, data):
        image.replace_with(f"{{image_{image_id}}}")
        images.append((f"image_{image_id}", data))
    
    def _add_emoji(image, emoji):
        # replace emoji img with markdown emoji
        image.replace_with(EMOJI_MAP.get(emoji, f":{emoji}:"))

    for idx, img in enumerate(soup.find_all("img")):
        if not (src := img.attrs.get("src")):
            _log_error(f' Invalid image src: {img}')
            continue

        img_id = str(uuid4())

        parent = img.parent
        if parent.name == 'a' and list(parent.children) == [img]:
            # this is a link to an attachement
            # find attachment index
            match = re.match(r'^(.*)\?.*lb=([^&]+).*$', parent.attrs['href'])
            if match is None:
                _log_error(f'could not match referenced attachment: {parent}')
            else:
                name = match[1].replace('/', '_')
                logbook = match[2]
                url = f'https://in.xfel.eu/elog/{logbook}/{name}'
                try:
                    index = attachments.index(url)
                except ValueError:
                    # we still assume it's an attachment but not attached to this elog
                    # this will fail later if not downloadable
                    _add_image(parent, img_id, name)
                else:
                    parent.replace_with(f'{{attachment_{index}}}')
                    images.append((f'attachment_{index}', index))
        elif m := re.match(r'https?://in\.xfel\.eu/elog/ckeditor/plugins/smiley/images/([^/"]+)\.\w+', src):
            # emoji from the elog emoji plugin
            _add_emoji(img, m.group(1))
        elif m := re.match(r'data:image/(png|jpe?g);base64,', src):
            data = src[m.span()[1]:]
            alt = img.attrs.get('alt', None)
            if alt == src:
                alt = None
            f = _buffer(b64decode(data), alt or f"image_{idx}.{m.group(1)}")
            _add_image(img, img_id, f)
        elif not src.startswith('http'):
            # we assume this is an attachment url in the elog
            _add_image(img, img_id, src)
        else:
            with open('./elog-zulip-info.log', 'a') as f:
                f.write(f'{datetime.now().isoformat(timespec="seconds")}: external image: {img}\n')

            res = None
            try:
                # try downloading
                res = requests.get(src)
            except requests.exceptions.TooManyRedirects as e:
                _log_error(f'Too many redirects while downloading img: {img}')
            except requests.exceptions.SSLError as e:
                _log_error(f'Invalid SSL certificate while downloading img: {img}')

            if res is not None and res.status_code == 200:
                f = _buffer(res.content, src.rpartition('/')[-1])
                _add_image(img, img_id, f)
            else:
                # replace img balise with empty string
                _log_error(f'Could not download img: {img}')
                img.replace_with("")

    return soup, images


def _collect_list_table_placeholders(container_soup: BeautifulSoup) -> dict:
    """Replace top-level tables with placeholders and return their markdown."""
    placeholder_map = {}
    for list_table in list(container_soup.find_all("table")):
        if list_table.find_parent("table"):
            continue
        md_table = table_to_md(list_table)
        if isinstance(md_table, list):
            md_table = "\n".join(md_table)
        placeholder = f"__TABLE_PLACEHOLDER_{uuid4().hex}__"
        placeholder_tag = container_soup.new_tag("p")
        placeholder_tag.string = placeholder
        list_table.replace_with(placeholder_tag)
        placeholder_map[placeholder] = md_table.strip("\n")
    return placeholder_map


def _replace_placeholder_in_md(md_text: str, placeholder: str, md_value: str) -> str:
    """Replace placeholder markers with markdown while preserving indentation."""
    while True:
        idx = md_text.find(placeholder)
        if idx == -1:
            break

        line_start = md_text.rfind("\n", 0, idx) + 1
        line_end = md_text.find("\n", idx)
        if line_end == -1:
            line_end = len(md_text)

        line = md_text[line_start:line_end]
        prefix = line[: idx - line_start]
        suffix = line[idx - line_start + len(placeholder):]

        indent_match = re.match(r"([ \t]*)(.*)", prefix)
        indent = indent_match.group(1)
        after_indent = indent_match.group(2)

        bullet_match = re.match(r"((?:[-\+\*]|\d+[.)])\s+)(.*)", after_indent)
        table_lines = md_value.splitlines()
        block_lines: List[str] = []

        if bullet_match:
            bullet = bullet_match.group(1)
            bullet_text = bullet_match.group(2)
            bullet_indent = indent + " " * len(bullet)
            bullet_line = indent + bullet + bullet_text
            block_lines.append(bullet_line)
            block_lines.append("")
            for table_line in table_lines:
                if table_line:
                    block_lines.append(f"{bullet_indent}{table_line}")
                else:
                    block_lines.append("")
        else:
            if table_lines:
                first_line = prefix + table_lines[0]
                block_lines.append(first_line)
                for table_line in table_lines[1:]:
                    if table_line:
                        block_lines.append(f"{indent}{table_line}")
                    else:
                        block_lines.append("")
            else:
                block_lines.append(prefix.rstrip())

        if suffix:
            if block_lines:
                block_lines[-1] = f"{block_lines[-1]}{suffix}"
            else:
                block_lines.append(suffix)

        replacement = "\n".join(block_lines)
        md_text = f"{md_text[:line_start]}{replacement}{md_text[line_end:]}"
    return md_text


def _render_list_container(container, attachments, maxchar):
    """Render a list container that includes tables without breaking the list."""
    container_soup, container_images = extract_embedded_images(
        str(container), attachments
    )
    placeholder_map = _collect_list_table_placeholders(container_soup)
    container_md = html_to_md(str(container_soup))
    for placeholder, md_value in placeholder_map.items():
        container_md = _replace_placeholder_in_md(container_md, placeholder, md_value)

    rendered_parts = []
    for chunk in split_string(container_md, maxchar=maxchar):
        if not chunk.strip():
            continue
        chunk_images = [im for im in container_images if im[0] in chunk]
        rendered_parts.append((chunk, chunk_images))
    return rendered_parts


def format_text(text: str, attachments: List[str], maxchar: int = MSG_MAX_CHAR) -> Iterator[Tuple[str, List]]:
    if not is_html(text):
        return [(p, []) for p in split_string(text, maxchar=maxchar)]

    soup = BeautifulSoup(text, "lxml")

    # split message in parts:
    #   - separate tables from the messages to be rendered with pandas
    #   - split text in multiple messages if it is too long
    parts = []
    processed_lists = set()

    def _add_part(_part):
        _part, images = extract_embedded_images(_part, attachments)
        for p in split_string(html_to_md(str(_part)), maxchar=maxchar):
            if not p.strip():
                continue
            part_images = [im for im in images if im[0] in p]
            parts.append((p, part_images))

    remain = text
    for table in get_sub_tables(soup, depth=0):
        li_parent = table.find_parent("li")
        if li_parent:
            container = li_parent.find_parent(["ul", "ol"]) or li_parent
            container_id = id(container)
            if container_id in processed_lists:
                continue

            normalized_remain = str(BeautifulSoup(remain, "lxml"))
            part, sep, tail = normalized_remain.partition(str(container))
            if sep:
                processed_lists.add(container_id)
                _add_part(part)

                parts.extend(
                    _render_list_container(container, attachments, maxchar)
                )
                remain = tail
                continue

        part, _, remain = str(BeautifulSoup(remain, "lxml")).partition(str(table))
        _add_part(part)

        table, table_images = extract_embedded_images(table, attachments)
        md_table = table_to_md(table)

        # TODO check for md_table > maxchar

        if isinstance(md_table, str):
            parts.append((md_table, table_images))
        else:
            for t in md_table:
                t_images = [im for im in table_images if im[0] in t]
                parts.append((t, t_images))
    if remain:
        _add_part(remain)

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
