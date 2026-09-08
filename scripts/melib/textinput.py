"""Android の文字入力を IME 経由で行う。

Flutter の TextField は `adb input text` / Maestro `inputText` で文字が落ちる
（速度に関係なく欠落・重複する。ネイティブの EditText では起きない）。IME として
テキストを確定する ADBKeyBoard を使うと全文入ることを実機で確認している。

実値を argv へ載せないため、`am broadcast` は `adb shell` の標準入力から渡す。
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import MeError
from .proc import RunCommand, run

# ADBKeyBoard（https://github.com/senzhk/ADBKeyBoard）の識別子。
# IME ID は実エミュレータの `adb shell ime list -a` で確認済み。APK は同梱しない。
ADBKEYBOARD_PACKAGE = "com.android.adbkeyboard"
ADBKEYBOARD_IME_ID = "com.android.adbkeyboard/.AdbIME"
ADBKEYBOARD_BROADCAST_ACTION = "ADB_INPUT_TEXT"
DEFAULT_APK_PATH = Path.home() / ".maestro-evidence" / "ADBKeyboard.apk"

TEXT_INPUT_MODES = ("adbkeyboard", "maestro")
# Maestro 側へ本文を渡すための環境変数。MAESTRO_ 始まりなら flow から
# ${...} で参照できるため、本文をファイルにも argv にも置かずに済む
MAESTRO_TEXT_VARIABLE = "MAESTRO_EVIDENCE_TYPE_TEXT"


def adb(device_id: str, *args: str) -> List[str]:
    return ["adb", "-s", device_id] + list(args)


def broadcast_script(text: str) -> str:
    """`adb shell` の標準入力へ流すコマンド文字列。

    本文はデバイス側 shell 向けにクォートする。ホスト側の argv には出ない。
    """

    return "am broadcast -a {0} --es msg {1}\n".format(
        ADBKEYBOARD_BROADCAST_ACTION, shlex.quote(text)
    )


def is_installed(
    device_id: str, *, run_command: RunCommand = subprocess.run
) -> bool:
    result = run(
        adb(device_id, "shell", "pm", "list", "packages", ADBKEYBOARD_PACKAGE),
        run_command=run_command,
    )
    return ADBKEYBOARD_PACKAGE in (result.stdout or "")


def current_ime(device_id: str, *, run_command: RunCommand = subprocess.run) -> str:
    result = run(
        adb(device_id, "shell", "settings", "get", "secure", "default_input_method"),
        run_command=run_command,
    )
    value = (result.stdout or "").strip()
    return "" if value in ("", "null") else value


def install(
    device_id: str, apk: Path, *, run_command: RunCommand = subprocess.run
) -> None:
    if not apk.is_file():
        raise MeError(
            "ADBKeyBoard の APK がありません: {0}\n"
            "https://github.com/senzhk/ADBKeyBoard から取得して置いてください".format(apk)
        )
    run(adb(device_id, "install", "-r", str(apk)), run_command=run_command, check=True)


def activate(
    device_id: str,
    apk: Path,
    *,
    run_command: RunCommand = subprocess.run,
) -> Dict[str, Any]:
    """未インストールなら入れて IME を切り替える。元の IME を返す。"""

    previous = current_ime(device_id, run_command=run_command)
    installed = is_installed(device_id, run_command=run_command)
    if not installed:
        install(device_id, apk, run_command=run_command)
    run(adb(device_id, "shell", "ime", "enable", ADBKEYBOARD_IME_ID), run_command=run_command)
    result = run(
        adb(device_id, "shell", "ime", "set", ADBKEYBOARD_IME_ID), run_command=run_command
    )
    if result.returncode != 0:
        raise MeError("ADBKeyBoard を IME として有効化できませんでした")
    return {
        "status": "active",
        "previous_ime": previous,
        "ime": ADBKEYBOARD_IME_ID,
        "installed_by_session": not installed,
    }


def restore(
    device_id: str,
    previous_ime: str,
    *,
    run_command: RunCommand = subprocess.run,
) -> str:
    """元の IME へ戻す。アンインストールはしない（次回の再インストールを避ける）。"""

    if not previous_ime:
        return "unknown_previous"
    result = run(
        adb(device_id, "shell", "ime", "set", previous_ime), run_command=run_command
    )
    return "restored" if result.returncode == 0 else "failed"


def send_text(
    device_id: str,
    text: str,
    *,
    run_command: RunCommand = subprocess.run,
) -> Dict[str, Any]:
    """ADBKeyBoard へ本文を送る。本文は stdin 経由なので argv に出ない。"""

    result = run(
        adb(device_id, "shell"),
        run_command=run_command,
        input=broadcast_script(text),
    )
    return {
        "method": "adbkeyboard",
        "exit_code": int(result.returncode),
        "status": "pass" if result.returncode == 0 else "fail",
    }
