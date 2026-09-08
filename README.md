# maestro-evidence

モバイルアプリの差分から確認ケースを洗い出し、Simulator / エミュレータを録画しながら
Maestro で1回だけ操作して、ログ・スクリーンショット付きの HTML 証跡を作るスキル。
Claude Code と Codex CLI の両方から同じ手順で使える。

作った証跡は `~/maestro-evidence-work/<repo>/<branch>/<timestamp>/` に置かれ、
`index.html` を開けばケースごとの動画・step・エラー行・目視レビューを一覧できる。
`gh` があれば PR へ動画付きで投稿できる。

対象リポジトリの中にはファイルを作らない（設定ファイル `.maestro-evidence.json` だけは
利用者の同意を得て置く）。

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

正本を `~/.codex/skills/maestro-evidence/` へ置き、そこから `install.sh` を実行する。

```
mkdir -p ~/.codex/skills
cp -R maestro-evidence ~/.codex/skills/maestro-evidence
~/.codex/skills/maestro-evidence/install.sh
```

`install.sh` は配置を確認し、`~/.claude/skills/maestro-evidence` を正本への symlink として
作り、環境診断まで行う。既存のファイルは上書きしない。

ダウンロードした場所（`~/Downloads` など）から直接実行した場合は、symlink を作らずに
上のコマンドを案内して終わる。一時的な場所を指す symlink が残らないようにするため。

### 片方しか使わない場合

**Claude Code だけ**なら `~/.claude/skills/maestro-evidence/` に直接置けばよい。symlink は
不要で、`install.sh` も実行しなくてよい（`me.py doctor` だけ実行する）。

**Codex CLI だけ**なら `~/.codex/skills/maestro-evidence/` に置くだけでよい。`install.sh` は
Claude 側の symlink を作ろうとするが、`~/.claude` が無ければ作成して symlink を張るだけで、
Codex 側の動作には影響しない。

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

AI エージェントに任せるのが前提のスキル。Claude Code か Codex CLI で次のように頼む。

```
$maestro-evidence を使って、このブランチの変更をSimulatorで検証し、証跡を作ってください。
```

エージェントは次の順で進める。人間が判断するのは 1 と 3 の2か所だけ。

1. 差分から確認ケースを提案する → **ユーザーが承認**
2. 録画しながら1回だけ操作し、ログとスクリーンショットを集める
3. `index.html` で結果を確認 → PR へ投稿するなら **ユーザーが承認**

手で動かす場合の最短例。

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

### Android: flow が最初の画面で止まる

新規作成した AVD は初回起動時のセットアップが残っていて、アプリの前にウィザードが出る。
一度手動で起動して初期設定を済ませてから使う。

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

新しい版を受け取ったら、正本のディレクトリを置き換える。symlink は張り直さなくてよい。

```
rm -rf ~/.codex/skills/maestro-evidence
cp -R <新しい maestro-evidence> ~/.codex/skills/maestro-evidence
python3 ~/.codex/skills/maestro-evidence/scripts/me.py doctor
```

`.maestro-evidence.json` は各リポジトリ側にあるので、更新の影響を受けない。設定項目が
増えた場合は `config validate` が未知のキーや不足を教える。

## テスト

```
python3 -m unittest discover -s ~/.codex/skills/maestro-evidence/scripts/tests -t ~/.codex/skills/maestro-evidence/scripts
```

外部パッケージも実機も使わない。外部コマンドはすべてモックする。
