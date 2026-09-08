# 成果物の形式

```
~/maestro-evidence-work/<repo>/<branch-sanitized>/<YYYYmmdd-HHMMSS>/
  session.json          # 全記録
  cases.json            # Phase 1 で承認したケース一覧（AIが書く。me.py は読まない）
  log-os.txt            # OSログ全量
  log-launch.txt        # launch.command の stdout/stderr
  collector-<source>.err  # 収集プロセス自体のエラー出力
  cases/<case-id>/
    video-source.mp4    # 録画原本（0600）
    video.mp4           # H.264/yuv420p/faststart、先頭静止トリム済み、9MB以下
    final-frame.png
    contact-sheet.png
    record.log          # 録画プロセスの出力（"Recording started" の確認に使う）
    steps.json          # 実行した step の記録
    screenshots/*.png
    flows/step-NNN.yaml # step ごとに生成した一時 flow
    log.txt             # ケース区間のログ（両ソースを時刻順にマージ）
    errors.txt          # error 行（launch は error_patterns、os は os_error_patterns。ignore_patterns 除外後）
  index.html
```

## ログ行の形式

`log-os.txt` / `log-launch.txt` は各行が次の形になる。

```
2026-09-08T19:04:01.004+09:00<TAB>flutter: ホーム画面を表示しました
```

外部プロセスの出力（Maestro の進捗、OSログ、hierarchy）は、ファイルへ書く前と stdout へ出す前に credential の実値・Bearer・署名付きURLを `[REDACTED]` へ置換する。`session stop` / `publish` 時の 全体scanは最後の砦であって唯一の防御ではない。

ANSI エスケープは収集時に落とすが、`flutter run` が中継する行では ESC の直前に literal
backslash が付く形で現れることがあるため、`log.txt` / `errors.txt` を書く直前にもう一度落とす。

先頭の時刻は収集側（`me.py _collect`）が付ける。`flutter run` の出力には時刻が無く、
これが無いと2ソースを時刻順にマージできないため。ANSIエスケープはこの時点で除去する。

`cases/<id>/log.txt` はさらにソース名が付く。

```
2026-09-08T19:04:01.004+09:00<TAB>[launch] flutter: ホーム画面を表示しました
```

## `session.json`

| キー | 内容 |
|---|---|
| `schema_version` | 1 |
| `work_dir` / `repo` | 絶対path |
| `config` | 検証・既定値補完後の設定（credential の実値は含まない）。`--work` 付きコマンドはこれを使う |
| `config_path` | `session start` で使った設定ファイルの絶対パス。`--config` 未指定なら既定ファイル |
| `fingerprint` | `base_ref` / `merge_base` / `head_sha` / `branch` / `diff_sha256` / `changed_files` / `untracked_files` |
| `device` | `id` / `name` / `state` / `runtime` / `booted` |
| `status` | `running` / `stopped` |
| `collectors` | ソース名 → `{pid, pgid, log, command}`。`pgid` は収集対象コマンドのプロセスグループID。`session stop` で空になる |
| `collector_stop` | `session stop` の結果。ソースごとに `collector_pid` / `pgid` / `collector` / `group` / `signals` / `group_remaining` |
| `text_input` | 文字入力方式の状態。`{status, previous_ime, ime, installed_by_session, restore}`。`status` は `active` / `maestro` / `not_applicable` |
| `os_log` | Android のみ。`{status, pid}`。`status` が `skipped` なら pid を引けず収集していない |
| `launch` / `login_flow` | 起動とログインflowの結果。`login_flow.credentials` は `available` / `unavailable` / `not_required` |
| `cases` | 後述 |
| `secret_scan` | `{at, status, violations, credential_values_checked, credential_values_expected, credential_check}`。`status` は `clean` / `redacted`、`credential_check` は `complete` / `unavailable`（実値を取れずに走ったscanは clean でも照合していない） |
| `publication` | `publish` 後のコメントURLなど |

