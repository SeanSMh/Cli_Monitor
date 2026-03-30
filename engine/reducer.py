#!/usr/bin/env python3
"""State reducer for monitor events."""

from __future__ import annotations

import time
from typing import Any

from parsers.common import collect_candidate_texts, extract_first_meaningful_text, extract_waiting_text

from engine.models import MonitorEvent, TaskState, STATUS_IDLE, STATUS_RUNNING, STATUS_RATE_LIMITED

WAITING_APPROVAL = "WAITING_APPROVAL"
WAITING_INPUT = "WAITING_INPUT"
IDLE_FALLBACK = "AI 已完成回复"
NOT_LOADED_FALLBACK = "线程未加载"
WAITING_STATUSES = {WAITING_APPROVAL, WAITING_INPUT}

_CLAUDE_HOOK_STATUS_MAP: dict[str, str] = {
    "idle": STATUS_IDLE,
    "running": STATUS_RUNNING,
}


def _map_claude_hook_event(previous: TaskState | None, event: MonitorEvent) -> tuple[str, str] | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    rate_limit = payload.get("rateLimitResetAt")
    status_str = str(payload.get("status", "") or "").lower()

    if rate_limit:
        return STATUS_RATE_LIMITED, ""

    mapped = _CLAUDE_HOOK_STATUS_MAP.get(status_str)
    if mapped is None:
        return None
    msg = "AI 已完成回复" if mapped == STATUS_IDLE else "运行中..."
    return mapped, msg


def now_ms() -> int:
    return int(time.time() * 1000)


def _clip_message(text: str, limit: int = 200) -> str:
    s = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    if len(s) > limit:
        return s[:limit]
    return s


def _extract_payload_text(payload: Any) -> str:
    texts = collect_candidate_texts(payload)
    return _clip_message(extract_first_meaningful_text(texts, dotted_mode="slash_or_dot"))


def _extract_prioritized_text(payload: Any, keys: tuple[str, ...]) -> str:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _clip_message(value.strip())
    return ""


def _normalize_flag(flag: Any) -> str:
    return str(flag or "").strip().lower()


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
        return status_type, [_normalize_flag(item) for item in flags], text

    status_type = str(payload.get("status") or payload.get("state") or "").strip().lower()
    flags = payload.get("activeFlags") or payload.get("active_flags") or []
    text = _extract_prioritized_text(payload, ("preview", "title", "summary", "message", "name", "description", "reason"))
    return status_type, [_normalize_flag(item) for item in flags], text


def _extract_thread_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    thread = payload.get("thread")
    if isinstance(thread, dict):
        thread_id = str(thread.get("id", "") or "").strip()
        if thread_id:
            return thread_id
    return str(payload.get("thread_id") or payload.get("threadId") or payload.get("id") or "").strip()


def _extract_turn_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    turn = payload.get("turn")
    if isinstance(turn, dict):
        text = _extract_prioritized_text(turn, ("summary", "message", "title", "description", "reason"))
        if text:
            return text
    return _extract_prioritized_text(payload, ("summary", "message", "title", "description", "reason"))


