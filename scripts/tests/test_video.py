from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support
from support import FakeRunner

from melib import video
from melib.errors import MeError


def probe_payload(size: int, codec: str = "h264", duration: float = 30.0) -> str:
    return json.dumps(
        {
            "streams": [
                {
                    "codec_name": codec,
                    "width": 886,
                    "height": 1918,
                    "pix_fmt": "yuv420p",
                    "avg_frame_rate": "30/1",
                }
            ],
            "format": {"duration": str(duration), "size": str(size)},
        }
    )


class FreezeTrimTest(unittest.TestCase):
    def _detect(self, stderr: str, duration: float = 60.0, code: int = 0):
        runner = FakeRunner(lambda command: (code, "", stderr))
        return video.detect_leading_freeze_trim_seconds(
            Path("/tmp/video.mp4"), duration, run_command=runner
        )

    def test_先頭の静止は安全余裕を引いて切り詰める(self):
        trim, status = self._detect("freeze_start: 0.0\nfreeze_end: 12.5\n")
        self.assertEqual(status, "applied")
        self.assertEqual(trim, 11.5)

    def test_先頭が静止でなければ切り詰めない(self):
        self.assertEqual(self._detect("freeze_start: 20.0\nfreeze_end: 30.0\n"), (0.0, "not_needed"))

    def test_静止が無ければ切り詰めない(self):
        self.assertEqual(self._detect(""), (0.0, "not_needed"))

    def test_終端が取れなければ諦める(self):
        trim, status = self._detect("freeze_start: 0.2\n")
        self.assertEqual(trim, 0.0)
        self.assertTrue(status.startswith("skipped:"))

    def test_切り詰め量が過大なら丸めずに諦める(self):
        trim, status = self._detect("freeze_start: 0.0\nfreeze_end: 58.0\n", duration=60.0)
        self.assertEqual(trim, 0.0)
        self.assertIn("大きすぎ", status)

    def test_解析失敗はfail_openで0を返す(self):
        trim, status = self._detect("", code=1)
        self.assertEqual(trim, 0.0)
        self.assertTrue(status.startswith("skipped:"))


class TranscodeCommandTest(unittest.TestCase):
    def test_H264とyuv420pとfaststartを必ず指定する(self):
        command = video.transcode_command(Path("a.mp4"), Path("b.mp4"), 30.0, 886)
        self.assertIn("libx264", command)
        self.assertIn("yuv420p", command)
        self.assertIn("+faststart", command)

    def test_幅指定がscaleフィルタに入る(self):
        command = video.transcode_command(Path("a.mp4"), Path("b.mp4"), 30.0, 720)
        self.assertIn("scale=w='min(720,iw)':h=-2,fps=30", command)


class PrepareReviewVideoTest(unittest.TestCase):
    def _prepare(self, sizes, directory: Path):
        """sizes は ffprobe が返す size を呼ばれた順に使う。"""

        state = {"index": 0}

        def responder(command):
            if command[0] == "ffprobe":
                size = sizes[min(state["index"], len(sizes) - 1)]
                state["index"] += 1
                return 0, probe_payload(size), ""
            return 0, "", ""

        runner = FakeRunner(responder)
        source = support.write(directory / "video-source.mp4", "x")
        output = support.write(directory / "video.mp4", "y")
        result = video.prepare_review_video(
            source,
            output,
            directory / "contact-sheet.png",
            directory / "final-frame.png",
            run_command=runner,
        )
        return result, runner

    def test_9MB以内なら1回のtranscodeで終わる(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, runner = self._prepare([1_000_000, 1_000_000], Path(temporary))
        transcodes = [command for command in runner.commands if "libx264" in command]
        self.assertEqual(len(transcodes), 1)
        self.assertEqual(result["encoded_width_limit"], video.MAX_REVIEW_WIDTH)

    def test_9MB超過なら幅720で再エンコードする(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, runner = self._prepare(
                [1_000_000, 20_000_000, 1_000_000], Path(temporary)
            )
        transcodes = [command for command in runner.commands if "libx264" in command]
        self.assertEqual(len(transcodes), 2)
        self.assertIn("scale=w='min(720,iw)':h=-2,fps=30", transcodes[1])
        self.assertEqual(result["encoded_width_limit"], video.FALLBACK_REVIEW_WIDTH)

    def test_再エンコードしても9MBを超えるなら失敗させる(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(MeError):
                self._prepare([1_000_000, 20_000_000], Path(temporary))

    def test_H264以外の出力は拒否する(self):
        def responder(command):
            if command[0] == "ffprobe":
                return 0, probe_payload(1_000_000, codec="hevc"), ""
            return 0, "", ""

        runner = FakeRunner(responder)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = support.write(directory / "video-source.mp4", "x")
            output = support.write(directory / "video.mp4", "y")
            with self.assertRaises(MeError):
                video.prepare_review_video(
                    source,
                    output,
                    directory / "contact-sheet.png",
                    directory / "final-frame.png",
                    run_command=runner,
                )

    def test_contact_sheetとfinal_frameを取り出す(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, runner = self._prepare([1_000_000], Path(temporary))
        self.assertTrue(runner.find("tile=4x3"))
        self.assertTrue(runner.find("-sseof"))
        self.assertEqual(result["final_frame"], "final-frame.png")


class ProbeTest(unittest.TestCase):
    def test_0byteの動画は拒否する(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = support.write(Path(temporary) / "video.mp4", "")
            with self.assertRaises(MeError):
                video.probe_video(path, run_command=FakeRunner())

    def test_durationが0以下なら拒否する(self):
        runner = FakeRunner(lambda command: (0, probe_payload(100, duration=0.0), ""))
        with tempfile.TemporaryDirectory() as temporary:
            path = support.write(Path(temporary) / "video.mp4", "x")
            with self.assertRaises(MeError):
                video.probe_video(path, run_command=runner)


if __name__ == "__main__":
    unittest.main()
