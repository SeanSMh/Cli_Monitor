#!/usr/bin/env python3
"""App-server structured event parsing helpers.

Parses JSON/JSONL lines that follow app-server notification style and maps
them to monitor states.
"""

from __future__ import annotations

from typing import Any

from parsers.common import (
    collect_candidate_texts,
    extract_first_meaningful_text,
    extract_waiting_text,
    parse_json_object_line,
)

_WAITING_METHOD_TOKENS = (
    "waiting",
    "input",
    "approval",
    "confirm",
    "question",
    "prompt",
    "action_required",
    "needs_input",
)
_IDLE_METHOD_TOKENS = ("completed", "finished", "done", "idle", "stopped")
_ERROR_METHOD_TOKENS = ("error", "failed", "failure", "aborted", "rejected", "denied")
_RUNNING_METHOD_TOKENS = (
    "started",
    "start",
    "running",
    "progress",
    "updated",
    "update",
    "diff",
    "plan",
    "stream",
    "tokenusage",
    "token_usage",
)


def _collect_methods(obj: dict[str, Any]) -> list[str]:
    methods: list[str] = []

    def _push(value: Any) -> None:
        if isinstance(value, str):
            s = value.strip()
            if s:
                methods.append(s.lower())

    for key in ("method", "event", "type", "name", "kind"):
        _push(obj.get(key))

    for key in ("notification", "params", "payload", "data", "result"):
        value = obj.get(key)
        if isinstance(value, dict):
            for field in ("method", "event", "type", "name", "kind", "status", "state", "phase"):
                _push(value.get(field))

    deduped = []
    seen = set()
    for item in methods:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _has_token(values: list[str], tokens: tuple[str, ...]) -> bool:
    for value in values:
        for token in tokens:
            if token in value:
                return True
    return False


def parse_app_server_status(
    lines: list[str], waiting_patterns: list[str] | None = None
) -> tuple[str, str] | None:
    """Parse app-server JSON notification lines and infer monitor status."""

    waiting_patterns = list(waiting_patterns or [])
    parsed: list[tuple[list[str], list[str]]] = []
    for raw_line in lines:
        obj = parse_json_object_line(raw_line)
        if obj is None:
            continue
        methods = _collect_methods(obj)
        texts = collect_candidate_texts(obj)
        parsed.append((methods, texts))

    if not parsed:
        return None

    saw_signal = False
    for methods, texts in reversed(parsed):
        waiting_text = extract_waiting_text(texts, waiting_patterns, line_limit=200)
        if waiting_text:
            return "WAITING", waiting_text

        if _has_token(methods, _WAITING_METHOD_TOKENS):
            saw_signal = True
            return "WAITING", waiting_text or "等待确认输入"

        if _has_token(methods, _IDLE_METHOD_TOKENS):
            saw_signal = True
            return "IDLE", "AI 已完成回复"

        if _has_token(methods, _ERROR_METHOD_TOKENS):
            saw_signal = True
            err = extract_first_meaningful_text(texts, dotted_mode="slash_or_dot")
            return "IDLE", err or "AI 已完成回复"

        if _has_token(methods, _RUNNING_METHOD_TOKENS):
            saw_signal = True
            msg = extract_first_meaningful_text(texts, dotted_mode="slash_or_dot")
            return "RUNNING", msg or "运行中..."

    if saw_signal:
        return "RUNNING", "运行中..."
    return None