def _normalize_request_id(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_request_id(payload: Any, meta: dict[str, Any] | None = None) -> str:
    if isinstance(meta, dict):
        request_id = _normalize_request_id(meta.get("request_id"))
        if request_id:
            return request_id
    if not isinstance(payload, dict):
        return ""
    return _normalize_request_id(payload.get("requestId") or payload.get("request_id"))


def _extract_mcp_approval_kind(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    meta = payload.get("_meta")
    if not isinstance(meta, dict):
        meta = payload.get("meta")
    if not isinstance(meta, dict):
        return ""
    return _normalize_flag(meta.get("codex_approval_kind") or meta.get("codexApprovalKind"))


def _is_meaningful_idle_message(text: str) -> bool:
    normalized = _clip_message(text)
    return normalized not in {"", "运行中...", "初始化...", "等待审批", "等待输入", "系统错误"}


def _resolve_idle_message(previous: TaskState | None, candidate: str) -> str:
    candidate = _clip_message(candidate)
    if candidate:
        return candidate
    previous_message = previous.message if previous else ""
    if _is_meaningful_idle_message(previous_message):
        return _clip_message(previous_message)
    return IDLE_FALLBACK


def _map_active_status(flags: list[str], payload: Any) -> tuple[str, str]:
    flags = [_normalize_flag(item) for item in (flags or [])]
    message = _extract_payload_text(payload)
    if "waitingonapproval" in flags:
        return WAITING_APPROVAL, message or "等待审批"
    if "waitingonuserinput" in flags:
        return WAITING_INPUT, message or "等待输入"
    return "RUNNING", message or "运行中..."


def _map_server_request(event_type: str, payload: Any) -> tuple[str, str] | None:
    event_key = str(event_type or "").strip().lower()
    waiting_patterns = [r"needs your approval", r"approval", r"confirm", r"continue", r"proceed"]

    if event_key in {
        "item/commandexecution/requestapproval",
        "item/filechange/requestapproval",
        "item/permissions/requestapproval",
    }:
        message = (
            _extract_prioritized_text(payload, ("reason", "command"))
            or extract_waiting_text(collect_candidate_texts(payload), waiting_patterns, line_limit=200)
            or _extract_payload_text(payload)
        )
        return WAITING_APPROVAL, message or "等待审批"

    if event_key == "item/tool/requestuserinput":
        questions = payload.get("questions") if isinstance(payload, dict) else None
        if isinstance(questions, list):
            for question in questions:
                if isinstance(question, dict):
                    text = _extract_prioritized_text(question, ("question", "header", "id"))
                    if text:
                        return WAITING_INPUT, text
        message = _extract_payload_text(payload)
        return WAITING_INPUT, message or "等待输入"

    if event_key == "mcpserver/elicitation/request":
        message = (
            _extract_prioritized_text(payload, ("message", "serverName"))
            or extract_waiting_text(collect_candidate_texts(payload), waiting_patterns, line_limit=200)
        )
        approval_kind = _extract_mcp_approval_kind(payload)
        if approval_kind == "mcp_tool_call":
            return WAITING_APPROVAL, message or "等待审批"
        if "approval" in str(message or "").lower():
            return WAITING_APPROVAL, message or "等待审批"
        return WAITING_INPUT, message or "等待输入"

    return None


def _restore_status_after_request(previous: TaskState | None, payload: Any) -> tuple[str, str] | None:
    if previous is None or previous.status not in WAITING_STATUSES:
        return None

    payload_thread_id = _extract_thread_id(payload)
    if payload_thread_id and previous.thread_id and payload_thread_id != previous.thread_id:
        return None

    resolved_request_id = _extract_request_id(payload)
    pending_request_id = _normalize_request_id(previous.meta.get("pending_request_id"))
    if pending_request_id and resolved_request_id and pending_request_id != resolved_request_id:
        return None

    restore_status = str(previous.meta.get("waiting_restore_status", "") or "").strip().upper()
    restore_message = _clip_message(previous.meta.get("waiting_restore_message", ""))
    if restore_status in WAITING_STATUSES or not restore_status:
        restore_status = "RUNNING"
    if restore_status == "IDLE":
        return "IDLE", _resolve_idle_message(previous, restore_message)
    if restore_status == "ERROR":
        return "ERROR", restore_message or "系统错误"
    return restore_status, restore_message or "运行中..."


def _map_codex_proxy_event(previous: TaskState | None, event: MonitorEvent) -> tuple[str, str] | None:
    event_type = str(event.event_type or "").strip().lower()
    payload = event.payload

    if event_type in {"thread/started", "thread/status/changed"}:
        status_type, flags, message = _extract_thread_status(payload)
        if status_type == "active":
            return _map_active_status(flags, payload)
        if status_type == "idle":
            return "IDLE", _resolve_idle_message(previous, message)
        if status_type == "systemerror":
            return "ERROR", message or "系统错误"
        if status_type == "notloaded":
            return "IDLE", message or NOT_LOADED_FALLBACK

    if event_type == "turn/started":
        return "RUNNING", _extract_turn_text(payload) or _extract_payload_text(payload) or "运行中..."
    if event_type == "turn/completed":
        return "IDLE", _resolve_idle_message(previous, _extract_turn_text(payload))
    if event_type == "serverrequest/resolved":
        return _restore_status_after_request(previous, payload)
    if event_type in {"turn/approval_required", "turn/confirm_required"}:
        return WAITING_APPROVAL, _extract_payload_text(payload) or "等待审批"
    if event_type in {"turn/input_required", "turn/waiting_for_input"}:
        return WAITING_INPUT, _extract_payload_text(payload) or "等待输入"
    if event_type in {"turn/error", "turn/failed"}:
        return "ERROR", _extract_payload_text(payload) or "系统错误"
    if event_type in {"item/started", "item/updated", "item/completed"}:
        return "RUNNING", _extract_payload_text(payload) or "运行中..."
    return None


def reduce_event(previous: TaskState | None, event: MonitorEvent) -> TaskState | None:
    source = str(event.source or "").strip().lower()
    mapped: tuple[str, str] | None = None

    if source == "claude_hook":
        mapped = _map_claude_hook_event(previous, event)

    elif source == "codex_proxy":
        mapped = _map_codex_proxy_event(previous, event)
        if mapped is None:
            mapped = _map_server_request(event.event_type, event.payload)

    if mapped is None:
        return previous

    status, message = mapped
    prev_meta = dict(previous.meta) if previous else {}
    merged_meta = dict(prev_meta)
    merged_meta.update(event.meta or {})
    if event.log_file:
        merged_meta["log_file"] = event.log_file

    # Determine rate_limit_reset_at.
    # Note: _map_claude_hook_event already inspects rateLimitResetAt to set STATUS_RATE_LIMITED.
    # We re-read it here to extract the actual timestamp value for storage.
    payload = event.payload if isinstance(event.payload, dict) else {}
    new_rate_limit = payload.get("rateLimitResetAt") if source == "claude_hook" else None
    if status != STATUS_RATE_LIMITED:
        new_rate_limit = None

    # Preserve subagents from previous state
    prev_subagents = previous.subagents if previous else []

    if status in WAITING_STATUSES:
        if previous and previous.status not in WAITING_STATUSES:
            merged_meta["waiting_restore_status"] = previous.status
            merged_meta["waiting_restore_message"] = previous.message
        else:
            merged_meta.setdefault("waiting_restore_status", "RUNNING")
            merged_meta.setdefault("waiting_restore_message", "运行中...")
        request_id = _extract_request_id(event.payload, event.meta)
        if request_id:
            merged_meta["pending_request_id"] = request_id
    else:
        merged_meta.pop("pending_request_id", None)
        merged_meta.pop("waiting_restore_status", None)
        merged_meta.pop("waiting_restore_message", None)

    return TaskState(
        session_id=event.session_id,
        tool_name=event.tool_name,
        status=status,
        message=_clip_message(message, limit=200),
        thread_id=event.thread_id or _extract_thread_id(event.payload) or (previous.thread_id if previous else ""),
        source=source,
        updated_at_ms=int(event.ts_ms or now_ms()),
        log_file=event.log_file or (previous.log_file if previous else ""),
        meta=merged_meta,
        rate_limit_reset_at=new_rate_limit,
        subagents=list(prev_subagents),
    )
