#!/usr/bin/env python3
"""Client helpers for monitord HTTP API."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_MONITORD_HOST = os.environ.get("CLI_MONITOR_DAEMON_HOST", "127.0.0.1")
DEFAULT_MONITORD_PORT = int(os.environ.get("CLI_MONITOR_DAEMON_PORT", "8766"))


def monitord_base_url() -> str:
    host = os.environ.get("CLI_MONITOR_DAEMON_HOST", DEFAULT_MONITORD_HOST)
    port = int(os.environ.get("CLI_MONITOR_DAEMON_PORT", str(DEFAULT_MONITORD_PORT)))
    return f"http://{host}:{port}"


def _request_json(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 0.35) -> dict[str, Any] | None:
    url = monitord_base_url().rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, TimeoutError, ValueError):
        return None


def get_state(tool_name: str = "") -> dict[str, Any] | None:
    path = "/state"
    if tool_name:
        query = urllib.parse.urlencode({"tool": tool_name})
        path = f"{path}?{query}"
    return _request_json(path)


def get_session(session_id: str) -> dict[str, Any] | None:
    if not session_id:
        return None
    return _request_json(f"/session/{urllib.parse.quote(session_id)}")


def post_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    return _request_json("/events", method="POST", payload=payload, timeout=1.0)


def healthz() -> bool:
    payload = _request_json("/healthz")
    return bool(payload and payload.get("ok"))
