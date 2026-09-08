from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from melib import session
from melib.errors import MeError


def sample(work: Path):
    return session.new_session(
        work=work,
        repo=Path("/repo"),
        config={"app_id": "com.example.app"},
        fingerprint={"branch": "feat/x"},
        device={"id": "AAA"},
        started_at="2026-09-08T10:00:00+00:00",
        config_path=Path("/repo/maestro-evidence.android.json"),
    )


class SessionTest(unittest.TestCase):
    def test_保存した内容を読み戻せる(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            session.save(work, sample(work))
            loaded = session.load(work)
            self.assertEqual(loaded["device"]["id"], "AAA")

    def test_使った設定ファイルのpathを記録する(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            value = sample(work)
        self.assertEqual(value["config_path"], "/repo/maestro-evidence.android.json")

    def test_設定pathの指定が無ければNoneになる(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            value = session.new_session(
                work=work,
                repo=Path("/repo"),
                config={},
                fingerprint={},
                device={},
                started_at="2026-09-08T10:00:00+00:00",
            )
        self.assertIsNone(value["config_path"])

    def test_session_jsonが無ければ失敗させる(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(MeError):
                session.load(Path(temporary))

    def test_存在しないcaseは失敗させる(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(MeError):
                session.require_case(sample(Path(temporary)), "missing")

    def test_running状態のcaseだけ拾う(self):
        with tempfile.TemporaryDirectory() as temporary:
            value = sample(Path(temporary))
            value["cases"] = [{"id": "a", "status": "pass"}, {"id": "b", "status": "running"}]
            self.assertEqual([case["id"] for case in session.open_cases(value)], ["b"])

    def test_case_idの書式を検証する(self):
        self.assertEqual(session.validate_case_id("open-album"), "open-album")
        for invalid in ("Open", "-open", "オープン", ""):
            with self.assertRaises(MeError):
                session.validate_case_id(invalid)

    def test_stepsを追記して読み戻せる(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            session.save_steps(work, "open-album", [{"index": 1}])
            self.assertEqual(session.load_steps(work, "open-album"), [{"index": 1}])

    def test_stepsが無ければ空リストを返す(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(session.load_steps(Path(temporary), "none"), [])


if __name__ == "__main__":
    unittest.main()
