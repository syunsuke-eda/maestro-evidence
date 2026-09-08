"""成果物テキストから credential・token・署名付きURLを消す。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Sequence

REDACTED = "[REDACTED]"
TEXT_SUFFIXES = (".json", ".txt", ".log", ".err", ".html", ".yaml", ".yml", ".xml", ".csv", ".md")

SENSITIVE_PATTERNS = (
    ("bearer", re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s\"']+")),
    ("signed_url", re.compile(r"(?i)(X-Amz-(?:Signature|Credential|Security-Token)=)[^&\s\"']+")),
    ("token_query", re.compile(r"(?i)([?&](?:access_token|refresh_token|id_token)=)[^&\s\"']+")),
    ("signature_query", re.compile(r"(?i)([?&]Signature=)[^&\s\"']+")),
    ("token_json", re.compile(r"(?i)(\"id_?token\"\s*:\s*\")[^\"]+")),
)


def redact_text(value: str, secrets: Sequence[str]) -> "tuple":
    """置換後テキストと、検出した種別の一覧を返す。"""

    kinds: List[str] = []
    result = value
    for secret in secrets:
        if secret and secret in result:
            result = result.replace(secret, REDACTED)
            kinds.append("credential")
    for kind, pattern in SENSITIVE_PATTERNS:
        if pattern.search(result):
            result = pattern.sub(lambda match: match.group(1) + REDACTED, result)
            kinds.append(kind)
    return result, kinds


def scan_directory(root: Path, secrets: Sequence[str]) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        redacted, kinds = redact_text(original, secrets)
        if redacted != original:
            path.write_text(redacted, encoding="utf-8")
            for kind in sorted(set(kinds)):
                violations.append({"file": str(path.relative_to(root)), "kind": kind})
    return violations
