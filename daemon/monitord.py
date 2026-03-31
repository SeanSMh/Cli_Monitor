#!/usr/bin/env python3
"""Shared state daemon for cli-monitor."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from daemon_client import DEFAULT_MONITORD_HOST, DEFAULT_MONITORD_PORT, healthz
from engine.models import MonitorEvent
from engine.store import TaskStore
from registry.session_registry import merge_session_registry, read_session_registry


STORE = TaskStore()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _build_task_payload(state) -> dict:
    data = state.to_dict()
    data["tool"] = data.pop("tool_name", "")
    if not data.get("log_file"):
        data["log_file"] = str((data.get("meta") or {}).get("log_file", "") or "")
    return data


class _Handler(BaseHTTPRequestHandler):
    server_version = "cli-monitor-monitord/0.1"

    def log_message(self, format, *args):
        return

    def _write_json(self, payload: dict, status: int = 200):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._write_json({"ok": True, "ts_ms": _now_ms()})
            return

        if parsed.path == "/state":
            query = parse_qs(parsed.query)
            tool_name = str((query.get("tool") or [""])[0] or "").strip()
            tasks = [_build_task_payload(item) for item in STORE.snapshot(tool_name)]
            self._write_json({"tasks": tasks, "ts_ms": _now_ms()})
            return

        if parsed.path.startswith("/session/"):
            session_id = parsed.path.rsplit("/", 1)[-1]
            state = STORE.get(session_id)
            registry = read_session_registry(session_id) or {}
            if state is None and not registry:
                self._write_json({"error": "not_found", "session_id": session_id}, status=404)
                return
            payload = {"session_id": session_id, "task": _build_task_payload(state) if state else None, "registry": registry}
            self._write_json(payload)
            return

        self._write_json({"error": "not_found"}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/events":
            self._write_json({"error": "not_found"}, status=404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except Exception:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._write_json({"error": "invalid_json"}, status=400)
            return

        session_id = str(payload.get("session_id", "") or "").strip()
        source = str(payload.get("source", "") or "").strip()
        tool_name = str(payload.get("tool_name") or payload.get("tool") or "").strip()
        event_type = str(payload.get("event_type") or payload.get("method") or "").strip()
        body = payload.get("payload")
        if body is None:
            body = payload.get("params")
        if body is None:
            body = {}
        if source == "claude_subagent":
            sub_payload = payload.get("payload") or payload.get("params") or {}
            parent_sid = str(sub_payload.get("parent_session_id", "") or "")
            subagent_id = str(sub_payload.get("subagent_id", "") or "")
            sub_status = str(sub_payload.get("status", "") or "running")
            if not parent_sid or not subagent_id:
                self._write_json({"error": "missing_fields"}, status=400)
                return
            STORE.apply_subagent_event(parent_sid, subagent_id, sub_status)
            self._write_json({"ok": True})
            return
        if not session_id or not source or not tool_name or not event_type:
            self._write_json({"error": "missing_fields"}, status=400)
            return

        event = MonitorEvent(
            source=source,
            session_id=session_id,
            tool_name=tool_name,
            event_type=event_type,
            payload=body,
            ts_ms=int(payload.get("ts_ms") or _now_ms()),
            thread_id=str(payload.get("thread_id", "") or "").strip(),
            log_file=str(payload.get("log_file", "") or "").strip(),
            meta=payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
        )
        state = STORE.apply(event)
        if event.log_file or event.thread_id or event.meta:
            merge_session_registry(
                session_id,
                {
                    "tool": tool_name,
                    "thread_id": event.thread_id or "",
                    "log_file": event.log_file or "",
                    "state_source": source,
                    **(event.meta or {}),
                },
            )
        self._write_json({"ok": True, "task": _build_task_payload(state) if state else None})


def run_server(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def ensure_running(host: str, port: int) -> int:
    os.environ["CLI_MONITOR_DAEMON_HOST"] = host
    os.environ["CLI_MONITOR_DAEMON_PORT"] = str(port)
    if healthz():
        return 0

    log_path = "/tmp/cli-monitor-monitord.log"
    with open(log_path, "a", encoding="utf-8") as log:
        subprocess.Popen(
            [
                sys.executable,
                os.path.abspath(__file__),
                "--host",
                host,
                "--port",
                str(port),
            ],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=os.environ.copy(),
        )

    deadline = time.time() + 3.0
    while time.time() < deadline:
        if healthz():
            return 0
        time.sleep(0.1)
    return 1


def main():
    parser = argparse.ArgumentParser(description="cli-monitor shared state daemon")
    parser.add_argument("--host", default=DEFAULT_MONITORD_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_MONITORD_PORT)
    parser.add_argument("--ensure-running", action="store_true")
    args = parser.parse_args()

    if args.ensure_running:
        raise SystemExit(ensure_running(args.host, args.port))

    os.environ["CLI_MONITOR_DAEMON_HOST"] = str(args.host)
    os.environ["CLI_MONITOR_DAEMON_PORT"] = str(args.port)
    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
