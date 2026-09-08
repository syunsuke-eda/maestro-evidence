"""外部コマンド実行とバックグラウンドプロセス管理。

subprocess への依存はこのモジュールへ集約し、テストからは run_command /
popen を差し替えてモックする。
"""

from __future__ import annotations

import errno
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .errors import MeError

RunCommand = Callable[..., "subprocess.CompletedProcess"]
Popen = Callable[..., "subprocess.Popen"]

# CSI（色・カーソル制御）と OSC（タイトル設定など）を落とす。
# iOS の log stream は 38;5;12 のような 256 色指定を挟み、flutter run が中継する行では
# ESC の直前に literal backslash が付く形（`\` + ESC + `[38;5;12m`）で現れる。backslash を
# 残すと証跡に読めない記号が混じるため一緒に落とす。ESC が文字列として書き出された
# 形（`\033[` `\e[` `\x1b[` `\u001b[`）も同様に扱う。
ANSI_PATTERN = re.compile(
    r"\\?\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\\?\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\\(?:033|e|x1b|u001b)\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b"
)


def strip_ansi(value: str) -> str:
    return ANSI_PATTERN.sub("", value)


def run(
    command: Sequence[str],
    *,
    run_command: RunCommand = subprocess.run,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    check: bool = False,
    timeout: Optional[float] = None,
    input: Optional[str] = None,
) -> "subprocess.CompletedProcess":
    """外部コマンドを1回実行する。check=True のとき失敗を MeError にする。

    input を渡すと標準入力から流し込む。秘密を含む文字列を argv に載せずに
    子へ渡す唯一の手段として使う。
    """

    kwargs = {
        "cwd": str(cwd) if cwd is not None else None,
        "env": env,
        "capture_output": True,
        "text": True,
        "check": False,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    if input is not None:
        kwargs["input"] = input
    try:
        result = run_command(list(command), **kwargs)
    except FileNotFoundError as error:
        raise MeError("コマンドが見つかりません: {0}".format(command[0])) from error
    if check and result.returncode != 0:
        raise MeError(
            "{0} が失敗しました (exit {1}): {2}".format(
                Path(command[0]).name,
                result.returncode,
                strip_ansi((result.stderr or result.stdout or "")).strip(),
            )
        )
    return result


def spawn_detached(
    command: Sequence[str],
    *,
    stdout_path: Optional[Path] = None,
    stderr_path: Optional[Path] = None,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    popen: Popen = subprocess.Popen,
) -> int:
    """me.py の終了後も生き残る子プロセスを起動し pid を返す。

    stdin を DEVNULL、stdout/stderr をファイルか DEVNULL へ固定するのは、
    呼び出し元シェルのパイプを継承すると、そのシェルが子の終了まで待って
    ハングするため。start_new_session で端末の SIGINT からも切り離す。
    """

    stdout: Any = subprocess.DEVNULL
    stderr: Any = subprocess.DEVNULL
    handles: List[Any] = []
    try:
        if stdout_path is not None:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout = open(str(stdout_path), "ab", buffering=0)
            handles.append(stdout)
        if stderr_path is not None:
            if stdout_path is not None and stderr_path == stdout_path:
                stderr = subprocess.STDOUT
            else:
                stderr_path.parent.mkdir(parents=True, exist_ok=True)
                stderr = open(str(stderr_path), "ab", buffering=0)
                handles.append(stderr)
        process = popen(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            close_fds=True,
        )
    except FileNotFoundError as error:
        raise MeError("コマンドが見つかりません: {0}".format(command[0])) from error
    finally:
        for handle in handles:
            try:
                handle.close()
            except OSError:
                pass
    return int(process.pid)


def process_alive(pid: int, *, kill: Callable[[int, int], None] = os.kill) -> bool:
    if pid <= 0:
        return False
    try:
        kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        if error.errno == errno.ESRCH:
            return False
        return True
    return True


def wait_for_exit(
    pid: int,
    timeout: float,
    *,
    kill: Callable[[int, int], None] = os.kill,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if not process_alive(pid, kill=kill):
            return True
        sleep(0.1)
    return not process_alive(pid, kill=kill)


def stop_pid(
    pid: int,
    *,
    timeout: float = 5.0,
    kill: Callable[[int, int], None] = os.kill,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> str:
    """SIGINT で止め、timeout 秒で終わらなければ SIGKILL する。

    戻り値は "not_running" / "stopped" / "killed" / "stuck"。
    """

    if not process_alive(pid, kill=kill):
        return "not_running"
    try:
        kill(pid, signal.SIGINT)
    except ProcessLookupError:
        return "not_running"
    except PermissionError as error:
        raise MeError("プロセス {0} を停止できません".format(pid)) from error
    if wait_for_exit(pid, timeout, kill=kill, sleep=sleep, monotonic=monotonic):
        return "stopped"
    try:
        kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "stopped"
    if wait_for_exit(pid, timeout, kill=kill, sleep=sleep, monotonic=monotonic):
        return "killed"
    return "stuck"

def group_alive(pgid: int, *, killpg: Callable[[int, int], None] = os.killpg) -> bool:
    """プロセスグループに生存メンバーが居るか。

    グループリーダーが終わっていても、子孫が同じグループに残っていれば真を返す。
    `fvm flutter run` のように wrapper が先に消えて dartvm が残る形を検知するため、
    pid ではなくグループで見る。
    """

    if pgid <= 0:
        return False
    try:
        killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        return error.errno != errno.ESRCH
    return True


def stop_group(
    pgid: int,
    *,
    grace: float = 5.0,
    killpg: Callable[[int, int], None] = os.killpg,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Dict[str, Any]:
    """プロセスグループ全体を SIGINT → SIGTERM → SIGKILL の順で止める。

    SIGINT を先に送るのは、simctl recordVideo が moov を書き終えるための正規の
    停止手段だから。SIGINT を無視する子（非対話 sh など）や、wrapper の下にぶら
    下がる孫まで確実に落とすために段階を上げる。
    """

    if not group_alive(pgid, killpg=killpg):
        return {"pgid": pgid, "status": "not_running", "signals": [], "remaining": False}
    sent: List[str] = []
    for number, name, status in (
        (signal.SIGINT, "SIGINT", "stopped"),
        (signal.SIGTERM, "SIGTERM", "terminated"),
        (signal.SIGKILL, "SIGKILL", "killed"),
    ):
        try:
            killpg(pgid, number)
        except ProcessLookupError:
            return {"pgid": pgid, "status": "stopped", "signals": sent, "remaining": False}
        except PermissionError as error:
            raise MeError("プロセスグループ {0} を停止できません".format(pgid)) from error
        sent.append(name)
        deadline = monotonic() + grace
        while monotonic() < deadline:
            if not group_alive(pgid, killpg=killpg):
                return {"pgid": pgid, "status": status, "signals": sent, "remaining": False}
            sleep(0.1)
    remaining = group_alive(pgid, killpg=killpg)
    return {
        "pgid": pgid,
        "status": "stuck" if remaining else "killed",
        "signals": sent,
        "remaining": remaining,
    }
