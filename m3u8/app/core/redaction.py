"""Utilities for removing secrets from logs and persisted metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
}

SENSITIVE_QUERY_KEYS = {
    "access_token",
    "auth",
    "auth_key",
    "awsaccesskeyid",
    "edge-auth",
    "e",
    "exp",
    "expires",
    "hdntl",
    "hdnts",
    "hmac",
    "key",
    "key-pair-id",
    "md5",
    "policy",
    "sig",
    "signature",
    "st",
    "token",
    "x-amz-credential",
    "x-amz-date",
    "x-amz-expires",
    "x-amz-security-token",
    "x-amz-signature",
}

REDACTED = "[REDACTED]"
_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_HEADER_RE = re.compile(
    r"(?im)^([ \t]*)(authorization|cookie|proxy-authorization|set-cookie|x-api-key|x-auth-token)([ \t]*[:=][ \t]*)(.+)$"
)


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of headers with sensitive values removed."""

    redacted: dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() in SENSITIVE_HEADER_NAMES:
            redacted[name] = REDACTED
        else:
            redacted[name] = value
    return redacted


def redact_url(url: str) -> str:
    """Redact sensitive query parameters from a URL."""

    parts = urlsplit(url)
    if not parts.query:
        return url

    query = []
    changed = False
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if is_sensitive_query_key(key):
            query.append((key, REDACTED))
            changed = True
        else:
            query.append((key, value))

    if not changed:
        return url

    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def redact_text(text: str) -> str:
    """Redact sensitive headers and signed URLs in arbitrary log text."""

    redacted = _HEADER_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}{REDACTED}", text)
    return _URL_RE.sub(lambda match: redact_url(match.group(0)), redacted)


def is_sensitive_query_key(key: str) -> bool:
    """Return whether a query parameter name is likely to carry signed URL material."""

    normalized = key.strip().lower()
    if normalized in SENSITIVE_QUERY_KEYS:
        return True
    return any(part in normalized for part in ("token", "signature", "credential"))
