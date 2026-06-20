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

# STATIC_ROOT must exist or WhiteNoise logs "No directory at: /app/staticfiles/"
# on every middleware init (including the test suite). collectstatic populates
# it at runtime (entrypoint); creating it here keeps the image quiet by default.
RUN mkdir -p /app/staticfiles

# The shared ssl-certs named volume mounts here at runtime. Create the directory
# in the image so a freshly-created (empty) volume inherits this dir's ownership
# — Docker copies the image directory + its perms into a new volume. Without it
# the volume mountpoint is root-owned and the non-root netpulse user can't write
# agent-ca.crt / netpulse.crt, which broke agent mTLS until a manual fix. The
# `chown -R netpulse:netpulse /app` below sets the ownership it propagates.
RUN mkdir -p /app/ssl

# Bake the version (git isn't present in the image). Supplied by the build —
# see docker-compose api build.args / netpulse.sh; empty in plain builds.
ARG GIT_COMMIT=""
ARG GIT_COUNT=""
ARG BUILT_AT=""
ARG SPANE_VERSION=""
ENV NETPULSE_GIT_COMMIT=$GIT_COMMIT \
    NETPULSE_GIT_COUNT=$GIT_COUNT \
    NETPULSE_BUILT_AT=$BUILT_AT \
    SPANE_VERSION=$SPANE_VERSION

RUN useradd -m -u 1000 netpulse && chown -R netpulse:netpulse /app
USER netpulse

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
# Worker recycling caps slow fd/memory leaks (workers replaced after ~1000
# requests, jittered); heartbeat file in /dev/shm avoids disk fd churn. The
# compose `command:` for the api service mirrors these flags. --timeout stays
# at 120s — some on-demand report PDFs legitimately run ~1 min.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "100", \
     "--timeout", "120", \
     "--keep-alive", "2", \
     "--worker-tmp-dir", "/dev/shm", \
     "--access-logfile", "-"]
