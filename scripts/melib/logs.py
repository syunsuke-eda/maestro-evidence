"""ケース区間のログ切り出し・マージ・エラー抽出。

log-launch.txt / log-os.txt は collector が「ISO8601 TAB 本文」で書く。
ケース開始時のバイトオフセットで切り出し、行頭の時刻でマージする。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

LINE_SEPARATOR = "\t"


def slice_bytes(path: Path, start: int, end: Optional[int] = None) -> str:
    """[start, end) を読み、行頭へ切り上げてからテキスト化する。

    collector は行単位で追記するが、開始位置が行の途中になっても壊れないよう
    次の改行まで捨てる。
    """

    if not path.is_file():
        return ""
    size = path.stat().st_size
    begin = max(0, min(int(start), size))
    finish = size if end is None else max(begin, min(int(end), size))
    with path.open("rb") as stream:
        aligned = True
        if begin > 0:
            stream.seek(begin - 1)
            aligned = stream.read(1) == b"\n"
        else:
            stream.seek(0)
        chunk = stream.read(finish - begin)
    if not aligned:
        newline = chunk.find(b"\n")
        chunk = b"" if newline < 0 else chunk[newline + 1 :]
    return chunk.decode("utf-8", errors="replace")


def parse_lines(text: str, source: str) -> List[Tuple[str, str, str]]:
    """(時刻, ソース名, 本文) の一覧にする。時刻の無い行は直前の時刻を継ぐ。"""

    parsed: List[Tuple[str, str, str]] = []
    previous = ""
    for line in text.splitlines():
        if not line:
            continue
        stamp, separator, body = line.partition(LINE_SEPARATOR)
        if separator and stamp and stamp[0].isdigit():
            previous = stamp
            parsed.append((stamp, source, body))
        else:
            parsed.append((previous, source, line))
    return parsed


def merge(slices: Dict[str, str]) -> List[str]:
    """複数ソースを時刻順に1本化する。同時刻はソース名で安定させる。"""

    entries: List[Tuple[str, str, str]] = []
    for source in sorted(slices):
        entries.extend(parse_lines(slices[source], source))
    entries.sort(key=lambda item: (item[0], item[1]))
    return ["{0}\t[{1}] {2}".format(stamp, source, body) for stamp, source, body in entries]


def split_merged(line: str) -> Tuple[str, str, str]:
    """merge() が作った行を (時刻, ソース名, 本文) へ戻す。"""

    stamp, separator, rest = line.partition(LINE_SEPARATOR)
    if not separator:
        return "", "", line
    if rest.startswith("[") and "] " in rest:
        source, _, body = rest[1:].partition("] ")
        return stamp, source, body
    return stamp, "", rest


def compile_patterns(patterns: Sequence[str]) -> List["re.Pattern"]:
    return [re.compile(pattern) for pattern in patterns]


def extract_errors(
    lines: Sequence[str],
    error_patterns: Sequence[str],
    ignore_patterns: Sequence[str] = (),
) -> List[str]:
    errors = compile_patterns(error_patterns)
    ignores = compile_patterns(ignore_patterns)
    if not errors:
        return []
    hits: List[str] = []
    for line in lines:
        if any(pattern.search(line) for pattern in ignores):
            continue
        if any(pattern.search(line) for pattern in errors):
            hits.append(line)
    return hits


def extract_errors_by_source(
    lines: Sequence[str],
    error_patterns: Dict[str, Sequence[str]],
    ignore_patterns: Sequence[str] = (),
) -> List[str]:
    """ソースごとに違う基準で error 行を拾う。

    launch は Dart/アプリ側の文言一致、os はログレベルというように判定基準が
    まったく違うため、同じ patterns を両方へ当てない。ignore_patterns は両方へ効く。
    判定はソース名を除いた本文に対して行う。
    """

    compiled = {source: compile_patterns(patterns) for source, patterns in error_patterns.items()}
    ignores = compile_patterns(ignore_patterns)
    hits: List[str] = []
    for line in lines:
        _, source, body = split_merged(line)
        patterns = compiled.get(source)
        if not patterns:
            continue
        if any(pattern.search(body) for pattern in ignores):
            continue
        if any(pattern.search(body) for pattern in patterns):
            hits.append(line)
    return hits


def tail(lines: Sequence[str], count: int) -> List[str]:
    if count <= 0:
        return list(lines)
    return list(lines[-count:])
