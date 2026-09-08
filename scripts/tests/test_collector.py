from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from melib import collector


class FakeChild:
    def __init__(self, lines):
        self.stdout = io.StringIO("".join(lines))
        self.pid = 5150
        self.waited = False

    def wait(self, timeout=None):
        self.waited = True
        return 0


class FakePopen:
    def __init__(self, lines):
        self.lines = lines
        self.kwargs = {}

    def __call__(self, command, **kwargs):
        self.kwargs = kwargs
        self.command = command
        return FakeChild(self.lines)


class CollectTest(unittest.TestCase):
    def _collect(self, lines, directory: Path):
        popen = FakePopen(lines)
        code = collector.collect(
            ["fvm", "flutter", "run"],
            directory / "log-launch.txt",
            popen=popen,
            install_signal=lambda number, handler: None,
        )
        return code, popen

    def test_子を独立したプロセスグループのリーダーにする(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, popen = self._collect(["a\n"], Path(temporary))
        # wrapper が消えても孫を group ごと止められるようにするための必須条件
        self.assertTrue(popen.kwargs["start_new_session"])

    def test_標準入力を塞ぎ出力だけを取り込む(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, popen = self._collect(["a\n"], Path(temporary))
        self.assertIs(popen.kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(popen.kwargs["stdout"], subprocess.PIPE)
        self.assertIs(popen.kwargs["stderr"], subprocess.STDOUT)

    def test_時刻を付けANSIを落として書き出す(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._collect(["\x1b[31mflutter: ok\x1b[0m\n", "second\n"], directory)
            written = (directory / "log-launch.txt").read_text(encoding="utf-8")
        lines = written.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith("\tflutter: ok"))
        self.assertNotIn("\x1b", written)

    def test_子のpidを書き出し終了時に消す(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output = directory / "log-launch.txt"
            self._collect(["a\n"], directory)
            self.assertFalse(collector.pid_file_for(output).exists())

    def test_pidファイルからpgidを読み戻せる(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "log-os.txt"
            support.write(collector.pid_file_for(output), "5150\n")
            self.assertEqual(collector.child_pid(output), 5150)
            self.assertEqual(collector.wait_for_child_pid(output, timeout=0.1), 5150)

    def test_pidファイルが無ければNoneを返す(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "log-os.txt"
            self.assertIsNone(collector.wait_for_child_pid(output, timeout=0.2))


if __name__ == "__main__":
    unittest.main()
