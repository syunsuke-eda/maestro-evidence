"""テスト用の共通ヘルパー。外部コマンドは実行せず、この偽物へ差し替える。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeRunner:
    """subprocess.run 互換の記録付きモック。"""

    def __init__(self, responder: Optional[Callable[[List[str]], Any]] = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.responder = responder

    def __call__(self, command, **kwargs) -> "subprocess.CompletedProcess":
        command = list(command)
        self.calls.append({"command": command, "kwargs": kwargs})
        response = self.responder(command) if self.responder else None
        if response is None:
            response = (0, "", "")
        code, stdout, stderr = response
        return subprocess.CompletedProcess(command, code, stdout, stderr)

    @property
    def commands(self) -> List[List[str]]:
        return [call["command"] for call in self.calls]

    def find(self, *fragments: str) -> List[List[str]]:
        return [
            command
            for command in self.commands
            if all(any(fragment in part for part in command) for fragment in fragments)
        ]


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