### `cases[]`

| キー | 内容 |
|---|---|
| `id` / `title` | ケース識別子と表示名 |
| `status` | `running` / `pass` / `fail` |
| `offsets` / `end_offsets` | ソース名 → ログのバイト位置。この区間を `log.txt` に切り出す |
| `recorder_pid` | 録画中のみ存在。`case end` で消える |
| `recording` | `started` / `unconfirmed` |
| `steps` | `steps.json` と同じ内容 |
| `screenshots` | work dir からの相対path |
| `video` | 後述 |
| `errors` / `errors_count` | `errors.txt` の内容と件数 |
| `review` | `{status, notes, at}`（`me.py review` が書く） |
| `notes` | `case end --notes` の所見 |
| `plan` | `cases.json` に同じ `id` があれば `report` が紐付ける（session.json には残らない） |

### `video`

| キー | 内容 |
|---|---|
| `status` | `pass` / `error` / `skipped` / `recording` |
| `reason` | `error` のときだけ。動画処理に失敗してもケースは閉じる |
| `file` | `video.mp4` |
| `source` / `review` | 原本と投稿用の `codec` / `pixel_format` / `width` / `height` / `duration_seconds` / `size_bytes` / `sha256` |
| `encoded_width_limit` | 886。9MB を超えたときだけ 720 |
| `trimmed_leading_seconds` / `trim_status` | 先頭静止の切り詰め量と `applied` / `not_needed` / `skipped:<理由>` |
| `stop` | 録画プロセスの停止結果（`stopped` / `killed` / `not_running`） |

`publish` は `review.sha256` と実ファイルの SHA-256 が一致することを確認してから投稿する。

## `steps.json`

`label` は step の意図を書く欄。座標で操作した step では「どの画像のどの要素から読んだ
座標か」を必ず書く。座標の妥当性を後から追える唯一の手掛かりになる。詳細は
`element-targeting.md` を参照。

`kind` は `step` か `type`。`type` の記録は本文を残さず、`text` が常に `[REDACTED]`、
`text_length` に文字数、`text_source` に出所（`text` か `env:<変数名>`）、`method` に
`adbkeyboard` か `maestro` が入る。本文は credential の可能性があるため実値を保存しない。

`credentials` は step 実行時に credential を渡せたかを表す。`unavailable` は
「揃わなかったので環境変数を渡さずに実行した」という警告で、step 自体は実行される。
一部だけ渡すと flow が中途半端に進むため、揃わないときは何も渡さない。

```json
[
  {
    "index": 1,
    "at": "2026-09-08T10:01:04+00:00",
    "label": "写真を追加",
    "commands": "- tapOn:\n    id: \"album-detail.add-media\"",
    "flow": "step-001.yaml",
    "exit_code": 0,
    "status": "pass",
    "duration_seconds": 6.42,
    "credentials": "available",
    "output_tail": ["...maestro の出力末尾30行..."]
  }
]
```

## プロセスの停止

収集対象のコマンドは独立したプロセスグループのリーダーとして起動する（`start_new_session`）。
`fvm flutter run` のような wrapper は自分が終わっても dartvm や `simctl spawn ... log stream` を
子孫として残すため、停止は pid ではなくグループ単位で SIGINT → SIGTERM → SIGKILL の順に送る。
SIGINT を先に送るのは、`simctl recordVideo` が moov を書き終えるための正規の停止手段だから。

`session stop` は停止した pid / pgid と、停止後に同グループの生存メンバーが居ないことの確認結果
（`group_remaining`）を出力する。1つでも残っていれば `all_groups_terminated` が false になり
終了コードは 2 になる。

`xcrun simctl spawn` が Simulator 内部で動かす `log` プロセスはホスト側のプロセスグループに
属さないが、ホスト側の `simctl spawn` を落とせば連動して終了することを実機で確認している。
