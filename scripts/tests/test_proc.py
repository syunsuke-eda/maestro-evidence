from __future__ import annotations

import signal
import subprocess
import unittest
from pathlib import Path
from typing import List

import support  # noqa: F401

from melib import proc


class FakePopen:
    def __init__(self) -> None:
        self.kwargs = {}
        self.pid = 4242

    def __call__(self, command, **kwargs):
        self.kwargs = kwargs
        self.command = command
        return self


class FakeProcessTable:
    """os.kill 互換。SIGINT を無視する子を再現できる。"""

    def __init__(self, alive: bool = True, dies_on=(signal.SIGINT,)) -> None:
        self.alive = alive
        self.dies_on = dies_on
        self.signals: List[int] = []

    def kill(self, pid: int, number: int) -> None:
        if not self.alive:
            raise ProcessLookupError(pid)
        if number == 0:
            return
        self.signals.append(number)
        if number in self.dies_on or number == signal.SIGKILL:
            self.alive = False


class SpawnTest(unittest.TestCase):
    def test_stdinを塞ぎ新しいsessionで起動する(self):
        popen = FakePopen()
        pid = proc.spawn_detached(["sleep", "1"], popen=popen)
        self.assertEqual(pid, 4242)
        self.assertIs(popen.kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(popen.kwargs["start_new_session"])
        self.assertTrue(popen.kwargs["close_fds"])

    def test_出力先を指定しなければ捨てる(self):
        popen = FakePopen()
        proc.spawn_detached(["sleep", "1"], popen=popen)
        self.assertIs(popen.kwargs["stdout"], subprocess.DEVNULL)


class StopTest(unittest.TestCase):
    def _stop(self, table: FakeProcessTable, timeout: float = 0.01) -> str:
        return proc.stop_pid(
            10, timeout=timeout, kill=table.kill, sleep=lambda seconds: None, monotonic=_clock()
        )

    def test_SIGINTで止まればstopped(self):
        table = FakeProcessTable()
        self.assertEqual(self._stop(table), "stopped")
        self.assertEqual(table.signals, [signal.SIGINT])

    def test_SIGINTを無視する子はSIGKILLへ進む(self):
        table = FakeProcessTable(dies_on=())
        self.assertEqual(self._stop(table), "killed")
        self.assertEqual(table.signals, [signal.SIGINT, signal.SIGKILL])

    def test_既に居ないプロセスは何も送らない(self):
        table = FakeProcessTable(alive=False)
        self.assertEqual(self._stop(table), "not_running")
        self.assertEqual(table.signals, [])


def _clock():
    state = {"value": 0.0}

    def monotonic() -> float:
        state["value"] += 0.005
        return state["value"]

    return monotonic


class FakeProcessGroup:
    """os.killpg 互換。グループ内に生存メンバーが居る限り 0 番シグナルが通る。"""

    def __init__(self, alive: bool = True, dies_on=(signal.SIGINT,)) -> None:
        self.alive = alive
        self.dies_on = dies_on
        self.signals: List[int] = []

    def killpg(self, pgid: int, number: int) -> None:
        if not self.alive:
            raise ProcessLookupError(pgid)
        if number == 0:
            return
        self.signals.append(number)
        if number in self.dies_on or number == signal.SIGKILL:
            self.alive = False


class StopGroupTest(unittest.TestCase):
    def _stop(self, group: FakeProcessGroup):
        return proc.stop_group(
            2000, grace=0.01, killpg=group.killpg, sleep=lambda seconds: None, monotonic=_clock()
        )

    def test_SIGINTで全滅すればstopped(self):
        group = FakeProcessGroup()
        result = self._stop(group)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["signals"], ["SIGINT"])
        self.assertFalse(result["remaining"])

    def test_SIGINTを無視する孫が居ればSIGTERMまで進む(self):
        group = FakeProcessGroup(dies_on=(signal.SIGTERM,))
        result = self._stop(group)
        self.assertEqual(result["status"], "terminated")
        self.assertEqual(result["signals"], ["SIGINT", "SIGTERM"])

    def test_SIGTERMも無視すればSIGKILLまで進む(self):
        group = FakeProcessGroup(dies_on=())
        result = self._stop(group)
        self.assertEqual(result["status"], "killed")
        self.assertEqual(result["signals"], ["SIGINT", "SIGTERM", "SIGKILL"])

    def test_既に空のグループへは何も送らない(self):
        group = FakeProcessGroup(alive=False)
        result = self._stop(group)
        self.assertEqual(result["status"], "not_running")
        self.assertEqual(group.signals, [])

    def test_pgidが0以下なら自分のグループへ撃たない(self):
        group = FakeProcessGroup()
        for pgid in (0, -1):
            result = proc.stop_group(pgid, killpg=group.killpg)
            self.assertEqual(result["status"], "not_running")
        self.assertEqual(group.signals, [])

    def test_グループリーダーが消えても子孫が残れば生存扱い(self):
        group = FakeProcessGroup()
        self.assertTrue(proc.group_alive(2000, killpg=group.killpg))
        group.alive = False
        self.assertFalse(proc.group_alive(2000, killpg=group.killpg))


class AnsiTest(unittest.TestCase):
    def test_CSIとOSCの両方を落とす(self):
        self.assertEqual(proc.strip_ansi("\x1b[2K\x1b[38;5;12mx\x1b[0m"), "x")


class RunTest(unittest.TestCase):
    def test_checkで失敗をMeErrorにする(self):
        from melib.errors import MeError
        from support import FakeRunner

        runner = FakeRunner(lambda command: (1, "", "boom"))
        with self.assertRaises(MeError):
            proc.run(["false"], run_command=runner, check=True)

    def test_check無しなら結果をそのまま返す(self):
        from support import FakeRunner

        runner = FakeRunner(lambda command: (3, "out", "err"))
        result = proc.run(["x"], run_command=runner)
        self.assertEqual(result.returncode, 3)


if __name__ == "__main__":
    unittest.main()
