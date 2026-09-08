from __future__ import annotations

import json
import unittest

import support  # noqa: F401
from support import FakeRunner

from melib.devices import (
    list_android_devices,
    list_ios_devices,
    resolve_process_id,
    select_device,
)

SIMCTL = json.dumps(
    {
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                {"udid": "AAA", "name": "iPhone 17 Pro", "state": "Booted", "isAvailable": True},
                {"udid": "BBB", "name": "iPhone 17", "state": "Shutdown", "isAvailable": True},
                {"udid": "CCC", "name": "壊れた端末", "state": "Shutdown", "isAvailable": False},
            ]
        }
    }
)

ADB = """List of devices attached
emulator-5554          device product:sdk model:Pixel_7 device:emu64x
"""


class ListTest(unittest.TestCase):
    def test_利用不可のSimulatorは除外する(self):
        found = list_ios_devices(run_command=FakeRunner(lambda command: (0, SIMCTL, "")))
        self.assertEqual([device["id"] for device in found], ["AAA", "BBB"])
        self.assertTrue(found[0]["booted"])
        self.assertEqual(found[0]["runtime"], "iOS-26-0")

    def test_adbの一覧からmodelを読む(self):
        found = list_android_devices(run_command=FakeRunner(lambda command: (0, ADB, "")))
        self.assertEqual(found[0]["id"], "emulator-5554")
        self.assertEqual(found[0]["name"], "Pixel_7")
        self.assertTrue(found[0]["booted"])


class SelectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.devices = list_ios_devices(run_command=FakeRunner(lambda command: (0, SIMCTL, "")))

    def test_booted優先ならBooted端末を選ぶ(self):
        selected = select_device(self.devices, {"prefer": "booted", "name": "iPhone 17"})
        self.assertEqual(selected["id"], "AAA")

    def test_name優先なら名前一致を選ぶ(self):
        selected = select_device(self.devices, {"prefer": "name", "name": "iPhone 17"})
        self.assertEqual(selected["id"], "BBB")

    def test_候補が無ければNoneを返す(self):
        self.assertIsNone(select_device([], {"prefer": "booted"}))


class ProcessIdTest(unittest.TestCase):
    def _clock(self):
        state = {"value": 0.0}

        def monotonic():
            state["value"] += 0.5
            return state["value"]

        return monotonic

    def test_pidofの出力からpidを取る(self):
        runner = FakeRunner(lambda command: (0, "4321\n", ""))
        self.assertEqual(
            resolve_process_id("emulator-5554", "com.example.app", run_command=runner), 4321
        )

    def test_複数pidなら先頭を使う(self):
        runner = FakeRunner(lambda command: (0, "4321 4322\n", ""))
        self.assertEqual(
            resolve_process_id("emulator-5554", "com.example.app", run_command=runner), 4321
        )

    def test_取れなければ待って諦める(self):
        runner = FakeRunner(lambda command: (1, "", "not found"))
        self.assertIsNone(
            resolve_process_id(
                "emulator-5554",
                "com.example.app",
                timeout=2,
                run_command=runner,
                sleep=lambda seconds: None,
                monotonic=self._clock(),
            )
        )
        self.assertGreater(len(runner.calls), 1)

    def test_pidofへパッケージ名を渡す(self):
        runner = FakeRunner(lambda command: (0, "1\n", ""))
        resolve_process_id("emulator-5554", "com.example.app", run_command=runner)
        self.assertEqual(
            runner.commands[0],
            ["adb", "-s", "emulator-5554", "shell", "pidof", "com.example.app"],
        )


if __name__ == "__main__":
    unittest.main()
