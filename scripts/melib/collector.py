"""子プロセスの出力をタイムスタンプ付きで1ファイルへ貯め続ける常駐処理。

`me.py _collect` の実体。log-launch.txt / log-os.txt は「行頭に採取時刻、TAB、
本文」で保存する。flutter run の出力には時刻が無く、これが無いと 2 ソースを
時刻順にマージできないため。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

from .proc import strip_ansi

LINE_SEPARATOR = "\t"
# SIGINT を無視する子プロセスを取りこぼさないための段階的エスカレーション
SIGTERM_AFTER_SECONDS = 8.0
SIGKILL_AFTER_SECONDS = 13.0


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def format_line(body: str, *, now: Optional[str] = None) -> str:
    return "{0}{1}{2}\n".format(now or timestamp(), LINE_SEPARATOR, strip_ansi(body).rstrip("\r\n"))


def pid_file_for(output: Path) -> Path:
    return output.with_name(output.name + ".pid")


def collect(
    command: Sequence[str],
    output: Path,
    *,
    popen=subprocess.Popen,
    install_signal=signal.signal,
) -> int:
    """command を起動し、その出力を output へ書き続ける。

    自身が SIGINT / SIGTERM を受けたら子へ SIGINT を送る。子の stdout が閉じた
    ところで書き込みを終え、子の exit code を返す。
    """

    output.parent.mkdir(parents=True, exist_ok=True)
    process = popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        close_fds=True,
        # 子を独立したプロセスグループのリーダーにする。`fvm flutter run` のような
        # wrapper は自分が消えた後も dartvm や simctl spawn を残すため、pid ではなく
        # グループ単位で止められるようにしておく
        start_new_session=True,
    )
    pid_path = pid_file_for(output)
    pid_path.write_text(str(process.pid) + "\n", encoding="utf-8")

    def send(signal_number: int) -> None:
        # 子の pgid は start_new_session により子の pid と同じ
        try:
            os.killpg(process.pid, signal_number)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def forward(signal_number: int, frame: object) -> None:
        # SIGINT は simctl recordVideo が moov を書き終えるための正規の停止手段。
        # ただし SIGINT を無視する子（非対話 sh など）が居るため段階的に強める。
        send(signal.SIGINT)
        for delay, escalation in (
            (SIGTERM_AFTER_SECONDS, signal.SIGTERM),
            (SIGKILL_AFTER_SECONDS, signal.SIGKILL),
        ):
            timer = threading.Timer(delay, send, args=(escalation,))
            timer.daemon = True
            timer.start()

    for received in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        install_signal(received, forward)

    assert process.stdout is not None
    with output.open("a", encoding="utf-8") as sink:
        while True:
            try:
                line = process.stdout.readline()
            except (OSError, ValueError):
                break
            if not line:
                break
            sink.write(format_line(line))
            sink.flush()
    try:
        code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        code = process.wait()
    try:
        pid_path.unlink()
    except OSError:
        pass
    return int(code or 0)


def child_pid(output: Path) -> Optional[int]:
    path = pid_file_for(output)
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def collector_argv(script: Path, command: Sequence[str], output: Path) -> List[str]:
    return [sys.executable, str(script), "_collect", "--out", str(output), "--"] + list(command)


def wait_for_child_pid(output: Path, timeout: float = 10.0) -> Optional[int]:
    """collector が子を起動して pid を書き出すまで待つ。

    停止時にプロセスグループごと落とすため、pgid（= 子の pid）を session.json へ
    残しておく必要がある。
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = child_pid(output)
        if pid is not None:
            return pid
        time.sleep(0.1)
    return child_pid(output)
