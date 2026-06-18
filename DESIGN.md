# DESIGN.md — Psephos（関数電卓 for PicoCalc）

> 計算に用いた小石（ψῆφος / psephos）に由来する、PicoCalc 向け関数電卓。
> 名称確定: **Psephos**（2026-06-18）。

- **対象ハード**: ClockworkPi PicoCalc + Raspberry Pi Pico 2W（RP2350）
- **言語/実行環境**: MicroPython（LofiFren / zenodante 系ファームウェア）
- **作成日**: 2026-06-18 (JST)
- **ライセンス方針**: MIT（依存元 LofiFren/zenodante が MIT のため整合）

---

## 1. 目的（Why）

graphing calculator をいじっていた頃の感覚を、PicoCalc の物理 QWERTY と
レトロ画面で再現する。単なる四則演算ではなく、**Python の式評価エンジンを
そのまま電卓にする**ことで、`sin(pi/6)+sqrt(2)` のような数式を直接打てる。

差別化ポイント = **計算履歴が画面に積み上がり、SD カードへ永続化される**こと。
電源を切っても過去の計算が残り、起動時に復元される。

---

## 2. スコープ（What）

### 2.1 MVP（このプロトタイプの範囲）

| 機能 | 内容 |
|---|---|
| 式入力 | QWERTY で数式文字列を直接入力 |
| 式評価 | `math` 関数群 + 定数を許可した安全な `eval` |
| 履歴表示 | 画面上部に古い順→最新、スクロール |
| 履歴永続化 | `/sd/psephos_history.txt` にタブ区切りで追記、起動時ロード |
| `ans` 参照 | 直前の結果を `ans` で再利用 |
| 編集 | Backspace で1文字削除 |
| エラー処理 | ゼロ除算・構文エラー等をメッセージ表示（クラッシュしない） |
| 終了 | ESC で REPL/メニューへ復帰 |

### 2.2 非スコープ（MVP では作らない）

- グラフ描画、行列・複素数、単位変換
- 履歴の上下キーによる呼び出し・再編集（Phase 2）
- 関数定義・変数束縛の永続化
- テーマ切替（LUT 切替）

---

## 3. 対応関数・定数（許可リスト）

`eval` の名前空間に**明示的に渡したものだけ**が使える（安全設計）。

- 三角: `sin cos tan asin acos atan atan2`
- 指数対数: `exp log log10 sqrt pow`
- 端数: `floor ceil fabs abs round`
- 角度: `radians degrees`
- 定数: `pi e tau`
- 補助: `min max ans`

> 拡張する場合は `_build_namespace()` に追記するだけでよい。

---

## 4. 画面レイアウト（320×320 / 6×8 フォント）

```
列数 COLS = 53, 行数 ROWS = 40

┌─────────────────────────────────────────────┐ row 0
│ 1+2*3 = 7                                     │   履歴域（DIM 色）
│ sin(pi/6) = 0.5                               │   末尾 HISTORY_ROWS 件を
│ sqrt(2) = 1.414213562                         │   古い順に表示
│ ...                                           │
│                                               │ row 37
├───────────────────────────────────────────────┤ row 38（区切り線 / メッセージ）
│ > sqrt(2)+1_                                  │ row 39（入力行, FG 色）
└─────────────────────────────────────────────┘
```

- 履歴 1 行が COLS を超える場合は末尾を `~` で省略
- エラー時は区切り行にアクセント色でメッセージ表示

---

## 5. データ設計

### 履歴ファイル `/sd/psephos_history.txt`

```
<式>\t<結果文字列>\n
```

- 1 計算 = 1 行（追記オンリー、書き換えなし）
- 起動時に全行ロード、メモリ保持は末尾 `HISTORY_MAX`(=200) 件
- SD 非搭載/未マウント時は `OSError` を握りつぶしメモリのみで動作

### 結果整形 `_format()`

- `float` が整数値なら小数点を出さない（`180.0` → `180`）
- それ以外は `{:.10g}`（有効数字 10 桁）

---

## 6. アーキテクチャ（モジュール内責務）

| 領域 | 関数/クラス | 責務 |
|---|---|---|
| 評価 | `evaluate()` / `_build_namespace()` | 安全な式評価、`ans` 更新 |
| 履歴 | `History` | ロード・追記・クリア |
| 入力 | `_read_key()` | キー1文字取得（**機種差をここに集約**） |
| 描画 | `render()` / `_draw_text()` / `_clear()` / `_show()` | framebuf 描画 |
| 制御 | `main()` | イベントループ |

> **移植容易性**: キーボード API が機種で異なるため、入力は `_read_key()` に
> 一点集約。実機で動かない場合はこの関数だけ直せば全体が動く。

---

## 7. セキュリティ設計（重要）

`eval` は任意コード実行のリスクがある。本実装では：

```python
eval(expr, {"__builtins__": {}}, local)
```

- 第2引数（globals）の `__builtins__` を空 dict にし、`__import__` や
  `open` 等を**呼べなくする**
- 第3引数（locals）に許可関数・定数のみを渡す

検証済み: `__import__("os").listdir()` は `NameError` で遮断される。

> 注意: これは「自分用電卓」前提の防御。完全なサンドボックスではない。
> 不特定多数に配布する場合は、字句解析ベースのパーサ等への置換を検討。

---

## 8. ハードウェア前提（要実機確認）

| 項目 | 値（zenodante/LofiFren ドライバ） |
|---|---|
| 画面 | 320×320 ILI9488、SPI1、4bit グレースケール/16色 |
| `picocalc.display` | `framebuf` サブクラス（標準 framebuf メソッド可） |
| フォント | 6×8（COLS=53, ROWS=40 を導出） |
| キーボード | I2C MCU @ 0x1F（取得 API は要確認） |
| SD | SPI0、FAT32、`/sd` にマウント |

> **未確認事項（HANDOFF で実機検証）**
> - `_read_key()` が当該ファームのキー取得 API と合致するか
> - 色番号（COL_FG=15 等）が当該 LUT と一致するか
> - `display.show()` の要否（auto-refresh が Core1 常時動作なら不要な場合あり）

---

## 9. ロードマップ

- **Phase 1（MVP / 本プロトタイプ）**: 入力・評価・履歴・永続化・エラー処理
- **Phase 2**: 上下キーで履歴呼び出し＆再編集、`ans2 ans3...` 多段参照
- **Phase 3**: ユーザ定義変数（`x = 3` を保持）、16進/2進入力（`hex()/bin()`）
- **Phase 4**: テーマ（LUT 切替）、関数一覧ヘルプ画面、設定ファイル

---

## 10. 既知のリスク・留意点

- MicroPython の `math` は単精度な場合あり。高精度計算要件があれば要検証。
- キーボードのエスケープシーケンス処理は最小実装（矢印キーは現状無視）。
- 履歴ファイルは追記のみのため、長期運用で肥大化する。Phase 2 でローテーション検討。

---

## 出典 / 参考

- ClockworkPi PicoCalc 公式: https://www.clockworkpi.com/picocalc
- zenodante/PicoCalc-micropython-driver（display=framebuf サブクラス、LUT 仕様）:
  https://github.com/zenodante/PicoCalc-micropython-driver
- LofiFren/PicoCalc（Pico 2W 向け UF2・modules 構成・HW 表）:
  https://github.com/LofiFren/PicoCalc
- MicroPython `math` モジュール: https://docs.micropython.org/en/latest/library/math.html
