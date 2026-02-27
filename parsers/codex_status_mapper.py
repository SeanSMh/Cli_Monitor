#!/usr/bin/env python3
"""Official Codex method -> monitor status mapping."""

from __future__ import annotations

from typing import Any

from parsers.common import (
    DEFAULT_NON_TEXT_KEYS,
    DEFAULT_TEXT_KEYS,
    collect_candidate_texts,
    extract_first_meaningful_text,
    extract_waiting_text,
)

_OFFICIAL_METHOD_MAP = {
    "turn/input_required": "WAITING",
    "turn/waiting_for_input": "WAITING",
    "turn/approval_required": "WAITING",
    "turn/confirm_required": "WAITING",
    "turn/completed": "IDLE",
    "turn/finished": "IDLE",
    "turn/stopped": "IDLE",
    "turn/interrupted": "IDLE",
    "turn/error": "IDLE",
    "turn/failed": "IDLE",
    "turn/started": "RUNNING",
    "turn/plan/updated": "RUNNING",
    "turn/diff/updated": "RUNNING",
    "item/started": "RUNNING",
    "item/updated": "RUNNING",
    "item/completed": "RUNNING",
    "thread/tokenusage/updated": "RUNNING",
    "thread/token_usage/updated": "RUNNING",
}

_TEXT_KEYS = set(DEFAULT_TEXT_KEYS)
_NON_TEXT_KEYS = set(DEFAULT_NON_TEXT_KEYS)


def normalize_method(method: str) -> str:
    return str(method or "").strip().lower()


def is_known_official_method(method: str) -> bool:
    return normalize_method(method) in _OFFICIAL_METHOD_MAP


def _extract_waiting_text(payload: Any, waiting_patterns: list[str]) -> str:
    texts = collect_candidate_texts(
        payload, text_keys=_TEXT_KEYS, non_text_keys=_NON_TEXT_KEYS
    )
    return extract_waiting_text(texts, waiting_patterns, line_limit=200)


def _extract_meaningful_text(payload: Any) -> str:
    texts = collect_candidate_texts(
        payload, text_keys=_TEXT_KEYS, non_text_keys=_NON_TEXT_KEYS
    )
    return extract_first_meaningful_text(texts, dotted_mode="slash_or_dot")


def map_official_method_to_status(
    method: str, payload: Any, waiting_patterns: list[str] | None = None
) -> tuple[str, str] | None:
    method_key = normalize_method(method)
    status = _OFFICIAL_METHOD_MAP.get(method_key)
    if not status:
        return None

    waiting_patterns = list(waiting_patterns or [])
    if status == "WAITING":
        waiting_text = _extract_waiting_text(payload, waiting_patterns)
        return "WAITING", (waiting_text or "等待确认输入")

    if status == "IDLE":
        msg = _extract_meaningful_text(payload)
        if method_key in {"turn/error", "turn/failed"} and msg:
            return "IDLE", msg
        return "IDLE", "AI 已完成回复"

    if status == "RUNNING":
        msg = _extract_meaningful_text(payload)
        return "RUNNING", (msg or "运行中...")

    return None
