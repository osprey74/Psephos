# HANDOFF_psephos.md — Psephos（関数電卓 for PicoCalc）

> Claude Code 向けハンドオフ。`DESIGN.md` と `psephos.py`（MVP プロトタイプ）を前提とする。
> 名称確定: **Psephos**（ψῆφος = 計算に用いた小石）。

- **作成日**: 2026-06-18 (JST)
- **対象ハード**: ClockworkPi PicoCalc + Raspberry Pi Pico 2W（RP2350）
- **実行環境**: MicroPython（LofiFren / zenodante 系ファームウェア）
- **ライセンス**: MIT
- **リポジトリ想定**: `osprey74/psephos`

---

## 0. このドキュメントの読み方

**Phase 1 完了済（2026-06-18 実機検証）**。実機で式評価・履歴永続化・キー入力・
セキュリティ防御がすべて動作することを確認した。残作業は SD 未挿入時の挙動の
実機確認のみ（コード上は OSError 握りつぶし実装済）。

次に着手するのは §4 Phase 2（履歴呼び出し UI）か、付帯課題（boot.py SD マウント race、
画面色微調整、関数追加等）。

---

## 1. 現状（What exists）

| ファイル | 状態 |
|---|---|
| `DESIGN.md` | 完成。設計の単一の真実（SSOT）。 |
| `psephos.py` | MVP。式評価・履歴・永続化・エラー処理を実装。PC 上でコアロジック検証済み。 |
| `HANDOFF_psephos.md` | 本書。 |

### 検証済み（PC フォールバック上）

- 四則・優先順位（`1+2*3 → 7`）
- 三角・平方根・対数（`sin(pi/6) → 0.5`, `sqrt(2)`, `log(e) → 1`）
- べき乗（`2**10 → 1024`）、角度変換（`degrees(pi) → 180`）
- `ans` 連鎖（直前結果の再利用）
- **セキュリティ**: `__import__("os").listdir()` が `NameError` で遮断
- ゼロ除算 → `ZeroDivisionError` 捕捉
- 結果整形（`180.0 → 180`、非整数は有効数字10桁）

### 未検証（実機依存）

- キーボード入力（`_read_key()`）
- 画面描画（色番号・`show()` 要否）
- SD カードへの履歴永続化（実機 `/sd` マウント挙動）

---

## 2. セットアップ（環境準備）

> 既存の PicoCalc MicroPython 環境がある前提。なければ LofiFren の手順に従う。

1. Pico 2W に `picocalc_micropython_pico2w.uf2`（LofiFren 版）を書き込み済みであること。
   - BOOTSEL 押しながら USB 接続 → `RPI-RP2` ドライブに UF2 をコピー。
2. `/modules/` に `picocalc.py` 等のドライバ群が配置済みであること。
3. SD カードを FAT32 でフォーマットし挿入（履歴永続化に使用）。
4. 開発は LofiFren の Dashboard（`python3 MicroPython/tools/dashboard.py`）または
   Thonny / mpremote を使用。`psephos.py` は SD の `/sd/py_scripts/` に配置し、
   メニューから起動する想定。

---

## 3. タスク（Phase 1：実機適合 = 完了 2026-06-18）

> 履歴的記録として残す。今後着手するのは §4 Phase 2 以降。

### T1. 実機ドライバ API の確定 ★最重要 — ✅ 完了

LofiFren / zenodante の実機 `picocalc.py` を読み、以下を**事実として**確認する。
推測で進めず、ソースまたは実機 REPL で確かめること。

1. **キーボード取得 API**
   - `_read_key()` は現状 `sys.stdin.read(1)` を仮置きしている。
   - 実機での正しいキー取得方法（`picocalc.keyboard` の有無、関数名、戻り値、
     ブロッキング/ノンブロッキングの別）を確認し、`_read_key()` を置換する。
   - Backspace / Enter / ESC / 矢印キーの実際のコードを実機で採取し、定数を更新する。

2. **色番号（LUT）**
   - `COL_FG=15 / COL_BG=0 / COL_DIM=8 / COL_ACC=11` が当該 LUT（既定 vt100）で
     意図通りの見た目になるか実機確認。ずれていれば調整する。

3. **`display.show()` の要否**
   - zenodante ドライバは Core1 が常時リフレッシュする設計のため、`show()` 呼び出しが
     不要、もしくは passive モード時のみ必要な可能性がある。
   - 実機で描画が出ない/ちらつく場合は `stopRefresh()/recoverRefresh()/show(0)` の
     扱いを DESIGN.md §8 と照合して調整する。

> **完了条件**: PicoCalc 実機で起動し、数式を打って Enter で結果が履歴に積まれ、
> ESC で抜けられること。

### T2. SD 永続化の実機確認 — ✅ 完了

- `/sd/psephos_history.txt` への追記・再起動後のロードを確認。
- SD 未挿入時の挙動は実機未検証（コード上 OSError 握りつぶし実装済）。
- **付帯発見**: LofiFren boot.py の SD 自動マウントが 500ms sleep で間に合わずコールドブート時に失敗することがある（`enhanced_sd.initsd()` 手動実行で復帰可）。

### T3. 入力可能文字の実機調整 — ✅ 完了

- `+ - * / ( ) . , ** %` 等の必須記号、`sin cos sqrt pi ans` 等の識別子をすべて入力可能と確認。
- 矢印キー・Enter・Backspace・ESC のキーコードを実機キャプチャ確定（`_read_key()` に反映済）。

---

## 4. タスク（Phase 2 以降：実機適合の完了後）

