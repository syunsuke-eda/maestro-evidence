# `.maestro-evidence.json`

リポジトリルートに置く。`me.py config init` の出力を雛形にし、ユーザーの同意を得てから配置する。
`me.py config validate` で検証できる（未知のキーはエラー）。

別のファイルを使う場合は `--config <path>` で渡す。指定はリポジトリ直下の既定ファイルより
優先し、相対パスはカレント基準で解決する。

## 項目

| キー | 必須 | 説明 |
|---|---|---|
| `app_id` | ○ | flow の `appId` に入るバンドルID／パッケージ名 |
| `platform` | ○ | `ios` / `android` |
| `device.prefer` | | `booted`（既定） / `name` / `first` |
| `device.name` | | `prefer=name` のとき必須。端末名の完全一致 |
| `launch.mode` | ○ | `command` / `installed` |
| `launch.command` | `command`時○ | 起動コマンド。`{device}` を udid / serial で置換する |
| `launch.ready_pattern` | | `log-launch.txt` をこの正規表現で待つ。省略すると待たない |
| `launch.ready_timeout_seconds` | | 既定 300 |
| `log.sources` | | `launch` / `os` の配列。省略時は `command`→`["launch"]`、`installed`→`["os"]` |
| `log.process` | `os`使用時○ | iOSは実行ファイル名（`processImagePath ENDSWITH "/<値>"` で絞る）、Androidはパッケージ名（`pidof` で pid を引いて `logcat --pid=` に使う） |
| `log.error_patterns` | | **`launch` ソース向け**の文言一致。既定は Flutter 向けの例で、プロジェクトごとに上書きする前提 |
| `log.os_error_patterns` | | **`os` ソース向け**。既定は compact style のログレベル（`E` = Error / `F` = Fault）で判定する |
| `log.ignore_patterns` | | 既知ノイズの除外。`launch` と `os` の両方へ適用する |
| `credentials.source` | | `keychain` / `env` / `none` |
| `credentials.keychain_service` | `keychain`時○ | `security -s` に渡す service 名 |
| `credentials.env_names` | `none`以外○ | `MAESTRO_` 始まりの大文字名。既定では Keychain の account 名も同じ |
| `credentials.keychain_accounts` | | env 名 → Keychain account 名の対応。既存登録が env 名と違うときだけ書く。キーは `env_names` のいずれかであること |
| `android.text_input` | | `adbkeyboard`（既定） / `maestro`。Android の文字入力方式 |
| `android.adbkeyboard_apk` | | ADBKeyBoard の APK path。未指定なら `~/.maestro-evidence/ADBKeyboard.apk` |
| `recording.bit_rate` | | Android の `screenrecord --bit-rate`。既定 4000000。`null` で指定しない |
| `recording.size` | | Android の `screenrecord --size`。既定 `720x1600`。`null` で指定しない |
| `login_flow` | | リポジトリ内の相対path。あれば `session start` の最後に録画外で実行する |
| `base_ref` | | 既定 `origin/main` |

### `launch.mode`

- `command`: コマンドで起動し、stdout/stderr を `log-launch.txt` へ貯め続ける。Flutter なら
  `flutter run` の出力が Dart 例外まで含む正本になる。
- `installed`: インストール済みアプリを起動する。iOS は `xcrun simctl launch`、Android は
  `adb shell monkey -p <app_id> -c android.intent.category.LAUNCHER 1`。

### `log.sources`

ログの出方はプロジェクトごとに違うため、ソースを設定で選ぶ。`launch` は Dart / アプリ側の
出力、`os` は iOS の `log stream` / Android の `logcat` で、ネイティブクラッシュや OS 由来の
エラー向け。両方指定すると `case end` で時刻順にマージする。

### `log.process`（iOS）

`processImagePath ENDSWITH "/<log.process>"` で絞る。`CONTAINS` にすると
`maestro-driver-iosUITests-Runner` のような兄弟プロセスまで一致し、Maestro ドライバの
ログが証跡に混ざる。`process == "<値>"` は対象アプリで1行も取れないことがあるので使わない。

