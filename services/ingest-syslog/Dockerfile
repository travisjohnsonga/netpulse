FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY ingest/ ingest/

RUN useradd -m -u 1000 netpulse && chown -R netpulse:netpulse /app
USER netpulse

# Ports are documented here; actual binding is controlled by the compose ports mapping.
# 514/udp — RFC 3164 / RFC 5424 syslog over UDP
# 601/tcp — RFC 6587 syslog over TCP (newline and octet-count framing)
EXPOSE 514/udp 601/tcp

CMD ["python", "-m", "ingest.syslog_server"]
