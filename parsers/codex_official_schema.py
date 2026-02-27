#!/usr/bin/env python3
"""Strict-ish parser for official Codex app-server notifications."""

from __future__ import annotations

from typing import Any

from parsers.common import parse_json_object_line
from parsers.codex_status_mapper import is_known_official_method, map_official_method_to_status


_OFFICIAL_METHOD_PREFIXES = (
    "turn/",
    "thread/",
    "item/",
    "command/",
    "session/",
    "config/",
    "account/",
    "app/",
    "mcpserver/",
)

def _extract_method_and_payload(obj: dict[str, Any]) -> tuple[str, Any] | None:
    method = obj.get("method")
    if not isinstance(method, str) or not method.strip():
        return None
    method_key = method.strip().lower()
    if not method_key.startswith(_OFFICIAL_METHOD_PREFIXES):
        return None

    payload = obj.get("params")
    if payload is None:
        payload = obj.get("result")
    if payload is None:
        payload = obj.get("payload")
    if payload is None:
        payload = obj
    return method_key, payload


def parse_codex_official_status(
    lines: list[str], waiting_patterns: list[str] | None = None
) -> tuple[tuple[str, str] | None, int]:
    """Return ((status, message) or None, unknown_official_event_count)."""

    waiting_patterns = list(waiting_patterns or [])
    events: list[tuple[str, Any]] = []
    for raw_line in lines:
        obj = parse_json_object_line(raw_line)
        if obj is None:
            continue
        extracted = _extract_method_and_payload(obj)
        if extracted is None:
            continue
        events.append(extracted)

    if not events:
        return None, 0

    unknown_count = 0
    for method, payload in reversed(events):
        mapped = map_official_method_to_status(
            method, payload, waiting_patterns=waiting_patterns
        )
        if mapped is not None:
            return mapped, unknown_count
        if not is_known_official_method(method):
            unknown_count += 1
    return None, unknown_count