### Phase 2 — 履歴の活用 — ✅ 完了 (2026-06-18 実機検証)
- [x] 上下キーで履歴を遡り、選んだ式を入力行へ呼び出して再編集できる（編集中バッファは Down で最新位置を超えると復元）。
- [x] カーソル位置編集（左右キー）— アクセント色の下線でカーソル位置を表示。
- [x] 文字挿入はカーソル位置に、Backspace はカーソル直前の文字を削除。
- [x] Home `\x1b[H` / End `\x1b[F` 対応（実機キー存在時のみ動作）。

### Phase 3 — 機能拡張 — ✅ 完了 (2026-06-18 実機検証)
- [x] ユーザ定義変数（`x = 3` 形式でセッション中保持、予約名は上書き禁止）
- [x] 進数リテラル入力（`0xFF` `0b101` `0o777`）と `hex()/bin()/oct()/int()/float()` 関数
- [x] `ans` 多段参照（`ans` = 最新、`ans2`〜`ans10` = N 計算前）
- 比較演算子 (`==` `<=` `>=` `!=`) は代入として誤判定しない
- 攻撃を含む RHS は `_check_safe` で遮断（例: `x = __import__('os')`）

### Phase 4 — 仕上げ — ✅ 完了 (2026-06-18 実機検証)
- [x] 関数一覧ヘルプ画面（`help` + Enter で全画面表示、任意キーで戻る）
- [x] **日本語ヘルプ画像対応**（`tools/gen_help_image.py` で `assets/help_ja.bin` を生成 → SD `/sd/psephos_help.bin` に配置、`_show_help` が画像を blit。画像が無ければ ASCII テキストヘルプにフォールバック）
- [x] テーマ切替（`theme` で一覧、`theme <name>` で適用、5 種類: default/amber/green/cyan/invert）
- [x] 設定ファイル `/sd/psephos_config.txt`（theme/precision/history_max を永続化、起動時自動ロード）
- [ ] 履歴ファイルのローテーション（肥大化対策、Phase 5 候補に送り）

> 補足 1: LofiFren ファームウェアは `switchPredefinedLUT` API を提供せず、C ドライバ内に LUT が固定で焼き込まれている。標準 VT100 16 色パレットなので、`COL_FG/BG/DIM/ACC` のスロット番号を入れ替えるだけでテーマを実現した（C 側変更不要）。
>
> 補足 2: 実機 framebuf.MONO_HMSB は **公式 docs と挙動が異なり実質 LSB-first** だった（実機検証 2026-06-18）。Pillow の MSB-first 出力をそのまま blit すると 8 ピクセル毎にバイト内が反転して読めなくなる。`gen_help_image.py` は出力時にビット反転テーブル `translate()` を適用して対処。

---

## 5. コーディング規約・制約

- **MicroPython 互換のみ**。CPython 専用機能（f-string は可だが、型ヒント実行時評価や
  一部標準ライブラリは不可）に注意。`str.format()` を基本とする。
- **メモリ意識**: 履歴は `HISTORY_MAX`(=200) で上限。大きな中間リストを作らない。
- **例外で落とさない**: 評価・I/O は必ず捕捉し、画面メッセージに変換する。
- **セキュリティ維持**: `eval` の `__builtins__` 無効化を**絶対に外さない**。
  関数追加は `_build_namespace()` への明示追加のみ。
- **移植容易性維持**: ハード依存は `_read_key()` と描画ヘルパ（`_draw_text/_clear/_show`）に
  閉じ込める。ロジック層（`evaluate/History/_format`）にハード依存を混ぜない。

---

## 6. テスト指針

### ロジック（PC 上で実行可能、ハード不要）
- `evaluate()` の正常系（§1 の検証項目を回帰テスト化）。
- 異常系：構文エラー、未定義名、ゼロ除算、空入力。
- セキュリティ：`__import__` / `open` / `eval` 自体が遮断されること。
- `History` の add/load/clear（一時ファイルで検証）。

### 実機
- T1〜T3 の完了条件を手動確認（チェックリスト化推奨）。

---

## 7. 受け入れ基準（Definition of Done）

**Phase 1 完了 = 以下すべて**（2026-06-18 実機検証済）
- [x] 実機で起動し、数式入力 → Enter → 履歴に結果が積まれる（`1+4*4 = 17` 等で確認）
- [x] ESC でメニュー/REPL に戻れる
- [x] 再起動後も履歴が復元される（SD 永続化、`/sd/psephos_history.txt`）
- [x] エラー入力でクラッシュせずメッセージ表示（`1/0` で `division by zero`）
- [x] `eval` のセキュリティ防御が維持されている（識別子ホワイトリストで補強、DESIGN.md §7 参照）
- [ ] SD 未挿入でもクラッシュしない（コード上は OSError 握りつぶし実装済、実機未検証）

---

## 8. 未解決の質問（着手前に判断 or 総司へ確認）

1. キーボードの記号入力に Fn 等の修飾が要る場合、`**`（べき乗）の入力 UX をどうするか。
   → 代替として `^` を内部で `**` に変換する糖衣構文を入れるか検討（DESIGN 未記載・要判断）。
2. 履歴の表示順は「古い→新しい（最新が下）」で確定でよいか（現状実装）。
3. 起動メニュー名・アイコンの扱い（py_scripts での表示名）。

---

## 出典 / 参考

- DESIGN.md（本リポジトリ）— 設計の SSOT
- zenodante/PicoCalc-micropython-driver（display=framebuf サブクラス、LUT、show/refresh 仕様）:
  https://github.com/zenodante/PicoCalc-micropython-driver
- LofiFren/PicoCalc（Pico 2W 向け UF2、modules 構成、HW 表、Dashboard/MCP）:
  https://github.com/LofiFren/PicoCalc
- MicroPython `math`: https://docs.micropython.org/en/latest/library/math.html
