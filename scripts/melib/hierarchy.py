"""`maestro hierarchy` の出力を1要素1行へ圧縮する。

CSV（--compact）と JSON（既定）の双方を受ける。操作対象になり得ない要素
（text / resource-id / accessibilityText をどれも持たない要素）は捨てる。
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List, Optional, Sequence

CSV_HEADER = "element_num,depth,attributes,parent_num"
IDENTIFYING_KEYS = ("text", "resource-id", "accessibilityText")
KEPT_KEYS = ("text", "resource-id", "accessibilityText", "bounds", "enabled")
# Android のステータスバー・ナビゲーションバーはアプリの要素ではないのに hierarchy へ
# 常に混ざる（実測で20行以上）。既定で落とし、--raw では残す
ANDROID_SYSTEM_RESOURCE_ID_PREFIXES = ("com.android.systemui:",)


def _clean(attributes: Dict[str, Any]) -> Dict[str, str]:
    cleaned: Dict[str, str] = {}
    for key in KEPT_KEYS:
        value = attributes.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            cleaned[key] = text
    return cleaned


def _excluded(cleaned: Dict[str, str], prefixes: Sequence[str]) -> bool:
    resource_id = cleaned.get("resource-id", "")
    return any(resource_id.startswith(prefix) for prefix in prefixes)


def _format(attributes: Dict[str, Any], prefixes: Sequence[str] = ()) -> Optional[str]:
    cleaned = _clean(attributes)
    if not any(key in cleaned for key in IDENTIFYING_KEYS):
        return None
    if _excluded(cleaned, prefixes):
        return None
    return " | ".join("{0}={1}".format(key, cleaned[key]) for key in KEPT_KEYS if key in cleaned)


def _parse_csv_attributes(raw: str) -> Dict[str, str]:
    attributes: Dict[str, str] = {}
    for chunk in raw.split("; "):
        key, separator, value = chunk.partition("=")
        if not separator:
            continue
        key = key.strip()
        # maestro --compact は enabled を2回出すことがあるので後勝ちで潰す
        attributes[key] = value.strip()
    return attributes


def _from_csv(raw: str, prefixes: Sequence[str]) -> List[str]:
    rows = list(csv.reader(io.StringIO(raw)))
    lines: List[str] = []
    for row in rows:
        if len(row) < 3 or row[0] == "element_num":
            continue
        formatted = _format(_parse_csv_attributes(row[2]), prefixes)
        if formatted:
            lines.append("{0}\t{1}".format(row[0], formatted))
    return lines


def _walk(node: Any, index: List[int], lines: List[str], prefixes: Sequence[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, index, lines, prefixes)
        return
    if not isinstance(node, dict):
        return
    attributes = node.get("attributes")
    if isinstance(attributes, dict):
        formatted = _format(attributes, prefixes)
        if formatted:
            lines.append("{0}\t{1}".format(index[0], formatted))
    index[0] += 1
    _walk(node.get("children") or [], index, lines, prefixes)


def compress(raw: str, *, exclude_resource_id_prefixes: Sequence[str] = ()) -> List[str]:
    prefixes = tuple(exclude_resource_id_prefixes)
    stripped = raw.strip()
    if not stripped:
        return []
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except ValueError:
            payload = None
        if payload is not None:
            lines: List[str] = []
            _walk(payload, [0], lines, prefixes)
            return lines
    return _from_csv(raw, prefixes)


def exclusions_for(platform: str) -> "tuple":
    return ANDROID_SYSTEM_RESOURCE_ID_PREFIXES if platform == "android" else ()


def render(raw: str, *, exclude_resource_id_prefixes: Sequence[str] = ()) -> str:
    return "\n".join(
        compress(raw, exclude_resource_id_prefixes=exclude_resource_id_prefixes)
    )
