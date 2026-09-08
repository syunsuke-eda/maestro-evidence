# 要素の特定と座標の使い方

## 大前提

証跡は「今あるビルドがどう動くか」の記録である。証跡を取りやすくするために
アプリのコードへ Semantics identifier・tooltip・key を足さない。アプリ側への変更提案も
しない。識別子は既にある場合だけ使う。

識別子が無くても、表示文言と画面の見た目だけでケースは通せる。実際に Kahoh の共有アルバム
参加ケースは、Semantics identifier を追加する前のビルドで、文言とスクリーンショットから
読んだ座標だけで PASS している。

## 優先順

1. **表示文言** — `tapOn: "参加する"` / `assertVisible: "参加する"`。最も壊れにくい。
   Maestro の `text` は全文一致なので、複数行の文言は1行目だけでは一致しない。
2. **accessibility label** — `hierarchy` の `accessibilityText`。アイコンだけのボタンなど、
   文字が出ていない要素で使う。
3. **`hierarchy` の `bounds` 中心座標** — 要素は hierarchy に出るが `text` でも
   `accessibilityText` でも一意に取れないとき。`bounds=[x1,y1][x2,y2]` の中心を取る。
4. **`screenshot` から読み取った座標** — hierarchy に要素そのものが出てこないとき。

3 と 4 を使うときは、直前に `hierarchy` か `screenshot` で実表示を確認してから座標を決め、
根拠を step の `--label` に書く。

```
$ME --work <work> step --case join-album --device <id> \
  --label "screenshot 03-album-detail.png の右下「+」FAB（画像1206x2622px の (300,2490) → 論理 (100,830)）" \
  --commands-file step.yaml
```

`--label` は steps.json とレポートに残る。後から「その座標がなぜ妥当だったか」を追える
唯一の手掛かりなので、省略しない。

## 座標の指定方法

Maestro の `tapOn` / `swipe` は割合と絶対値のどちらも受ける。

```yaml
- tapOn:
    point: "50%,80%"      # 割合。端末解像度に依存しない
- tapOn:
    point: "100,830"      # 絶対値
```

割合を優先する。端末やスケールが変わっても壊れないため。

絶対値を使う場合、`hierarchy` の `bounds` と同じ座標系（論理ポイント）で指定する。
`xcrun simctl io ... screenshot` が出す PNG は物理ピクセルなので、そのまま渡すと3倍ずれる。

実測（iPhone 17 Pro / iOS 26.5）:

| | 値 |
|---|---|
| screenshot の画素数 | 1206 x 2622 px |
| hierarchy の画面 bounds | [0,0][402,874] 論理pt |
| スケール | 3.0 |

つまり `論理座標 = 画像ピクセル ÷ スケール`。スケールは端末ごとに違うので、
画面全体の bounds と screenshot の画素数から毎回求める。

## hierarchy に出ない要素

要素が hierarchy にまったく出てこないことがある。Kahoh の実走では、アルバム詳細の
右下「+」FAB と右上「…」メニューが該当した。この2つは screenshot から座標を読んで操作した。

hierarchy に出ない要素へ当たったときの手順:

1. `screenshot` を撮り、証跡として残す
2. 画像から対象の位置を読み、スケールで割って論理座標にする
3. `--label` に「どの画像のどこを読んだか」を書いて `step` を実行
4. 期待結果は文言の `assertVisible` で確認する（座標で確認しない）

## Android 固有の注意

### `hideKeyboard` を使わない

Android では `hideKeyboard` が「戻る」操作として働き、キーボードを閉じるつもりで画面ごと
閉じることがある。実際にアルバム参加画面が閉じた。キーボードは出したままボタンをタップする。
ボタンがキーボードに隠れているなら `scrollUntilVisible` で送り出す。

### 文字入力は `type` を使う。`inputText` は使わない

Flutter の TextField は `adb input text` でも Maestro の `inputText` でも文字が落ちる。
入力速度を落としても1文字ずつでも再現し、ネイティブの EditText では起きない。

`me.py type` を使う。Android では IME（ADBKeyBoard）経由でテキストを確定するため全文入る。
iOS では Maestro の `inputText` として実行されるので、呼び出し側で分岐する必要はない。

```
$ME --work <work> type --case <case-id> --device <id> --label "メールアドレスを入力" --text-file text.txt
```

本文は引数で渡さない。ファイルか標準入力から渡す。credential を入れる場合は
`--env MAESTRO_XXX` で環境変数名を指定すると、値がファイルにも argv にも残らない。

steps.json に残るのは方式・文字数・出所だけで、本文は常に `[REDACTED]`。

Android で使うには ADBKeyBoard の APK を用意する必要がある。入手と配置は `config.md` の
「文字入力（Android）」を参照。日本語・半角カナを含む非 ASCII 文字も全文入ることを実機で
確認済み。

### ダイアログでは index に頼らず bounds を使う

Android では、Flutter の確認ダイアログのコンテナ自体にもボタンと同じ `accessibilityText` が
付く（例「アルバムから抜ける」）。コンテナの `bounds` は画面全体になるため、`tapOn` の
`text` + `index: 1` が iOS とは違う要素に当たって失敗する。index の並び順は階層構造に依存し、
プラットフォーム間で一致しない。

同じ文言の要素が複数あるときは index を使わず、`hierarchy` の `bounds` からボタンの中心座標を
取ってタップする。ボタンとコンテナは `bounds` の大きさで見分けられる。画面全体に近いものが
コンテナ、ボタン1つ分の高さしかないものが実際のボタン。

```
1  accessibilityText=アルバムから抜ける | bounds=[0,0][1080,2400]   ← ダイアログのコンテナ
2  accessibilityText=アルバムから抜ける | bounds=[620,1290][900,1400] ← 押したいボタン
```

この場合は 2 の中心 `(760, 1345)` を使い、根拠を `--label` に書く。

### hierarchy にステータスバーが混ざる

`resource-id` が `com.android.systemui:` で始まる要素（時計・電池・ナビゲーションバーなど）は
アプリの要素ではないため、`hierarchy` の既定出力から除外している。除外した件数は出力の
`excluded_count` に出る。生の階層が要るときは `--raw` を使う。
