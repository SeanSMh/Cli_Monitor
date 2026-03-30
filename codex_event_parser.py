#!/usr/bin/env python3
"""Codex structured event parsing helpers.

This module parses JSON/JSONL event lines (for example from `codex exec --json`)
and maps them to monitor states.
"""

from __future__ import annotations

from typing import Any

from parsers.common import (
    collect_candidate_texts,
    extract_first_meaningful_text,
    extract_waiting_text,
    parse_json_object_line,
)

_WAITING_TYPE_TOKENS = (
    "waiting",
    "input_required",
    "needs_input",
    "approval",
    "confirm",
    "prompt",
    "question",
    "action_required",
    "review_required",
)
_IDLE_TYPE_TOKENS = ("completed", "finished", "done", "idle", "stopped")
_RUNNING_TYPE_TOKENS = (
    "started",
    "running",
    "progress",
    "delta",
    "stream",
    "update",
    "plan",
    "diff",
    "thinking",
)
_ERROR_TYPE_TOKENS = ("error", "failed", "failure", "rejected", "denied", "aborted")


def _collect_event_types(obj: dict[str, Any]) -> list[str]:
    event_types: list[str] = []

    def _push(value: Any) -> None:
        if isinstance(value, str):
            v = value.strip()
            if v:
                event_types.append(v.lower())

    _push(obj.get("type"))
    _push(obj.get("event_type"))
    _push(obj.get("event"))
    _push(obj.get("name"))
    _push(obj.get("kind"))

    for key in ("event", "msg", "payload", "data"):
        value = obj.get(key)
        if isinstance(value, dict):
            _push(value.get("type"))
            _push(value.get("event"))
            _push(value.get("name"))
            _push(value.get("kind"))

    deduped = []
    seen = set()
    for item in event_types:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _has_token(event_types: list[str], tokens: tuple[str, ...]) -> bool:
    for event_type in event_types:
        for token in tokens:
            if token in event_type:
                return True
    return False


def _contains_exact_event(event_types: list[str], needle: str) -> bool:
    needle_key = str(needle or "").strip().lower()
    for event_type in event_types:
        if str(event_type or "").strip().lower() == needle_key:
            return True
    return False


def parse_codex_structured_status(
    lines: list[str], waiting_patterns: list[str] | None = None
) -> tuple[str, str] | None:
    """Parse recent lines and infer status from Codex structured events.

    Returns `(status, message)` when a structured signal is detected,
    otherwise `None`.
    """

    waiting_patterns = list(waiting_patterns or [])
    parsed_events: list[tuple[list[str], list[str]]] = []
    for raw_line in lines:
        obj = parse_json_object_line(raw_line)
        if obj is None:
            continue
        event_types = _collect_event_types(obj)
        texts = collect_candidate_texts(
            obj, non_text_keys={"type", "event_type", "event", "name", "kind"}
        )
        parsed_events.append((event_types, texts))

    if not parsed_events:
        return None

    saw_structured_signal = False
    for event_types, texts in reversed(parsed_events):
        waiting_text = extract_waiting_text(texts, waiting_patterns, line_limit=200)
        if waiting_text:
            return "WAITING", waiting_text

        if _contains_exact_event(event_types, "item.completed"):
            saw_structured_signal = True
            msg = extract_first_meaningful_text(texts, dotted_mode="dot_only")
            return "RUNNING", msg or "运行中..."

        if _has_token(event_types, _WAITING_TYPE_TOKENS):
            saw_structured_signal = True
            return "WAITING", waiting_text or "等待确认输入"

        if _has_token(event_types, _IDLE_TYPE_TOKENS):
            saw_structured_signal = True
            return "IDLE", "AI 已完成回复"

        if _has_token(event_types, _ERROR_TYPE_TOKENS):
            saw_structured_signal = True
            err = waiting_text or extract_first_meaningful_text(texts, dotted_mode="dot_only")
            return "IDLE", err or "AI 已完成回复"

        if _has_token(event_types, _RUNNING_TYPE_TOKENS):
            saw_structured_signal = True
            msg = extract_first_meaningful_text(texts, dotted_mode="dot_only")
            return "RUNNING", msg or "运行中..."

    if saw_structured_signal:
        return "RUNNING", "运行中..."
    return None
