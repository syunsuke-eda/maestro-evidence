# maestro-evidence

モバイルアプリの差分から確認ケースを洗い出し、Simulator / エミュレータを録画しながら
Maestro で1回だけ操作して、ログ・スクリーンショット付きの HTML 証跡を作るスキル。
Claude Code と Codex CLI の両方から同じ手順で使える。

作った証跡は `~/maestro-evidence-work/<repo>/<branch>/<timestamp>/` に置かれ、
`index.html` を開けばケースごとの動画・step・エラー行・目視レビューを一覧できる。
`gh` があれば PR へ動画付きで投稿できる。

対象リポジトリの中にはファイルを作らない（設定ファイル `.maestro-evidence.json` だけは
利用者の同意を得て置く）。

## クイックスタート

```
git clone https://github.com/syunsuke-eda/maestro-evidence.git ~/.codex/skills/maestro-evidence
bash ~/.codex/skills/maestro-evidence/install.sh
cd <対象リポジトリ> && python3 ~/.codex/skills/maestro-evidence/scripts/me.py config init > .maestro-evidence.json
python3 ~/.codex/skills/maestro-evidence/scripts/me.py credentials set   # ログインが要る場合だけ
```

あとは AI エージェントに「このブランチの変更を Simulator で検証して証跡を作って」と頼む。

詳しくは「前提ツール」以降を読む。うまくいかないときは
`python3 ~/.codex/skills/maestro-evidence/scripts/me.py doctor` が足りないものを教える。

## 何が起きるか

エージェントが3段階で進める。人間が判断するのは1と3の2か所だけで、途中は止まらない。

| | やること | 人間の関与 |
|---|---|---|
| Phase 1 | 差分を読んで確認ケースを提案する | **承認する** |
| Phase 2 | 録画しながら1回だけ操作し、ログとスクリーンショットを集める | なし |
| Phase 3 | `index.html` を作る。PR へ投稿する場合は本文と動画を提示 | **投稿を承認する** |

Phase 2 は「探索してから本番を録り直す」ことをしない。1回の操作をそのまま証跡にする。

## 何が出てくるか

`~/maestro-evidence-work/<repo>/<branch>/<timestamp>/` に次が残る。

```
index.html              ← これを開く
session.json            全記録
log-launch.txt          アプリの出力（時刻付き）
log-os.txt              OSログ（時刻付き）
cases/<case-id>/
  video.mp4             ケースの録画（H.264、9MB以下、先頭の静止は切り詰め済み）
  final-frame.png       最終状態
  contact-sheet.png     全体を12コマで俯瞰
  screenshots/*.png     途中で撮ったもの
  steps.json            実行した操作の記録
  log.txt               このケースの区間のログ
  errors.txt            error と判定された行だけ
```

## 用語

| 用語 | 意味 |
|---|---|
| session | アプリの起動から停止までの1回。ログ収集の単位。`session start` で始まり `session stop` で終わる |
| case | 1つの確認項目。**録画の単位**で、`case start` から `case end` までが1本の動画になる |
| step | Maestro コマンドのまとまり1回分。「操作 + 待機 + assert」を1 step にする |
| type | 文字入力。Android では ADBKeyBoard 経由になるので `step` の `inputText` は使わない |
| work dir | 成果物の置き場。`session start` が作り、以降のコマンドに `--work` で渡す |

## 前提ツール

| ツール | 要否 | 用途 |
|---|---|---|
| `maestro` | 必須 | 端末操作と画面階層の取得 |
| `ffmpeg` / `ffprobe` | 必須 | 録画の正規化、contact sheet、最終フレーム |
| `python3` 3.9 以上 | 必須 | macOS 同梱の `/usr/bin/python3` で動く。追加パッケージ不要 |
| `xcrun`（Xcode） | iOS を使うなら必須 | Simulator の録画・ログ・スクリーンショット |
| `adb`（platform-tools） | Android を使うなら必須 | 録画・logcat・スクリーンショット |
| `gh` 2.100.0 以上 | 任意 | PR へ証跡を投稿する場合だけ |

`maestro` と `ffmpeg` は Homebrew で入る。

```
brew install ffmpeg
curl -Ls "https://get.maestro.mobile.dev" | bash
```

## インストール

```
git clone https://github.com/syunsuke-eda/maestro-evidence.git ~/.codex/skills/maestro-evidence
bash ~/.codex/skills/maestro-evidence/install.sh
```

`install.sh` は配置を確認し、`~/.claude/skills/maestro-evidence` を正本への symlink として
作り、環境診断まで行う。既存のファイルは上書きしない。

