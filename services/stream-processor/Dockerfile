FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY stream_processor/ stream_processor/

RUN useradd -m -u 1000 netpulse && chown -R netpulse:netpulse /app
USER netpulse

CMD ["python", "-m", "stream_processor.main"]