### `log.process`（Android）

`session start` は launch の ready 後に `adb shell pidof <log.process>` で pid を待ち、
`logcat -v time --pid=<pid>` で収集する。pid で絞らないと SystemServiceRegistry などシステム
全体の行が入り、実測で errors.txt が1,069件になった。pid が取れないまま上限（60秒）に達した
場合は、全量を貯めずに os ログ収集自体を行わない（`session.json` の `os_log.status` が
`skipped`）。

### 文字入力（Android）

Flutter の TextField は `adb input text` でも Maestro の `inputText` でも文字が落ちる。
入力速度を落としても1文字ずつでも再現し、ネイティブの EditText では起きない。IME として
テキストを確定する **ADBKeyBoard** を使うと全文入ることを実機で確認している。

既定（`android.text_input: "adbkeyboard"`）では、`session start` が次を行う。

1. `adb shell pm list packages` で未インストールなら `adb install -r <apk>`
2. 現在の IME を `settings get secure default_input_method` で記録
3. `ime enable` と `ime set` で ADBKeyBoard へ切り替え

`session stop` で元の IME へ戻す（`ime set <元のID>`）。ADBKeyBoard 自体はアンインストール
しない。毎回入れ直すのを避けるため。

#### APK の入手と配置

APK はスキルに同梱せず、自動ダウンロードもしない。次のいずれかで用意する。

- https://github.com/senzhk/ADBKeyBoard の releases から `ADBKeyboard.apk`（約17KB）を取得
- 同リポジトリを clone して自分でビルド

取得した APK を `~/.maestro-evidence/ADBKeyboard.apk` へ置くか、`android.adbkeyboard_apk` で
パスを指定する。APK が無い状態で `session start` すると、入手先を示して停止する。

#### 制約

本文は `adb shell` の標準入力から渡すため、**ホスト側の argv には出ない**。ただし端末側では
`am broadcast` のコマンドラインに載るため、端末で `ps` を見られる状況では読める。共有端末では
credential の入力に使わない。

非 ASCII 文字も `ADB_INPUT_TEXT` で入る。`テスト日本語ｶﾀｶﾅ123` が全文正しく入力されることを
実エミュレータで確認済み。base64 で送る `ADB_INPUT_B64` も動くが、ASCII と日本語のどちらも
`ADB_INPUT_TEXT` で足りるため使っていない。

`android.text_input: "maestro"` にすると Maestro の `inputText` を使う。ADBKeyBoard を入れられ
ない端末向けの逃げ道で、Flutter では文字が落ちる前提で使う。

### 録画の負荷（Android）

エミュレータでは既定解像度・高ビットレートの `screenrecord` で CPU が 250〜340% まで上がる。
`recording.bit_rate` と `recording.size` で軽くする。既定は 4Mbps / 720x1600。iOS の
`recordVideo` には相当する引数が無いため、この設定は Android にだけ効く。

### error 判定

`launch` と `os` は判定基準がまったく違うため、パターンを分けて持つ。

- `launch`（Dart / アプリ側の出力）は `error_patterns` の文言一致。`Unhandled Exception` の
  ような文字列で拾うのが妥当。
- `os`（`log stream` / `logcat`）は既定でログレベル判定。`--style compact` は
  `2026-09-08 19:54:27.843 E  Runner[123:abc] ...` のように固定桁のタイプ列を持つので、
  そこが `E`（Error）か `F`（Fault）の行だけを拾う。素朴な文言一致にすると `hasError: 0`、
  カテゴリ名 `XPCErrors`、Simulator 固有の CoreHaptics plist 欠落、`UIKBFeedbackGenerator` の
  ような無関係な行を大量に拾ってしまう。
- Android の `logcat` など判定を変えたい場合は `os_error_patterns` を上書きする
  （例: `["FATAL EXCEPTION", "E/AndroidRuntime"]`）。