正本を `~/.codex/skills/` に置いて Claude 側を symlink にすると、1つの実体を両方のホストから
使えて更新も1回で済む。

### 片方しか使わない場合

**Claude Code だけ**なら `~/.claude/skills/maestro-evidence` へ clone すればよい。symlink は
不要で、`install.sh` の代わりに `python3 scripts/me.py doctor` だけ実行する。

**Codex CLI だけ**なら上のコマンドのままでよい。`install.sh` が作る Claude 側の symlink は
Codex の動作に影響しない。

### zip で受け取った場合

git を使わずに受け取ったときは、展開して `~/.codex/skills/maestro-evidence` へ置いてから
そこの `install.sh` を実行する。展開先から直接実行すると、一時的な場所を指す symlink が
残らないよう、symlink を作らずに手順だけ案内して終わる。

## 初回セットアップ

### 1. 環境診断

```
python3 ~/.codex/skills/maestro-evidence/scripts/me.py doctor
```

足りないものが「要対応」として出る。解消してから次へ進む。

### 2. 設定ファイルを作る

対象リポジトリのルートに `.maestro-evidence.json` を置く。雛形は次で作れる。

```
cd <対象リポジトリ>
python3 ~/.codex/skills/maestro-evidence/scripts/me.py config init > .maestro-evidence.json
```

質問は標準エラーへ、生成された JSON は標準出力へ出る。上のように `> .maestro-evidence.json`
で受けると、画面には質問だけが出てファイルには JSON だけが入る。

答えるときの注意が2つある。

- `launch.command` の `{device}` は端末IDに置換される。固定のIDを書かない
- `log.process` は iOS なら実行ファイル名（Flutter なら通常 `Runner`。バンドルIDではない）、
  Android ならパッケージ名

`app_id` / `platform` / `launch.command` などを自分のプロジェクトに合わせて直し、検証する。

```
python3 ~/.codex/skills/maestro-evidence/scripts/me.py config validate
```

項目の意味と設定例は [references/config.md](references/config.md) を参照。iOS と Android を
同じリポジトリで切り替える場合はファイルを分けて `--config` で渡す。

### 3. 検証アカウントを登録する（ログインが要る場合）

値は macOS の Keychain に入れ、`security` 自身の非表示プロンプトへ入力する。
チャットにもファイルにも残らない。

**登録は開発者ごとに自分の Keychain へ行う。** 設定ファイルに入るのは Keychain の service 名と
環境変数名だけで、値そのものは共有されない。リポジトリを共有しても、各自が自分のアカウントを
登録する必要がある。

```
python3 ~/.codex/skills/maestro-evidence/scripts/me.py credentials set
python3 ~/.codex/skills/maestro-evidence/scripts/me.py credentials status
```

### 4. Android を使うなら ADBKeyBoard を用意する

Flutter の TextField は `adb input text` でも Maestro の `inputText` でも文字が落ちる。
IME 経由で入力する ADBKeyBoard（約17KB）を使う。

1. https://github.com/senzhk/ADBKeyBoard の releases から `ADBKeyboard.apk` を取得
2. `~/.maestro-evidence/ADBKeyboard.apk` へ置く

```
mkdir -p ~/.maestro-evidence
cp <ダウンロード先>/ADBKeyboard.apk ~/.maestro-evidence/ADBKeyboard.apk
```

端末への導入と IME の切り替えは `session start` が自動で行い、`session stop` で元の IME へ戻す。

## 使い方

AI エージェントに任せるのが前提のスキル。呼び出し方はホストで違う。

**Codex CLI**

```
$maestro-evidence を使って、このブランチの変更をSimulatorで検証し、証跡を作ってください。
```

**Claude Code**

```
/maestro-evidence このブランチの変更をSimulatorで検証して証跡を作って
```

自然文だけでも起動する。

```
このブランチの変更をSimulatorで検証して証跡を作って
```

進み方は「何が起きるか」のとおり。手で動かす場合の最短例は次。

```
ME="python3 ~/.codex/skills/maestro-evidence/scripts/me.py"
$ME devices                                   # 端末IDを調べる
WORK=$($ME session start --device <id> | python3 -c 'import json,sys;print(json.load(sys.stdin)["work"])')
$ME --work "$WORK" case start --id login --title "ログインできる"
$ME --work "$WORK" hierarchy --device <id>    # 実表示を読む
$ME --work "$WORK" step --case login --device <id> --commands-file step.yaml
$ME --work "$WORK" case end --id login --status pass --notes "所見"
$ME --work "$WORK" session stop
$ME --work "$WORK" report                     # index.html を作る
```

