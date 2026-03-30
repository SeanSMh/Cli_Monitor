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
    "turn/input_required": "WAITING_INPUT",
    "turn/waiting_for_input": "WAITING_INPUT",
    "turn/approval_required": "WAITING_APPROVAL",
    "turn/confirm_required": "WAITING_APPROVAL",
    "turn/completed": "IDLE",
    "turn/finished": "IDLE",
    "turn/stopped": "IDLE",
    "turn/interrupted": "IDLE",
    "turn/error": "ERROR",
    "turn/failed": "ERROR",
    "turn/started": "RUNNING",
    "turn/plan/updated": "RUNNING",
    "turn/diff/updated": "RUNNING",
    "item/started": "RUNNING",
    "item/updated": "RUNNING",
    "item/completed": "RUNNING",
    "item/commandexecution/requestapproval": "WAITING_APPROVAL",
    "item/filechange/requestapproval": "WAITING_APPROVAL",
    "item/permissions/requestapproval": "WAITING_APPROVAL",
    "item/tool/requestuserinput": "WAITING_INPUT",
    "mcpserver/elicitation/request": "WAITING_INPUT",
    "thread/started": "THREAD_STATUS",
    "thread/status/changed": "THREAD_STATUS",
    "thread/tokenusage/updated": "RUNNING",
    "thread/token_usage/updated": "RUNNING",
}

_TEXT_KEYS = set(DEFAULT_TEXT_KEYS)
_NON_TEXT_KEYS = set(DEFAULT_NON_TEXT_KEYS)
_IDLE_FALLBACK = "AI 已完成回复"
_NOT_LOADED_FALLBACK = "线程未加载"


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


def _extract_prioritized_text(payload: Any, keys: tuple[str, ...]) -> str:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _extract_thread_status(payload: Any) -> tuple[str, list[str], str]:
    if not isinstance(payload, dict):
        return "", [], ""
    thread = payload.get("thread")
    if isinstance(thread, dict):
        payload = thread
    status = payload.get("status")
    if isinstance(status, dict):
        status_type = str(status.get("type", "") or "").strip().lower()
        flags = status.get("activeFlags") or status.get("active_flags") or []
        text = (
            _extract_prioritized_text(status, ("message", "title", "summary", "description", "reason"))
            or _extract_prioritized_text(payload, ("preview", "title", "summary", "message", "name", "description", "reason"))
        )
        return status_type, [str(item or "").strip().lower() for item in flags], text
    status_type = str(payload.get("status") or payload.get("state") or "").strip().lower()
    flags = payload.get("activeFlags") or payload.get("active_flags") or []
    text = _extract_prioritized_text(payload, ("preview", "title", "summary", "message", "name", "description", "reason"))
    return status_type, [str(item or "").strip().lower() for item in flags], text


def _extract_turn_text(payload: Any) -> str:
    if isinstance(payload, dict):
        turn = payload.get("turn")
        if isinstance(turn, dict):
            text = _extract_prioritized_text(turn, ("summary", "message", "title", "description", "reason"))
            if text:
                return text
    return _extract_prioritized_text(payload, ("summary", "message", "title", "description", "reason"))


def _normalize_flag(value: Any) -> str:
    return str(value or "").strip().lower()


def _extract_mcp_approval_kind(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    meta = payload.get("_meta")
    if not isinstance(meta, dict):
        meta = payload.get("meta")
    if not isinstance(meta, dict):
        return ""
    return _normalize_flag(meta.get("codex_approval_kind") or meta.get("codexApprovalKind"))


def map_official_method_to_status(
    method: str, payload: Any, waiting_patterns: list[str] | None = None
) -> tuple[str, str] | None:
    method_key = normalize_method(method)
    status = _OFFICIAL_METHOD_MAP.get(method_key)
    if not status:
        return None

    waiting_patterns = list(waiting_patterns or [])
    if status in {"WAITING_APPROVAL", "WAITING_INPUT"}:
        if method_key == "mcpserver/elicitation/request" and _extract_mcp_approval_kind(payload) == "mcp_tool_call":
            status = "WAITING_APPROVAL"
        waiting_text = _extract_waiting_text(payload, waiting_patterns)
        fallback = "等待审批" if status == "WAITING_APPROVAL" else "等待输入"
        if status == "WAITING_APPROVAL":
            waiting_text = waiting_text or _extract_prioritized_text(payload, ("reason", "command", "message", "title"))
        else:
            waiting_text = waiting_text or _extract_prioritized_text(payload, ("question", "message", "title"))
            if not waiting_text and isinstance(payload, dict):
                questions = payload.get("questions")
                if isinstance(questions, list):
                    for question in questions:
                        waiting_text = _extract_prioritized_text(question, ("question", "header", "id"))
                        if waiting_text:
                            break
        return status, (waiting_text or fallback)

    if status == "THREAD_STATUS":
        status_type, flags, message = _extract_thread_status(payload)
        if status_type == "active":
            if "waitingonapproval" in flags:
                return "WAITING_APPROVAL", message or "等待审批"
            if "waitingonuserinput" in flags:
                return "WAITING_INPUT", message or "等待输入"
            return "RUNNING", message or "运行中..."
        if status_type == "idle":
            return "IDLE", message or _IDLE_FALLBACK
        if status_type == "systemerror":
            return "ERROR", message or "系统错误"
        if status_type == "notloaded":
            return "IDLE", message or _NOT_LOADED_FALLBACK
        return None

    if status == "IDLE":
        msg = _extract_turn_text(payload) or _extract_meaningful_text(payload)
        if method_key in {"turn/error", "turn/failed"} and msg:
            return "IDLE", msg
        return "IDLE", msg or _IDLE_FALLBACK

    if status == "ERROR":
        msg = _extract_meaningful_text(payload)
        return "ERROR", msg or "系统错误"

    if status == "RUNNING":
        msg = _extract_meaningful_text(payload)
        return "RUNNING", (msg or "运行中...")

    return None
