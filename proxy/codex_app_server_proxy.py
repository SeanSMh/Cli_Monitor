#!/usr/bin/env python3
"""Single-client Codex app-server proxy."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from typing import Any

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from daemon_client import post_event
from registry.session_registry import delete_session_registry, merge_session_registry

from proxy.simple_websocket import WebSocketConnection, open_websocket_client, server_handshake


def _extract_payload(message: dict[str, Any]) -> Any:
    for key in ("params", "result", "payload"):
        value = message.get(key)
        if value is not None:
            return value
    return {}


def _extract_thread_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    if isinstance(payload.get("thread"), dict):
        thread = payload.get("thread") or {}
        thread_id = str(thread.get("id", "") or "").strip()
        if thread_id:
            return thread_id
    return str(payload.get("thread_id") or payload.get("threadId") or payload.get("id") or "").strip()


class CodexProxyServer:
    def __init__(self, listen_host: str, listen_port: int, upstream_url: str, session_id: str, log_file: str):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.upstream_url = upstream_url
        self.session_id = session_id
        self.log_file = log_file
        self.thread_id = ""
        self._client_connected = False

    def _mirror_event(self, message: dict[str, Any]) -> None:
        method = str(message.get("method", "") or "").strip()
        if not method:
            return
        payload = _extract_payload(message)
        thread_id = _extract_thread_id(payload) or self.thread_id
        if thread_id and not self.thread_id:
            self.thread_id = thread_id
        meta = {"thread_id": thread_id} if thread_id else {}
        if "id" in message:
            meta["request_id"] = message.get("id")
        post_event(
            {
                "source": "codex_proxy",
                "session_id": self.session_id,
                "tool_name": "codex",
                "event_type": method,
                "payload": payload,
                "thread_id": thread_id,
                "log_file": self.log_file,
                "meta": meta,
            }
        )
        if thread_id:
            merge_session_registry(
                self.session_id,
                {"tool": "codex", "thread_id": thread_id, "log_file": self.log_file, "state_source": "codex_proxy"},
            )

    async def _pipe(self, source: WebSocketConnection, sink: WebSocketConnection, *, mirror: bool = False):
        while True:
            message = await source.recv_text()
            if message is None:
                break
            if mirror:
                try:
                    decoded = json.loads(message)
                except Exception:
                    decoded = None
                if isinstance(decoded, dict):
                    self._mirror_event(decoded)
            await sink.send_text(message)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        if self._client_connected:
            writer.close()
            await writer.wait_closed()
            return
        self._client_connected = True

        client_ws = None
        upstream_ws = None
        try:
            await server_handshake(reader, writer)
            client_ws = WebSocketConnection(reader=reader, writer=writer, mask_outgoing=False)
            upstream_ws = await open_websocket_client(self.upstream_url)
            merge_session_registry(
                self.session_id,
                {
                    "tool": "codex",
                    "log_file": self.log_file,
                    "proxy_url": f"ws://{self.listen_host}:{self.listen_port}",
                    "real_app_server_url": self.upstream_url,
                    "state_source": "codex_proxy",
                },
            )
            tasks = [
                asyncio.create_task(self._pipe(client_ws, upstream_ws, mirror=False)),
                asyncio.create_task(self._pipe(upstream_ws, client_ws, mirror=True)),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            for task in done:
                with contextlib.suppress(
                    asyncio.CancelledError,
                    asyncio.IncompleteReadError,
                    ConnectionResetError,
                    BrokenPipeError,
                ):
                    task.result()
        finally:
            for ws in (client_ws, upstream_ws):
                if ws is None:
                    continue
                try:
                    await ws.close()
                except Exception:
                    pass
            self._client_connected = False

    async def serve(self):
        server = await asyncio.start_server(self._handle_client, self.listen_host, self.listen_port)
        async with server:
            await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="single-client Codex app-server proxy")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--upstream-url", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--log-file", default="")
    args = parser.parse_args()

    merge_session_registry(
        args.session_id,
        {
            "tool": "codex",
            "log_file": args.log_file,
            "proxy_url": f"ws://{args.listen_host}:{args.listen_port}",
            "real_app_server_url": args.upstream_url,
            "proxy_pid": os.getpid(),
            "state_source": "codex_proxy",
        },
    )
    try:
        asyncio.run(
            CodexProxyServer(
                listen_host=args.listen_host,
                listen_port=args.listen_port,
                upstream_url=args.upstream_url,
                session_id=args.session_id,
                log_file=args.log_file,
            ).serve()
        )
    finally:
        delete_session_registry(args.session_id)


if __name__ == "__main__":
    main()
