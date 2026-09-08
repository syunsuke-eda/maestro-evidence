from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import support

import me
from melib import session as session_module
from melib.errors import MeError

IOS_CONFIG = {
    "app_id": "com.example.app.dev",
    "platform": "ios",
    "launch": {
        "mode": "command",
        "command": "fvm flutter run -d {device} --flavor dev",
        "ready_pattern": "A Dart VM Service",
        "ready_timeout_seconds": 300,
    },
    "log": {
        "sources": ["launch", "os"],
        "process": "Runner",
        "error_patterns": ["Exception"],
        "os_error_patterns": [r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+\s+(?:E|Er|F|Fa)\s"],
        "ignore_patterns": ["known-noise"],
    },
}


ANDROID_CONFIG = {
    "android": {"text_input": "adbkeyboard", "adbkeyboard_apk": None},
    "app_id": "com.example.app.debug",
    "platform": "android",
    "launch": {"mode": "installed"},
    "log": {
        "sources": ["os"],
        "process": "com.example.app.debug",
        "error_patterns": [],
        "os_error_patterns": ["FATAL EXCEPTION"],
        "ignore_patterns": [],
    },
    "recording": {"bit_rate": 4_000_000, "size": "720x1600"},
}


class ArgvTest(unittest.TestCase):
    def test_launchコマンドのdeviceを置換する(self):
        self.assertEqual(
            me.launch_argv(IOS_CONFIG, "UDID"),
            ["fvm", "flutter", "run", "-d", "UDID", "--flavor", "dev"],
        )

    def test_iOSのログ収集はcompactかつinfoで絞る(self):
        argv = me.os_log_argv(IOS_CONFIG, "UDID")
        self.assertEqual(argv[:6], ["xcrun", "simctl", "spawn", "UDID", "log", "stream"])
        self.assertIn("--style", argv)
        self.assertEqual(argv[argv.index("--style") + 1], "compact")
        self.assertEqual(argv[argv.index("--level") + 1], "info")
        self.assertIn('processImagePath ENDSWITH "/Runner"', argv)

    def test_OSログのpredicateは実行ファイル名の末尾一致にする(self):
        # CONTAINS だと maestro-driver-iosUITests-Runner まで拾ってしまう
        argv = me.os_log_argv(IOS_CONFIG, "UDID")
        predicate = argv[argv.index("--predicate") + 1]
        self.assertTrue(predicate.endswith('ENDSWITH "/Runner"'), predicate)
        self.assertNotIn("CONTAINS", predicate)

    def test_iOSの録画コマンドはH264と強制上書きを指定する(self):
        argv = me.recorder_argv(IOS_CONFIG, "UDID", Path("/tmp/video-source.mp4"))
        self.assertEqual(
            argv,
            [
                "xcrun",
                "simctl",
                "io",
                "UDID",
                "recordVideo",
                "--codec=h264",
                "--force",
                "/tmp/video-source.mp4",
            ],
        )

    def test_Androidの録画はscreenrecordを使う(self):
        argv = me.recorder_argv(ANDROID_CONFIG, "emulator-5554", Path("/tmp/v.mp4"))
        self.assertIn("screenrecord", argv)
        self.assertEqual(argv[:3], ["adb", "-s", "emulator-5554"])

    def test_Androidの録画は既定でビットレートと解像度を絞る(self):
        argv = me.recorder_argv(ANDROID_CONFIG, "emulator-5554", Path("/tmp/v.mp4"))
        self.assertEqual(argv[argv.index("--bit-rate") + 1], "4000000")
        self.assertEqual(argv[argv.index("--size") + 1], "720x1600")

    def test_録画オプションはconfigで上書きできる(self):
        config = dict(ANDROID_CONFIG, recording={"bit_rate": 2_000_000, "size": "540x1200"})
        argv = me.recorder_argv(config, "emulator-5554", Path("/tmp/v.mp4"))
        self.assertEqual(argv[argv.index("--bit-rate") + 1], "2000000")
        self.assertEqual(argv[argv.index("--size") + 1], "540x1200")

    def test_録画オプションをnullにすると引数を付けない(self):
        config = dict(ANDROID_CONFIG, recording={"bit_rate": None, "size": None})
        argv = me.recorder_argv(config, "emulator-5554", Path("/tmp/v.mp4"))
        self.assertNotIn("--bit-rate", argv)
        self.assertNotIn("--size", argv)

    def test_Androidのlogcatはpidで絞る(self):
        # pid で絞らないと SystemServiceRegistry などシステム行が大量に入る
        argv = me.os_log_argv(ANDROID_CONFIG, "emulator-5554", pid=4321)
        self.assertIn("--pid=4321", argv)
        self.assertEqual(argv[:6], ["adb", "-s", "emulator-5554", "logcat", "-v", "time"])

    def test_pidが無ければpid指定を付けない(self):
        argv = me.os_log_argv(ANDROID_CONFIG, "emulator-5554")
        self.assertTrue(all(not part.startswith("--pid") for part in argv))


class CloseCaseTest(unittest.TestCase):
    def _work(self, temporary: str):
        work = Path(temporary)
        support.write(
            work / "log-launch.txt",
            "2026-09-08T10:00:00.000+09:00\tbefore\n"
            "2026-09-08T10:00:02.000+09:00\tflutter: ok\n"
            "2026-09-08T10:00:04.000+09:00\tUnhandled Exception: boom\n"
            "2026-09-08T10:00:05.000+09:00\tException known-noise\n",
        )
        support.write(
            work / "log-os.txt",
            "2026-09-08T10:00:01.000+09:00\tos-before\n"
            "2026-09-08T10:00:03.000+09:00\t2026-09-08 10:00:03.000 Df Runner[1:a] hasError: 0\n"
            "2026-09-08T10:00:03.500+09:00\t2026-09-08 10:00:03.500 Df Runner[1:a] [xpc:XPCErrors] ok\n"
            "2026-09-08T10:00:06.000+09:00\t2026-09-08 10:00:06.000 E  Runner[1:a] Failed to load album\n",
        )
        session = session_module.new_session(
            work=work,
            repo=Path(temporary),
            config=IOS_CONFIG,
            fingerprint={"branch": "feat/x"},
            device={"id": "UDID", "name": "iPhone"},
            started_at="2026-09-08T01:00:00+00:00",
        )
        case = {
            "id": "open-album",
            "title": "アルバム",
            "status": "running",
            "offsets": {
                "launch": len("2026-09-08T10:00:00.000+09:00\tbefore\n"),
                "os": len("2026-09-08T10:00:01.000+09:00\tos-before\n"),
            },
            "screenshots": [],
            "steps": [],
        }
        session["cases"].append(case)
        return work, session, case

    def setUp(self) -> None:
        self.original = me.finalize_video
        me.finalize_video = lambda work, session, case: {"status": "skipped"}

    def tearDown(self) -> None:
        me.finalize_video = self.original

    def test_開始オフセット以降だけを時刻順にマージする(self):
        with tempfile.TemporaryDirectory() as temporary:
            work, session, case = self._work(temporary)
            me.close_case(work, session, case, "pass", "所見")
            merged = (work / "cases" / "open-album" / "log.txt").read_text(encoding="utf-8")
        lines = [line for line in merged.splitlines() if line]
        self.assertNotIn("before", merged)
        self.assertEqual(
            [line.split("\t", 1)[1].split("] ", 1)[0] + "]" for line in lines],
            ["[launch]", "[os]", "[os]", "[launch]", "[launch]", "[os]"],
        )

    def test_ignore_patternを除いたerrorだけ数える(self):
        with tempfile.TemporaryDirectory() as temporary:
            work, session, case = self._work(temporary)
            me.close_case(work, session, case, "pass", None)
            errors = (work / "cases" / "open-album" / "errors.txt").read_text(encoding="utf-8")
        # launch の Exception 1件 + os の E レベル 1件
        self.assertEqual(case["errors_count"], 2)
        self.assertIn("Unhandled Exception: boom", errors)
        self.assertNotIn("known-noise", errors)

    def test_OSログのノイズをerrorに数えない(self):
        with tempfile.TemporaryDirectory() as temporary:
            work, session, case = self._work(temporary)
            me.close_case(work, session, case, "pass", None)
            errors = (work / "cases" / "open-album" / "errors.txt").read_text(encoding="utf-8")
        self.assertNotIn("hasError: 0", errors)
        self.assertNotIn("XPCErrors", errors)
        self.assertIn("Failed to load album", errors)

    def test_launchのANSIを保存前に落とす(self):
        with tempfile.TemporaryDirectory() as temporary:
            work, session, case = self._work(temporary)
            with (work / "log-launch.txt").open("a", encoding="utf-8") as sink:
                sink.write("2026-09-08T10:00:07.000+09:00\t\\\x1b[38;5;12mflutter: colored\\\x1b[0m\n")
            me.close_case(work, session, case, "pass", None)
            merged = (work / "cases" / "open-album" / "log.txt").read_text(encoding="utf-8")
        self.assertIn("flutter: colored", merged)
        self.assertNotIn("\x1b", merged)
        self.assertNotIn("38;5;12", merged)

    def test_ソース別のerror基準を組み立てる(self):
        patterns = me.error_patterns_by_source(IOS_CONFIG)
        self.assertEqual(patterns["launch"], ["Exception"])
        self.assertTrue(patterns["os"][0].startswith("^"))

    def test_終了時にstatusと所見を確定する(self):
        with tempfile.TemporaryDirectory() as temporary:
            work, session, case = self._work(temporary)
            me.close_case(work, session, case, "fail", "白画面")
        self.assertEqual(case["status"], "fail")
        self.assertEqual(case["notes"], "白画面")
        self.assertIsNotNone(case["ended_at"])
        self.assertNotIn("recorder_pid", case)


class ScrubTest(unittest.TestCase):
    def test_credentialの実値を保存前に落とす(self):
        self.assertEqual(
            me.scrub("Input text s3cret into field", ["s3cret"]),
            "Input text [REDACTED] into field",
        )

    def test_行ごとにスクラブする(self):
        self.assertEqual(
            me.scrub_lines(["a s3cret", "b"], ["s3cret"]), ["a [REDACTED]", "b"]
        )

    def test_Bearerトークンも落とす(self):
        self.assertIn("[REDACTED]", me.scrub("Authorization: Bearer abc", []))

    def test_credentialを取得できなくても失敗させない(self):
        config = {
            "credentials": {
                "source": "env",
                "keychain_service": None,
                "env_names": ["MAESTRO_TEST_PASSWORD"],
            }
        }
        self.assertEqual(me.session_secrets(config), [])


class StepScrubTest(unittest.TestCase):
    """Maestro は inputText の解決後の値を進捗出力へ書くため、保存前に落とす。"""

    def setUp(self) -> None:
        self.original_run_step = me.flow_module.run_step
        self.original_load = me.credentials_module.resolve_credentials
        me.credentials_module.resolve_credentials = lambda config, **kwargs: (
            {"MAESTRO_TEST_PASSWORD": "s3cret"},
            "available",
        )
        me.flow_module.run_step = lambda **kwargs: {
            "label": kwargs.get("label"),
            "commands": kwargs["commands"],
            "flow": "step-001.yaml",
            "exit_code": 0,
            "status": "pass",
            "duration_seconds": 1.0,
            "output_tail": ["Input text s3cret into password"],
        }

    def tearDown(self) -> None:
        me.flow_module.run_step = self.original_run_step
        me.credentials_module.resolve_credentials = self.original_load

    def test_stepの出力からcredentialを落としてから保存する(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            session = session_module.new_session(
                work=work,
                repo=work,
                config=IOS_CONFIG,
                fingerprint={},
                device={"id": "UDID"},
                started_at="2026-09-08T01:00:00+00:00",
            )
            session["cases"].append({"id": "smoke", "status": "running", "steps": []})
            session_module.save(work, session)
            commands_file = support.write(work / "step.yaml", "- inputText: ${MAESTRO_TEST_PASSWORD}\n")
            args = me.build_parser().parse_args(
                [
                    "step",
                    "--work",
                    str(work),
                    "--case",
                    "smoke",
                    "--device",
                    "UDID",
                    "--commands-file",
                    str(commands_file),
                ]
            )
            with contextlib.redirect_stdout(io.StringIO()):
                me.command_step(args)
            saved = (work / "cases" / "smoke" / "steps.json").read_text(encoding="utf-8")
        self.assertNotIn("s3cret", saved)
        self.assertIn("[REDACTED]", saved)


class StepWithoutCredentialsTest(unittest.TestCase):
    """ログイン済み端末では credential を使わない step も多い。止めずに続ける。"""

    def setUp(self) -> None:
        self.original_run_step = me.flow_module.run_step
        self.original_resolve = me.credentials_module.resolve_credentials
        self.received = {}

        def fake_run_step(**kwargs):
            self.received.update(kwargs)
            return {
                "label": kwargs.get("label"),
                "commands": kwargs["commands"],
                "flow": "step-001.yaml",
                "exit_code": 0,
                "status": "pass",
                "duration_seconds": 1.0,
                "output_tail": [],
            }

        me.flow_module.run_step = fake_run_step
        me.credentials_module.resolve_credentials = lambda config, **kwargs: ({}, "unavailable")

    def tearDown(self) -> None:
        me.flow_module.run_step = self.original_run_step
        me.credentials_module.resolve_credentials = self.original_resolve

    def _run_step(self, work: Path):
        session = session_module.new_session(
            work=work,
            repo=work,
            config=IOS_CONFIG,
            fingerprint={},
            device={"id": "UDID"},
            started_at="2026-09-08T01:00:00+00:00",
        )
        session["cases"].append({"id": "smoke", "status": "running", "steps": []})
        session_module.save(work, session)
        commands_file = support.write(work / "step.yaml", "- tapOn: \"次へ\"\n")
        args = me.build_parser().parse_args(
            [
                "step",
                "--work",
                str(work),
                "--case",
                "smoke",
                "--device",
                "UDID",
                "--commands-file",
                str(commands_file),
            ]
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = me.command_step(args)
        return code, json.loads(stdout.getvalue())

    def test_credentialが揃わなくてもstepを実行する(self):
        with tempfile.TemporaryDirectory() as temporary:
            code, result = self._run_step(Path(temporary))
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "pass")

    def test_結果JSONにunavailableの警告を含める(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, result = self._run_step(Path(temporary))
        self.assertEqual(result["credentials"], "unavailable")

    def test_環境変数を渡さずに実行する(self):
        with tempfile.TemporaryDirectory() as temporary:
            self._run_step(Path(temporary))
        self.assertEqual(self.received["credentials"], {})

    def test_steps_jsonにも状態を残す(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            self._run_step(work)
            saved = session_module.load_steps(work, "smoke")
        self.assertEqual(saved[0]["credentials"], "unavailable")


class SecretScanResultTest(unittest.TestCase):
    def test_実値を照合できなかったscanはunavailableと記録する(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            session = session_module.new_session(
                work=work,
                repo=work,
                config={
                    "credentials": {
                        "source": "env",
                        "keychain_service": None,
                        "env_names": ["MAESTRO_TEST_PASSWORD"],
                    }
                },
                fingerprint={},
                device={},
                started_at="2026-09-08T01:00:00+00:00",
            )
            result = me.run_secret_scan(work, session, work)
        self.assertEqual(result["status"], "clean")
        self.assertEqual(result["credential_check"], "unavailable")
        self.assertEqual(result["credential_values_checked"], 0)


class PlanTest(unittest.TestCase):
    def test_cases_jsonをidで引けるようにする(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            support.write(
                work / "cases.json",
                '{"cases": [{"id": "open-album", "expected": ["一覧が出る"]}]}',
            )
            plan = me.load_plan(work)
        self.assertEqual(plan["open-album"]["expected"], ["一覧が出る"])

    def test_cases_jsonが無ければ空になる(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(me.load_plan(Path(temporary)), {})

    def test_壊れたcases_jsonでも落とさない(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            support.write(work / "cases.json", "{ broken")
            self.assertEqual(me.load_plan(work), {})


class StopCollectorsTest(unittest.TestCase):
    """`fvm flutter run` は wrapper が消えても dartvm を残すため group で止める。"""

    def setUp(self) -> None:
        self.original_stop_pid = me.stop_pid
        self.original_stop_group = me.stop_group
        self.stopped_pids = []
        self.stopped_groups = []
        me.stop_pid = lambda pid, **kwargs: self.stopped_pids.append(pid) or "stopped"

        def fake_stop_group(pgid, **kwargs):
            self.stopped_groups.append(pgid)
            return {
                "pgid": pgid,
                "status": "terminated",
                "signals": ["SIGINT", "SIGTERM"],
                "remaining": pgid in self.remaining,
            }

        self.remaining = set()
        me.stop_group = fake_stop_group

    def tearDown(self) -> None:
        me.stop_pid = self.original_stop_pid
        me.stop_group = self.original_stop_group

    def _session(self, work: Path, collectors):
        session = session_module.new_session(
            work=work,
            repo=work,
            config=IOS_CONFIG,
            fingerprint={},
            device={},
            started_at="2026-09-08T01:00:00+00:00",
        )
        session["collectors"] = collectors
        return session

    def test_collectorと配下のグループの両方を止める(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            session = self._session(
                work,
                {
                    "launch": {"pid": 100, "pgid": 101, "log": "log-launch.txt"},
                    "os": {"pid": 200, "pgid": 201, "log": "log-os.txt"},
                },
            )
            results = me.stop_collectors(work, session)
        self.assertEqual(self.stopped_pids, [100, 200])
        self.assertEqual(self.stopped_groups, [101, 201])
        self.assertEqual([item["source"] for item in results], ["launch", "os"])

    def test_停止したpidとpgidを結果に含める(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            session = self._session(work, {"launch": {"pid": 100, "pgid": 101}})
            results = me.stop_collectors(work, session)
        self.assertEqual(results[0]["collector_pid"], 100)
        self.assertEqual(results[0]["pgid"], 101)
        self.assertEqual(results[0]["signals"], ["SIGINT", "SIGTERM"])
        self.assertFalse(results[0]["group_remaining"])

    def test_残存があればgroup_remainingが真になる(self):
        self.remaining = {101}
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            session = self._session(work, {"launch": {"pid": 100, "pgid": 101}})
            results = me.stop_collectors(work, session)
        self.assertTrue(results[0]["group_remaining"])

    def test_pgidが記録されていなければpidファイルから拾う(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            support.write(work / "log-launch.txt.pid", "777\n")
            session = self._session(work, {"launch": {"pid": 100}})
            me.stop_collectors(work, session)
        self.assertEqual(self.stopped_groups, [777])

    def test_pgidが分からなければグループへ撃たない(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            session = self._session(work, {"launch": {"pid": 100}})
            results = me.stop_collectors(work, session)
        self.assertEqual(self.stopped_groups, [])
        self.assertEqual(results[0]["group"], "unknown")


class TypeCommandTest(unittest.TestCase):
    """Flutter の TextField へ確実に文字を入れるための入力経路。"""

    def setUp(self) -> None:
        self.original_send = me.textinput.send_text
        self.original_run_step = me.flow_module.run_step
        self.sent = {}
        self.step_kwargs = {}

        def fake_send(device_id, text, **kwargs):
            self.sent = {"device": device_id, "text": text}
            return {"method": "adbkeyboard", "exit_code": 0, "status": "pass"}

        def fake_run_step(**kwargs):
            self.step_kwargs = kwargs
            return {
                "commands": kwargs["commands"],
                "exit_code": 0,
                "status": "pass",
                "duration_seconds": 0.5,
                "output_tail": [],
            }

        me.textinput.send_text = fake_send
        me.flow_module.run_step = fake_run_step

    def tearDown(self) -> None:
        me.textinput.send_text = self.original_send
        me.flow_module.run_step = self.original_run_step

    def _run(self, work: Path, config, extra=None, text="こんにちは"):
        session = session_module.new_session(
            work=work,
            repo=work,
            config=config,
            fingerprint={},
            device={"id": "DEV"},
            started_at="2026-09-09T01:00:00+00:00",
        )
        session["cases"].append({"id": "smoke", "status": "running", "steps": []})
        session_module.save(work, session)
        argv = ["type", "--work", str(work), "--case", "smoke", "--device", "DEV"]
        if extra:
            argv.extend(extra)
        else:
            argv.extend(["--text-file", str(support.write(work / "text.txt", text + "\n"))])
        args = me.build_parser().parse_args(argv)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            me.command_type(args)
        return json.loads(stdout.getvalue())

    def test_AndroidではADBKeyBoard経由で入力する(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = self._run(Path(temporary), ANDROID_CONFIG)
        self.assertEqual(record["method"], "adbkeyboard")
        self.assertEqual(self.sent["text"], "こんにちは")

    def test_iOSではMaestroのinputTextで入力する(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = self._run(Path(temporary), IOS_CONFIG)
        self.assertEqual(record["method"], "maestro")
        self.assertIn("inputText", self.step_kwargs["commands"])

    def test_Maestro経由でも本文をflowへ直書きしない(self):
        with tempfile.TemporaryDirectory() as temporary:
            self._run(Path(temporary), IOS_CONFIG, text="s3cret")
        self.assertNotIn("s3cret", self.step_kwargs["commands"])
        self.assertIn("${MAESTRO_EVIDENCE_TYPE_TEXT}", self.step_kwargs["commands"])
        self.assertEqual(
            self.step_kwargs["credentials"]["MAESTRO_EVIDENCE_TYPE_TEXT"], "s3cret"
        )

    def test_android_text_inputをmaestroにすると経路が変わる(self):
        config = dict(ANDROID_CONFIG, android={"text_input": "maestro", "adbkeyboard_apk": None})
        with tempfile.TemporaryDirectory() as temporary:
            record = self._run(Path(temporary), config)
        self.assertEqual(record["method"], "maestro")

    def test_本文をstepsへ残さない(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            self._run(work, ANDROID_CONFIG, text="p@ssw0rd")
            saved = (work / "cases" / "smoke" / "steps.json").read_text(encoding="utf-8")
        self.assertNotIn("p@ssw0rd", saved)
        self.assertIn("[REDACTED]", saved)

    def test_文字数と種別を記録する(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = self._run(Path(temporary), ANDROID_CONFIG, text="abcde")
        self.assertEqual(record["kind"], "type")
        self.assertEqual(record["text_length"], 5)
        self.assertEqual(record["text"], "[REDACTED]")

    def test_envで環境変数から本文を解決する(self):
        os.environ["MAESTRO_TYPE_TEST"] = "from-env"
        try:
            with tempfile.TemporaryDirectory() as temporary:
                record = self._run(
                    Path(temporary), ANDROID_CONFIG, extra=["--env", "MAESTRO_TYPE_TEST"]
                )
        finally:
            del os.environ["MAESTRO_TYPE_TEST"]
        self.assertEqual(self.sent["text"], "from-env")
        self.assertEqual(record["text_source"], "env:MAESTRO_TYPE_TEST")

    def test_envはMAESTRO_接頭辞を要求する(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(MeError):
                self._run(Path(temporary), ANDROID_CONFIG, extra=["--env", "SECRET"])

    def test_解決できないenvは失敗させる(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(MeError):
                self._run(Path(temporary), ANDROID_CONFIG, extra=["--env", "MAESTRO_MISSING_X"])

    def test_ファイル末尾の改行は入力に含めない(self):
        with tempfile.TemporaryDirectory() as temporary:
            self._run(Path(temporary), ANDROID_CONFIG, text="abc")
        self.assertEqual(self.sent["text"], "abc")


class TextInputSetupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_activate = me.textinput.activate
        self.original_restore = me.textinput.restore
        me.textinput.activate = lambda device_id, apk, **kwargs: {
            "status": "active",
            "previous_ime": "jp.example/.Keyboard",
            "ime": "com.android.adbkeyboard/.AdbIME",
            "installed_by_session": True,
            "apk": str(apk),
        }
        me.textinput.restore = lambda device_id, previous, **kwargs: "restored"

    def tearDown(self) -> None:
        me.textinput.activate = self.original_activate
        me.textinput.restore = self.original_restore

    def test_iOSでは何もしない(self):
        self.assertEqual(me.setup_text_input(IOS_CONFIG, "UDID")["status"], "not_applicable")

    def test_maestroモードならIMEを触らない(self):
        config = dict(ANDROID_CONFIG, android={"text_input": "maestro", "adbkeyboard_apk": None})
        self.assertEqual(me.setup_text_input(config, "DEV")["status"], "maestro")

    def test_adbkeyboardモードでIMEを切り替える(self):
        state = me.setup_text_input(ANDROID_CONFIG, "DEV")
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["previous_ime"], "jp.example/.Keyboard")

    def test_APKパスは設定が無ければ既定を使う(self):
        state = me.setup_text_input(ANDROID_CONFIG, "DEV")
        self.assertTrue(state["apk"].endswith(".maestro-evidence/ADBKeyboard.apk"))

    def test_APKパスを設定で上書きできる(self):
        config = dict(
            ANDROID_CONFIG,
            android={"text_input": "adbkeyboard", "adbkeyboard_apk": "/tmp/custom.apk"},
        )
        self.assertEqual(me.setup_text_input(config, "DEV")["apk"], "/tmp/custom.apk")

    def test_session_stopで元のIMEへ戻す(self):
        session = {
            "device": {"id": "DEV"},
            "text_input": {"status": "active", "previous_ime": "jp.example/.Keyboard"},
        }
        self.assertEqual(me.restore_text_input(session)["restore"], "restored")

    def test_切り替えていなければ戻さない(self):
        session = {"device": {"id": "DEV"}, "text_input": {"status": "maestro"}}
        self.assertNotIn("restore", me.restore_text_input(session))


class ConfigForTest(unittest.TestCase):
    """同一リポジトリで iOS / Android を切り替えるための設定解決。"""

    def _write(self, directory: Path, name: str, platform: str, app_id: str) -> Path:
        config = {
            "app_id": app_id,
            "platform": platform,
            "device": {"prefer": "booted"},
            "launch": {"mode": "installed"},
            "log": {"sources": ["os"], "process": app_id},
            "credentials": {"source": "none", "env_names": []},
            "login_flow": None,
            "base_ref": "main",
        }
        return support.write(directory / name, json.dumps(config))

    def test_指定が無ければリポジトリ直下の設定を読む(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            self._write(repo, ".maestro-evidence.json", "ios", "com.example.ios")
            args = me.build_parser().parse_args(["devices", "--repo", str(repo)])
            config, source = me.config_for(args)
        self.assertEqual(config["platform"], "ios")
        self.assertTrue(source.endswith(".maestro-evidence.json"))

    def test_configを渡すとそちらを読む(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            self._write(repo, ".maestro-evidence.json", "ios", "com.example.ios")
            android = self._write(repo, "android.json", "android", "com.example.android")
            args = me.build_parser().parse_args(
                ["devices", "--repo", str(repo), "--config", str(android)]
            )
            config, source = me.config_for(args)
        self.assertEqual(config["platform"], "android")
        self.assertTrue(source.endswith("android.json"))

    def test_workがあればsession_jsonの設定を使う(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary) / "work"
            work.mkdir()
            session = session_module.new_session(
                work=work,
                repo=work,
                config={"app_id": "com.example.recorded", "platform": "android"},
                fingerprint={},
                device={},
                started_at="2026-09-08T01:00:00+00:00",
                config_path=Path("/repo/android.json"),
            )
            session_module.save(work, session)
            args = me.build_parser().parse_args(["devices", "--work", str(work)])
            config, source = me.config_for(args)
        self.assertEqual(source, "session")
        self.assertEqual(config["app_id"], "com.example.recorded")

    def test_workがあってもconfig明示ならそちらを優先する(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work"
            work.mkdir()
            session_module.save(
                work,
                session_module.new_session(
                    work=work,
                    repo=root,
                    config={"app_id": "com.example.recorded", "platform": "android"},
                    fingerprint={},
                    device={},
                    started_at="2026-09-08T01:00:00+00:00",
                ),
            )
            override = self._write(root, "ios.json", "ios", "com.example.ios")
            args = me.build_parser().parse_args(
                ["devices", "--repo", str(root), "--work", str(work), "--config", str(override)]
            )
            config, source = me.config_for(args)
        self.assertEqual(config["app_id"], "com.example.ios")
        self.assertTrue(source.endswith("ios.json"))


class ConfigInitPromptTest(unittest.TestCase):
    """設定作成で間違えやすい2項目は、プロンプト自身に注意を書く。"""

    def _questions(self):
        asked = []

        def fake_ask(question, default):
            asked.append(question)
            return default

        me.build_config_interactively(fake_ask)
        return asked

    def test_launch_commandはdevice置換を説明する(self):
        question = next(q for q in self._questions() if q.startswith("launch.command"))
        self.assertIn("{device}", question)
        self.assertIn("固定ID", question)

    def test_log_processはiOSとAndroidの違いを説明する(self):
        question = next(q for q in self._questions() if q.startswith("log.process"))
        self.assertIn("Runner", question)
        self.assertIn("バンドルID", question)
        self.assertIn("パッケージ名", question)

    def test_生成したJSONはそのまま検証を通る(self):
        import json as json_module

        from melib.config import validate_config

        rendered = me.build_config_interactively(lambda question, default: default)
        _, errors = validate_config(json_module.loads(rendered))
        self.assertEqual(errors, [])


class ParserTest(unittest.TestCase):
    def test_サブコマンドの前後どちらでもworkを受け取れる(self):
        parser = me.build_parser()
        before = parser.parse_args(["--work", "/tmp/a", "report"])
        after = parser.parse_args(["report", "--work", "/tmp/a"])
        self.assertEqual(before.work, "/tmp/a")
        self.assertEqual(after.work, "/tmp/a")

    def test_サブコマンドの前後どちらでもconfigを受け取れる(self):
        parser = me.build_parser()
        self.assertEqual(parser.parse_args(["--config", "a.json", "devices"]).config, "a.json")
        self.assertEqual(parser.parse_args(["devices", "--config", "a.json"]).config, "a.json")

    def test__collectはダブルダッシュ以降をコマンドとして受け取る(self):
        parser = me.build_parser()
        args = parser.parse_args(["_collect", "--out", "/tmp/a.log", "--", "sh", "-c", "echo x"])
        self.assertEqual(args.argv, ["--", "sh", "-c", "echo x"])
        self.assertEqual(args.out, "/tmp/a.log")

    def test_case_endはstatusを検証する(self):
        parser = me.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["case", "end", "--id", "a", "--status", "maybe"])


if __name__ == "__main__":
    unittest.main()
