"""ClamAV MalwareScanPort implementation (§3.2.7, TB-6). Talks to
clamd's INSTREAM protocol directly over a plain TCP socket — the
protocol is a handful of length-prefixed chunks and one response line,
simple enough that a raw client is the "thin, in-house, no unnecessary
dependency" choice (ADR-3.5's philosophy applied here too) rather than
pulling in a wrapper package for three socket calls.

Runs against clamd's own process, not embedded in this process — TB-6's
mitigation for "parsers are the most historically vulnerable code
class" extends to the scanner itself: it's an external service handling
untrusted bytes, isolated in its own container.
"""

from __future__ import annotations

import asyncio

from aether.ports.malware_scan import ScanResult

_CHUNK_SIZE = 65536
_MAX_RESPONSE_BYTES = 4096


class ClamAvScanner:
    def __init__(self, *, host: str, port: int, timeout_seconds: float = 30.0) -> None:
        self._host = host
        self._port = port
        self._timeout_seconds = timeout_seconds

    async def scan(self, content: bytes) -> ScanResult:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), timeout=self._timeout_seconds
        )
        try:
            writer.write(b"zINSTREAM\0")
            for offset in range(0, len(content), _CHUNK_SIZE):
                chunk = content[offset : offset + _CHUNK_SIZE]
                writer.write(len(chunk).to_bytes(4, "big"))
                writer.write(chunk)
            writer.write((0).to_bytes(4, "big"))  # zero-length chunk terminates the stream
            await asyncio.wait_for(writer.drain(), timeout=self._timeout_seconds)

            response = await asyncio.wait_for(
                reader.read(_MAX_RESPONSE_BYTES), timeout=self._timeout_seconds
            )
        finally:
            writer.close()
            await writer.wait_closed()

        return _parse_response(response)


def _parse_response(response: bytes) -> ScanResult:
    text = response.decode("utf-8", errors="replace").strip("\x00").strip()
    # clamd's INSTREAM reply is one line: "stream: OK", "stream: <sig> FOUND",
    # or "stream: <message> ERROR".
    body = text.removeprefix("stream:").strip()
    if body == "OK":
        return ScanResult(clean=True, signature=None)
    if body.endswith("FOUND"):
        signature = body.removesuffix("FOUND").strip()
        return ScanResult(clean=False, signature=signature)
    raise RuntimeError(f"unexpected clamd response: {text!r}")
