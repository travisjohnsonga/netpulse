"""InfluxDB line-protocol writer. Thin wrapper so handlers stay testable."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class InfluxWriter:
    def __init__(self, url: str, token: str, org: str, bucket: str) -> None:
        self._bucket = bucket
        self._write_api = None
        try:
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import ASYNCHRONOUS
            client = InfluxDBClient(url=url, token=token, org=org)
            self._write_api = client.write_api(write_options=ASYNCHRONOUS)
            logger.info("InfluxDB connected: %s", url)
        except Exception as exc:
            logger.warning("InfluxDB unavailable — writes disabled: %s", exc)

    @property
    def available(self) -> bool:
        return self._write_api is not None

    def write(self, measurement: str, tags: dict[str, str], fields: dict[str, float | str], ts=None) -> None:
        if not self._write_api:
            return
        try:
            from influxdb_client import Point
            p = Point(measurement)
            for k, v in tags.items():
                p = p.tag(k, str(v))
            for k, v in fields.items():
                p = p.field(k, float(v) if isinstance(v, (int, float)) else str(v))
            if ts:
                p = p.time(ts)
            self._write_api.write(bucket=self._bucket, record=p)
        except Exception as exc:
            logger.error("InfluxDB write error: %s", exc)
