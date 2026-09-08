"""検証対象の差分を一意に特定する fingerprint。"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Union

from .errors import MeError
from .proc import RunCommand


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_git(
    repo: Path, *args: str, binary: bool = False, run_command: RunCommand = subprocess.run
) -> Union[str, bytes]:
    result = run_command(
        ["git"] + list(args),
        cwd=str(repo),
        capture_output=True,
        check=False,
        text=not binary,
    )
    if result.returncode != 0:
        detail = result.stderr if isinstance(result.stderr, str) else str(result.stderr)
        raise MeError("git {0} が失敗しました: {1}".format(" ".join(args), (detail or "").strip()))
    return result.stdout if binary else str(result.stdout).strip()


def repository_root(repo: Path, *, run_command: RunCommand = subprocess.run) -> Path:
    return Path(str(run_git(repo, "rev-parse", "--show-toplevel", run_command=run_command)))


def compute_fingerprint(
    repo: Path, base_ref: str, *, run_command: RunCommand = subprocess.run
) -> Dict[str, Any]:
    """merge-base からの差分に、追跡外ファイルの内容まで含めて hash を取る。

    未コミットの新規ファイルだけが変更点であるケースを取りこぼさないため、
    untracked も hash の対象に含める。
    """

    root = repository_root(repo, run_command=run_command)
    merge_base = str(run_git(root, "merge-base", base_ref, "HEAD", run_command=run_command))
    head_sha = str(run_git(root, "rev-parse", "HEAD", run_command=run_command))
    branch = str(run_git(root, "rev-parse", "--abbrev-ref", "HEAD", run_command=run_command))
    diff = run_git(root, "diff", merge_base, binary=True, run_command=run_command)
    untracked_raw = run_git(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        binary=True,
        run_command=run_command,
    )
    if isinstance(diff, str):
        diff = diff.encode("utf-8")
    if isinstance(untracked_raw, str):
        untracked_raw = untracked_raw.encode("utf-8")
    untracked = sorted(path.decode("utf-8") for path in untracked_raw.split(b"\0") if path)

    digest = hashlib.sha256()
    digest.update("merge-base\0{0}\0head\0{1}\0".format(merge_base, head_sha).encode("utf-8"))
    digest.update(diff)
    for relative in untracked:
        path = root / relative
        digest.update(b"\0untracked\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())

    changed: List[str] = [
        line
        for line in str(
            run_git(root, "diff", "--name-only", merge_base, run_command=run_command)
        ).splitlines()
        if line
    ]
    return {
        "base_ref": base_ref,
        "merge_base": merge_base,
        "head_sha": head_sha,
        "branch": branch,
        "diff_sha256": digest.hexdigest(),
        "changed_files": changed,
        "untracked_files": untracked,
        "generated_at": utc_now(),
    }


def sanitize_branch(branch: str) -> str:
    safe = "".join(character if character.isalnum() else "-" for character in branch)
    while "--" in safe:
        safe = safe.replace("--", "-")
    return safe.strip("-").lower() or "detached"