操作の書き方と要素の特定手順は [references/element-targeting.md](references/element-targeting.md)、
成果物の構造は [references/session-format.md](references/session-format.md) を参照。

## Codex CLI で使う場合の注意

**Codex CLI で使うにはサンドボックスを無効にする必要がある。** 既定の `workspace-write` では
端末操作そのものができない。

`~/.codex/config.toml` に次を書く。

```toml
sandbox_mode = "danger-full-access"
```

単発なら実行時に指定してもよい。

```
codex exec --dangerously-bypass-approvals-and-sandbox "..."
```

### なぜ writable_roots では足りないか

`sandbox_mode = "workspace-write"` のまま `[sandbox_workspace_write] writable_roots` に
`~/maestro-evidence-work` や `~/.maestro` を足す方法は**動かない**（実測）。

| 対象 | 結果 |
|---|---|
| 成果物ディレクトリへの書き込み | 通る |
| `xcrun simctl` | CoreSimulatorService への XPC 接続が拒否される |
| `maestro` | 起動時に例外 |
| `adb` | サーバーの起動に失敗する |

writable_roots はファイル書き込みの許可であって、プロセス間通信やデーモンの起動までは
開かない。このスキルは simctl / maestro / adb のいずれも使うため、書き込み許可だけでは
成立しない。

### 設定の意味

`danger-full-access` はサンドボックスを外す設定で、**Codex が実行するコマンドが制限なしに
走る**ようになる。このスキルのためだけでなく、そのセッションで実行される全コマンドに効く。
意味を理解したうえで設定し、必要な作業が終わったら戻すかどうかを判断してほしい。単発の
`--dangerously-bypass-approvals-and-sandbox` なら影響をその実行だけに閉じ込められる。

### 削除コマンド

Codex の自動レビューは `rm -f` などの削除コマンドを拒否する。このスキルは削除処理をすべて
`me.py` の内部で行うので、利用者がシェルの削除コマンドを実行する必要はない。

## トラブルシューティング

### Android: 文字が入らない・重複する

`adb input text` と Maestro の `inputText` は Flutter の TextField で文字を落とす。
入力速度を変えても直らない。`me.py type` を使う（Android では ADBKeyBoard 経由になる）。
APK の配置は「初回セットアップ」を参照。

### Android: 画面が勝手に閉じる

`hideKeyboard` が「戻る」操作として働き、画面ごと閉じることがある。使わない。
ボタンがキーボードに隠れているなら `scrollUntilVisible` で送り出す。

### Android: エミュレータが通信できない

AVD によっては Wi-Fi と DNS の初期状態で外部へ出られない。次のオプションで起動する。

```
emulator -avd <AVD名> -feature -VirtioWifi -dns-server 8.8.8.8
```

### Android: 通知許可ダイアログに操作を遮られる

初回起動時に「Allow / Don’t allow」の通知許可ダイアログが出て、その先の操作が届かない。
step の先頭に `optional: true` で閉じる操作を入れておく。既に許可済みでも失敗しない。

```yaml
- tapOn:
    text: "Don’t allow"
    optional: true
```

アポストロフィは `’`（U+2019）で、キーボードから打つ `'`（U+0027）とは別の文字。Maestro の
`text` は全文一致なので `"Don't allow"` と打ち直すと一致しない。上の例をそのままコピーする。

### iOS: ログが1行も取れない

`log.process` は実行ファイル名。`processImagePath ENDSWITH "/<値>"` で絞るので、
バンドルIDではなく `Runner` のような名前を書く。

### 録画が0秒になる・壊れる

録画は `case end` で停止するまでファイルが完成しない。`case start` した case は必ず
`case end` で閉じる。`session stop` は開いたままの case を fail として閉じる。

### 残ったプロセスが気になる

`session stop` はプロセスグループごと停止し、残存が無いことを確認して
`all_groups_terminated` に出す。false のときは stderr に残った pgid が出る。

## 更新方法

```
cd ~/.codex/skills/maestro-evidence
git pull
python3 scripts/me.py doctor
```

symlink は張り直さなくてよい。zip で受け取っている場合は、ディレクトリを置き換えてから
`doctor` を実行する。

`.maestro-evidence.json` は各リポジトリ側にあるので更新の影響を受けない。設定項目が増えた
場合は `config validate` が未知のキーや不足を教える。

## テスト

```
python3 -m unittest discover -s ~/.codex/skills/maestro-evidence/scripts/tests -t ~/.codex/skills/maestro-evidence/scripts
```

外部パッケージも実機も使わない。外部コマンドはすべてモックする。
