from __future__ import annotations

import unittest

import support  # noqa: F401
from support import FakeRunner

from melib import credentials
from melib.errors import MeError

CONFIG = {
    "credentials": {
        "source": "keychain",
        "keychain_service": "jp.example.maestro-evidence",
        "env_names": ["MAESTRO_TEST_EMAIL", "MAESTRO_TEST_PASSWORD"],
    }
}


def keychain(values):
    def responder(command):
        account = command[command.index("-a") + 1]
        value = values.get(account)
        return (0, value + "\n", "") if value else (1, "", "not found")

    return FakeRunner(responder)


class LoadTest(unittest.TestCase):
    def test_環境変数を優先して読む(self):
        runner = keychain({})
        loaded = credentials.load_credentials(
            CONFIG,
            environ={"MAESTRO_TEST_EMAIL": "a@example.com", "MAESTRO_TEST_PASSWORD": "p"},
            run_command=runner,
            which=lambda name: "/usr/bin/security",
        )
        self.assertEqual(loaded["MAESTRO_TEST_PASSWORD"], "p")
        self.assertEqual(runner.calls, [])

    def test_環境変数が無ければKeychainを読む(self):
        loaded = credentials.load_credentials(
            CONFIG,
            environ={},
            run_command=keychain(
                {"MAESTRO_TEST_EMAIL": "a@example.com", "MAESTRO_TEST_PASSWORD": "p"}
            ),
            which=lambda name: "/usr/bin/security",
        )
        self.assertEqual(sorted(loaded), ["MAESTRO_TEST_EMAIL", "MAESTRO_TEST_PASSWORD"])

    def test_片方だけなら失敗させる(self):
        with self.assertRaises(MeError):
            credentials.load_credentials(
                CONFIG,
                environ={"MAESTRO_TEST_EMAIL": "a@example.com"},
                run_command=keychain({}),
                which=lambda name: "/usr/bin/security",
            )

    def test_sourceがnoneなら何も読まない(self):
        loaded = credentials.load_credentials(
            {"credentials": {"source": "none", "env_names": []}}, environ={}
        )
        self.assertEqual(loaded, {})

    def test_Keychain検索のaccount名はenv名を使う(self):
        runner = keychain({"MAESTRO_TEST_EMAIL": "a", "MAESTRO_TEST_PASSWORD": "p"})
        credentials.load_credentials(
            CONFIG, environ={}, run_command=runner, which=lambda name: "/usr/bin/security"
        )
        first = runner.commands[0]
        self.assertIn("-s", first)
        self.assertEqual(first[first.index("-s") + 1], "jp.example.maestro-evidence")
        self.assertEqual(first[first.index("-a") + 1], "MAESTRO_TEST_EMAIL")

    def test_Keychainのコマンドに値を渡さない(self):
        runner = keychain({"MAESTRO_TEST_EMAIL": "a", "MAESTRO_TEST_PASSWORD": "p"})
        credentials.load_credentials(
            CONFIG, environ={}, run_command=runner, which=lambda name: "/usr/bin/security"
        )
        for command in runner.commands:
            self.assertNotIn("p", command[-1:])
            self.assertNotIn("add-generic-password", command)


SAMPLE_CONFIG = {
    "credentials": {
        "source": "keychain",
        "keychain_service": "com.example.app.maestro-evidence",
        "env_names": ["MAESTRO_TEST_EMAIL", "MAESTRO_TEST_PASSWORD"],
        "keychain_accounts": {
            "MAESTRO_TEST_EMAIL": "test-email",
            "MAESTRO_TEST_PASSWORD": "test-password",
        },
    }
}


