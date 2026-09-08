from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support
from support import FakeRunner

from melib import doctor

ALL_TOOLS = doctor.REQUIRED_TOOLS + doctor.PLATFORM_TOOLS + doctor.OPTIONAL_TOOLS + ("security",)


def which_factory(available):
    return lambda name: "/usr/bin/" + name if name in available else None


def version_runner():
    return FakeRunner(lambda command: (0, "{0} version 1.2.3".format(command[0]), ""))


class ToolTest(unittest.TestCase):
    def test_見つからないコマンドを報告する(self):
        result = doctor.check_tool("maestro", which=which_factory(()), run_command=version_runner())
        self.assertFalse(result["available"])
        self.assertIsNone(result["version"])

    def test_versionを1行目から取る(self):
        result = doctor.check_tool(
            "ffmpeg", which=which_factory(("ffmpeg",)), run_command=version_runner()
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["version"], "ffmpeg version 1.2.3")

    def test_stderrへ出すコマンドでもversionを拾う(self):
        runner = FakeRunner(lambda command: (0, "", "adb version 9"))
        result = doctor.check_tool("adb", which=which_factory(("adb",)), run_command=runner)
        self.assertEqual(result["version"], "adb version 9")

    def test_version取得に失敗しても落とさない(self):
        def boom(command, **kwargs):
            raise OSError("boom")

        result = doctor.check_tool("maestro", which=which_factory(("maestro",)), run_command=boom)
        self.assertTrue(result["available"])
        self.assertIsNone(result["version"])


class PythonTest(unittest.TestCase):
    def test_3_9以上ならok(self):
        self.assertTrue(doctor.check_python((3, 9))["ok"])
        self.assertTrue(doctor.check_python((3, 12))["ok"])

    def test_3_8はngになる(self):
        self.assertFalse(doctor.check_python((3, 8))["ok"])


class ChecksTest(unittest.TestCase):
    def _run(self, available, apk_present=True, system="Darwin", version_info=(3, 9)):
        with tempfile.TemporaryDirectory() as temporary:
            apk = Path(temporary) / "ADBKeyboard.apk"
            if apk_present:
                support.write(apk, "x")
            return doctor.run_checks(
                which=which_factory(available),
                run_command=version_runner(),
                apk_path=apk,
                system=system,
                version_info=version_info,
            )

    def test_全部揃えばok(self):
        report = self._run(ALL_TOOLS)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["problems"], [])

    def test_必須が欠けるとblockedになる(self):
        report = self._run(("xcrun", "ffmpeg", "ffprobe"))
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any("maestro" in problem for problem in report["problems"]))

    def test_端末操作コマンドが片方あればよい(self):
        report = self._run(("maestro", "ffmpeg", "ffprobe", "adb"))
        self.assertEqual(report["status"], "ok")

    def test_端末操作コマンドが両方無いとblockedになる(self):
        report = self._run(("maestro", "ffmpeg", "ffprobe"))
        self.assertTrue(any("xcrun" in problem for problem in report["problems"]))

    def test_ghが無くてもokのまま(self):
        report = self._run(("maestro", "ffmpeg", "ffprobe", "xcrun"))
        self.assertEqual(report["status"], "ok")
        self.assertFalse(report["optional"][0]["available"])

    def test_pythonが古いとblockedになる(self):
        report = self._run(ALL_TOOLS, version_info=(3, 8))
        self.assertEqual(report["status"], "blocked")

    def test_APKの有無を見る(self):
        self.assertTrue(self._run(ALL_TOOLS)["adbkeyboard_apk"]["present"])
        self.assertFalse(self._run(ALL_TOOLS, apk_present=False)["adbkeyboard_apk"]["present"])

    def test_APKが無くてもokのまま(self):
        # Android の文字入力にしか要らないので必須にしない
        self.assertEqual(self._run(ALL_TOOLS, apk_present=False)["status"], "ok")

    def test_macOS以外ではKeychainを使えないと報告する(self):
        report = self._run(ALL_TOOLS, system="Linux")
        self.assertFalse(report["keychain"]["available"])
        self.assertEqual(report["status"], "ok")


class SummaryTest(unittest.TestCase):
    def test_人向けサマリに要対応を出す(self):
        report = doctor.run_checks(
            which=which_factory(("ffmpeg", "ffprobe")),
            run_command=version_runner(),
            apk_path=Path("/nonexistent/ADBKeyboard.apk"),
            system="Darwin",
            version_info=(3, 9),
        )
        summary = doctor.format_summary(report)
        self.assertIn("要対応:", summary)
        self.assertIn("maestro", summary)

    def test_問題が無ければ揃っていると出す(self):
        with tempfile.TemporaryDirectory() as temporary:
            apk = support.write(Path(temporary) / "ADBKeyboard.apk", "x")
            report = doctor.run_checks(
                which=which_factory(ALL_TOOLS),
                run_command=version_runner(),
                apk_path=apk,
                system="Darwin",
                version_info=(3, 9),
            )
        self.assertIn("必要なものは揃っています", doctor.format_summary(report))


if __name__ == "__main__":
    unittest.main()
