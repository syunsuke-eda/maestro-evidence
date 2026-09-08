from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support
from support import FakeRunner

from melib import textinput
from melib.errors import MeError


class BroadcastTest(unittest.TestCase):
    def test_ADBKeyBoardのbroadcastを組み立てる(self):
        script = textinput.broadcast_script("hello")
        self.assertEqual(script, "am broadcast -a ADB_INPUT_TEXT --es msg hello\n")

    def test_クォートが必要な本文を安全に囲む(self):
        script = textinput.broadcast_script("it's a test")
        self.assertIn("--es msg", script)
        self.assertNotIn("--es msg it's", script)

    def test_本文はホスト側のargvに載らない(self):
        runner = FakeRunner(lambda command: (0, "Broadcast completed", ""))
        textinput.send_text("emulator-5554", "s3cret-text", run_command=runner)
        call = runner.calls[0]
        self.assertEqual(call["command"], ["adb", "-s", "emulator-5554", "shell"])
        self.assertNotIn("s3cret-text", " ".join(call["command"]))
        self.assertIn("s3cret-text", call["kwargs"]["input"])

    def test_送信結果をstatusで返す(self):
        ok = textinput.send_text(
            "emulator-5554", "a", run_command=FakeRunner(lambda c: (0, "", ""))
        )
        self.assertEqual(ok["status"], "pass")
        self.assertEqual(ok["method"], "adbkeyboard")
        ng = textinput.send_text(
            "emulator-5554", "a", run_command=FakeRunner(lambda c: (1, "", "error"))
        )
        self.assertEqual(ng["status"], "fail")


class SetupTest(unittest.TestCase):
    def _runner(self, installed: bool, previous: str = "com.google.android.inputmethod/.IME"):
        def responder(command):
            joined = " ".join(command)
            if "pm list packages" in joined:
                return 0, (textinput.ADBKEYBOARD_PACKAGE + "\n") if installed else "", ""
            if "default_input_method" in joined:
                return 0, previous + "\n", ""
            return 0, "", ""

        return FakeRunner(responder)

    def test_未インストールならAPKを入れる(self):
        runner = self._runner(installed=False)
        with tempfile.TemporaryDirectory() as temporary:
            apk = support.write(Path(temporary) / "ADBKeyboard.apk", "x")
            state = textinput.activate("emulator-5554", apk, run_command=runner)
        self.assertTrue(runner.find("install", "-r"))
        self.assertTrue(state["installed_by_session"])

    def test_インストール済みなら入れ直さない(self):
        runner = self._runner(installed=True)
        with tempfile.TemporaryDirectory() as temporary:
            apk = support.write(Path(temporary) / "ADBKeyboard.apk", "x")
            state = textinput.activate("emulator-5554", apk, run_command=runner)
        self.assertEqual(runner.find("install", "-r"), [])
        self.assertFalse(state["installed_by_session"])

    def test_元のIMEを記録する(self):
        runner = self._runner(installed=True, previous="jp.example/.Keyboard")
        with tempfile.TemporaryDirectory() as temporary:
            apk = support.write(Path(temporary) / "ADBKeyboard.apk", "x")
            state = textinput.activate("emulator-5554", apk, run_command=runner)
        self.assertEqual(state["previous_ime"], "jp.example/.Keyboard")
        self.assertEqual(state["ime"], textinput.ADBKEYBOARD_IME_ID)

    def test_IMEをenableしてsetする(self):
        runner = self._runner(installed=True)
        with tempfile.TemporaryDirectory() as temporary:
            apk = support.write(Path(temporary) / "ADBKeyboard.apk", "x")
            textinput.activate("emulator-5554", apk, run_command=runner)
        self.assertTrue(runner.find("ime", "enable"))
        self.assertTrue(runner.find("ime", "set"))

    def test_APKが無ければ入手先を案内して失敗させる(self):
        runner = self._runner(installed=False)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(MeError) as raised:
                textinput.activate(
                    "emulator-5554", Path(temporary) / "none.apk", run_command=runner
                )
        self.assertIn("github.com/senzhk/ADBKeyBoard", str(raised.exception))

    def test_元のIMEへ戻す(self):
        runner = FakeRunner(lambda command: (0, "", ""))
        self.assertEqual(
            textinput.restore("emulator-5554", "jp.example/.Keyboard", run_command=runner),
            "restored",
        )
        self.assertEqual(
            runner.commands[0][-1:], ["jp.example/.Keyboard"]
        )

    def test_元のIMEが不明なら何もしない(self):
        runner = FakeRunner()
        self.assertEqual(textinput.restore("emulator-5554", "", run_command=runner), "unknown_previous")
        self.assertEqual(runner.calls, [])

    def test_戻せなければfailedを返す(self):
        runner = FakeRunner(lambda command: (1, "", "error"))
        self.assertEqual(
            textinput.restore("emulator-5554", "jp.example/.Keyboard", run_command=runner),
            "failed",
        )


if __name__ == "__main__":
    unittest.main()