- どちらのソースでも残るノイズは `ignore_patterns` で落とす。E レベルであっても、UIKit や
  XCTest の枠組み由来の行（フォーカス管理、トレイト解決、自動化の型不一致など）は
  アプリの不具合ではない。実走で目についたものを `ignore_patterns` へ足していく。

### `credentials`

値は Maestro 子プロセスの環境変数としてだけ渡す。ファイル・引数・ログ・stdout には出さない。
flow からは `${MAESTRO_TEST_EMAIL}` のように参照する（Maestro は `MAESTRO_` 始まりの環境変数を
自動で読むため `-e` は不要）。

Keychain への登録は `me.py credentials set`。値は `security` 自身の非表示プロンプトへ入力する。

`step` と `login_flow` は credential が揃わなくても止まらず、環境変数を渡さずに実行して結果へ
`credentials: "unavailable"` を残す。`credentials status` の判定（available / incomplete /
missing / unsupported / not_required）は従来どおり。

account 名は既定で env 名と同じ（`security find-generic-password -s <service> -a <env名> -w`）。
既に別の account 名で登録済みの Keychain を使い回す場合だけ `keychain_accounts` を書く。
指定しなかった env は従来どおり env 名を account 名として扱う。`credentials set` も
差し替え後の account 名で登録する。

## テンプレート: Flutter iOS

`<>` の部分を自分のプロジェクトの値へ置き換える。

```json
{
  "app_id": "<バンドルID。例 com.example.app.dev>",
  "platform": "ios",
  "device": { "prefer": "booted", "name": "<Simulator名。例 iPhone 17 Pro>" },
  "launch": {
    "mode": "command",
    "command": "<起動コマンド。{device} が udid に置換される>",
    "ready_pattern": "A Dart VM Service|Flutter DevTools",
    "ready_timeout_seconds": 300
  },
  "log": {
    "sources": ["launch", "os"],
    "process": "<実行ファイル名。Flutter iOS なら Runner>",
    "error_patterns": ["Exception", "Error", "FATAL", "Unhandled", "E/flutter", "Assertion failed"],
    "ignore_patterns": ["CoreHaptics", "UIKBFeedbackGenerator", "hasError: 0", "com.apple.UIKit:UIFocus", "com.apple.UIKit:TraitCollection", "com.apple.dt.xctest"]
  },
  "credentials": {
    "source": "keychain",
    "keychain_service": "<Keychain の service 名。例 jp.example.maestro-evidence>",
    "env_names": ["MAESTRO_TEST_EMAIL", "MAESTRO_TEST_PASSWORD"]
  },
  "login_flow": null,
  "base_ref": "origin/main"
}
```

ログインが不要なら `credentials` は `{"source": "none", "env_names": []}` でよい。
`ignore_patterns` は iOS Simulator で必ず出るノイズを入れてある。実走で別のノイズが
出たら足す。

## テンプレート: Flutter Android

```json
{
  "app_id": "<パッケージ名。例 com.example.app.debug>",
  "platform": "android",
  "device": { "prefer": "booted" },
  "launch": { "mode": "installed" },
  "log": {
    "sources": ["os"],
    "process": "<パッケージ名。app_id と同じで良い>",
    "os_error_patterns": ["FATAL EXCEPTION", "E/AndroidRuntime", "E/flutter"],
    "ignore_patterns": []
  },
  "android": { "text_input": "adbkeyboard", "adbkeyboard_apk": null },
  "recording": { "bit_rate": 4000000, "size": "720x1600" },
  "credentials": { "source": "none", "env_names": [] },
  "login_flow": null,
  "base_ref": "origin/main"
}
```

## 例: Flutter iOS（Kahoh の実設定）

