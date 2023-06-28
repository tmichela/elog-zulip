#!/usr/bin/env python
from pathlib import Path
import re
from setuptools import setup, find_packages


def read(*parts):
    return Path(__file__).parent.joinpath(*parts).read_text()


def find_version(*parts):
    vers_file = read(*parts)
    match = re.search(r'^__version__ = "(\d+\.\d+\.\d+)"', vers_file, re.M)
    if match is not None:
        return match.group(1)
    raise RuntimeError("Unable to find version string.")


setup(
    name="ELog-zulip",
    version=find_version("elog_zulip", "__init__.py"),
    author="Thomas Michelat",
    author_email="thomas.michelat@gmail.com",
    maintainer="Thomas Michelat",
    url="",
    description=("Publish ELog entries to Zulip"),
    long_description=read("README.md"),
    license="BSD-3-Clause",
    entry_points={
        "console_scripts": [
            "elog-zulip-publisher = elog_zulip.elog:main",
        ],
    },

    python_requires='>=3.7',
    install_requires=[
        'bs4',
        'dataset',
        'elog @ https://github.com/paulscherrerinstitute/py_elog/releases/tag/1.3.16',
        'html2text',
        'jinja2',
        'loguru',
        'pandas',
        'sqlalchemy==1.4.48',
        'tabulate>=0.8.10',
        'toml',
        'zulip>=0.7.1',
    ],
    packages=find_packages(),
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Console',
        'License :: OSI Approved :: BSD License',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
    ]
)
