"""Secret/config detector for GitHub Scanner — keeps evidence as found in-repo."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretAlert:
    kind: str
    severity: str
    # Exact source line (trimmed/capped) so analysts can see the leak as committed.
    evidence: str
    fingerprint: str
    line_number: int


_PATTERNS = (
    (
        "private-key",
        "critical",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.I),
    ),
    (
        "github-token",
        "critical",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})\b"),
    ),
    (
        "aws-access-key",
        "critical",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ),
    (
        "aws-secret-key",
        "critical",
        re.compile(
            r"(?i)\b(?:aws_secret_access_key|secret_access_key)\b\s*[:=]\s*[\"']?"
            r"([A-Za-z0-9/+=]{30,})"
        ),
    ),
    (
        "database-url",
        "critical",
        re.compile(
            r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|mariadb|mssql)"
            r"://[^:\s/]+:[^@\s]+@[^\s\"']+",
            re.I,
        ),
    ),
    (
        "jdbc-url",
        "critical",
        re.compile(
            r"\bjdbc:(?:postgresql|mysql|sqlserver|oracle):[^\s\"']+",
            re.I,
        ),
    ),
    (
        "connection-string",
        "critical",
        re.compile(
            r"(?i)\b(?:connection[_-]?string|database_url|db_url|mongo_uri|redis_url)"
            r"\b\s*[:=]\s*[\"']?([^\s\"'#]{12,})"
        ),
    ),
    (
        "django-secret",
        "critical",
        re.compile(
            r"(?i)\b(?:secret_key|django_secret_key)\b\s*[:=]\s*[\"']?([^\s\"'#]{8,})"
        ),
    ),
    # Assignment-style passwords (env, yaml, properties, code).
    # UI keys like LBL_SHOW_PASSWORD are rejected in post-filters, not here.
    (
        "password",
        "high",
        re.compile(
            r"(?i)(?<![A-Za-z0-9])"
            r"(?:db_password|mysql_password|postgres_password|mongo_password|"
            r"redis_password|admin_password|mysql_root_password|root_password|"
            r"password|passwd|pwd|mat_?khau)"
            r"(?![A-Za-z0-9])"
            r"\s*[:=]\s*[\"']?([^\s\"'#]{4,})"
        ),
    ),
    # JSON object fields: "password": "secret" (exact keys only).
    (
        "password",
        "high",
        re.compile(
            r'(?i)"(?:password|passwd|pwd|db_password|mysql_password|'
            r'admin_password|mysql_root_password)"\s*:\s*"([^"]{4,})"'
        ),
    ),
    # SQL CREATE USER / SET PASSWORD style.
    (
        "password",
        "high",
        re.compile(r"(?i)\b(?:IDENTIFIED BY|PASSWORD)\s+'([^']{4,})'"),
    ),
    (
        "account-identifier",
        "medium",
        re.compile(
            r"(?i)\b(?:db_user|db_username|mysql_user|postgres_user|database_name|db_name)"
            r"\b\s*[:=]\s*[\"']?([^\s\"'#]{2,})"
        ),
    ),
    (
        "api-key",
        "high",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|"
            r"auth_token|bearer_token|private_token)\b"
            r"\s*[:=]\s*[\"']?([A-Za-z0-9_./+=-]{12,})"
        ),
    ),
    (
        "config-host",
        "medium",
        re.compile(
            r"(?i)\b(?:db_host|database_host|mysql_host|postgres_host|redis_host|"
            r"mongo_host)\b\s*[:=]\s*[\"']?([^\s\"'#]{3,})"
        ),
    ),
)

_SEVERITY_WEIGHT = {"info": 0, "medium": 1, "high": 2, "critical": 3}

# Kinds whose captured values need plausibility checks (reduces UI/i18n FPs).
_VALUE_CHECKED_KINDS = frozenset(
    {
        "password",
        "django-secret",
        "connection-string",
        "api-key",
        "aws-secret-key",
    }
)

_PLACEHOLDER_VALUES = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "secrets",
        "changeme",
        "change_me",
        "changeit",
        "your_password",
        "your-password",
        "yourpassword",
        "insert_password",
        "enter_password",
        "new_password",
        "old_password",
        "confirm_password",
        "showpassword",
        "show_password",
        "hidepassword",
        "hide_password",
        "errormessage",
        "error_message",
        "errormsg",
        "placeholder",
        "example",
        "sample",
        "test",
        "testing",
        "todo",
        "null",
        "none",
        "nil",
        "undefined",
        "true",
        "false",
        "string",
        "number",
        "boolean",
        "object",
        "array",
        "value",
        "default",
        "empty",
        "blank",
        "xxxxx",
        "xxxxxx",
        "******",
        "********",
        "...",
        "redacted",
        "masked",
        "hidden",
    }
)

_UI_PHRASE = re.compile(
    r"(?i)\b(?:"
    r"show|hide|enter|type|your|forgot|reset|confirm|message|label|"
    r"placeholder|error|hint|title|text|button|toggle|input|field|"
    r"display|visible|visibility|required|invalid|correct|incorrect"
    r")\b"
)
# Vietnamese UI copy referring to passwords, not credential values.
_VI_UI_PHRASE = re.compile(
    r"(?i)(?:"
    r"hiện|ẩn|mật\s*khẩu|mat\s*khau|nhập\s*mật|hiển\s*thị|ẩn\s*/?\s*hiện|"
    r"hiện\s*/\s*ẩn|ẩn\s*giấu"
    r")"
)
# Localization / label keys: LBL_SHOW_PASSWORD, MSG_PASSWORD_HINT, …
_I18N_OR_UI_KEY = re.compile(
    r"(?i)(?:^|[\"'\s,{])(?:lbl_|msg_|btn_|str_|txt_|title_|hint_|label_|err_|i18n_)?"
    r"[a-z0-9_]*(?:show|hide|toggle|forgot|enter|confirm|change|reset|visible)"
    r"[_-]?password"
    r"|password[_-]?(?:show|hide|toggle|label|title|hint|text|message|placeholder|visible)"
)
_CAMEL_OR_PASCAL = re.compile(r"^[A-Za-z][a-z0-9]*(?:[A-Z][a-z0-9]*)+$")
_SCREAMING_SNAKE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_MASK_ONLY = re.compile(r"^[\s*•·.xX#_-]+$")
# Slash alone (Hiện/ẩn) is UI, not a secret signal — require stronger specials.
_SECRET_SPECIAL = re.compile(r"[@#$%^&*!+=~?:\\]")

ALERT_KIND_LABELS = {
    "private-key": "Private key",
    "github-token": "GitHub token",
    "aws-access-key": "AWS access key",
    "aws-secret-key": "AWS secret key",
    "database-url": "Database URL / credentials",
    "jdbc-url": "JDBC connection",
    "connection-string": "DB connection string",
    "django-secret": "App secret key",
    "password": "Password",
    "account-identifier": "Account / DB user",
    "api-key": "API key / token",
    "config-host": "DB / service host",
}


def alert_kind_label(kind: str) -> str:
    return ALERT_KIND_LABELS.get(kind, kind.replace("-", " ").title())


def _strip_value(raw: str) -> str:
    return (raw or "").strip().strip("\"'`")


def _is_ui_or_i18n_password_context(line: str, value: str) -> bool:
    """True when the hit is a localization/UI string, not a credential."""
    if _I18N_OR_UI_KEY.search(line or ""):
        return True
    if _VI_UI_PHRASE.search(line or "") or _VI_UI_PHRASE.search(value or ""):
        return True
    if _UI_PHRASE.search(value or "") and not any(ch.isdigit() for ch in value):
        return True
    # Toggle labels like "Hiện/ẩn" or "show/hide" without digits.
    if "/" in value and not any(ch.isdigit() for ch in value):
        return True
    return False


def _is_plausible_secret_value(kind: str, raw: str, *, line: str = "") -> bool:
    """Reject UI labels, i18n keys, and placeholders that are not real secrets."""
    value = _strip_value(raw)
    if len(value) < 4:
        return False
    lower = value.casefold()
    if lower in _PLACEHOLDER_VALUES:
        return False
    if _MASK_ONLY.fullmatch(value):
        return False
    if kind == "password" and _is_ui_or_i18n_password_context(line, value):
        return False
    # Phrases like "Show password", "Enter your password".
    if " " in value or "\t" in value:
        if _UI_PHRASE.search(value) or _VI_UI_PHRASE.search(value):
            return False
        # Multi-word alphabetic UI copy without digits/symbols.
        letters_only = "".join(ch for ch in value if not ch.isspace())
        if letters_only.isalpha() and not any(ch.isdigit() for ch in value):
            return False
    # camelCase / PascalCase identifiers: errorMessage, showPassword.
    if _CAMEL_OR_PASCAL.fullmatch(value) and not any(ch.isdigit() for ch in value):
        return False
    # Env-var references: secret_key=AWS_SECRET_KEY (not a literal secret).
    if _SCREAMING_SNAKE.fullmatch(value) and not any(ch.isdigit() for ch in value):
        return False
    if kind == "password":
        has_digit = any(ch.isdigit() for ch in value)
        has_secret_special = bool(_SECRET_SPECIAL.search(value))
        # Require digit or real secret punctuation — "/" alone is not enough.
        if not (
            has_digit
            or has_secret_special
            or (len(value) >= 12 and "_" in value)
            or (len(value) >= 16 and "-" in value)
        ):
            if not (("-" in value or "_" in value) and len(value) >= 8 and has_digit):
                return False
    if kind in {"django-secret", "api-key", "aws-secret-key", "connection-string"}:
        if lower in _PLACEHOLDER_VALUES or _CAMEL_OR_PASCAL.fullmatch(value):
            return False
    return True


def detect_secrets(content: str, *, max_alerts: int = 40) -> list[SecretAlert]:
    alerts: list[SecretAlert] = []
    seen: set[tuple[str, str]] = set()
    for line_no, line in enumerate((content or "").splitlines(), start=1):
        if len(alerts) >= max_alerts:
            break
        line_matches: list[tuple[str, str, re.Match[str]]] = []
        for kind, severity, pattern in _PATTERNS:
            for match in pattern.finditer(line):
                line_matches.append((kind, severity, match))
        if not line_matches:
            continue
        evidence = line.strip()[:500]
        for kind, severity, match in line_matches:
            raw = match.group(1) if match.lastindex else match.group(0)
            if kind in _VALUE_CHECKED_KINDS and not _is_plausible_secret_value(
                kind, raw, line=line
            ):
                continue
            fingerprint = hashlib.sha256(
                _strip_value(raw).encode("utf-8", errors="replace")
            ).hexdigest()
            key = (kind, fingerprint)
            if key in seen:
                continue
            seen.add(key)
            alerts.append(
                SecretAlert(
                    kind=kind,
                    severity=severity,
                    evidence=evidence,
                    fingerprint=fingerprint,
                    line_number=line_no,
                )
            )
            if len(alerts) >= max_alerts:
                break
    return sorted(
        alerts,
        key=lambda alert: _SEVERITY_WEIGHT.get(alert.severity, 0),
        reverse=True,
    )
