#!/usr/bin/env python3
"""maestro-evidence の単一CLI。

差分からのケース洗い出し（AI側の仕事）を除く機械作業をすべて担う。
録画・ログ収集は me.py の終了後も生き残る子プロセスとして起動し、pid を
session.json に持つ。削除もこのCLIの内部で行い、利用側にシェルの rm を
実行させない。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from melib import config as config_module  # noqa: E402
from melib import credentials as credentials_module  # noqa: E402
from melib import devices as devices_module  # noqa: E402
from melib import flow as flow_module  # noqa: E402
from melib import gitinfo, hierarchy, logs, publish, report, secretscan, session as session_module  # noqa: E402
from melib import doctor as doctor_module  # noqa: E402
from melib import textinput  # noqa: E402
from melib import video as video_module  # noqa: E402
from melib.collector import child_pid, collect, collector_argv, wait_for_child_pid  # noqa: E402
from melib.errors import MeError  # noqa: E402
from melib.proc import run, spawn_detached, stop_group, stop_pid, strip_ansi  # noqa: E402

WORK_ROOT = Path.home() / "maestro-evidence-work"
RECORDING_START_TIMEOUT_SECONDS = 15
RECORDING_STOP_TIMEOUT_SECONDS = 40
# collector 配下のプロセスグループへ次の signal を送るまでの猶予
COLLECTOR_STOP_GRACE_SECONDS = 5.0
ERROR_PREVIEW_LINES = 20
ANDROID_REMOTE_VIDEO = "/sdcard/maestro-evidence.mp4"
# アプリ起動から pid が引けるようになるまでの待ち上限
ANDROID_PID_TIMEOUT_SECONDS = 60.0


def session_secrets(config: Dict[str, Any]) -> List[str]:
    """credential の実値を取れるだけ取る。取れなくてもここでは失敗させない。"""

    resolved, _ = credentials_module.resolve_credentials(config)
    return [value for value in resolved.values() if value]


def scrub(value: str, secrets: Sequence[str]) -> str:
    """外部プロセスの出力を保存・表示する前に秘密情報を落とす。

    session stop / publish の全体scanは最後の砦であって、それまでの間に
    steps.json やstdoutへ実値が載るのを防げない。Maestro は inputText の解決後の
    文字列を進捗出力へ書くため、書く直前に必ず通す。
    """

    return secretscan.redact_text(value, secrets)[0]


def scrub_lines(lines: Sequence[str], secrets: Sequence[str]) -> List[str]:
    # 収集時にも ANSI を落としているが、ソースによって落とし切れない形があるため
    # 保存・表示の直前にもう一度通す
    return [strip_ansi(scrub(line, secrets)) for line in lines]


def emit(payload: Dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def note(message: str) -> None:
    print(message, file=sys.stderr)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_commands(names: Sequence[str]) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise MeError("必須コマンドが見つかりません: " + ", ".join(missing))


def resolve_repo(value: Optional[str]) -> Path:
    return Path(value).expanduser().resolve() if value else Path.cwd().resolve()


def resolve_work(value: Optional[str]) -> Path:
    if not value:
        raise MeError("--work を指定してください（`session start` の出力にあります）")
    return Path(value).expanduser().resolve()


# ---------------------------------------------------------------- config


def ask(question: str, default: str) -> str:
    """質問は stderr、回答は stdin。stdout は生成結果だけに使う。"""

    if not sys.stdin.isatty():
        return default
    note("{0} [{1}]: ".format(question, default))
    answer = sys.stdin.readline().strip()
    return answer or default


def build_config_interactively(ask_fn=None) -> str:
    ask_fn = ask_fn or ask
    template = config_module.TEMPLATE
    platform = ask_fn("platform (ios / android)", str(template["platform"]))
    mode = ask_fn("launch.mode (command / installed)", str(template["launch"]["mode"]))
    answers = {
        "app_id": ask_fn("app_id", str(template["app_id"])),
        "platform": platform,
        "device": {
            "prefer": ask_fn("device.prefer (booted / name / first)", "booted"),
            "name": ask_fn("device.name", str(template["device"]["name"])),
        },
        "launch": {
            "mode": mode,
            "command": ask_fn(
                "launch.command（{device} が端末IDに置換される。固定IDを書かない）",
                str(template["launch"]["command"]),
            ),
            "ready_pattern": ask_fn("launch.ready_pattern", str(template["launch"]["ready_pattern"])),
            "ready_timeout_seconds": config_module.DEFAULT_READY_TIMEOUT_SECONDS,
        },
        "log": {
            "sources": [
                value
                for value in ask_fn(
                    "log.sources (カンマ区切り: launch,os)",
                    ",".join(template["log"]["sources"]),
                ).split(",")
                if value.strip()
            ],
            "process": ask_fn(
                "log.process（iOSは実行ファイル名。Flutterなら通常 Runner でバンドルIDではない"
                " / Androidはパッケージ名）",
                str(template["log"]["process"]),
            ),
            "error_patterns": list(config_module.DEFAULT_ERROR_PATTERNS),
            "os_error_patterns": list(config_module.DEFAULT_OS_ERROR_PATTERNS),
            "ignore_patterns": [],
        },
        "credentials": {
            "source": ask_fn("credentials.source (keychain / env / none)", "keychain"),
            "keychain_service": ask_fn(
                "credentials.keychain_service", str(template["credentials"]["keychain_service"])
            ),
            "env_names": [
                value.strip()
                for value in ask_fn(
                    "credentials.env_names (カンマ区切り、MAESTRO_ 始まり)",
                    ",".join(template["credentials"]["env_names"]),
                ).split(",")
                if value.strip()
            ],
        },
        "login_flow": ask_fn("login_flow (無ければ空)", "") or None,
        "base_ref": ask_fn("base_ref", str(template["base_ref"])),
    }
    answers["log"]["sources"] = [value.strip() for value in answers["log"]["sources"]]
    return json.dumps(answers, ensure_ascii=False, indent=2) + "\n"


def config_for(args: argparse.Namespace) -> "tuple":
    """このコマンドが使う設定と、その出所を返す。

    `--work` があれば session.json に取り込み済みの設定を使う。`session start`
    で使った設定が以降のコマンドへ引き継がれるので、`--config` を毎回付けなくて
    済む。`--config` を明示した場合はそちらが優先される。
    """

    work = getattr(args, "work", None)
    override = getattr(args, "config", None)
    if work and not override:
        path = session_module.session_path(Path(work).expanduser().resolve())
        if path.is_file():
            return session_module.load(Path(work).expanduser().resolve())["config"], "session"
    repo = resolve_repo(getattr(args, "repo", None))
    resolved = config_module.resolve_config_path(repo, override)
    return config_module.load_config(repo, path=resolved), str(resolved)


def command_config(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.repo)
    if args.action == "init":
        sys.stdout.write(build_config_interactively())
        note("この内容を {0} へ置く場合はユーザーの同意を得てください。".format(
            config_module.resolve_config_path(repo, getattr(args, "config", None))))
        return 0
    path = config_module.resolve_config_path(repo, getattr(args, "config", None))
    if not path.is_file():
        raise MeError("{0} がありません".format(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    normalized, errors = config_module.validate_config(raw)
    if args.action == "validate":
        emit({"path": str(path), "valid": not errors, "errors": errors})
        return 0 if not errors else 2
    if errors:
        raise MeError("設定が不正です:\n- " + "\n- ".join(errors))
    emit(normalized)
    return 0


# ---------------------------------------------------------------- devices / fingerprint


def command_doctor(args: argparse.Namespace) -> int:
    report = doctor_module.run_checks()
    note(doctor_module.format_summary(report))
    emit(report)
    return 0 if report["status"] == "ok" else 2


def command_devices(args: argparse.Namespace) -> int:
    config, _ = config_for(args)
    found = devices_module.list_devices(config["platform"])
    emit(
        {
            "platform": config["platform"],
            "devices": found,
            "selected": devices_module.select_device(found, config["device"]),
        }
    )
    return 0


def command_fingerprint(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.repo)
    base_ref = args.base_ref or config_for(args)[0]["base_ref"]
    emit(gitinfo.compute_fingerprint(repo, base_ref))
    return 0


# ---------------------------------------------------------------- credentials


def command_credentials(args: argparse.Namespace) -> int:
    config, _ = config_for(args)
    settings = config["credentials"]
    if args.action == "set":
        if settings["source"] != "keychain":
            raise MeError("credentials.source=keychain のときだけ set を使えます")
        require_commands(("security",))
        credentials_module.store_keychain_values(
            settings["keychain_service"],
            settings["env_names"],
            accounts=settings.get("keychain_accounts"),
            write=note,
        )
    status = credentials_module.credentials_status(config)
    emit(
        {
            "status": status,
            "env_names": settings["env_names"],
            "source": settings["source"],
            "keychain_accounts": settings.get("keychain_accounts") or {},
        }
    )
    return 0 if status in (credentials_module.STATUS_AVAILABLE, credentials_module.STATUS_NOT_REQUIRED) else 2


# ---------------------------------------------------------------- session


def launch_argv(config: Dict[str, Any], device_id: str) -> List[str]:
    command = config["launch"]["command"] or ""
    return shlex.split(command.replace("{device}", device_id))


def os_log_argv(config: Dict[str, Any], device_id: str, pid: Optional[int] = None) -> List[str]:
    process = config["log"]["process"] or ""
    if config["platform"] == "ios":
        return [
            "xcrun",
            "simctl",
            "spawn",
            device_id,
            "log",
            "stream",
            "--style",
            "compact",
            # --level debug は十数秒で数万行になるため info で止める
            "--level",
            "info",
            "--predicate",
            # CONTAINS だと maestro-driver-iosUITests-Runner のような兄弟プロセスまで
            # 拾ってドライバのログが混ざる。実行ファイル名の末尾一致で絞る
            # （process == "<名前>" は対象アプリで1行も取れないことがあり使わない）
            'processImagePath ENDSWITH "/{0}"'.format(process),
        ]
    command = ["adb", "-s", device_id, "logcat", "-v", "time"]
    if pid is not None:
        # pid で絞らないと SystemServiceRegistry などシステム全体の行が入る
        command.append("--pid={0}".format(pid))
    return command


def wait_for_ready(log_file: Path, pattern: Optional[str], timeout: int) -> Dict[str, Any]:
    if not pattern:
        return {"status": "skipped", "waited_seconds": 0}
    compiled = re.compile(pattern)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_file.is_file():
            text = log_file.read_text(encoding="utf-8", errors="replace")
            if compiled.search(text):
                return {"status": "ready", "waited_seconds": round(timeout - (deadline - time.monotonic()), 1)}
        time.sleep(1.0)
    return {"status": "timeout", "waited_seconds": timeout}


def start_collector(work: Path, source: str, command: List[str], repo: Path) -> Dict[str, Any]:
    output = session_module.log_path(work, source)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.touch()
    pid = spawn_detached(
        collector_argv(Path(__file__).resolve(), command, output),
        stdout_path=work / "collector-{0}.err".format(source),
        stderr_path=work / "collector-{0}.err".format(source),
        cwd=repo,
    )
    # 子は独立したプロセスグループのリーダーなので pgid = 子の pid。
    # 停止時にグループごと落とすため記録しておく
    pgid = wait_for_child_pid(output)
    return {"pid": pid, "pgid": pgid, "log": output.name, "command": command}


def adbkeyboard_apk_path(config: Dict[str, Any]) -> Path:
    configured = (config.get("android") or {}).get("adbkeyboard_apk")
    return Path(configured).expanduser() if configured else textinput.DEFAULT_APK_PATH


def setup_text_input(config: Dict[str, Any], device_id: str) -> Dict[str, Any]:
    """Android で ADBKeyBoard を使うなら導入と IME 切り替えを行う。"""

    if config["platform"] != "android":
        return {"status": "not_applicable"}
    mode = (config.get("android") or {}).get("text_input", "adbkeyboard")
    if mode != "adbkeyboard":
        return {"status": "maestro"}
    result = textinput.activate(device_id, adbkeyboard_apk_path(config))
    note("ADBKeyBoard を IME に設定しました。元の IME は session stop で戻します。")
    return result


def restore_text_input(session: Dict[str, Any]) -> Dict[str, Any]:
    state = session.get("text_input") or {}
    if state.get("status") != "active":
        return state
    outcome = textinput.restore(
        session["device"]["id"], str(state.get("previous_ime") or "")
    )
    restored = dict(state)
    restored["restore"] = outcome
    return restored


def command_session_start(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.repo)
    # session start だけは --work が「これから作る場所」なので session.json を見ない
    config_source = config_module.resolve_config_path(repo, getattr(args, "config", None))
    config = config_module.load_config(repo, path=config_source)
    require_commands(("maestro", "ffmpeg", "ffprobe"))
    require_commands(("xcrun",) if config["platform"] == "ios" else ("adb",))
    fingerprint = gitinfo.compute_fingerprint(repo, config["base_ref"])
    found = devices_module.list_devices(config["platform"])
    device = next((item for item in found if item["id"] == args.device), None)
    if device is None:
        raise MeError("device が見つかりません: {0}".format(args.device))

    work = (
        Path(args.work).expanduser().resolve()
        if args.work
        else WORK_ROOT
        / repo.name
        / gitinfo.sanitize_branch(fingerprint["branch"])
        / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    work.mkdir(parents=True, exist_ok=True)
    session = session_module.new_session(
        work=work,
        repo=repo,
        config=config,
        fingerprint=fingerprint,
        device=device,
        started_at=now(),
        config_path=config_source,
    )

    launch_result: Dict[str, Any] = {"status": "skipped"}
    if not args.skip_launch:
        if config["launch"]["mode"] == "command":
            session["collectors"]["launch"] = start_collector(
                work, "launch", launch_argv(config, device["id"]), repo
            )
            launch_result = wait_for_ready(
                session_module.log_path(work, "launch"),
                config["launch"]["ready_pattern"],
                config["launch"]["ready_timeout_seconds"],
            )
        else:
            if config["platform"] == "ios":
                command = ["xcrun", "simctl", "launch", device["id"], config["app_id"]]
            else:
                command = [
                    "adb",
                    "-s",
                    device["id"],
                    "shell",
                    "monkey",
                    "-p",
                    config["app_id"],
                    "-c",
                    "android.intent.category.LAUNCHER",
                    "1",
                ]
            result = run(command, cwd=repo, check=True)
            launch_result = {"status": "ready", "stdout": strip_ansi(result.stdout or "").strip()}
    session["launch"] = launch_result

    if "os" in config["log"]["sources"]:
        os_pid = None
        if config["platform"] == "android":
            target = config["log"]["process"] or config["app_id"]
            os_pid = devices_module.resolve_process_id(
                device["id"], target, timeout=ANDROID_PID_TIMEOUT_SECONDS
            )
            if os_pid is None:
                note(
                    "{0} の pid を取得できなかったため os ログ収集を開始しません。"
                    "全量を貯めるとシステム行でノイズになるためです。".format(target)
                )
        if config["platform"] != "android" or os_pid is not None:
            session["collectors"]["os"] = start_collector(
                work, "os", os_log_argv(config, device["id"], pid=os_pid), repo
            )
            session["os_log"] = {"status": "started", "pid": os_pid}
        else:
            session["os_log"] = {"status": "skipped", "reason": "pid unresolved"}
            # 収集していないソースを case のオフセット対象から外す
            config["log"]["sources"] = [
                source for source in config["log"]["sources"] if source != "os"
            ]

    session["text_input"] = setup_text_input(config, device["id"])

    login = {"status": "skipped"}
    if config["login_flow"] and not args.skip_login:
        login_flow = flow_module.safe_repo_path(repo, config["login_flow"], "login_flow")
        credentials, credentials_state = credentials_module.resolve_credentials(config)
        if credentials_state == credentials_module.STATUS_UNAVAILABLE:
            note("検証アカウントが揃わないため、環境変数を渡さずに login_flow を実行します。")
        result = run(
            flow_module.maestro_test_command(device["id"], login_flow),
            cwd=repo,
            env=flow_module.step_environment(credentials),
        )
        login = {
            "status": "pass" if result.returncode == 0 else "fail",
            "exit_code": int(result.returncode),
            "credentials": credentials_state,
            "output_tail": scrub_lines(
                strip_ansi((result.stdout or "") + (result.stderr or "")).splitlines()[-30:],
                list(credentials.values()),
            ),
        }
    session["login_flow"] = login

    session_module.save(work, session)
    emit(
        {
            "work": str(work),
            "config": str(config_source),
            "device": device,
            "launch": launch_result,
            "login_flow": login,
            "collectors": {name: value["pid"] for name, value in session["collectors"].items()},
        }
    )
    return 0


def stop_collectors(work: Path, session: Dict[str, Any]) -> List[Dict[str, Any]]:
    """収集プロセスと、その配下のプロセスグループ全体を止める。

    `fvm flutter run` のような wrapper は自分が終わっても dartvm や
    `simctl spawn ... log stream` を残す。pid ではなくグループへ signal を送り、
    最後に同グループの残存が無いことを確認する。
    """

    results: List[Dict[str, Any]] = []
    for name, value in (session.get("collectors") or {}).items():
        output = session_module.log_path(work, name)
        pgid = value.get("pgid") or child_pid(output)
        collector_pid = int(value.get("pid", 0))
        # 子のグループを先に止める。子が終われば collector の読み取りループが自然に
        # 終わるため、末尾のログを取りこぼさずに済む。collector を先に殺すと
        # SIGINT を無視する子の分だけ待たされ、collector が SIGKILL される
        group = (
            stop_group(int(pgid), grace=COLLECTOR_STOP_GRACE_SECONDS)
            if pgid
            else {"pgid": None, "status": "unknown", "signals": [], "remaining": False}
        )
        collector_status = stop_pid(collector_pid)
        results.append(
            {
                "source": name,
                "collector_pid": collector_pid,
                "pgid": pgid,
                "collector": collector_status,
                "group": group["status"],
                "signals": group["signals"],
                "group_remaining": bool(group["remaining"]),
            }
        )
    return results


def command_session_stop(args: argparse.Namespace) -> int:
    work = resolve_work(args.work)
    session = session_module.load(work)
    repo = Path(session["repo"])
    for case in session_module.open_cases(session):
        note("進行中の case を fail として閉じます: {0}".format(case["id"]))
        close_case(work, session, case, "fail", "session stop により強制終了")
    stopped = stop_collectors(work, session)
    session["collector_stop"] = stopped
    session["text_input"] = restore_text_input(session)
    session["collectors"] = {}
    scan = run_secret_scan(work, session, repo)
    session["secret_scan"] = scan
    session["status"] = "stopped"
    session["finished_at"] = now()
    session_module.save(work, session)
    survivors = [item for item in stopped if item["group_remaining"]]
    if survivors:
        note(
            "停止できなかったプロセスグループがあります: "
            + ", ".join(str(item["pgid"]) for item in survivors)
        )
    emit(
        {
            "work": str(work),
            "stopped": stopped,
            "all_groups_terminated": not survivors,
            "text_input": session["text_input"],
            "secret_scan": scan,
        }
    )
    return 0 if not survivors else 2


# ---------------------------------------------------------------- cases


def recorder_argv(config: Dict[str, Any], device_id: str, destination: Path) -> List[str]:
    if config["platform"] == "ios":
        return [
            "xcrun",
            "simctl",
            "io",
            device_id,
            "recordVideo",
            "--codec=h264",
            "--force",
            str(destination),
        ]
    recording = config.get("recording") or {}
    command = ["adb", "-s", device_id, "shell", "screenrecord"]
    # エミュレータでは既定解像度・高ビットレートの録画で CPU が 250〜340% まで上がる
    if recording.get("bit_rate"):
        command.extend(["--bit-rate", str(recording["bit_rate"])])
    if recording.get("size"):
        command.extend(["--size", str(recording["size"])])
    command.append(ANDROID_REMOTE_VIDEO)
    return command


def wait_for_recording(record_log: Path, timeout: int) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if record_log.is_file():
            text = record_log.read_text(encoding="utf-8", errors="replace")
            if "Recording started" in text:
                return "started"
        time.sleep(0.25)
    return "unconfirmed"


def command_case_start(args: argparse.Namespace) -> int:
    work = resolve_work(args.work)
    session = session_module.load(work)
    config = session["config"]
    session_module.validate_case_id(args.id)
    if session_module.find_case(session, args.id) is not None:
        raise MeError("同じ id の case が既にあります: {0}".format(args.id))
    if session_module.open_cases(session):
        raise MeError("進行中の case があります。先に case end してください")

    case_directory = session_module.case_dir(work, args.id)
    (case_directory / "screenshots").mkdir(parents=True, exist_ok=True)
    offsets = {
        source: session_module.log_path(work, source).stat().st_size
        if session_module.log_path(work, source).is_file()
        else 0
        for source in config["log"]["sources"]
    }
    source_video = case_directory / "video-source.mp4"
    record_log = case_directory / "record.log"
    pid = spawn_detached(
        recorder_argv(config, session["device"]["id"], source_video),
        stdout_path=record_log,
        stderr_path=record_log,
        cwd=Path(session["repo"]),
    )
    recording = (
        wait_for_recording(record_log, RECORDING_START_TIMEOUT_SECONDS)
        if config["platform"] == "ios"
        else "started"
    )
    case = {
        "id": args.id,
        "title": args.title,
        "status": "running",
        "started_at": now(),
        "ended_at": None,
        "notes": None,
        "offsets": offsets,
        "end_offsets": {},
        "recorder_pid": pid,
        "recording": recording,
        "screenshots": [],
        "steps": [],
        "video": {"status": "recording"},
        "errors": [],
        "errors_count": 0,
        "review": {},
    }
    session["cases"].append(case)
    session_module.save(work, session)
    session_module.save_steps(work, args.id, [])
    emit({"case": args.id, "recording": recording, "offsets": offsets, "recorder_pid": pid})
    return 0


def error_patterns_by_source(config: Dict[str, Any]) -> Dict[str, List[str]]:
    """launch は文言一致、os はログレベル。判定基準が違うので分けて持つ。"""

    return {
        "launch": list(config["log"]["error_patterns"]),
        "os": list(config["log"].get("os_error_patterns") or []),
    }


def build_case_logs(work: Path, session: Dict[str, Any], case: Dict[str, Any]) -> List[str]:
    slices: Dict[str, str] = {}
    for source, start in (case.get("offsets") or {}).items():
        path = session_module.log_path(work, source)
        end = case.get("end_offsets", {}).get(source)
        slices[source] = logs.slice_bytes(path, int(start), end)
    return logs.merge(slices)


def finalize_video(work: Path, session: Dict[str, Any], case: Dict[str, Any]) -> Dict[str, Any]:
    config = session["config"]
    directory = session_module.case_dir(work, case["id"])
    source_video = directory / "video-source.mp4"
    pid = int(case.get("recorder_pid") or 0)
    # 録画プロセスも spawn_detached で独立グループのリーダーになっているため
    # pgid = pid。moov が書かれるまで待ってから ffprobe へ渡す
    stop_status = (
        stop_group(pid, grace=RECORDING_STOP_TIMEOUT_SECONDS)["status"] if pid else "not_running"
    )
    if config["platform"] == "android":
        run(
            ["adb", "-s", session["device"]["id"], "pull", ANDROID_REMOTE_VIDEO, str(source_video)],
            cwd=Path(session["repo"]),
        )
    if not source_video.is_file() or source_video.stat().st_size == 0:
        return {"status": "error", "reason": "録画ファイルがありません", "stop": stop_status}
    try:
        result = video_module.prepare_review_video(
            source_video,
            directory / "video.mp4",
            directory / "contact-sheet.png",
            directory / "final-frame.png",
        )
    except MeError as error:
        return {"status": "error", "reason": str(error), "stop": stop_status}
    result.update(
        {"file": "video.mp4", "stop": stop_status, "source_file": source_video.name}
    )
    return result


def close_case(
    work: Path,
    session: Dict[str, Any],
    case: Dict[str, Any],
    status: str,
    notes: Optional[str],
) -> Dict[str, Any]:
    config = session["config"]
    case["end_offsets"] = {
        source: session_module.log_path(work, source).stat().st_size
        if session_module.log_path(work, source).is_file()
        else 0
        for source in (case.get("offsets") or {})
    }
    case["video"] = finalize_video(work, session, case)
    secrets = session_secrets(config)
    merged = scrub_lines(build_case_logs(work, session, case), secrets)
    directory = session_module.case_dir(work, case["id"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "log.txt").write_text("\n".join(merged) + "\n", encoding="utf-8")
    errors = logs.extract_errors_by_source(
        merged, error_patterns_by_source(config), config["log"]["ignore_patterns"]
    )
    (directory / "errors.txt").write_text("\n".join(errors) + "\n", encoding="utf-8")
    case["steps"] = session_module.load_steps(work, case["id"])
    case["errors"] = errors
    case["errors_count"] = len(errors)
    case["status"] = status
    case["notes"] = notes
    case["ended_at"] = now()
    case.pop("recorder_pid", None)
    return case


def command_case_end(args: argparse.Namespace) -> int:
    work = resolve_work(args.work)
    session = session_module.load(work)
    case = session_module.require_case(session, args.id)
    if case.get("status") != "running":
        raise MeError("この case は既に終了しています: {0}".format(args.id))
    close_case(work, session, case, args.status, args.notes)
    session_module.save(work, session)
    emit(
        {
            "case": args.id,
            "status": case["status"],
            "video": {
                "status": case["video"].get("status"),
                "reason": case["video"].get("reason"),
                "size_bytes": (case["video"].get("review") or {}).get("size_bytes"),
            },
            "steps_count": len(case["steps"]),
            "errors_count": case["errors_count"],
            "errors_preview": case["errors"][:ERROR_PREVIEW_LINES],
        }
    )
    return 0


# ---------------------------------------------------------------- interaction


def command_hierarchy(args: argparse.Namespace) -> int:
    repo = resolve_repo(args.repo)
    config, _ = config_for(args)
    result = run(
        flow_module.maestro_hierarchy_command(args.device, compact=args.compact),
        cwd=repo,
        check=True,
    )
    secrets = session_secrets(config)
    raw = scrub(strip_ansi(result.stdout or ""), secrets)
    if args.raw:
        sys.stdout.write(raw if raw.endswith("\n") else raw + "\n")
        return 0
    exclusions = hierarchy.exclusions_for(config["platform"])
    lines = hierarchy.compress(raw, exclude_resource_id_prefixes=exclusions)
    emit(
        {
            "app_id": config["app_id"],
            "element_count": len(lines),
            "excluded_count": len(hierarchy.compress(raw)) - len(lines),
            "elements": lines,
        }
    )
    return 0


def read_commands(args: argparse.Namespace, label: str = "--commands-file") -> str:
    """本文をファイルか標準入力から読む。引数では受けない。

    日本語・引用符・改行が argv を通ると壊れるため。
    """

    if args.commands_file:
        path = Path(args.commands_file).expanduser()
        if not path.is_file():
            raise MeError("{0} のファイルがありません: {1}".format(label, path))
        return path.read_text(encoding="utf-8")
    if sys.stdin.isatty():
        raise MeError("{0} か標準入力で本文を渡してください".format(label))
    return sys.stdin.read()


def command_step(args: argparse.Namespace) -> int:
    work = resolve_work(args.work)
    session = session_module.load(work)
    config = session["config"]
    case = session_module.require_case(session, args.case)
    commands = read_commands(args)
    steps = session_module.load_steps(work, args.case)
    flow_path = session_module.case_dir(work, args.case) / "flows" / "step-{0:03d}.yaml".format(
        len(steps) + 1
    )
    credentials, credentials_state = credentials_module.resolve_credentials(config)
    if credentials_state == credentials_module.STATUS_UNAVAILABLE:
        note("検証アカウントが揃わないため、環境変数を渡さずに step を実行します。")
    result = flow_module.run_step(
        device=args.device,
        app_id=config["app_id"],
        commands=commands,
        flow_path=flow_path,
        credentials=credentials,
        label=args.label,
    )
    secrets = list(credentials.values())
    result["commands"] = scrub(result["commands"], secrets)
    result["output_tail"] = scrub_lines(result["output_tail"], secrets)
    result["kind"] = "step"
    result["credentials"] = credentials_state
    result["index"] = len(steps) + 1
    result["at"] = now()
    steps.append(result)
    session_module.save_steps(work, args.case, steps)
    case["steps"] = steps
    session_module.save(work, session)
    emit(result)
    return 0


def resolve_text(args: argparse.Namespace, credentials: Dict[str, str]) -> "tuple":
    """入力する本文と、その出所を返す。実値はここから外へ出さない。

    `--env` を使うと本文が環境変数から解決され、ファイルにも argv にも残らない。
    """

    name = getattr(args, "env", None)
    if name:
        if not name.startswith(config_module.ENV_NAME_PREFIX):
            raise MeError(
                "--env は {0} 始まりの環境変数名にしてください: {1}".format(
                    config_module.ENV_NAME_PREFIX, name
                )
            )
        value = credentials.get(name) or os.environ.get(name, "")
        if not value:
            raise MeError("{0} の値を解決できません".format(name))
        return value, "env:{0}".format(name)
    # ファイル末尾の改行はエディタが付けたものなので入力に含めない
    return read_commands(args, "--text-file").rstrip("\n"), "text"


def type_with_maestro(
    *,
    device: str,
    app_id: str,
    text: str,
    source: str,
    flow_path: Path,
    credentials: Dict[str, str],
) -> Dict[str, Any]:
    """Maestro の inputText で入力する。本文は環境変数経由で渡す。

    flow へ本文を直書きすると work dir のファイルに実値が残るため、
    MAESTRO_ 始まりの環境変数へ入れて flow からは ${...} で参照する。
    """

    variable = (
        source.split(":", 1)[1]
        if source.startswith("env:")
        else textinput.MAESTRO_TEXT_VARIABLE
    )
    environment = dict(credentials)
    environment[variable] = text
    return flow_module.run_step(
        device=device,
        app_id=app_id,
        commands="- inputText: ${{{0}}}".format(variable),
        flow_path=flow_path,
        credentials=environment,
    )


def command_type(args: argparse.Namespace) -> int:
    work = resolve_work(args.work)
    session = session_module.load(work)
    config = session["config"]
    session_module.require_case(session, args.case)
    credentials, credentials_state = credentials_module.resolve_credentials(config)
    text, source = resolve_text(args, credentials)
    steps = session_module.load_steps(work, args.case)
    index = len(steps) + 1

    use_adbkeyboard = (
        config["platform"] == "android"
        and (config.get("android") or {}).get("text_input", "adbkeyboard") == "adbkeyboard"
    )
    if use_adbkeyboard:
        result = textinput.send_text(args.device, text)
        result.update({"duration_seconds": None, "output_tail": []})
    else:
        result = type_with_maestro(
            device=args.device,
            app_id=config["app_id"],
            text=text,
            source=source,
            flow_path=session_module.case_dir(work, args.case)
            / "flows"
            / "type-{0:03d}.yaml".format(index),
            credentials=credentials,
        )
        result["method"] = "maestro"

    # 本文は credential の可能性があるので、長さと出所だけ残す
    record = {
        "kind": "type",
        "index": index,
        "at": now(),
        "label": args.label,
        "method": result["method"],
        "text": "[REDACTED]",
        "text_length": len(text),
        "text_source": source,
        "status": result["status"],
        "exit_code": result["exit_code"],
        "duration_seconds": result.get("duration_seconds"),
        "credentials": credentials_state,
        "output_tail": scrub_lines(result.get("output_tail") or [], list(credentials.values()) + [text]),
    }
    steps.append(record)
    session_module.save_steps(work, args.case, steps)
    case = session_module.require_case(session, args.case)
    case["steps"] = steps
    session_module.save(work, session)
    emit(record)
    return 0


def command_screenshot(args: argparse.Namespace) -> int:
    work = resolve_work(args.work)
    session = session_module.load(work)
    config = session["config"]
    session_module.require_case(session, args.case)
    directory = session_module.case_dir(work, args.case) / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    name = args.name if args.name.endswith(".png") else args.name + ".png"
    destination = directory / Path(name).name
    device_id = args.device or session["device"]["id"]
    if config["platform"] == "ios":
        run(["xcrun", "simctl", "io", device_id, "screenshot", str(destination)], check=True)
    else:
        result = subprocess.run(
            ["adb", "-s", device_id, "exec-out", "screencap", "-p"],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            raise MeError("screencap に失敗しました")
        destination.write_bytes(result.stdout)
    relative = str(destination.relative_to(work))
    case = session_module.require_case(session, args.case)
    case.setdefault("screenshots", []).append(relative)
    session_module.save(work, session)
    emit({"case": args.case, "screenshot": relative})
    return 0


def command_review(args: argparse.Namespace) -> int:
    work = resolve_work(args.work)
    session = session_module.load(work)
    case = session_module.require_case(session, args.case)
    case["review"] = {"status": args.status, "notes": args.notes, "at": now()}
    session_module.save(work, session)
    emit({"case": args.case, "review": case["review"]})
    return 0


def command_logs(args: argparse.Namespace) -> int:
    work = resolve_work(args.work)
    session = session_module.load(work)
    config = session["config"]
    case = session_module.require_case(session, args.case)
    stored = session_module.case_dir(work, args.case) / "log.txt"
    if stored.is_file():
        lines = stored.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        lines = build_case_logs(work, session, case)
    if args.errors_only:
        lines = logs.extract_errors_by_source(
            lines, error_patterns_by_source(config), config["log"]["ignore_patterns"]
        )
    lines = scrub_lines(logs.tail(lines, args.tail), session_secrets(config))
    emit({"case": args.case, "line_count": len(lines), "lines": lines})
    return 0


# ---------------------------------------------------------------- output


def run_secret_scan(work: Path, session: Dict[str, Any], repo: Path) -> Dict[str, Any]:
    config = session["config"]
    secrets = session_secrets(config)
    required = len(config.get("credentials", {}).get("env_names") or [])
    violations = secretscan.scan_directory(work, secrets)
    return {
        "at": now(),
        "violations": violations,
        "status": "clean" if not violations else "redacted",
        # credential を1つも取れないまま走ったscanは「実値の照合をしていない」。
        # cleanと同じ顔をさせない
        "credential_values_checked": len(secrets),
        "credential_values_expected": required,
        "credential_check": "complete" if len(secrets) >= required else "unavailable",
    }


def command_secret_scan(args: argparse.Namespace) -> int:
    work = resolve_work(args.work)
    session = session_module.load(work)
    result = run_secret_scan(work, session, Path(session["repo"]))
    session["secret_scan"] = result
    session_module.save(work, session)
    emit(result)
    return 0


def load_plan(work: Path) -> Dict[str, Any]:
    """AI が書いた cases.json を id で引けるようにする。無ければ空。"""

    path = work / "cases.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = value.get("cases") if isinstance(value, dict) else value
    if not isinstance(entries, list):
        return {}
    return {
        str(entry.get("id")): entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("id")
    }


def command_report(args: argparse.Namespace) -> int:
    work = resolve_work(args.work)
    session = session_module.load(work)
    plan = load_plan(work)
    for case in session.get("cases", []):
        if isinstance(case, dict) and str(case.get("id")) in plan:
            case["plan"] = plan[str(case["id"])]
    output = report.write_report(session, work)
    emit({"report": str(output), "cases": len(session.get("cases", []))})
    return 0


def command_publish(args: argparse.Namespace) -> int:
    work = resolve_work(args.work)
    session = session_module.load(work)
    repo = Path(session["repo"])
    session["secret_scan"] = run_secret_scan(work, session, repo)
    session_module.save(work, session)
    result = publish.publish(
        repo=repo,
        work=work,
        session=session,
        pr_number=args.pr,
        approved=args.approved,
        case_ids=args.case or None,
    )
    session["publication"] = result
    session_module.save(work, session)
    emit(result)
    return 0


def command_collect(args: argparse.Namespace) -> int:
    argv = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
    if not argv:
        raise MeError("_collect には `--` の後ろに実行するコマンドが必要です")
    return collect(argv, Path(args.out))


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="me.py", description=__doc__)
    parser.add_argument("--repo", help="対象リポジトリ（既定はカレント）")
    parser.add_argument("--work", help="成果物ディレクトリ（session start が作る）")
    parser.add_argument("--config", help="設定ファイル（既定はリポジトリ直下の .maestro-evidence.json）")
    # サブコマンドの前後どちらに --repo / --work を書いても通るようにする。
    # SUPPRESS にしないと、後段のparserが未指定の既定値で前段の指定を潰す。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=argparse.SUPPRESS, help="対象リポジトリ（既定はカレント）")
    common.add_argument("--work", default=argparse.SUPPRESS, help="成果物ディレクトリ")
    common.add_argument(
        "--config", default=argparse.SUPPRESS, help="設定ファイル（既定はリポジトリ直下）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    config_parser = sub.add_parser("config", parents=[common], help="設定の表示・雛形生成・検証")
    config_parser.add_argument("action", choices=("show", "init", "validate"))
    config_parser.set_defaults(handler=command_config)

    doctor_parser = sub.add_parser("doctor", parents=[common], help="必要なツールが揃っているか調べる")
    doctor_parser.set_defaults(handler=command_doctor)

    devices_parser = sub.add_parser("devices", parents=[common], help="Simulator / エミュレータ一覧")
    devices_parser.set_defaults(handler=command_devices)

    fingerprint_parser = sub.add_parser("fingerprint", parents=[common], help="検証対象の差分を特定する")
    fingerprint_parser.add_argument("--base-ref")
    fingerprint_parser.set_defaults(handler=command_fingerprint)

    credentials_parser = sub.add_parser("credentials", parents=[common], help="検証アカウントの状態確認・登録")
    credentials_parser.add_argument("action", choices=("status", "set"))
    credentials_parser.set_defaults(handler=command_credentials)

    session_parser = sub.add_parser("session", parents=[common], help="セッションの開始・終了")
    session_sub = session_parser.add_subparsers(dest="session_command", required=True)
    start_parser = session_sub.add_parser("start", parents=[common], help="work dir 作成・起動・ログ収集開始")
    start_parser.add_argument("--device", required=True)
    start_parser.add_argument("--skip-launch", action="store_true")
    start_parser.add_argument("--skip-login", action="store_true")
    start_parser.set_defaults(handler=command_session_start)
    stop_parser = session_sub.add_parser("stop", parents=[common], help="ログ収集停止・secret scan・確定")
    stop_parser.set_defaults(handler=command_session_stop)

    case_parser = sub.add_parser("case", parents=[common], help="ケースの録画開始・終了")
    case_sub = case_parser.add_subparsers(dest="case_command", required=True)
    case_start = case_sub.add_parser("start", parents=[common], help="録画開始とログ位置の記録")
    case_start.add_argument("--id", required=True)
    case_start.add_argument("--title", required=True)
    case_start.set_defaults(handler=command_case_start)
    case_end = case_sub.add_parser("end", parents=[common], help="録画停止・動画整形・ログ切り出し")
    case_end.add_argument("--id", required=True)
    case_end.add_argument("--status", required=True, choices=("pass", "fail"))
    case_end.add_argument("--notes")
    case_end.set_defaults(handler=command_case_end)

    hierarchy_parser = sub.add_parser("hierarchy", parents=[common], help="実表示の要素を1行形式で取得")
    hierarchy_parser.add_argument("--device", required=True)
    hierarchy_parser.add_argument("--compact", action="store_true", help="CSV形式で取得する")
    hierarchy_parser.add_argument("--raw", action="store_true", help="圧縮せずそのまま出す")
    hierarchy_parser.set_defaults(handler=command_hierarchy)

    step_parser = sub.add_parser("step", parents=[common], help="Maestro commands を1 step 実行する")
    step_parser.add_argument("--case", required=True)
    step_parser.add_argument("--device", required=True)
    step_parser.add_argument("--label")
    step_parser.add_argument("--commands-file", help="省略時は標準入力から読む")
    step_parser.set_defaults(handler=command_step)

    type_parser = sub.add_parser("type", parents=[common], help="文字を入力する（Androidは IME 経由）")
    type_parser.add_argument("--case", required=True)
    type_parser.add_argument("--device", required=True)
    type_parser.add_argument("--label")
    type_parser.add_argument("--env", help="本文を解決する MAESTRO_ 始まりの環境変数名")
    type_parser.add_argument("--text-file", dest="commands_file", help="省略時は標準入力から読む")
    type_parser.set_defaults(handler=command_type)

    screenshot_parser = sub.add_parser("screenshot", parents=[common], help="スクリーンショットを保存する")
    screenshot_parser.add_argument("--case", required=True)
    screenshot_parser.add_argument("--device", required=True)
    screenshot_parser.add_argument("--name", required=True)
    screenshot_parser.set_defaults(handler=command_screenshot)

    review_parser = sub.add_parser("review", parents=[common], help="目視レビュー結果を記録する")
    review_parser.add_argument("--case", required=True)
    review_parser.add_argument("--status", required=True, choices=("pass", "fail"))
    review_parser.add_argument("--notes", required=True)
    review_parser.set_defaults(handler=command_review)

    logs_parser = sub.add_parser("logs", parents=[common], help="ケース区間のログを読む")
    logs_parser.add_argument("--case", required=True)
    logs_parser.add_argument("--tail", type=int, default=80)
    logs_parser.add_argument("--errors-only", action="store_true")
    logs_parser.set_defaults(handler=command_logs)

    report_parser = sub.add_parser("report", parents=[common], help="index.html を生成する")
    report_parser.set_defaults(handler=command_report)

    publish_parser = sub.add_parser("publish", parents=[common], help="PRへ証跡を投稿する")
    publish_parser.add_argument("--pr", type=int, required=True)
    publish_parser.add_argument("--approved", action="store_true")
    publish_parser.add_argument("--case", action="append")
    publish_parser.set_defaults(handler=command_publish)

    scan_parser = sub.add_parser("secret-scan", parents=[common], help="成果物から秘密情報を消す")
    scan_parser.set_defaults(handler=command_secret_scan)

    collect_parser = sub.add_parser("_collect")
    collect_parser.add_argument("--out", required=True)
    collect_parser.add_argument("argv", nargs=argparse.REMAINDER)
    collect_parser.set_defaults(handler=command_collect)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", None) == "session":
        args.handler = {
            "start": command_session_start,
            "stop": command_session_stop,
        }[args.session_command]
    if getattr(args, "command", None) == "case":
        args.handler = {"start": command_case_start, "end": command_case_end}[args.case_command]
    try:
        return int(args.handler(args))
    except MeError as error:
        note(str(error))
        return 2
    except KeyboardInterrupt:
        note("中断しました")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
