FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    # Network discovery tooling (run_discovery): nmap ping sweep + service
    # scan, ping/ip/nc for connectivity checks.
    nmap \
    iputils-ping \
    iproute2 \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Bake the version (git isn't present in the image). Supplied by the build —
# see docker-compose api build.args / netpulse.sh; empty in plain builds.
ARG GIT_COMMIT=""
ARG GIT_COUNT=""
ARG BUILT_AT=""
ENV NETPULSE_GIT_COMMIT=$GIT_COMMIT \
    NETPULSE_GIT_COUNT=$GIT_COUNT \
    NETPULSE_BUILT_AT=$BUILT_AT

RUN useradd -m -u 1000 netpulse && chown -R netpulse:netpulse /app
USER netpulse

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "120", \
     "--access-logfile", "-"]
