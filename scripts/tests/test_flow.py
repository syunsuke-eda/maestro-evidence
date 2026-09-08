from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support
from support import FakeRunner

from melib.errors import MeError
from melib.flow import build_flow, maestro_test_command, run_step, safe_repo_path, step_environment

COMMANDS = '- tapOn:\n    id: "login.submit"\n- assertVisible: "ようこそ"\n'


class BuildFlowTest(unittest.TestCase):
    def test_appIdを先頭に付ける(self):
        flow = build_flow("com.example.app", COMMANDS)
        self.assertTrue(flow.startswith("appId: com.example.app\n---\n"))

    def test_commands本文をそのまま埋め込む(self):
        flow = build_flow("com.example.app", COMMANDS)
        self.assertIn('- assertVisible: "ようこそ"', flow)

    def test_空のcommandsは拒否する(self):
        with self.assertRaises(MeError):
            build_flow("com.example.app", "\n \n")


class RunStepTest(unittest.TestCase):
    def test_maestroのdevice指定はtestの前に置く(self):
        self.assertEqual(
            maestro_test_command("UDID", Path("/tmp/step.yaml")),
            ["maestro", "--device", "UDID", "test", "/tmp/step.yaml"],
        )

    def test_credentialはenvへ渡りargvへ載らない(self):
        runner = FakeRunner(lambda command: (0, "Flow passed", ""))
        with tempfile.TemporaryDirectory() as temporary:
            run_step(
                device="UDID",
                app_id="com.example.app",
                commands=COMMANDS,
                flow_path=Path(temporary) / "step-001.yaml",
                credentials={"MAESTRO_TEST_PASSWORD": "s3cret"},
                run_command=runner,
                environ={"PATH": "/usr/bin"},
            )
        call = runner.calls[0]
        self.assertEqual(call["kwargs"]["env"]["MAESTRO_TEST_PASSWORD"], "s3cret")
        self.assertNotIn("s3cret", " ".join(call["command"]))

    def test_失敗しても例外にせず結果を返す(self):
        runner = FakeRunner(lambda command: (1, "", "Element not found"))
        with tempfile.TemporaryDirectory() as temporary:
            result = run_step(
                device="UDID",
                app_id="com.example.app",
                commands=COMMANDS,
                flow_path=Path(temporary) / "step-001.yaml",
                credentials={},
                run_command=runner,
                environ={},
            )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("Element not found", result["output_tail"])

    def test_出力のANSIを落として末尾30行に絞る(self):
        body = "".join("\x1b[31mline{0}\x1b[0m\n".format(index) for index in range(50))
        runner = FakeRunner(lambda command: (0, body, ""))
        with tempfile.TemporaryDirectory() as temporary:
            result = run_step(
                device="UDID",
                app_id="com.example.app",
                commands=COMMANDS,
                flow_path=Path(temporary) / "step-001.yaml",
                credentials={},
                run_command=runner,
                environ={},
            )
        self.assertEqual(len(result["output_tail"]), 30)
        self.assertEqual(result["output_tail"][-1], "line49")

    def test_flowファイルがwork_dir内に書かれる(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as temporary:
            flow_path = Path(temporary) / "flows" / "step-001.yaml"
            run_step(
                device="UDID",
                app_id="com.example.app",
                commands=COMMANDS,
                flow_path=flow_path,
                credentials={},
                run_command=runner,
                environ={},
            )
            self.assertTrue(flow_path.is_file())
            self.assertIn("appId: com.example.app", flow_path.read_text(encoding="utf-8"))


class StepEnvironmentTest(unittest.TestCase):
    def test_既存の環境変数を保ったままcredentialを足す(self):
        environment = step_environment({"MAESTRO_A": "1"}, environ={"PATH": "/bin"})
        self.assertEqual(environment["PATH"], "/bin")
        self.assertEqual(environment["MAESTRO_A"], "1")


class SafeRepoPathTest(unittest.TestCase):
    def test_リポジトリ外のpathを拒否する(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(MeError):
                safe_repo_path(Path(temporary), "../escape.yaml", "login_flow")

    def test_存在するリポジトリ内pathを返す(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            support.write(repo / "maestro" / "login.yaml", "appId: x\n")
            self.assertEqual(
                safe_repo_path(repo, "maestro/login.yaml", "login_flow"),
                (repo / "maestro" / "login.yaml").resolve(),
            )


if __name__ == "__main__":
    unittest.main()
