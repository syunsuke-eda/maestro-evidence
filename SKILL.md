---
name: maestro-evidence
description: モバイルアプリの差分から確認ケースを洗い出し、Simulator / エミュレータを録画しながらMaestroで1回だけ操作し、ログとスクリーンショット付きのHTML証跡を作ってPRへ投稿する。「この変更を実機で確認して証跡を残して」「Maestroで動作確認して」「PRに動画を貼って」と依頼されたときに使う。単体テストや静的解析の代替には使わない。
---

# Maestro Evidence

差分に対して「ユーザーから見た挙動が変更どおりか」を実機相当で1回確かめ、その過程を
動画・ログ・スクリーンショットとして残す。判断はAIが行い、機械作業は `scripts/me.py`
がすべて担う。

## 前提

- リポジトリルートに `.maestro-evidence.json` がある（無ければ `me.py config init` の出力を
  ユーザーへ提示し、同意を得てから置く）。別ファイルを使うときは `--config <path>` で渡す
- `maestro` / `ffmpeg` / `ffprobe` と、iOSなら `xcrun`、Androidなら `adb` が使える
- 成果物は `~/maestro-evidence-work/<repo>/<branch>/<timestamp>/` に置く。リポジトリ内には
  何も作らない

以降 `ME="python3 <このskill>/scripts/me.py"` として書く。`--repo` を省くとカレントを使う。

iOS と Android を同じリポジトリで切り替えるときは、`platform` ごとに設定ファイルを分け、
`--config <path>` で渡す。`session start` が使った設定は `session.json` へ取り込まれるので、
以降の `--work` 付きコマンドに `--config` を付け直す必要はない。

## Phase 1 — ケースの提案と承認（端末に触らない）

1. `$ME fingerprint` で変更ファイルと merge-base を取る。
2. 変更ファイルと関連する仕様・画面を読み、**ユーザーから見た挙動**の確認ケースを列挙する。
3. 各ケースに次を書き、`<work候補>/cases.json` ではなくまずチャットでユーザーへ提示する。
   - `id`（英小文字・数字・ハイフン） / `title`
   - 手順（どの画面から何を操作するか）
   - 期待結果（画面上で何が見えれば合格か）
   - データ変更の有無と戻し方（cleanup）
   - 前提（ログイン状態、必要なデータ）
4. ユーザーの承認を得るまで、端末・アプリ・外部データに一切触らない。work dir は
   `session start` が作るため、承認内容は `session start` の直後に `<work>/cases.json` へ
   `{"cases": [{"id", "title", "precondition", "steps", "expected", "mutations"}]}` の形で保存する。
   `report` はこれを読み、実行結果の横に承認済みの手順と期待結果を並べる。

## Phase 2 — 録画しながら1回だけ操作する

```
$ME credentials status
$ME devices
$ME session start --device <id>          # → work dir が出力される
$ME --work <work> case start --id <case-id> --title <title>
  ループ: hierarchy で実表示を読む → step を実行 → 必要なら screenshot
  （hierarchy に出ない要素は screenshot を撮って座標を読む）
$ME --work <work> case end --id <case-id> --status pass|fail --notes "<所見>"
$ME --work <work> session stop
```

- `session start` は起動・ready待ち・ログ収集開始・`login_flow` 実行までを行う。**録画はしない**。
- 録画はケース単位。`case start` から `case end` までが1本の動画になる。
- 探索と録画を分けない。試し打ちも録画に含める前提で、最初から本番として操作する。
- cleanup（データの戻し）が要るケースは、同じケース内で続けて実行し結果を `--notes` に残す。
- 失敗したら原因を直して再実行してよい。ただし再実行の前に、データ変更の戻しが必要かを
  判断してユーザーへ報告する。

### step の粒度

1 step は「操作 + 待機 + assert」をまとめる。

```yaml
- tapOn:
    id: "album-detail.add-media"
- extendedWaitUntil:
    visible: "写真を追加"
    timeout: 10000
- assertVisible: "写真を追加"
```

`maestro test` は1回ごとにドライバ接続の待ちが入り、動画に数秒のポーズが残る。ポーズは
許容し、`hierarchy` は分岐点だけで呼ぶ。

要素の特定手順と座標の使い方は `references/element-targeting.md` にまとめてある。

commands は必ずファイルか標準入力で渡す。引数に本文を書かない（日本語・引用符・改行で壊れる）。

```
$ME --work <work> step --case <case-id> --device <id> --label "写真を追加" --commands-file step.yaml
$ME --work <work> type --case <case-id> --device <id> --label "メールを入力" --text-file text.txt
```

