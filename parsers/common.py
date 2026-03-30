#!/usr/bin/env python3
"""Shared parser helpers for structured CLI monitor signals."""

from __future__ import annotations

import json
import re
from typing import Any


WAITING_MENU_RE = re.compile(r"^\s*(?:[❯›>•*\-]\s*)?\d+[.)]\s+\S+")
WAITING_QUESTION_RE = re.compile(
    r"(?:do you want to|would you like to|confirm\b|choose\b|select\b|save file to continue|press enter|apply changes\?|needs your approval)",
    re.IGNORECASE,
)
WAITING_CONFIRM_LINE_RE = re.compile(
    r"^\s*(?:[❯›>•*\-]\s*)?(?:proceed|continue)\s*(?:\?|\:|\((?:y/n|yes/no)\)|\[(?:y/n|yes/no)\])\s*$",
    re.IGNORECASE,
)

DEFAULT_TEXT_KEYS = {
    "text",
    "message",
    "content",
    "prompt",
    "question",
    "title",
    "subtitle",
    "body",
    "detail",
    "summary",
    "reason",
    "error",
    "output",
    "delta",
    "line",
    "description",
}

DEFAULT_NON_TEXT_KEYS = {
    "type",
    "event_type",
    "event",
    "name",
    "kind",
    "method",
    "jsonrpc",
    "id",
    "threadid",
    "thread_id",
    "turnid",
    "turn_id",
    "itemid",
    "item_id",
    "callid",
    "call_id",
    "approvalid",
    "approval_id",
    "modelprovider",
    "model_provider",
    "source",
    "cliversion",
    "cli_version",
    "cwd",
    "path",
    "createdat",
    "created_at",
    "updatedat",
    "updated_at",
}


def clip_text(text: str, limit: int = 60) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]", "", str(text or "")).strip()
    if len(s) > limit:
        return s[:limit]
    return s


def parse_json_object_line(line: str) -> dict[str, Any] | None:
    raw = str(line or "").strip()
    if not raw:
        return None

    candidates: list[str] = [raw]
    if raw.startswith("data:"):
        candidates.append(raw[5:].strip())
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            candidates.append(raw[start : end + 1].strip())

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen or not candidate.startswith("{"):
            continue
        seen.add(candidate)
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def collect_candidate_texts(
    obj: Any,
    depth: int = 0,
    key_hint: str = "",
    *,
    max_depth: int = 5,
    text_keys: set[str] | None = None,
    non_text_keys: set[str] | None = None,
) -> list[str]:
    if depth > max_depth:
        return []

    text_keys = text_keys or DEFAULT_TEXT_KEYS
    non_text_keys = non_text_keys or DEFAULT_NON_TEXT_KEYS

    texts: list[str] = []
    if isinstance(obj, str):
        s = obj.strip()
        if not s:
            return []
        if key_hint and key_hint.lower() in non_text_keys:
            return []
        hinted = key_hint.lower() in text_keys if key_hint else False
        if hinted or depth <= 1:
            texts.append(s)
        elif " " in s or "\n" in s or "?" in s:
            texts.append(s)
        return texts

    if isinstance(obj, dict):
        for k, v in obj.items():
            texts.extend(
                collect_candidate_texts(
                    v,
                    depth + 1,
                    str(k),
                    max_depth=max_depth,
                    text_keys=text_keys,
                    non_text_keys=non_text_keys,
                )
            )
        return texts

    if isinstance(obj, list):
        for item in obj:
            texts.extend(
                collect_candidate_texts(
                    item,
                    depth + 1,
                    key_hint,
                    max_depth=max_depth,
                    text_keys=text_keys,
                    non_text_keys=non_text_keys,
                )
            )
        return texts

    return []


def extract_waiting_text(
    texts: list[str], waiting_patterns: list[str], *, line_limit: int = 200
) -> str:
    normalized: list[str] = []
    for t in texts:
        for chunk in str(t).splitlines():
            s = clip_text(chunk, limit=line_limit)
            if s:
                normalized.append(s)
    if not normalized:
        return ""

    if len([line for line in normalized if WAITING_MENU_RE.search(line)]) >= 2:
        for line in normalized:
            if WAITING_QUESTION_RE.search(line) or WAITING_CONFIRM_LINE_RE.search(line):
                return clip_text(line)
        return clip_text(normalized[0])

    for line in normalized:
        if WAITING_QUESTION_RE.search(line) or WAITING_CONFIRM_LINE_RE.search(line):
            return clip_text(line)

    blob = "\n".join(normalized)
    for pattern in waiting_patterns:
        try:
            if re.search(pattern, blob, re.IGNORECASE):
                for line in normalized:
                    if re.search(pattern, line, re.IGNORECASE):
                        return clip_text(line)
                return clip_text(normalized[0])
        except re.error:
            continue
    return ""


def extract_first_meaningful_text(
    texts: list[str], *, dotted_mode: str = "slash_or_dot"
) -> str:
    """Extract first meaningful free text line.

    dotted_mode:
      - "slash_or_dot": drop `foo/bar` and `foo.bar` dotted tokens
      - "dot_only": drop only `foo.bar` dotted tokens
    """
    if dotted_mode == "dot_only":
        dotted_re = re.compile(r"[a-z_]+(?:\.[a-z0-9_]+)+", re.IGNORECASE)
    else:
        dotted_re = re.compile(r"[a-z_]+(?:[./][a-z0-9_]+)+", re.IGNORECASE)

    for raw in texts:
        for chunk in str(raw).splitlines():
            s = clip_text(chunk, limit=200)
            if not s:
                continue
            if dotted_re.fullmatch(s):
                continue
            return clip_text(s)
    return ""
