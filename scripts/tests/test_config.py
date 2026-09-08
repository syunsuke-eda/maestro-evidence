from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from melib.config import (
    DEFAULT_OS_ERROR_PATTERNS,
    TEMPLATE,
    load_config,
    resolve_config_path,
    validate_config,
)
from melib.errors import MeError


def base(**overrides):
    value = json.loads(json.dumps(TEMPLATE))
    value.update(overrides)
    return value


class ValidateConfigTest(unittest.TestCase):
    def test_雛形はそのまま妥当と判定される(self):
        config, errors = validate_config(TEMPLATE)
        self.assertEqual(errors, [])
        self.assertEqual(config["app_id"], "com.example.app.dev")

    def test_app_idが無いとエラーになる(self):
        raw = base()
        del raw["app_id"]
        _, errors = validate_config(raw)
        self.assertTrue(any("app_id" in error for error in errors))

    def test_未知のplatformはエラーになる(self):
        _, errors = validate_config(base(platform="web"))
        self.assertTrue(any("platform" in error for error in errors))

    def test_未知のlaunch_modeはエラーになる(self):
        raw = base()
        raw["launch"] = dict(raw["launch"], mode="attach")
        _, errors = validate_config(raw)
        self.assertTrue(any("launch.mode" in error for error in errors))

    def test_command_modeでcommandが無いとエラーになる(self):
        raw = base()
        raw["launch"] = {"mode": "command"}
        _, errors = validate_config(raw)
        self.assertTrue(any("launch.command" in error for error in errors))

    def test_未知のlog_sourceはエラーになる(self):
        raw = base()
        raw["log"] = dict(raw["log"], sources=["launch", "syslog"])
        _, errors = validate_config(raw)
        self.assertTrue(any("log.sources" in error for error in errors))

    def test_installedでlaunchソースを選ぶとエラーになる(self):
        raw = base()
        raw["launch"] = {"mode": "installed"}
        raw["log"] = dict(raw["log"], sources=["launch"])
        _, errors = validate_config(raw)
        self.assertTrue(any("launch.mode=command" in error for error in errors))

    def test_log_sources省略時はmodeから既定が決まる(self):
        raw = base()
        raw["log"] = {"process": "Runner"}
        config, errors = validate_config(raw)
        self.assertEqual(errors, [])
        self.assertEqual(config["log"]["sources"], ["launch"])
        raw["launch"] = {"mode": "installed"}
        config, _ = validate_config(raw)
        self.assertEqual(config["log"]["sources"], ["os"])

    def test_osソースにprocessが無いとエラーになる(self):
        raw = base()
        raw["log"] = {"sources": ["os"]}
        _, errors = validate_config(raw)
        self.assertTrue(any("log.process" in error for error in errors))

    def test_os_error_patternsは既定でログレベル判定になる(self):
        raw = base()
        raw["log"] = {"sources": ["os"], "process": "Runner"}
        config, errors = validate_config(raw)
        self.assertEqual(errors, [])
        self.assertEqual(config["log"]["os_error_patterns"], DEFAULT_OS_ERROR_PATTERNS)

    def test_os_error_patternsを上書きできる(self):
        raw = base()
        raw["log"] = dict(raw["log"], os_error_patterns=["FATAL EXCEPTION"])
        config, errors = validate_config(raw)
        self.assertEqual(errors, [])
        self.assertEqual(config["log"]["os_error_patterns"], ["FATAL EXCEPTION"])

    def test_os_error_patternsの壊れた正規表現はエラーになる(self):
        raw = base()
        raw["log"] = dict(raw["log"], os_error_patterns=["("])
        _, errors = validate_config(raw)
        self.assertTrue(any("os_error_patterns" in error for error in errors))

    def test_error_patternsとos_error_patternsは別物として保持される(self):
        raw = base()
        raw["log"] = dict(raw["log"], error_patterns=["E/flutter"], os_error_patterns=["^X"])
        config, _ = validate_config(raw)
        self.assertEqual(config["log"]["error_patterns"], ["E/flutter"])
        self.assertEqual(config["log"]["os_error_patterns"], ["^X"])

    def test_env_namesはMAESTRO_接頭辞が必要(self):
        raw = base()
        raw["credentials"] = dict(raw["credentials"], env_names=["TEST_EMAIL"])
        _, errors = validate_config(raw)
        self.assertTrue(any("MAESTRO_" in error for error in errors))

    def test_keychainにservice指定が無いとエラーになる(self):
        raw = base()
        raw["credentials"] = {"source": "keychain", "env_names": ["MAESTRO_A"]}
        _, errors = validate_config(raw)
        self.assertTrue(any("keychain_service" in error for error in errors))

    def test_未知のcredential_sourceはエラーになる(self):
        raw = base()
        raw["credentials"] = dict(raw["credentials"], source="vault")
        _, errors = validate_config(raw)
        self.assertTrue(any("credentials.source" in error for error in errors))

    def test_keychain_accountsでaccount名を差し替えられる(self):
        raw = base()
        raw["credentials"] = dict(
            raw["credentials"],
            keychain_accounts={"MAESTRO_TEST_EMAIL": "test-email"},
        )
        config, errors = validate_config(raw)
        self.assertEqual(errors, [])
        self.assertEqual(config["credentials"]["keychain_accounts"], {"MAESTRO_TEST_EMAIL": "test-email"})

    def test_keychain_accountsのキーはenv_namesに無いとエラーになる(self):
        raw = base()
        raw["credentials"] = dict(
            raw["credentials"], keychain_accounts={"MAESTRO_UNKNOWN": "x"}
        )
        _, errors = validate_config(raw)
        self.assertTrue(any("keychain_accounts" in error for error in errors))

    def test_keychain_accountsの値が空ならエラーになる(self):
        raw = base()
        raw["credentials"] = dict(
            raw["credentials"], keychain_accounts={"MAESTRO_TEST_EMAIL": ""}
        )
        _, errors = validate_config(raw)
        self.assertTrue(any("keychain_accounts" in error for error in errors))

    def test_keychain_accountsがobjectでなければエラーになる(self):
        raw = base()
        raw["credentials"] = dict(raw["credentials"], keychain_accounts=["a"])
        _, errors = validate_config(raw)
        self.assertTrue(any("keychain_accounts" in error for error in errors))

    def test_keychain_accounts未指定なら空のmapになる(self):
        config, errors = validate_config(TEMPLATE)
        self.assertEqual(errors, [])
        self.assertEqual(config["credentials"]["keychain_accounts"], {})

    def test_recordingは既定で軽量な値になる(self):
        config, errors = validate_config(TEMPLATE)
        self.assertEqual(errors, [])
        self.assertEqual(config["recording"], {"bit_rate": 4000000, "size": "720x1600"})

    def test_recordingを上書きできる(self):
        config, errors = validate_config(base(recording={"bit_rate": 2000000, "size": "540x1200"}))
        self.assertEqual(errors, [])
        self.assertEqual(config["recording"]["size"], "540x1200")

    def test_recordingにnullを指定できる(self):
        config, errors = validate_config(base(recording={"bit_rate": None, "size": None}))
        self.assertEqual(errors, [])
        self.assertIsNone(config["recording"]["bit_rate"])

    def test_不正なbit_rateはエラーになる(self):
        _, errors = validate_config(base(recording={"bit_rate": 0}))
        self.assertTrue(any("bit_rate" in error for error in errors))

    def test_不正なsize表記はエラーになる(self):
        _, errors = validate_config(base(recording={"size": "720*1600"}))
        self.assertTrue(any("size" in error for error in errors))

    def test_login_flowにリポジトリ外パスは書けない(self):
        _, errors = validate_config(base(login_flow="../outside.yaml"))
        self.assertTrue(any("login_flow" in error for error in errors))

    def test_壊れた正規表現はエラーになる(self):
        raw = base()
        raw["log"] = dict(raw["log"], error_patterns=["("])
        _, errors = validate_config(raw)
        self.assertTrue(any("正規表現" in error for error in errors))

    def test_未知のキーはエラーになる(self):
        _, errors = validate_config(base(extra=1))
        self.assertTrue(any("未知のキー" in error for error in errors))

    def test_base_refは既定値で補完される(self):
        raw = base()
        del raw["base_ref"]
        config, errors = validate_config(raw)
        self.assertEqual(errors, [])
        self.assertEqual(config["base_ref"], "origin/main")


