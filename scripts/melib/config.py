"""`.maestro-evidence.json` の読み込み・検証・雛形生成。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import MeError

CONFIG_FILENAME = ".maestro-evidence.json"

PLATFORMS = ("ios", "android")
LAUNCH_MODES = ("command", "installed")
LOG_SOURCES = ("launch", "os")
CREDENTIAL_SOURCES = ("keychain", "env", "none")
DEVICE_PREFERENCES = ("booted", "name", "first")

DEFAULT_ERROR_PATTERNS = [
    "Exception",
    "Error",
    "FATAL",
    "Unhandled",
    "E/flutter",
    "Assertion failed",
]
# OSログの error 判定は、素朴な文字列一致だと `hasError: 0` やカテゴリ名 `XPCErrors` まで
# 拾ってしまう。compact style は固定桁のタイプ列（E=Error / F=Fault）を持つので、既定は
# ログレベルで判定する。
DEFAULT_OS_ERROR_PATTERNS = [
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+\s+(?:E|Er|F|Fa)\s"
]
DEFAULT_READY_TIMEOUT_SECONDS = 300
# Android の screenrecord は既定だと端末解像度・高ビットレートで録るためエミュレータの
# CPU を食い潰す。実測で 250〜340% まで上がったので既定を軽くする
DEFAULT_RECORDING_BIT_RATE = 4_000_000
DEFAULT_RECORDING_SIZE = "720x1600"
RECORDING_SIZE_PATTERN = re.compile(r"^\d{2,5}x\d{2,5}$")
TEXT_INPUT_MODES = ("adbkeyboard", "maestro")
DEFAULT_TEXT_INPUT = "adbkeyboard"
DEFAULT_BASE_REF = "origin/main"
ENV_NAME_PREFIX = "MAESTRO_"
ENV_NAME_PATTERN = re.compile(r"^MAESTRO_[A-Z0-9_]+$")

TEMPLATE = {
    "app_id": "com.example.app.dev",
    "platform": "ios",
    "device": {"prefer": "booted", "name": "iPhone 17 Pro"},
    "launch": {
        "mode": "command",
        "command": "fvm flutter run -d {device} --flavor dev --dart-define=FLAVOR=dev",
        "ready_pattern": "A Dart VM Service|Flutter DevTools",
        "ready_timeout_seconds": DEFAULT_READY_TIMEOUT_SECONDS,
    },
    "log": {
        "sources": ["launch", "os"],
        "process": "Runner",
        "error_patterns": list(DEFAULT_ERROR_PATTERNS),
        "os_error_patterns": list(DEFAULT_OS_ERROR_PATTERNS),
        "ignore_patterns": [],
    },
    "credentials": {
        "source": "keychain",
        "keychain_service": "jp.example.maestro-evidence",
        "env_names": ["MAESTRO_TEST_EMAIL", "MAESTRO_TEST_PASSWORD"],
    },
    "recording": {"bit_rate": DEFAULT_RECORDING_BIT_RATE, "size": DEFAULT_RECORDING_SIZE},
    "android": {"text_input": DEFAULT_TEXT_INPUT, "adbkeyboard_apk": None},
    "login_flow": None,
    "base_ref": "origin/develop",
}


def config_path(repo: Path) -> Path:
    return repo / CONFIG_FILENAME


def resolve_config_path(repo: Path, override: Optional[str] = None) -> Path:
    """使う設定ファイルを決める。

    `--config` の明示指定を、リポジトリ直下の既定ファイルより優先する。同じ
    リポジトリで iOS と Android を切り替える場合に、platform ごとの設定を別
    ファイルへ分けて渡すため。相対パスはカレント基準で解決する。
    """

    if override:
        return Path(override).expanduser().resolve()
    return config_path(repo)


def load_config(repo: Path, *, path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or config_path(repo)
    if not path.is_file():
        raise MeError(
            "{0} がありません。`me.py config init` で雛形を作ってください".format(path)
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MeError("{0} を読み込めません: {1}".format(path, error)) from error
    if not isinstance(raw, dict):
        raise MeError("{0} のルートはobjectである必要があります".format(path))
    normalized, errors = validate_config(raw)
    if errors:
        raise MeError("設定が不正です:\n- " + "\n- ".join(errors))
    return normalized


def _string(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value.strip() else None


def _string_list(value: Any, label: str, errors: List[str]) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        errors.append("{0} は文字列のarrayである必要があります".format(label))
        return []
    return list(value)


def _check_patterns(patterns: List[str], label: str, errors: List[str]) -> None:
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as error:
            errors.append("{0} の正規表現が不正です: {1} ({2})".format(label, pattern, error))


def _keychain_accounts(
    value: Any, env_names: List[str], errors: List[str]
) -> Dict[str, str]:
    """env 名 → Keychain の account 名。

    既存のKeychain登録が env 名と違う account 名で入っている場合の逃げ道。
    指定が無い env は env 名をそのまま account 名として使う。
    """

    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append("credentials.keychain_accounts はobjectである必要があります")
        return {}
    accounts: Dict[str, str] = {}
    for key, account in value.items():
        if key not in env_names:
            errors.append(
                "credentials.keychain_accounts のキーは env_names のいずれかにしてください: {0}".format(key)
            )
            continue
        name = _string(account)
        if name is None:
            errors.append("credentials.keychain_accounts.{0} は空でない文字列にしてください".format(key))
            continue
        accounts[key] = name
    return accounts


def validate_config(raw: Dict[str, Any]) -> "tuple":
    """設定を既定値で補完して返す。第2要素はエラー文言の一覧。"""

    errors: List[str] = []
    config: Dict[str, Any] = {}

    app_id = _string(raw.get("app_id"))
    if app_id is None:
        errors.append("app_id は必須です")
    config["app_id"] = app_id or ""

    platform = raw.get("platform")
    if platform not in PLATFORMS:
        errors.append("platform は {0} のいずれかです".format(" / ".join(PLATFORMS)))
    config["platform"] = platform if platform in PLATFORMS else "ios"

    device = raw.get("device") or {}
    if not isinstance(device, dict):
        errors.append("device はobjectである必要があります")
        device = {}
    prefer = device.get("prefer", "booted")
    if prefer not in DEVICE_PREFERENCES:
        errors.append("device.prefer は {0} のいずれかです".format(" / ".join(DEVICE_PREFERENCES)))
        prefer = "booted"
    if prefer == "name" and _string(device.get("name")) is None:
        errors.append("device.prefer=name には device.name が必要です")
    config["device"] = {"prefer": prefer, "name": _string(device.get("name"))}

    launch = raw.get("launch") or {}
    if not isinstance(launch, dict):
        errors.append("launch はobjectである必要があります")
        launch = {}
    mode = launch.get("mode")
    if mode not in LAUNCH_MODES:
        errors.append("launch.mode は {0} のいずれかです".format(" / ".join(LAUNCH_MODES)))
        mode = "installed"
    command = _string(launch.get("command"))
    if mode == "command" and command is None:
        errors.append("launch.mode=command には launch.command が必要です")
    ready_pattern = _string(launch.get("ready_pattern"))
    if ready_pattern is not None:
        _check_patterns([ready_pattern], "launch.ready_pattern", errors)
    timeout = launch.get("ready_timeout_seconds", DEFAULT_READY_TIMEOUT_SECONDS)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        errors.append("launch.ready_timeout_seconds は正の整数である必要があります")
        timeout = DEFAULT_READY_TIMEOUT_SECONDS
    config["launch"] = {
        "mode": mode,
        "command": command,
        "ready_pattern": ready_pattern,
        "ready_timeout_seconds": timeout,
    }

    log = raw.get("log") or {}
    if not isinstance(log, dict):
        errors.append("log はobjectである必要があります")
        log = {}
    if "sources" in log and log.get("sources") is not None:
        sources = _string_list(log.get("sources"), "log.sources", errors)
        unknown = [item for item in sources if item not in LOG_SOURCES]
        if unknown:
            errors.append("log.sources に未知の値があります: " + ", ".join(unknown))
        if not sources:
            errors.append("log.sources は1つ以上必要です")
        sources = [item for item in LOG_SOURCES if item in sources]
    else:
        sources = ["launch"] if mode == "command" else ["os"]
    if "launch" in sources and mode != "command":
        errors.append("log.sources の launch は launch.mode=command のときだけ使えます")
    process = _string(log.get("process"))
    if "os" in sources and process is None:
        errors.append("log.sources に os を含める場合は log.process が必要です")
    error_patterns = (
        _string_list(log.get("error_patterns"), "log.error_patterns", errors)
        if log.get("error_patterns") is not None
        else list(DEFAULT_ERROR_PATTERNS)
    )
    _check_patterns(error_patterns, "log.error_patterns", errors)
    os_error_patterns = (
        _string_list(log.get("os_error_patterns"), "log.os_error_patterns", errors)
        if log.get("os_error_patterns") is not None
        else list(DEFAULT_OS_ERROR_PATTERNS)
    )
    _check_patterns(os_error_patterns, "log.os_error_patterns", errors)
    ignore_patterns = _string_list(log.get("ignore_patterns"), "log.ignore_patterns", errors)
    _check_patterns(ignore_patterns, "log.ignore_patterns", errors)
    config["log"] = {
        "sources": sources,
        "process": process,
        # error_patterns は launch ソース向け、os_error_patterns は os ソース向け。
        # ignore_patterns は両方へ適用する
        "error_patterns": error_patterns,
        "os_error_patterns": os_error_patterns,
        "ignore_patterns": ignore_patterns,
    }

    credentials = raw.get("credentials") or {}
    if not isinstance(credentials, dict):
        errors.append("credentials はobjectである必要があります")
        credentials = {}
    source = credentials.get("source", "none")
    if source not in CREDENTIAL_SOURCES:
        errors.append("credentials.source は {0} のいずれかです".format(" / ".join(CREDENTIAL_SOURCES)))
        source = "none"
    env_names = _string_list(credentials.get("env_names"), "credentials.env_names", errors)
    for name in env_names:
        if not ENV_NAME_PATTERN.match(name):
            errors.append(
                "credentials.env_names は {0} 始まりの大文字名にしてください: {1}".format(
                    ENV_NAME_PREFIX, name
                )
            )
    keychain_service = _string(credentials.get("keychain_service"))
    if source == "keychain" and keychain_service is None:
        errors.append("credentials.source=keychain には keychain_service が必要です")
    if source != "none" and not env_names:
        errors.append("credentials.source が none 以外なら env_names が必要です")
    accounts = _keychain_accounts(credentials.get("keychain_accounts"), env_names, errors)
    config["credentials"] = {
        "source": source,
        "keychain_service": keychain_service,
        "env_names": env_names,
        "keychain_accounts": accounts,
    }

    recording = raw.get("recording")
    if recording is None:
        recording = {}
    if not isinstance(recording, dict):
        errors.append("recording はobjectである必要があります")
        recording = {}
    bit_rate = recording.get("bit_rate", DEFAULT_RECORDING_BIT_RATE)
    if bit_rate is not None and (
        not isinstance(bit_rate, int) or isinstance(bit_rate, bool) or bit_rate <= 0
    ):
        errors.append("recording.bit_rate は正の整数か null である必要があります")
        bit_rate = DEFAULT_RECORDING_BIT_RATE
    size = recording.get("size", DEFAULT_RECORDING_SIZE)
    if size is not None and (
        not isinstance(size, str) or not RECORDING_SIZE_PATTERN.match(size)
    ):
        errors.append("recording.size は 720x1600 の形式か null である必要があります")
        size = DEFAULT_RECORDING_SIZE
    # どちらも Android の screenrecord 用。iOS の recordVideo に相当する引数は無い
    config["recording"] = {"bit_rate": bit_rate, "size": size}

    android = raw.get("android")
    if android is None:
        android = {}
    if not isinstance(android, dict):
        errors.append("android はobjectである必要があります")
        android = {}
    text_input = android.get("text_input", DEFAULT_TEXT_INPUT)
    if text_input not in TEXT_INPUT_MODES:
        errors.append("android.text_input は {0} のいずれかです".format(" / ".join(TEXT_INPUT_MODES)))
        text_input = DEFAULT_TEXT_INPUT
    apk = _string(android.get("adbkeyboard_apk"))
    if android.get("adbkeyboard_apk") is not None and apk is None:
        errors.append("android.adbkeyboard_apk は空でない文字列か null にしてください")
    # Flutter の TextField は adb input text / Maestro inputText で文字が落ちるため、
    # Android の既定は IME 経由（ADBKeyBoard）にする
    config["android"] = {"text_input": text_input, "adbkeyboard_apk": apk}

    login_flow = raw.get("login_flow")
    if login_flow is not None:
        text = _string(login_flow)
        if text is None:
            errors.append("login_flow は null かリポジトリ内の相対pathです")
        else:
            candidate = Path(text)
            if candidate.is_absolute() or ".." in candidate.parts:
                errors.append("login_flow はリポジトリ内の相対pathである必要があります")
    config["login_flow"] = _string(login_flow) if login_flow is not None else None

    base_ref = _string(raw.get("base_ref")) or DEFAULT_BASE_REF
    config["base_ref"] = base_ref

    unknown_keys = sorted(set(raw) - set(TEMPLATE))
    if unknown_keys:
        errors.append("未知のキーがあります: " + ", ".join(unknown_keys))
    return config, errors


def render_template(answers: Optional[Dict[str, Any]] = None) -> str:
    value = json.loads(json.dumps(TEMPLATE))
    for key, override in (answers or {}).items():
        if key in value:
            value[key] = override
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
