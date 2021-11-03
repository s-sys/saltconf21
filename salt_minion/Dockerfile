FROM ubuntu:21.04

USER root

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
    python3 python3-pip ca-certificates curl

ARG SALT_ARCHIVE_URL="https://repo.saltproject.io/py3/ubuntu/20.04/amd64/latest"
RUN curl -fsSL -o /usr/share/keyrings/salt-archive-keyring.gpg "$SALT_ARCHIVE_URL/salt-archive-keyring.gpg" && \
    echo "deb [signed-by=/usr/share/keyrings/salt-archive-keyring.gpg arch=amd64] $SALT_ARCHIVE_URL focal main" | \
    tee /etc/apt/sources.list.d/salt.list
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends salt-minion

RUN apt-get clean all && \
    rm -rf /var/lib/apt/lists/*

COPY "./requirements.txt" "/tmp/"
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

COPY "./etc/apt/sources.list" "/etc/apt/sources.list"
RUN rm /etc/apt/sources.list.d/salt.list && \
    apt-get update -y

COPY "./etc/supervisor/supervisord.conf" "/etc/supervisor/supervisord.conf"
COPY "./etc/salt/minion.d/" "/etc/salt/minion.d/"

CMD ["supervisord", "-c", "/etc/supervisor/supervisord.conf"]
