"""検証済みの証跡だけを `gh pr comment --attach` で PR へ投稿する。"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .errors import MeError
from .proc import RunCommand
from .video import MAX_REVIEW_VIDEO_BYTES, sha256_file

MINIMUM_GH_VERSION = (2, 100, 0)
MAX_IMAGE_BYTES = 10_000_000
SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")
COMMENT_URL_PATTERN = re.compile(r"https://github\.com/[^\s]+/pull/\d+#issuecomment-(\d+)")


def _run(
    command: Sequence[str], *, cwd: Path, run_command: RunCommand = subprocess.run
) -> "subprocess.CompletedProcess":
    result = run_command(list(command), cwd=str(cwd), check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise MeError(
            "{0} が失敗しました: {1}".format(Path(command[0]).name, (result.stderr or "").strip())
        )
    return result


def gh_version(repo: Path, *, run_command: RunCommand = subprocess.run) -> tuple:
    output = _run(["gh", "--version"], cwd=repo, run_command=run_command).stdout
    match = re.search(r"gh version (\d+)\.(\d+)\.(\d+)", output or "")
    if not match:
        raise MeError("GitHub CLI のversionを判定できません")
    return tuple(int(value) for value in match.groups())


def safe_artifact(work: Path, relative: str, label: str) -> Path:
    candidate = Path(relative)
    if not relative or candidate.is_absolute() or ".." in candidate.parts:
        raise MeError("{0} の path が work dir の外です: {1}".format(label, relative))
    resolved = (work / candidate).resolve()
    try:
        resolved.relative_to(work.resolve())
    except ValueError as error:
        raise MeError("{0} の path が work dir の外です: {1}".format(label, relative)) from error
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise MeError("{0} が存在しないか0 byteです: {1}".format(label, relative))
    return resolved


def collect_attachments(
    session: Dict[str, Any], work: Path, case_ids: Optional[Sequence[str]] = None
) -> List[Dict[str, Any]]:
    """投稿対象ケースを検証して、動画と最終フレームの実体パスを返す。"""

    selected: List[Dict[str, Any]] = []
    for case in session.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id"))
        if case_ids and case_id not in case_ids:
            continue
        if case.get("status") != "pass":
            raise MeError("{0} は pass ではないため投稿できません".format(case_id))
        video = case.get("video") or {}
        if video.get("status") != "pass" or not video.get("file"):
            raise MeError("{0} の動画が投稿可能な状態ではありません".format(case_id))
        base = "cases/{0}".format(case_id)
        video_path = safe_artifact(work, "{0}/{1}".format(base, video["file"]), "投稿用動画")
        if video_path.name == "video-source.mp4":
            raise MeError("原本動画は投稿できません")
        if video_path.stat().st_size > MAX_REVIEW_VIDEO_BYTES:
            raise MeError("{0} の動画が上限を超えています".format(case_id))
        recorded = (video.get("review") or {}).get("sha256")
        if recorded != sha256_file(video_path):
            raise MeError("{0} の動画が case end 後に変更されています".format(case_id))
        final_frame = safe_artifact(
            work, "{0}/{1}".format(base, video.get("final_frame") or ""), "最終フレーム"
        )
        if final_frame.stat().st_size > MAX_IMAGE_BYTES:
            raise MeError("{0} の最終フレームがGitHubの上限を超えています".format(case_id))
        selected.append({"case": case, "video": video_path, "final_frame": final_frame})
    if not selected:
        raise MeError("投稿対象のケースがありません")
    return selected


def build_body(session: Dict[str, Any], attachments: List[Dict[str, Any]]) -> str:
    fingerprint = session.get("fingerprint") or {}
    lines = [
        "## Maestro evidence",
        "",
        "- Commit: `{0}`".format(fingerprint.get("head_sha")),
        "- Device: {0}".format((session.get("device") or {}).get("name")),
        "",
    ]
    for item in attachments:
        case = item["case"]
        title = case.get("title") or case.get("id")
        lines.extend(
            [
                "### {0}".format(title),
                "",
                "![]({0})".format(item["video"]),
                "",
                "![{0}の最終状態]({1})".format(title, item["final_frame"]),
                "",
            ]
        )
        notes = (case.get("review") or {}).get("notes") or case.get("notes")
        if notes:
            lines.extend([str(notes), ""])
    return "\n".join(lines)


def publish(
    *,
    repo: Path,
    work: Path,
    session: Dict[str, Any],
    pr_number: int,
    approved: bool,
    case_ids: Optional[Sequence[str]] = None,
    run_command: RunCommand = subprocess.run,
) -> Dict[str, Any]:
    if not approved:
        raise MeError("PR投稿直前のユーザー承認が必要です（--approved）")
    if gh_version(repo, run_command=run_command) < MINIMUM_GH_VERSION:
        raise MeError("GitHub CLI 2.100.0 以上が必要です")
    account = _run(
        ["gh", "api", "user", "--jq", ".login"], cwd=repo, run_command=run_command
    ).stdout.strip()
    if not account:
        raise MeError("gh へログインできていません")
    repository = json.loads(
        _run(
            ["gh", "repo", "view", "--json", "nameWithOwner,viewerPermission"],
            cwd=repo,
            run_command=run_command,
        ).stdout
    )
    if repository.get("viewerPermission") not in ("ADMIN", "MAINTAIN", "WRITE"):
        raise MeError(
            "添付に必要なrepository権限がありません: {0}".format(repository.get("viewerPermission"))
        )
    head_sha = str((session.get("fingerprint") or {}).get("head_sha", ""))
    if not SHA1_PATTERN.match(head_sha):
        raise MeError("session の head SHA が不正です")
    pull_request = json.loads(
        _run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--repo",
                str(repository["nameWithOwner"]),
                "--json",
                "headRefOid,url",
            ],
            cwd=repo,
            run_command=run_command,
        ).stdout
    )
    if pull_request.get("headRefOid") != head_sha:
        raise MeError("PRのhead SHAと証跡取得時のSHAが一致しません")

    attachments = collect_attachments(session, work, case_ids)
    body_path = Path(
        tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".md", delete=False
        ).name
    )
    body_path.write_text(build_body(session, attachments), encoding="utf-8")
    try:
        command = [
            "gh",
            "pr",
            "comment",
            str(pr_number),
            "--repo",
            str(repository["nameWithOwner"]),
            "--body-file",
            str(body_path),
        ]
        for item in attachments:
            command.extend(("--attach", str(item["video"])))
            command.extend(("--attach", str(item["final_frame"])))
        result = _run(command, cwd=repo, run_command=run_command)
    finally:
        try:
            body_path.unlink()
        except OSError:
            pass
    match = COMMENT_URL_PATTERN.search(result.stdout or "")
    if not match:
        raise MeError("投稿は完了した可能性がありますが comment URL を確認できません")
    url = match.group(0)
    read_back = _run(
        [
            "gh",
            "api",
            "repos/{0}/issues/comments/{1}".format(repository["nameWithOwner"], match.group(1)),
            "--jq",
            ".html_url",
        ],
        cwd=repo,
        run_command=run_command,
    ).stdout.strip()
    if read_back != url:
        raise MeError("投稿後の comment 再取得結果が一致しません")
    return {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "pr_number": pr_number,
        "repository": repository["nameWithOwner"],
        "account": account,
        "comment_url": url,
        "pull_request_url": pull_request.get("url"),
        "head_sha": head_sha,
        "cases": [str(item["case"].get("id")) for item in attachments],
    }
