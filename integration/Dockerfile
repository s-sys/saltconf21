FROM python:3.9.7

WORKDIR "/usr/src/app/"

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
    python3-dev libpq-dev build-essential cron && \
    apt-get clean all && \
    rm -rf /var/lib/apt/lists/*

COPY "./requirements.txt" "/tmp/"
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

RUN echo "0 */4 * * * curl -X POST http://127.0.0.1:8080/sync" | crontab -

COPY "./etc/supervisor/supervisord.conf" "/etc/supervisor/supervisord.conf"
COPY "./integration.py" "/usr/src/app/"
COPY "./jira_patch.py" "/usr/src/app/"
COPY "./pepper_patch.py" "/usr/src/app/"

CMD ["supervisord", "-c", "/etc/supervisor/supervisord.conf"]