class ConfigPathTest(unittest.TestCase):
    def test_指定が無ければリポジトリ直下を使う(self):
        self.assertEqual(
            resolve_config_path(Path("/repo")), Path("/repo/.maestro-evidence.json")
        )

    def test_明示指定が既定より優先される(self):
        with tempfile.TemporaryDirectory() as temporary:
            override = Path(temporary) / "android.json"
            # macOS の /var は /private/var への symlink なので resolve 後で比べる
            self.assertEqual(
                resolve_config_path(Path("/repo"), str(override)), override.resolve()
            )

    def test_相対パスはカレント基準で解決する(self):
        with tempfile.TemporaryDirectory() as temporary:
            previous = os.getcwd()
            os.chdir(temporary)
            try:
                resolved = resolve_config_path(Path("/repo"), "android.json")
            finally:
                os.chdir(previous)
            self.assertEqual(resolved.name, "android.json")
            self.assertTrue(resolved.is_absolute())
            self.assertNotIn("/repo/", str(resolved))

    def test_指定したファイルを読む(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            android = json.loads(json.dumps(TEMPLATE))
            android.update(
                {
                    "app_id": "com.example.app.debug",
                    "platform": "android",
                    "launch": {"mode": "installed"},
                    "log": {"sources": ["os"], "process": "com.example.app.debug"},
                }
            )
            (directory / "android.json").write_text(json.dumps(android), encoding="utf-8")
            (directory / ".maestro-evidence.json").write_text(
                json.dumps(TEMPLATE), encoding="utf-8"
            )
            self.assertEqual(load_config(directory)["platform"], "ios")
            self.assertEqual(
                load_config(directory, path=directory / "android.json")["platform"], "android"
            )

    def test_存在しないファイルを指定したら失敗させる(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(MeError):
                load_config(Path(temporary), path=Path(temporary) / "none.json")


if __name__ == "__main__":
    unittest.main()
