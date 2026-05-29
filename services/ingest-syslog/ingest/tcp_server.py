"""
Async TCP syslog receiver — RFC 6587 framing.

Supports both framing modes on the same connection:
  • Octet-counting  (§3.4.1): message is prefixed with "<length> "
  • Non-transparent (§3.4.2): messages delimited by newline (\\n)

Detection is per-message: if the first bytes on a new message look like
"<digits> <", assume octet-counting; otherwise fall back to newline framing.
This covers the common case where a device switches framing mid-session
(unusual but possible with misconfigured devices).
"""
import asyncio
import logging
import re

from .parser import parse
from .publisher import NATSPublisher

logger = logging.getLogger(__name__)

_OCTET_COUNT_RE = re.compile(rb"^(\d+) ")


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    publisher: NATSPublisher,
    max_line: int,
) -> None:
    peername = writer.get_extra_info("peername")
    source_ip = peername[0]
    source_port = peername[1]
    logger.debug("TCP client connected: %s:%d", source_ip, source_port)
    n = 0

    try:
        while True:
            # Peek at up to 20 bytes to detect framing without consuming
            header = await reader.read(20)
            if not header:
                break  # client closed connection

            m = _OCTET_COUNT_RE.match(header)
            if m:
                # Octet-counting: length SP <message>
                length = int(m.group(1))
                consumed = len(header) - len(header.lstrip(b"0123456789")) - 1  # digits + space
                # We already consumed `len(header)` bytes; read the rest of the message
                already_read = header[len(m.group(0)):]
                remaining = length - len(already_read)
                if remaining > 0:
                    if remaining > max_line:
                        logger.warning(
                            "octet-count %d exceeds max_line %d from %s — skipping",
                            length, max_line, source_ip,
                        )
                        await reader.readexactly(remaining)
                        continue
                    rest = await reader.readexactly(remaining)
                    data = already_read + rest
                else:
                    data = already_read[:length]
            else:
                # Non-transparent framing: treat as a newline-delimited stream.
                # Put back the peeked bytes by prepending to the next readline.
                # asyncio.StreamReader doesn't support unread, so we inline the rest.
                rest = await reader.readline()
                data = header + rest
                data = data.rstrip(b"\r\n")

            if not data:
                continue

            try:
                msg = parse(data, source_ip, source_port, "tcp")
                await publisher.publish(msg)
                n += 1
            except Exception as exc:
                logger.error("parse/publish error from %s: %s", source_ip, exc)

    except asyncio.IncompleteReadError:
        pass  # client disconnected mid-octet-count frame
    except Exception as exc:
        logger.error("TCP client error %s:%d: %s", source_ip, source_port, exc, exc_info=True)
    finally:
        logger.debug("TCP client %s:%d disconnected — %d messages", source_ip, source_port, n)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
