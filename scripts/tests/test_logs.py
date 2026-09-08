from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support

from melib import logs
from melib.collector import format_line
from melib.proc import strip_ansi


class SliceTest(unittest.TestCase):
    def test_バイトオフセット以降だけを切り出す(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = support.write(Path(temporary) / "log.txt", "a\nb\nc\n")
            offset = len("a\n")
            self.assertEqual(logs.slice_bytes(path, offset), "b\nc\n")

    def test_行の途中から始まっても次の行頭まで捨てる(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = support.write(Path(temporary) / "log.txt", "abcdef\nghi\n")
            self.assertEqual(logs.slice_bytes(path, 3), "ghi\n")

    def test_終端オフセットまでで打ち切る(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = support.write(Path(temporary) / "log.txt", "a\nb\nc\n")
            self.assertEqual(logs.slice_bytes(path, 0, 4), "a\nb\n")

    def test_ファイルが無ければ空文字を返す(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(logs.slice_bytes(Path(temporary) / "none.txt", 0), "")


class MergeTest(unittest.TestCase):
    def test_2ソースを時刻順にマージする(self):
        merged = logs.merge(
            {
                "launch": "2026-09-08T10:00:01.000+09:00\tlaunch-1\n"
                "2026-09-08T10:00:03.000+09:00\tlaunch-2\n",
                "os": "2026-09-08T10:00:02.000+09:00\tos-1\n",
            }
        )
        self.assertEqual(
            [line.split("\t", 1)[1] for line in merged],
            ["[launch] launch-1", "[os] os-1", "[launch] launch-2"],
        )

    def test_時刻の無い継続行は直前の時刻を継ぐ(self):
        merged = logs.merge(
            {"launch": "2026-09-08T10:00:01.000+09:00\thead\n  continued\n"}
        )
        self.assertEqual(len(merged), 2)
        self.assertTrue(merged[1].startswith("2026-09-08T10:00:01.000+09:00"))


class ErrorExtractionTest(unittest.TestCase):
    def test_error_patternに一致する行だけ拾う(self):
        lines = ["ok", "Unhandled Exception: boom", "still ok"]
        self.assertEqual(
            logs.extract_errors(lines, ["Exception"]), ["Unhandled Exception: boom"]
        )

    def test_ignore_patternに一致する行は除外する(self):
        lines = ["Exception: keep", "Exception: known-noise"]
        self.assertEqual(
            logs.extract_errors(lines, ["Exception"], ["known-noise"]), ["Exception: keep"]
        )

    def test_error_patternが空なら何も拾わない(self):
        self.assertEqual(logs.extract_errors(["Exception"], []), [])

    def test_tailは末尾から数える(self):
        self.assertEqual(logs.tail(["a", "b", "c"], 2), ["b", "c"])


OS_NOISE = [
    "2026-09-08T19:54:27.000+09:00\t[os] 2026-09-08 19:54:27.100 Df Runner[1:a] (UIKit) hasError: 0",
    "2026-09-08T19:54:27.001+09:00\t[os] 2026-09-08 19:54:27.101 Df Runner[1:a] [com.apple.xpc:XPCErrors] ok",
    "2026-09-08T19:54:27.002+09:00\t[os] 2026-09-08 19:54:27.102 I  Runner[1:a] (CoreHaptics) plist not found",
    "2026-09-08T19:54:27.003+09:00\t[os] 2026-09-08 19:54:27.103 Df Runner[1:a] UIKBFeedbackGenerator prepare",
]
OS_REAL = [
    "2026-09-08T19:54:27.843+09:00\t[os] 2026-09-08 19:54:27.843 E  Runner[1:a] Failed to load album",
    "2026-09-08T19:54:28.869+09:00\t[os] 2026-09-08 19:54:28.869 F  Runner[1:a] Fatal error in isolate",
]
LAUNCH = [
    "2026-09-08T19:54:27.500+09:00\t[launch] flutter: Unhandled Exception: boom",
    "2026-09-08T19:54:27.600+09:00\t[launch] flutter: ok",
]

LEVEL = [r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+\s+(?:E|Er|F|Fa)\s"]


class SplitMergedTest(unittest.TestCase):
    def test_時刻とソースと本文へ分解する(self):
        self.assertEqual(
            logs.split_merged("2026-09-08T10:00:00.000+09:00\t[os] body here"),
            ("2026-09-08T10:00:00.000+09:00", "os", "body here"),
        )

    def test_ソース表記が無ければ空のソースを返す(self):
        self.assertEqual(logs.split_merged("no separator"), ("", "", "no separator"))


class ErrorsBySourceTest(unittest.TestCase):
    def test_OSログはログレベルで判定しノイズを拾わない(self):
        hits = logs.extract_errors_by_source(OS_NOISE + OS_REAL, {"os": LEVEL})
        self.assertEqual(len(hits), 2)
        self.assertTrue(all(" E  " in hit or " F  " in hit for hit in hits))

    def test_文言一致だとノイズを拾ってしまうことを対比で示す(self):
        naive = logs.extract_errors(OS_NOISE + OS_REAL, ["Error", "Exception"])
        self.assertGreater(len(naive), 0)
        self.assertLess(
            len(logs.extract_errors_by_source(OS_NOISE + OS_REAL, {"os": LEVEL})), len(naive) + 1
        )

    def test_launchは文言一致で判定する(self):
        hits = logs.extract_errors_by_source(LAUNCH, {"launch": ["Exception"]})
        self.assertEqual(len(hits), 1)
        self.assertIn("boom", hits[0])

    def test_ソースごとに違う基準を同時に適用する(self):
        hits = logs.extract_errors_by_source(
            LAUNCH + OS_NOISE + OS_REAL, {"launch": ["Exception"], "os": LEVEL}
        )
        self.assertEqual(len(hits), 3)

    def test_ignore_patternsは両ソースへ効く(self):
        hits = logs.extract_errors_by_source(
            LAUNCH + OS_REAL,
            {"launch": ["Exception"], "os": LEVEL},
            ["boom", "Fatal error"],
        )
        self.assertEqual(len(hits), 1)
        self.assertIn("Failed to load album", hits[0])

    def test_patternsが無いソースは何も拾わない(self):
        self.assertEqual(logs.extract_errors_by_source(OS_REAL, {"launch": ["Exception"]}), [])


# E レベルでも出る、UIKit / XCTest の枠組み由来ノイズ
FRAMEWORK_NOISE = [
    "2026-09-08T20:00:01.000+09:00\t[os] 2026-09-08 20:00:01.000 E  Runner[1:a] [com.apple.UIKit:UIFocus] focus update failed",
    "2026-09-08T20:00:02.000+09:00\t[os] 2026-09-08 20:00:02.000 E  Runner[1:a] [com.apple.UIKit:TraitCollection] trait resolution",
    "2026-09-08T20:00:03.000+09:00\t[os] 2026-09-08 20:00:03.000 E  Runner[1:a] [com.apple.dt.xctest:Default] Automation type mismatch",
]
KAHOH_IGNORE = [
    "CoreHaptics",
    "UIKBFeedbackGenerator",
    "hasError: 0",
    "com.apple.UIKit:UIFocus",
    "com.apple.UIKit:TraitCollection",
    "com.apple.dt.xctest",
]


class FrameworkNoiseTest(unittest.TestCase):
    def test_Eレベルの枠組みノイズはignore_patternsで落とせる(self):
        hits = logs.extract_errors_by_source(
            FRAMEWORK_NOISE + OS_REAL, {"os": LEVEL}, KAHOH_IGNORE
        )
        self.assertEqual(len(hits), 2)
        self.assertTrue(all("com.apple" not in hit for hit in hits))

    def test_ignoreを外すと枠組みノイズも数に入る(self):
        hits = logs.extract_errors_by_source(FRAMEWORK_NOISE + OS_REAL, {"os": LEVEL})
        self.assertEqual(len(hits), 5)

    def test_アプリ由来のエラーは落とさない(self):
        hits = logs.extract_errors_by_source(OS_REAL, {"os": LEVEL}, KAHOH_IGNORE)
        self.assertEqual(len(hits), 2)


class AnsiTest(unittest.TestCase):
    def test_保存時にANSIエスケープを落とす(self):
        line = format_line("\x1b[38;5;12mflutter: hello\x1b[0m\n", now="2026-09-08T10:00:00.000+09:00")
        self.assertEqual(line, "2026-09-08T10:00:00.000+09:00\tflutter: hello\n")

    def test_OSCシーケンスも落とす(self):
        self.assertEqual(strip_ansi("\x1b]0;title\x07body"), "body")

    def test_ESCの直前のbackslashも一緒に落とす(self):
        self.assertEqual(strip_ansi("\\\x1b[38;5;12mflutter: ok\\\x1b[0m"), "flutter: ok")

    def test_文字列として書かれたESCも落とす(self):
        self.assertEqual(strip_ansi("a \\033[31mred\\033[0m b"), "a red b")

    def test_単独のESCも落とす(self):
        self.assertEqual(strip_ansi("bare \x1b end"), "bare  end")


if __name__ == "__main__":
    unittest.main()
