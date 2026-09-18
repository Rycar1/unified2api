"""Small, safe diagnostics for errors returned by upstream services."""
import json
import re
from urllib.parse import quote


_HIDDEN = "[已隐藏]"
_DETAIL_KEYS = ("message", "detail", "error", "error_description", "msg", "errors")
_SENSITIVE = r"(?:authorization|proxy-authorization|(?:set-)?cookie|(?:x[-_])?api[-_]?key|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|password|secret|client[-_]?secret|credentials?|monkeycode_ai_session|session(?:[-_]?(?:id|token))?)"
_QUOTED = r'''(?:"(?:\\.|[^"\\])*(?:"|$)|'(?:\\.|[^'\\])*(?:'|$))'''
_COOKIE_PAIR = r"[!#$%&'*+.^_`|~\w-]+\s*=\s*[^;\s,\"'<>]+"
_COOKIE = re.compile(r"(?i)(\b(?:set-)?cookie[\"']?\s*[:=]\s*)(" + _QUOTED + "|" + _COOKIE_PAIR + r"(?:\s*;\s*" + _COOKIE_PAIR + r")*)")
_ASSIGNMENT = re.compile(r"(?i)(\b" + _SENSITIVE + r"[\"']?\s*[:=]\s*)(" + _QUOTED + r"|(?:Bearer|Basic)\s+[^\s,;\"'{}<>]+|[^\s,;\"'{}<>&]+)")


def _redact(text, secrets):
    # Exact values also catch credentials echoed without a field name or prefix.
    for secret in sorted({s for s in secrets if isinstance(s, str) and s}, key=len, reverse=True):
        variants = {secret, json.dumps(secret, ensure_ascii=False)[1:-1], quote(secret, safe="")}
        for variant in sorted(variants, key=len, reverse=True):
            text = text.replace(variant, _HIDDEN)
            # A bounded response may end partway through an echoed credential.
            for length in range(min(len(variant) - 1, len(text)), 3, -1):
                if text.endswith(variant[:length]):
                    text = text[:-length] + _HIDDEN
                    break
    text = _COOKIE.sub(lambda m: m[1] + _HIDDEN, text)
    text = _ASSIGNMENT.sub(lambda m: m[1] + _HIDDEN, text)
    text = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s,;\"'{}<>]+", _HIDDEN, text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", _HIDDEN, text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", _HIDDEN, text)
    return text


def _details(value, depth=0):
    if depth > 8:
        return []
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        if value[:1] in ('{', '[', '"'):
            try:
                decoded = json.loads(value)
            except (ValueError, RecursionError):
                pass
            else:
                return _details(decoded, depth + 1)
        return [value]
    if isinstance(value, list):
        return [part for item in value[:8] for part in _details(item, depth + 1)][:8]
    if isinstance(value, dict):
        parts = []
        for key in _DETAIL_KEYS:
            parts.extend(_details(value.get(key), depth + 1))
        code = value.get("code")
        if isinstance(code, (str, int)) and not isinstance(code, bool) and str(code).strip():
            parts.append("code=" + str(code))
        return parts[:8]
    return []


def safe_error_message(data, status, secrets=()):
    """Return HTTP status and useful diagnostics, never arbitrary response fields.

    ``data`` accepts a decoded JSON object/list or raw response text. Supply any
    known credential values in ``secrets`` to hide even bare echoes of those
    values. Only error/message/detail/code fields are extracted from JSON.
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    parts = list(dict.fromkeys(_details(data)))
    value = "；".join(parts) if parts else "上游没有返回错误详情"
    value = _redact(value, secrets)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value).strip()
    # A custom relay may already have formatted the same upstream HTTP status.
    value = re.sub(r"^HTTP " + re.escape(str(status)) + r"[：:]\s*", "", value)
    if len(value) > 1200:
        value = value[:1200] + "…"
    return f"HTTP {status}：{value}"
