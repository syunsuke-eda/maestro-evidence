"""iOS Simulator / Android エミュレータの一覧と選択。"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any, Dict, List, Optional

from .errors import MeError
from .proc import RunCommand, run


def list_ios_devices(*, run_command: RunCommand = subprocess.run) -> List[Dict[str, Any]]:
    result = run(
        ["xcrun", "simctl", "list", "devices", "-j"], run_command=run_command, check=True
    )
    try:
        payload = json.loads(result.stdout)
    except ValueError as error:
        raise MeError("simctl の一覧を解釈できません") from error
    devices: List[Dict[str, Any]] = []
    for runtime, entries in sorted((payload.get("devices") or {}).items()):
        for entry in entries or []:
            if not entry.get("isAvailable", True):
                continue
            devices.append(
                {
                    "id": str(entry.get("udid", "")),
                    "name": str(entry.get("name", "")),
                    "state": str(entry.get("state", "")),
                    "runtime": runtime.rsplit(".", 1)[-1],
                    "booted": str(entry.get("state", "")) == "Booted",
                }
            )
    return devices


def list_android_devices(*, run_command: RunCommand = subprocess.run) -> List[Dict[str, Any]]:
    result = run(["adb", "devices", "-l"], run_command=run_command, check=True)
    devices: List[Dict[str, Any]] = []
    for line in (result.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or parts[1] not in ("device", "offline", "unauthorized"):
            continue
        properties = dict(
            item.split(":", 1) for item in parts[2:] if ":" in item
        )
        devices.append(
            {
                "id": parts[0],
                "name": properties.get("model", parts[0]),
                "state": parts[1],
                "runtime": properties.get("device", ""),
                "booted": parts[1] == "device",
            }
        )
    return devices


def list_devices(platform: str, *, run_command: RunCommand = subprocess.run) -> List[Dict[str, Any]]:
    if platform == "ios":
        return list_ios_devices(run_command=run_command)
    if platform == "android":
        return list_android_devices(run_command=run_command)
    raise MeError("未知の platform です: {0}".format(platform))


def select_device(
    devices: List[Dict[str, Any]], preference: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    prefer = preference.get("prefer", "booted")
    name = preference.get("name")
    if prefer == "name" and name:
        matched = [device for device in devices if device.get("name") == name]
        booted = [device for device in matched if device.get("booted")]
        return (booted or matched or [None])[0]
    booted = [device for device in devices if device.get("booted")]
    if prefer == "booted" and booted:
        return booted[0]
    if name:
        matched = [device for device in devices if device.get("name") == name]
        if matched:
            return matched[0]
    return (booted or devices or [None])[0]


def resolve_process_id(
    device_id: str,
    package: str,
    *,
    timeout: float = 60.0,
    run_command: RunCommand = subprocess.run,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> Optional[int]:
    """Android で対象パッケージの pid を待って取る。取れなければ None。

    logcat を pid で絞らないとシステム全体の行が入り、errors.txt がノイズで
    埋まる（実測で1,069件）。pid が取れるまで収集を始めないため、待っても
    取れない場合は呼び出し側が「収集しない」を選べるよう None を返す。
    """

    deadline = monotonic() + timeout
    while True:
        result = run(
            ["adb", "-s", device_id, "shell", "pidof", package], run_command=run_command
        )
        candidates = (result.stdout or "").strip().split()
        if candidates and candidates[0].isdigit():
            return int(candidates[0])
        if monotonic() >= deadline:
            return None
        sleep(1.0)