```json
{
  "app_id": "com.kahoh.app.dev",
  "platform": "ios",
  "device": { "prefer": "booted", "name": "iPhone 17 Pro" },
  "launch": {
    "mode": "command",
    "command": "fvm flutter run -d {device} --flavor dev --dart-define=FLAVOR=dev",
    "ready_pattern": "A Dart VM Service|Flutter DevTools",
    "ready_timeout_seconds": 300
  },
  "log": {
    "sources": ["launch", "os"],
    "process": "Runner",
    "error_patterns": ["Exception", "Error", "FATAL", "Unhandled", "E/flutter", "Assertion failed"],
    "os_error_patterns": ["^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}\\.\\d+\\s+(?:E|Er|F|Fa)\\s"],
    "ignore_patterns": [
      "CoreHaptics",
      "UIKBFeedbackGenerator",
      "hasError: 0",
      "com.apple.UIKit:UIFocus",
      "com.apple.UIKit:TraitCollection",
      "com.apple.dt.xctest"
    ]
  },
  "credentials": {
    "source": "keychain",
    "keychain_service": "jp.eda.kahoh.simulator-evidence",
    "env_names": ["MAESTRO_KAHOH_TEST_EMAIL", "MAESTRO_KAHOH_TEST_PASSWORD"],
    "keychain_accounts": {
      "MAESTRO_KAHOH_TEST_EMAIL": "test-email",
      "MAESTRO_KAHOH_TEST_PASSWORD": "test-password"
    }
  },
  "login_flow": null,
  "base_ref": "origin/develop"
}
```

`ignore_patterns` は実走で拾ってしまったノイズ。前半3つは Simulator 固有で、CoreHaptics の
plist 欠落と `UIKBFeedbackGenerator` は Simulator でしか出ず、`hasError: 0` は「エラーなし」を
表す行。後半3つは E レベルで出る枠組み由来のノイズで、UIKit のフォーカス管理・トレイト解決と、
XCTest（Maestro ドライバ）の自動化型不一致。いずれもアプリの不具合ではない。
`os_error_patterns` は既定値と同じなので省略してもよい。

`keychain_accounts` は、既存の Keychain 登録が `test-email` / `test-password` という account 名に
なっているため必要。これが無いと `credentials status` は `missing` になる。

この skill は SDD の Verification / E2E / Diff Review / machine gate の判定を置き換えない。
それらの判定を持つ工程の補助として使う。

## 例: Android（インストール済みアプリを起動する構成）

```json
{
  "app_id": "com.example.app.debug",
  "platform": "android",
  "device": { "prefer": "booted" },
  "launch": { "mode": "installed" },
  "log": {
    "sources": ["os"],
    "process": "com.example.app.debug",
    "os_error_patterns": ["FATAL EXCEPTION", "E/AndroidRuntime", "E/flutter"],
    "ignore_patterns": []
  },
  "recording": { "bit_rate": 4000000, "size": "720x1600" },
  "android": { "text_input": "adbkeyboard", "adbkeyboard_apk": null },
  "credentials": { "source": "none", "env_names": [] },
  "login_flow": "maestro/login.yaml",
  "base_ref": "origin/main"
}
```

## iOS と Android を同一リポジトリで切り替える

`platform` は単一値なので、1つのファイルで両対応はできない。プラットフォームごとに
ファイルを分け、`--config` で切り替える。

```
me.py --config .maestro-evidence.ios.json session start --device <udid>
me.py --config .maestro-evidence.android.json session start --device <serial>
```

`session start` は使った設定ファイルの絶対パスを `session.json` の `config_path` へ記録し、
検証・既定値補完後の設定そのものを `config` へ取り込む。以降の `--work` 付きコマンド
（`case` / `step` / `logs` / `report` / `publish` など）はその設定を使うため、`--config` を
毎回付ける必要はない。

`--work` と `--config` を同時に渡した場合は `--config` が優先される。実行中のセッションと
違う設定を混ぜることになるので、意図がある場合だけ使う。

ファイル名は任意だが、既定ファイルと紛れないよう `.maestro-evidence.ios.json` のように
platform が分かる名前を推奨する。リポジトリへ置くかどうかはユーザーの同意で決める。
