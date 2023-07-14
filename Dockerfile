FROM python:3

# install elog-zulip
RUN mkdir -p venv-tracker && \
    python3 -m venv /venv-tracker && \
    . /venv-tracker/bin/activate && \
    pip install pip --upgrade

COPY . /data/elog-zulip/
RUN . /venv-tracker/bin/activate && pip install /data/elog-zulip/
RUN rm -rf /data/elog-zulip

CMD ["/venv-tracker/bin/elog-zulip-publisher", "/data/config.toml"]
