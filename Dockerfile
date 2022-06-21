FROM python:3

# install pandoc
RUN mkdir -p data && \
    cd data/ && \
    wget https://github.com/jgm/pandoc/releases/download/2.14.1/pandoc-2.14.1-1-amd64.deb && \
    dpkg -i pandoc-2.14.1-1-amd64.deb && \
    rm pandoc-2.14.1-1-amd64.deb

# install elog-zulip
RUN mkdir -p venv-tracker && \
    python3 -m venv /venv-tracker && \
    . /venv-tracker/bin/activate && \
    pip install pip --upgrade

COPY . /data/elog-zulip/
RUN . /venv-tracker/bin/activate && pip install /data/elog-zulip/
RUN rm -rf /data/elog-zulip

CMD ["/venv-tracker/bin/elog-zulip-publisher", "/data/config.toml"]
