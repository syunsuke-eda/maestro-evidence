"""実行に必要な環境が揃っているかを調べる。

セットアップ直後に一度走らせて、足りないものを名指しで出すためのもの。
判定は純粋関数にまとめ、外部コマンドは呼び出し側から差し替えられるようにする。
"""

from __future__ import annotations

import platform as platform_module
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .proc import RunCommand, run
from .textinput import DEFAULT_APK_PATH

MINIMUM_PYTHON = (3, 9)
# 必須。どれか1つでも欠けると動かない
REQUIRED_TOOLS = ("maestro", "ffmpeg", "ffprobe")
# platform ごとの必須。iOS は xcrun、Android は adb。どちらか一方あればよい
PLATFORM_TOOLS = ("xcrun", "adb")
# 無くても検証はできる。PR投稿だけができない
OPTIONAL_TOOLS = ("gh",)

VERSION_FLAGS = {
    "maestro": ["--version"],
    "ffmpeg": ["-version"],
    "ffprobe": ["-version"],
    "xcrun": ["--version"],
    "adb": ["--version"],
    "gh": ["--version"],
}


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def check_tool(
    name: str,
    *,
    which=shutil.which,
    run_command: RunCommand = subprocess.run,
) -> Dict[str, Any]:
    path = which(name)
    if not path:
        return {"name": name, "available": False, "path": None, "version": None}
    version = ""
    try:
        result = run([name] + VERSION_FLAGS.get(name, ["--version"]), run_command=run_command)
        version = _first_line(result.stdout) or _first_line(result.stderr)
    except Exception:  # noqa: BLE001 - version 取得の失敗で診断全体を止めない
        version = ""
    return {"name": name, "available": True, "path": str(path), "version": version or None}


def check_python(version_info: Optional[Tuple[int, int]] = None) -> Dict[str, Any]:
    current = version_info or (sys.version_info[0], sys.version_info[1])
    return {
        "name": "python3",
        "available": True,
        "version": "{0}.{1}".format(current[0], current[1]),
        "ok": current >= MINIMUM_PYTHON,
        "minimum": "{0}.{1}".format(*MINIMUM_PYTHON),
    }


def run_checks(
    *,
    which=shutil.which,
    run_command: RunCommand = subprocess.run,
    apk_path: Optional[Path] = None,
    system: Optional[str] = None,
    version_info: Optional[Tuple[int, int]] = None,
) -> Dict[str, Any]:
    apk = apk_path or DEFAULT_APK_PATH
    current_system = system or platform_module.system()
    required = [check_tool(name, which=which, run_command=run_command) for name in REQUIRED_TOOLS]
    platform_tools = [
        check_tool(name, which=which, run_command=run_command) for name in PLATFORM_TOOLS
    ]
    optional = [check_tool(name, which=which, run_command=run_command) for name in OPTIONAL_TOOLS]
    python = check_python(version_info)
    keychain = current_system == "Darwin" and bool(which("security"))

    missing_required = [tool["name"] for tool in required if not tool["available"]]
    available_platform = [tool["name"] for tool in platform_tools if tool["available"]]
    problems: List[str] = []
    if missing_required:
        problems.append("必須コマンドがありません: " + ", ".join(missing_required))
    if not available_platform:
        problems.append("xcrun（iOS）か adb（Android）のどちらかが必要です")
    if not python["ok"]:
        problems.append("python3 {0} 以上が必要です".format(python["minimum"]))
    return {
        "python": python,
        "required": required,
        "platform_tools": platform_tools,
        "optional": optional,
        "adbkeyboard_apk": {"path": str(apk), "present": apk.is_file()},
        "keychain": {"available": keychain, "system": current_system},
        "problems": problems,
        "status": "ok" if not problems else "blocked",
    }


def _mark(ok: bool) -> str:
    return "OK  " if ok else "NG  "


def format_summary(report: Dict[str, Any]) -> str:
    lines: List[str] = ["maestro-evidence の環境診断", ""]
    python = report["python"]
    lines.append(
        "{0}python3 {1}（必要: {2} 以上）".format(
            _mark(python["ok"]), python["version"], python["minimum"]
        )
    )
    for tool in report["required"]:
        lines.append(
            "{0}{1} {2}".format(
                _mark(tool["available"]), tool["name"], tool["version"] or "（未インストール）"
            )
        )
    available_platform = [tool for tool in report["platform_tools"] if tool["available"]]
    lines.append("")
    lines.append("端末操作（どちらか1つ必要）")
    for tool in report["platform_tools"]:
        lines.append(
            "{0}{1} {2}".format(
                _mark(tool["available"]), tool["name"], tool["version"] or "（未インストール）"
            )
        )
    if not available_platform:
        lines.append("    → iOS を使うなら Xcode、Android を使うなら platform-tools を入れる")
    lines.append("")
    lines.append("任意")
    for tool in report["optional"]:
        lines.append(
            "{0}{1} {2}（PR投稿に使う）".format(
                _mark(tool["available"]), tool["name"], tool["version"] or "（未インストール）"
            )
        )
    apk = report["adbkeyboard_apk"]
    lines.append(
        "{0}ADBKeyBoard APK {1}".format(
            _mark(apk["present"]), apk["path"] if apk["present"] else "（Android の文字入力に必要）"
        )
    )
    keychain = report["keychain"]
    lines.append(
        "{0}Keychain {1}".format(
            _mark(keychain["available"]),
            "利用可能" if keychain["available"] else "（macOS 以外では credentials は環境変数で渡す）",
        )
    )
    lines.append("")
    if report["problems"]:
        lines.append("要対応:")
        lines.extend("  - " + problem for problem in report["problems"])
    else:
        lines.append("必要なものは揃っています。")
    return "\n".join(lines)