class KeychainAccountTest(unittest.TestCase):
    def test_指定があればaccount名を差し替える(self):
        self.assertEqual(
            credentials.account_name(SAMPLE_CONFIG["credentials"], "MAESTRO_TEST_EMAIL"),
            "test-email",
        )

    def test_指定が無いenvはenv名をそのまま使う(self):
        self.assertEqual(
            credentials.account_name(CONFIG["credentials"], "MAESTRO_TEST_EMAIL"),
            "MAESTRO_TEST_EMAIL",
        )

    def test_旧account名のKeychainからでも読める(self):
        runner = keychain({"test-email": "a@example.com", "test-password": "p"})
        loaded = credentials.load_credentials(
            SAMPLE_CONFIG, environ={}, run_command=runner, which=lambda name: "/usr/bin/security"
        )
        self.assertEqual(loaded["MAESTRO_TEST_EMAIL"], "a@example.com")
        self.assertEqual(loaded["MAESTRO_TEST_PASSWORD"], "p")

    def test_旧account名でもstatusがavailableになる(self):
        status = credentials.credentials_status(
            SAMPLE_CONFIG,
            environ={},
            run_command=keychain({"test-email": "a", "test-password": "p"}),
            which=lambda name: "/usr/bin/security",
            system="Darwin",
        )
        self.assertEqual(status, "available")

    def test_env名のままでは見つからないことを確認する(self):
        status = credentials.credentials_status(
            SAMPLE_CONFIG,
            environ={},
            run_command=keychain({"MAESTRO_TEST_EMAIL": "a", "MAESTRO_TEST_PASSWORD": "p"}),
            which=lambda name: "/usr/bin/security",
            system="Darwin",
        )
        self.assertEqual(status, "missing")

    def test_setも差し替え後のaccount名で登録する(self):
        runner = FakeRunner(lambda command: (0, "", ""))
        credentials.store_keychain_values(
            "com.example.app.maestro-evidence",
            SAMPLE_CONFIG["credentials"]["env_names"],
            accounts=SAMPLE_CONFIG["credentials"]["keychain_accounts"],
            run_command=runner,
            write=lambda message: None,
        )
        accounts = [command[command.index("-a") + 1] for command in runner.commands]
        self.assertEqual(accounts, ["test-email", "test-password"])


class ResolveTest(unittest.TestCase):
    def test_揃えばavailableで値を返す(self):
        resolved, status = credentials.resolve_credentials(
            CONFIG,
            environ={"MAESTRO_TEST_EMAIL": "a@example.com", "MAESTRO_TEST_PASSWORD": "p"},
            run_command=keychain({}),
            which=lambda name: "/usr/bin/security",
        )
        self.assertEqual(status, "available")
        self.assertEqual(len(resolved), 2)

    def test_片方だけなら例外にせずunavailableを返す(self):
        resolved, status = credentials.resolve_credentials(
            CONFIG,
            environ={"MAESTRO_TEST_EMAIL": "a@example.com"},
            run_command=keychain({}),
            which=lambda name: "/usr/bin/security",
        )
        self.assertEqual(status, "unavailable")
        self.assertEqual(resolved, {})

    def test_ひとつも無ければunavailableを返す(self):
        _, status = credentials.resolve_credentials(
            CONFIG, environ={}, run_command=keychain({}), which=lambda name: "/usr/bin/security"
        )
        self.assertEqual(status, "unavailable")

    def test_不要な設定ならnot_requiredを返す(self):
        resolved, status = credentials.resolve_credentials(
            {"credentials": {"source": "none", "env_names": []}}, environ={}
        )
        self.assertEqual((resolved, status), ({}, "not_required"))

    def test_load_credentialsは揃わなければ従来どおり失敗させる(self):
        with self.assertRaises(MeError):
            credentials.load_credentials(
                CONFIG,
                environ={"MAESTRO_TEST_EMAIL": "a@example.com"},
                run_command=keychain({}),
                which=lambda name: "/usr/bin/security",
            )


class StatusTest(unittest.TestCase):
    def _status(self, environ, values, system="Darwin"):
        return credentials.credentials_status(
            CONFIG,
            environ=environ,
            run_command=keychain(values),
            which=lambda name: "/usr/bin/security",
            system=system,
        )

    def test_全部揃えばavailable(self):
        self.assertEqual(
            self._status({}, {"MAESTRO_TEST_EMAIL": "a", "MAESTRO_TEST_PASSWORD": "p"}),
            "available",
        )

    def test_片方だけならincomplete(self):
        self.assertEqual(self._status({}, {"MAESTRO_TEST_EMAIL": "a"}), "incomplete")

    def test_何も無ければmissing(self):
        self.assertEqual(self._status({}, {}), "missing")

    def test_macOS以外ではunsupported(self):
        self.assertEqual(self._status({}, {}, system="Linux"), "unsupported")

    def test_必要なければnot_required(self):
        self.assertEqual(
            credentials.credentials_status({"credentials": {"source": "none", "env_names": []}}),
            "not_required",
        )

    def test_statusは値そのものを返さない(self):
        status = self._status({}, {"MAESTRO_TEST_EMAIL": "a", "MAESTRO_TEST_PASSWORD": "p"})
        self.assertNotIn("a", [status])
        self.assertIn(status, ("available", "incomplete", "missing", "unsupported"))


if __name__ == "__main__":
    unittest.main()
