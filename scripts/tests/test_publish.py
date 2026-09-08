from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support
from support import FakeRunner

from melib import publish
from melib.errors import MeError
from melib.video import sha256_file

HEAD_SHA = "b" * 40
COMMENT_URL = "https://github.com/owner/repo/pull/12#issuecomment-999"


def make_work(directory: Path, *, video_bytes: bytes = b"video-data"):
    case = directory / "cases" / "open-album"
    case.mkdir(parents=True, exist_ok=True)
    (case / "video.mp4").write_bytes(video_bytes)
    support.write(case / "final-frame.png", "png")
    session = {
        "repo": str(directory),
        "fingerprint": {"head_sha": HEAD_SHA},
        "device": {"name": "iPhone 17 Pro"},
        "cases": [
            {
                "id": "open-album",
                "title": "アルバムを開く",
                "status": "pass",
                "review": {"status": "pass", "notes": "問題なし"},
                "video": {
                    "status": "pass",
                    "file": "video.mp4",
                    "final_frame": "final-frame.png",
                    "review": {"sha256": sha256_file(case / "video.mp4")},
                },
            }
        ],
    }
    return session


def responder_factory(*, gh_version="2.100.0", head="b" * 40, comment=COMMENT_URL):
    def responder(command):
        joined = " ".join(command)
        if "--version" in joined:
            return 0, "gh version {0} (2026-01-01)".format(gh_version), ""
        if "api user" in joined:
            return 0, "tester\n", ""
        if "repo view" in joined:
            return 0, json.dumps({"nameWithOwner": "owner/repo", "viewerPermission": "WRITE"}), ""
        if "pr view" in joined:
            return 0, json.dumps({"headRefOid": head, "url": "https://github.com/owner/repo/pull/12"}), ""
        if "pr comment" in joined:
            return 0, comment + "\n", ""
        if "issues/comments" in joined:
            return 0, comment + "\n", ""
        return 0, "", ""

    return responder


class PublishTest(unittest.TestCase):
    def _publish(self, directory: Path, session, runner, **kwargs):
        return publish.publish(
            repo=directory,
            work=directory,
            session=session,
            pr_number=12,
            approved=kwargs.pop("approved", True),
            run_command=runner,
            **kwargs
        )

    def test_承認なしでは投稿しない(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaises(MeError):
                self._publish(directory, make_work(directory), FakeRunner(), approved=False)

    def test_gh_versionが古いと拒否する(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            runner = FakeRunner(responder_factory(gh_version="2.99.9"))
            with self.assertRaises(MeError):
                self._publish(directory, make_work(directory), runner)

    def test_head_SHA不一致は拒否する(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            runner = FakeRunner(responder_factory(head="f" * 40))
            with self.assertRaises(MeError):
                self._publish(directory, make_work(directory), runner)

    def test_動画のSHA256が変わっていたら拒否する(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            session = make_work(directory)
            (directory / "cases" / "open-album" / "video.mp4").write_bytes(b"tampered")
            with self.assertRaises(MeError):
                self._publish(directory, session, FakeRunner(responder_factory()))

    def test_9MB超過の動画は拒否する(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            session = make_work(directory, video_bytes=b"x" * 9_000_001)
            with self.assertRaises(MeError):
                self._publish(directory, session, FakeRunner(responder_factory()))

    def test_passでないケースは拒否する(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            session = make_work(directory)
            session["cases"][0]["status"] = "fail"
            with self.assertRaises(MeError):
                self._publish(directory, session, FakeRunner(responder_factory()))

    def test_原本動画は投稿できない(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            session = make_work(directory)
            case = directory / "cases" / "open-album"
            (case / "video-source.mp4").write_bytes(b"raw")
            session["cases"][0]["video"]["file"] = "video-source.mp4"
            session["cases"][0]["video"]["review"]["sha256"] = sha256_file(
                case / "video-source.mp4"
            )
            with self.assertRaises(MeError):
                self._publish(directory, session, FakeRunner(responder_factory()))

    def test_動画と最終フレームを添付して投稿する(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            runner = FakeRunner(responder_factory())
            result = self._publish(directory, make_work(directory), runner)
        comment = runner.find("pr", "comment")[0]
        self.assertEqual(comment.count("--attach"), 2)
        self.assertEqual(result["comment_url"], COMMENT_URL)
        self.assertEqual(result["cases"], ["open-album"])

    def test_投稿後の再取得が食い違えば失敗させる(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)

            def responder(command):
                base = responder_factory()(command)
                if "issues/comments" in " ".join(command):
                    return 0, "https://github.com/owner/repo/pull/12#issuecomment-1\n", ""
                return base

            with self.assertRaises(MeError):
                self._publish(directory, make_work(directory), FakeRunner(responder))


class VersionTest(unittest.TestCase):
    def test_versionを数値のtupleにする(self):
        runner = FakeRunner(lambda command: (0, "gh version 2.101.3 (2026-02-02)", ""))
        self.assertEqual(publish.gh_version(Path("/tmp"), run_command=runner), (2, 101, 3))

    def test_version文字列が読めなければ失敗させる(self):
        runner = FakeRunner(lambda command: (0, "unexpected", ""))
        with self.assertRaises(MeError):
            publish.gh_version(Path("/tmp"), run_command=runner)


class ArtifactTest(unittest.TestCase):
    def test_work_dir外の成果物を拒否する(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(MeError):
                publish.safe_artifact(Path(temporary), "../outside.mp4", "投稿用動画")


if __name__ == "__main__":
    unittest.main()
