"""検証アカウントの取得。実値は戻り値としてのみ扱い、記録も表示もしない。"""

from __future__ import annotations

import os
import platform as platform_module
import shutil
import subprocess
from typing import Dict, List, Optional, Sequence, Tuple

from .errors import MeError
from .proc import RunCommand, run

STATUS_AVAILABLE = "available"
STATUS_MISSING = "missing"
STATUS_INCOMPLETE = "incomplete"
STATUS_UNSUPPORTED = "unsupported"
STATUS_NOT_REQUIRED = "not_required"
STATUS_UNAVAILABLE = "unavailable"


def account_name(settings: Dict[str, object], env_name: str) -> str:
    """env 名に対応する Keychain の account 名。既定は env 名そのもの。"""

    accounts = settings.get("keychain_accounts") or {}
    return str(accounts.get(env_name) or env_name)


def read_keychain_value(
    service: str,
    account: str,
    *,
    run_command: RunCommand = subprocess.run,
    which=shutil.which,
) -> str:
    if which("security") is None:
        return ""
    result = run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        run_command=run_command,
    )
    return (result.stdout or "").rstrip("\n") if result.returncode == 0 else ""


def resolve_credentials(
    config: Dict[str, object],
    *,
    environ: Optional[Dict[str, str]] = None,
    run_command: RunCommand = subprocess.run,
    which=shutil.which,
) -> Tuple[Dict[str, str], str]:
    """取れた credential と状態を返す。揃わなくても例外にしない。

    ログイン済み端末では credential を使わない操作も多いため、呼び出し側が
    「渡さずに続ける」を選べるようにする。一部だけ渡すと flow が中途半端に
    進むため、全部揃わないときは何も渡さない。
    戻り値の状態は available / unavailable / not_required。
    """

    settings = config.get("credentials") or {}
    source = settings.get("source", "none")
    names: List[str] = list(settings.get("env_names") or [])
    if source == "none" or not names:
        return {}, STATUS_NOT_REQUIRED
    values = os.environ if environ is None else environ
    resolved: Dict[str, str] = {}
    for name in names:
        value = values.get(name, "")
        if not value and source == "keychain":
            value = read_keychain_value(
                str(settings.get("keychain_service") or ""),
                account_name(settings, name),
                run_command=run_command,
                which=which,
            )
        if value:
            resolved[name] = value
    if len(resolved) != len(names):
        return {}, STATUS_UNAVAILABLE
    return resolved, STATUS_AVAILABLE


def load_credentials(
    config: Dict[str, object],
    *,
    environ: Optional[Dict[str, str]] = None,
    run_command: RunCommand = subprocess.run,
    which=shutil.which,
) -> Dict[str, str]:
    """credential が揃っていることを前提にする呼び出し用。揃わなければ失敗させる。"""

    resolved, status = resolve_credentials(
        config, environ=environ, run_command=run_command, which=which
    )
    if status == STATUS_UNAVAILABLE:
        names = list((config.get("credentials") or {}).get("env_names") or [])
        raise MeError(
            "検証アカウントが揃っていません: "
            + ", ".join(names)
            + "。`me.py credentials set` で登録してください"
        )
    return resolved


def credentials_status(
    config: Dict[str, object],
    *,
    environ: Optional[Dict[str, str]] = None,
    run_command: RunCommand = subprocess.run,
    which=shutil.which,
    system: Optional[str] = None,
) -> str:
    settings = config.get("credentials") or {}
    source = settings.get("source", "none")
    names: List[str] = list(settings.get("env_names") or [])
    if source == "none" or not names:
        return STATUS_NOT_REQUIRED
    if source == "keychain":
        current = system or platform_module.system()
        if current != "Darwin" or which("security") is None:
            return STATUS_UNSUPPORTED
    values = os.environ if environ is None else environ
    found = 0
    for name in names:
        value = values.get(name, "")
        if not value and source == "keychain":
            value = read_keychain_value(
                str(settings.get("keychain_service") or ""),
                account_name(settings, name),
                run_command=run_command,
                which=which,
            )
        if value:
            found += 1
    if found == len(names):
        return STATUS_AVAILABLE
    if found == 0:
        return STATUS_MISSING
    return STATUS_INCOMPLETE


def store_keychain_values(
    service: str,
    names: Sequence[str],
    *,
    accounts: Optional[Dict[str, str]] = None,
    run_command=subprocess.run,
    write=print,
) -> None:
    """`security` 自身の非表示プロンプトへ入力させる。

    値を Python 側で受け取って引数に渡すと `ps` から見えるため、-w を値なしで
    使い security にプロンプトを任せる。
    """

    settings = {"keychain_accounts": accounts or {}}
    for name in names:
        account = account_name(settings, name)
        write(
            "{0}（Keychain account: {1}）の値を、続くプロンプトへ2回入力してください。".format(
                name, account
            )
        )
        result = run_command(
            ["security", "add-generic-password", "-U", "-s", service, "-a", account, "-w"],
            check=False,
        )
        if result.returncode != 0:
            raise MeError("{0} をKeychainへ保存できませんでした".format(name))