文字入力は `step` の `inputText` ではなく `type` を使う。Android は IME 経由、iOS は
`inputText` に自動で振り分けられる。本文は引数で渡さずファイルか標準入力から渡し、
credential は `--env MAESTRO_XXX` で環境変数名を指定する。

credential が未登録でも `step` と `login_flow` は止まらない。環境変数を渡さずに実行し、結果 JSON の
`credentials` が `unavailable` になる。ログイン済み端末で credential を使わない操作はそのまま通る。
`${MAESTRO_...}` を参照する step が `unavailable` で失敗したら、`credentials status` を確認して
ユーザーへ登録を促す。

### ログの読み方

ログはバックグラウンドで貯まる。操作中に逐一読まない。読むのは2点だけ。

1. `step` が失敗したとき → `$ME --work <work> logs --case <case-id> --tail 80` で原因を見る
2. `case end` のとき → stdout の `errors_count` と `errors_preview` を必ず読む

エラー行があればケース所見とユーザーへの報告に含める。UIの期待結果が満たされていれば
自動で fail にはしないが、レポートには必ず残す。

error 判定はソースごとに違う。`launch` は `error_patterns` の文言一致、`os` は
ログレベル（compact style の `E` / `F`）。OSログに無関係なノイズが残るときは
`.maestro-evidence.json` の `ignore_patterns` へ追加するようユーザーへ提案する。

## Phase 3 — レビューと出力

1. 各ケースの `final-frame.png` と `screenshots/` を目視する。白画面・エラーダイアログ・
   クラッシュ・想定外の画面なら fail。
2. `$ME --work <work> review --case <case-id> --status pass|fail --notes "<所見>"`
3. `$ME --work <work> report` → `index.html` を開いて内容を確認する。
4. PR投稿は**別の承認**。投稿する動画・最終フレーム・コメント本文をユーザーへ提示し、
   同意を得てから `$ME --work <work> publish --pr <n> --approved` を実行する。

## 守ること

- リポジトリ内にファイルを作らない。例外は `.maestro-evidence.json` の新規作成だけで、
  これもユーザー同意が要る。
- credential をチャット・ファイル・コマンド引数へ出さない。値は `me.py` が環境変数として
  Maestro の子プロセスへ渡す。`-e KEY=値` は使わない（`ps` から読めるため）。
- **検証のためにアプリのコードを変更・追加しない。** Semantics identifier、tooltip、key など
  テストしやすさのための追加も含めて行わず、アプリ側への変更提案もしない。証跡は「今ある
  ビルドがどう動くか」の記録であって、証跡を取りやすくするために対象を変えない。
- 要素の特定は次の優先順で試す。上から順に試し、取れなかったときだけ下へ降りる。
  1. 表示文言（`text`）
  2. accessibility label（`hierarchy` の `accessibilityText`）
  3. `hierarchy` の `bounds` 中心座標
  4. `screenshot` から読み取った座標
- 座標タップは禁止しない。ただし座標を使うときは、直前に `hierarchy` か `screenshot` で
  実表示を確認してから使い、その根拠（どの画像・どの要素から読んだか）を step の
  `--label` に書く。`--label` は steps.json とレポートに残り、後から座標の妥当性を追える
  唯一の手掛かりになる。
- 識別子（`id`）はアプリに既にある場合だけ使う。無いことを理由に追加しない。
- アニメーションで現れる要素は tap の前に `waitForAnimationToEnd`。固定 `sleep` より
  `extendedWaitUntil`。
- Maestro の `text` は全文一致。複数行の文言は1行目だけでは一致しない。
- Android では `hideKeyboard` を使わない。戻る操作として働き、画面ごと閉じることがある。
  キーボードは出したままボタンをタップし、隠れているなら `scrollUntilVisible` で送り出す。
- **文字入力は `type` を使い、`inputText` を step に書かない。** Flutter の TextField は
  `adb input text` でも Maestro の `inputText` でも文字が落ちる。`type` は Android では
  IME（ADBKeyBoard）経由で入力し、iOS では `inputText` として実行するので、呼び出し側で
  分岐しなくてよい。credential を入れるときは `--env MAESTRO_XXX` を使う。
- `rm` などの削除シェルコマンドを実行しない。削除は `me.py` の内部で行う。
- Claude で Maestro MCP が使える場合でも、操作と記録は `me.py` だけを使う。1つの端末に
  MCP と CLI の2ドライバを同時に刺さない。Codex も同じ手順。

## 参照

- `README.md` — 導入手順、初回セットアップ、トラブルシューティング（人間向け）
- `references/config.md` — `.maestro-evidence.json` の全項目と設定例
- `references/session-format.md` — `session.json` / `steps.json` / ログ形式
- `references/element-targeting.md` — 要素の特定手順と座標の使い方
