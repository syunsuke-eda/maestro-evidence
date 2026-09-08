"""session.json の読み書きと成果物パスの決定。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import MeError

SCHEMA_VERSION = 1
SESSION_FILENAME = "session.json"
LOG_FILENAMES = {"launch": "log-launch.txt", "os": "log-os.txt"}
CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def validate_case_id(case_id: str) -> str:
    if not CASE_ID_PATTERN.match(case_id or ""):
        raise MeError("case id は英小文字・数字・ハイフンで63文字以内にしてください: {0}".format(case_id))
    return case_id


def session_path(work: Path) -> Path:
    return work / SESSION_FILENAME


def load(work: Path) -> Dict[str, Any]:
    path = session_path(work)
    if not path.is_file():
        raise MeError("session.json がありません。先に `session start` を実行してください: {0}".format(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MeError("session.json を読み込めません: {0}".format(error)) from error
    if not isinstance(value, dict):
        raise MeError("session.json のルートはobjectである必要があります")
    return value


def save(work: Path, session: Dict[str, Any]) -> None:
    work.mkdir(parents=True, exist_ok=True)
    session_path(work).write_text(
        json.dumps(session, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def new_session(
    *,
    work: Path,
    repo: Path,
    config: Dict[str, Any],
    fingerprint: Dict[str, Any],
    device: Dict[str, Any],
    started_at: str,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "work_dir": str(work),
        "repo": str(repo),
        # 検証・既定値補完後の設定そのものを持つ。以降のコマンドはこれを使うので
        # `--config` を毎回付ける必要はない。config_path は出所の記録
        "config": config,
        "config_path": str(config_path) if config_path else None,
        "fingerprint": fingerprint,
        "device": device,
        "started_at": started_at,
        "finished_at": None,
        "status": "running",
        "collectors": {},
        "login_flow": None,
        "cases": [],
        "secret_scan": None,
        "publication": None,
    }


def find_case(session: Dict[str, Any], case_id: str) -> Optional[Dict[str, Any]]:
    for case in session.get("cases", []):
        if isinstance(case, dict) and case.get("id") == case_id:
            return case
    return None


def require_case(session: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    case = find_case(session, case_id)
    if case is None:
        raise MeError("case が見つかりません: {0}".format(case_id))
    return case


def open_cases(session: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [case for case in session.get("cases", []) if case.get("status") == "running"]


def case_dir(work: Path, case_id: str) -> Path:
    return work / "cases" / case_id


def log_path(work: Path, source: str) -> Path:
    return work / LOG_FILENAMES[source]


def steps_path(work: Path, case_id: str) -> Path:
    return case_dir(work, case_id) / "steps.json"


def load_steps(work: Path, case_id: str) -> List[Dict[str, Any]]:
    path = steps_path(work, case_id)
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return value if isinstance(value, list) else []


def save_steps(work: Path, case_id: str, steps: List[Dict[str, Any]]) -> None:
    path = steps_path(work, case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(steps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
