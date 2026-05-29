FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Compile proto definitions → proto_generated/
# grpcio-tools bundles the google well-known types; no extra include path needed.
COPY proto/ proto/
RUN mkdir -p proto_generated && \
    python -m grpc_tools.protoc \
        --proto_path=proto \
        --python_out=proto_generated \
        --grpc_python_out=proto_generated \
        gnmi.proto gnmi_ext.proto && \
    touch proto_generated/__init__.py

COPY ingest/ ingest/

RUN useradd -m -u 1000 netpulse && chown -R netpulse:netpulse /app
USER netpulse

EXPOSE 50051

CMD ["python", "-m", "ingest.grpc_server"]
