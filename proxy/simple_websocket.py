#!/usr/bin/env python3
"""Minimal WebSocket client/server helpers for JSON text frames."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import struct
from dataclasses import dataclass
from urllib.parse import urlparse


_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _header_value(headers: dict[str, str], key: str) -> str:
    return str(headers.get(key.lower(), "") or "").strip()


async def _read_http_headers(reader: asyncio.StreamReader) -> tuple[str, dict[str, str]]:
    raw = await reader.readuntil(b"\r\n\r\n")
    text = raw.decode("utf-8", errors="ignore")
    lines = text.split("\r\n")
    request_line = lines[0]
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return request_line, headers


async def server_handshake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    request_line, headers = await _read_http_headers(reader)
    if "upgrade: websocket" not in request_line.lower() and _header_value(headers, "upgrade").lower() != "websocket":
        raise ValueError("not a websocket upgrade request")
    key = _header_value(headers, "sec-websocket-key")
    if not key:
        raise ValueError("missing sec-websocket-key")
    accept = base64.b64encode(hashlib.sha1((key + _GUID).encode("utf-8")).digest()).decode("ascii")
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    writer.write(response.encode("utf-8"))
    await writer.drain()


@dataclass
class WebSocketConnection:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    mask_outgoing: bool

    async def recv_text(self) -> str | None:
        fragments: list[bytes] = []
        text_opcode_seen = False
        while True:
            header = await self.reader.readexactly(2)
            b1, b2 = header[0], header[1]
            fin = bool(b1 & 0x80)
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", await self.reader.readexactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", await self.reader.readexactly(8))[0]
            mask_key = await self.reader.readexactly(4) if masked else b""
            payload = await self.reader.readexactly(length) if length else b""
            if masked and payload:
                payload = bytes(byte ^ mask_key[idx % 4] for idx, byte in enumerate(payload))

            if opcode == 0x8:
                try:
                    await self.close()
                except Exception:
                    pass
                return None
            if opcode == 0x9:
                await self._send_frame(payload, opcode=0xA)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1:
                fragments.append(payload)
                text_opcode_seen = True
                if fin:
                    return b"".join(fragments).decode("utf-8", errors="ignore")
                continue
            if opcode == 0x0 and text_opcode_seen:
                fragments.append(payload)
                if fin:
                    return b"".join(fragments).decode("utf-8", errors="ignore")
                continue

    async def _send_frame(self, payload: bytes, opcode: int = 0x1) -> None:
        length = len(payload)
        first = 0x80 | (opcode & 0x0F)
        mask_bit = 0x80 if self.mask_outgoing else 0
        if length < 126:
            header = bytes([first, mask_bit | length])
        elif length < (1 << 16):
            header = bytes([first, mask_bit | 126]) + struct.pack("!H", length)
        else:
            header = bytes([first, mask_bit | 127]) + struct.pack("!Q", length)

        if self.mask_outgoing:
            mask_key = os.urandom(4)
            encoded = bytes(byte ^ mask_key[idx % 4] for idx, byte in enumerate(payload))
            self.writer.write(header + mask_key + encoded)
        else:
            self.writer.write(header + payload)
        await self.writer.drain()

    async def send_text(self, text: str) -> None:
        await self._send_frame(str(text or "").encode("utf-8"), opcode=0x1)

    async def close(self) -> None:
        if self.writer.is_closing():
            return
        try:
            await self._send_frame(b"", opcode=0x8)
        except Exception:
            pass
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except Exception:
            pass


async def open_websocket_client(url: str) -> WebSocketConnection:
    parsed = urlparse(url)
    if parsed.scheme not in {"ws", "wss"}:
        raise ValueError(f"unsupported websocket scheme: {parsed.scheme}")
    if parsed.scheme == "wss":
        raise ValueError("wss is not supported by the minimal proxy")

    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    reader, writer = await asyncio.open_connection(host, port)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    writer.write(request.encode("utf-8"))
    await writer.drain()

    status_line, headers = await _read_http_headers(reader)
    if "101" not in status_line:
        raise ValueError(f"upstream websocket handshake failed: {status_line}")
    accept = _header_value(headers, "sec-websocket-accept")
    expected = base64.b64encode(hashlib.sha1((key + _GUID).encode("utf-8")).digest()).decode("ascii")
    if accept != expected:
        raise ValueError("invalid sec-websocket-accept from upstream")

    return WebSocketConnection(reader=reader, writer=writer, mask_outgoing=True)
