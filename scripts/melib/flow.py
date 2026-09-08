"""Maestro flow の生成と1 step の実行。"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .errors import MeError
from .proc import RunCommand, strip_ansi

STDOUT_TAIL_LINES = 30


def build_flow(app_id: str, commands: str) -> str:
    """appId ヘッダ付きの flow を作る。commands は本文をそのまま埋める。"""

    body = commands.rstrip("\n")
    if not body.strip():
        raise MeError("flow の commands が空です")
    return "appId: {0}\n---\n{1}\n".format(app_id, body)


def maestro_test_command(device: str, flow: Path) -> List[str]:
    return ["maestro", "--device", device, "test", str(flow)]


def maestro_hierarchy_command(device: str, compact: bool = False) -> List[str]:
    command = ["maestro", "--device", device, "hierarchy"]
    if compact:
        command.append("--compact")
    return command


def step_environment(
    credentials: Dict[str, str], *, environ: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """credential は環境変数だけで子へ渡す。

    Maestro は MAESTRO_ 始まりの環境変数を flow から参照できるため `-e` は使わない。
    `-e KEY=値` にすると値が argv に載り `ps` から読めてしまう。
    """

    environment = dict(os.environ if environ is None else environ)
    environment.update(credentials)
    return environment


def run_step(
    *,
    device: str,
    app_id: str,
    commands: str,
    flow_path: Path,
    credentials: Dict[str, str],
    label: Optional[str] = None,
    run_command: RunCommand = subprocess.run,
    environ: Optional[Dict[str, str]] = None,
    monotonic=time.monotonic,
) -> Dict[str, Any]:
    """1 step を実行する。失敗しても例外にせず結果を返す。"""

    flow_path.parent.mkdir(parents=True, exist_ok=True)
    flow_path.write_text(build_flow(app_id, commands), encoding="utf-8")
    started = monotonic()
    try:
        result = run_command(
            maestro_test_command(device, flow_path),
            env=step_environment(credentials, environ=environ),
            capture_output=True,
            text=True,
            check=False,
        )
        exit_code = int(result.returncode)
        output = strip_ansi((result.stdout or "") + (result.stderr or ""))
    except FileNotFoundError:
        exit_code = 127
        output = "maestro コマンドが見つかりません"
    elapsed = round(monotonic() - started, 3)
    lines = [line for line in output.splitlines() if line.strip()]
    return {
        "label": label,
        "commands": commands.rstrip("\n"),
        "flow": flow_path.name,
        "exit_code": exit_code,
        "status": "pass" if exit_code == 0 else "fail",
        "duration_seconds": elapsed,
        "output_tail": lines[-STDOUT_TAIL_LINES:],
    }


def safe_repo_path(repo: Path, relative: str, label: str) -> Path:
    candidate = Path(relative)
    if not relative or candidate.is_absolute() or ".." in candidate.parts:
        raise MeError("{0} はリポジトリ内の相対pathである必要があります: {1}".format(label, relative))
    resolved = (repo / candidate).resolve()
    try:
        resolved.relative_to(repo.resolve())
    except ValueError as error:
        raise MeError("{0} がリポジトリ外を指しています: {1}".format(label, relative)) from error
    if not resolved.is_file():
        raise MeError("{0} が存在しません: {1}".format(label, relative))
    return resolved
