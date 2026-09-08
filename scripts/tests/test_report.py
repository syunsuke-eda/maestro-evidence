from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import support

from melib import report
from melib.errors import MeError

HREF_OR_SRC = re.compile(r'(?:href|src)="([^"]*)"')


def sample_session():
    return {
        "fingerprint": {
            "branch": "feat/example",
            "base_ref": "origin/develop",
            "merge_base": "a" * 40,
            "head_sha": "b" * 40,
            "diff_sha256": "c" * 64,
            "changed_files": ["lib/view/home.dart"],
        },
        "config": {"app_id": "com.example.app.dev", "platform": "ios"},
        "device": {"id": "AAA", "name": "iPhone 17 Pro"},
        "started_at": "2026-09-08T10:00:00+00:00",
        "finished_at": "2026-09-08T10:20:00+00:00",
        "secret_scan": {"violations": []},
        "cases": [
            {
                "id": "open-album",
                "title": "アルバムを開く",
                "status": "pass",
                "started_at": "2026-09-08T10:01:00+00:00",
                "ended_at": "2026-09-08T10:05:00+00:00",
                "notes": "戻し不要",
                "screenshots": ["cases/open-album/screenshots/final.png"],
                "steps": [
                    {
                        "label": "アルバムを開く",
                        "status": "pass",
                        "duration_seconds": 4.2,
                        "commands": '- openLink: https://example.com/a\n- tapOn: "開く"',
                    }
                ],
                "video": {
                    "status": "pass",
                    "file": "video.mp4",
                    "final_frame": "final-frame.png",
                    "contact_sheet": "contact-sheet.png",
                    "trimmed_leading_seconds": 3.5,
                    "trim_status": "applied",
                    "review": {
                        "codec": "h264",
                        "pixel_format": "yuv420p",
                        "size_bytes": 1234,
                        "sha256": "d" * 64,
                    },
                },
                "errors": ["10:02\t[launch] Unhandled Exception: boom"],
                "errors_count": 1,
                "review": {"status": "pass", "notes": "白画面なし"},
            }
        ],
    }


def build(directory: Path) -> str:
    case = directory / "cases" / "open-album"
    support.write(case / "screenshots" / "final.png", "png")
    support.write(case / "video.mp4", "mp4")
    support.write(case / "final-frame.png", "png")
    support.write(case / "contact-sheet.png", "png")
    return report.build_html(sample_session(), directory)


class ReportTest(unittest.TestCase):
    def test_リンク先は相対pathだけになる(self):
        with tempfile.TemporaryDirectory() as temporary:
            html = build(Path(temporary))
        for value in HREF_OR_SRC.findall(html):
            self.assertFalse(value.startswith("http"), value)
            self.assertFalse(value.startswith("/"), value)
            self.assertNotIn("..", value)

    def test_step本文のURLは表示されてもリンクにしない(self):
        with tempfile.TemporaryDirectory() as temporary:
            html = build(Path(temporary))
        self.assertIn("https://example.com/a", html)
        self.assertNotIn('href="https://example.com/a"', html)

    def test_error件数と本文を出す(self):
        with tempfile.TemporaryDirectory() as temporary:
            html = build(Path(temporary))
        self.assertIn("Unhandled Exception: boom", html)
        self.assertIn("<dt>error 行</dt><dd>1</dd>", html)

    def test_存在しない成果物はリンクしない(self):
        with tempfile.TemporaryDirectory() as temporary:
            html = report.build_html(sample_session(), Path(temporary))
        self.assertIn("動画なし", html)

    def test_work_dirの外を指す成果物は拒否する(self):
        session = sample_session()
        session["cases"][0]["screenshots"] = ["../../etc/passwd"]
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(MeError):
                report.build_html(session, Path(temporary))

    def test_HTMLを特殊文字ごとエスケープする(self):
        session = sample_session()
        session["cases"][0]["title"] = '<script>alert("x")</script>'
        with tempfile.TemporaryDirectory() as temporary:
            html = report.build_html(session, Path(temporary))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_承認済み計画があれば併記する(self):
        session = sample_session()
        session["cases"][0]["plan"] = {
            "precondition": "ログイン済み",
            "mutations": "なし",
            "steps": ["アルバムを開く"],
            "expected": ["写真が12枚見える"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            html = report.build_html(session, Path(temporary))
        self.assertIn("写真が12枚見える", html)
        self.assertIn("ログイン済み", html)

    def test_計画が無ければその旨を出す(self):
        with tempfile.TemporaryDirectory() as temporary:
            html = report.build_html(sample_session(), Path(temporary))
        self.assertIn("計画の記録なし", html)

    def test_index_htmlを書き出す(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = report.write_report(sample_session(), Path(temporary))
            self.assertEqual(output.name, "index.html")
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
