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

# 4317/tcp — OTLP gRPC receiver
# 4318/tcp — OTLP HTTP receiver
EXPOSE 4317/tcp
EXPOSE 4318/tcp

CMD ["python", "-m", "ingest.otlp_server"]
