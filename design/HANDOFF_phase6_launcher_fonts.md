# HANDOFF: Phase 6 — 自前ランチャ + プリベイク bitmap フォント

> 2026-06-22 セッションで判明したメモリ断片化問題と、その対策方針をまとめる。
> 翌セッション以降で実装を進めるためのリファレンス。

## 0. 背景（2026-06-22 セッションの結論）

### 観測された不具合

LofiFren ランチャから Psephos を **2 回目以降に起動**すると `Psephos exited. Press Enter for menu...` がすぐに表示され、まともに動かない。さらに、`alpha+omega+sigma+delta`（4 Greek glyph）や `alpha*omega+23` を続けて評価するとフリーズする。

電源完全 OFF（バッテリ取外）後の初回起動は正常。1 セッション内であれば数回の計算は問題なく回ることもある。

### 切り分けで判明した事

- `i2c.scan() == ['0x1f']`：キーボード MCU は応答している。
- `gc.collect()` 直後の `gc.mem_free()` は ~330 KB（健全）。
- しかし `bytearray(50000)` の確保が `MemoryError` で失敗：**RAM 全体は余裕があっても、連続した 50 KB を確保できない＝メモリ断片化が深刻**。
- chrome.bin の framebuf 確保（`bytearray(51200)`）が再起動 2 回目以降で失敗するため、`_show_big_calc` 等の途中で例外が出て即終了している（症状 B：入力行のままフリーズに見える）。
- 弊機（Claude）の `picocalc_exec` 介入は `mpremote` の Ctrl-C で動作中アプリを kill し、dupterm も deactivate するため、デバッグ自体が断片化を悪化させる。

### 根本原因

LofiFren ランチャ（`/modules/py_run.py`）は `exec(script_content, script_globals)` でアプリを起動する。Psephos の動作中に確保されたオブジェクトは `script_globals` 経由・closure 経由で各所に参照を残し、`exec` が return した後に GC を待っても **完全には回収しきれない**。chrome.bin (51 KB) や `_GLYPH_*` データ、`_CasBox` クロージャが残骸として漂い、次回起動の連続領域確保を阻害する。

`gc.collect()` を呼べば「free byte 総数」は回復するが、断片化したフリーリストはそのままで、大きな連続領域確保は通らない。

---

## 1. Phase 6-A: 自前ランチャ実装（最優先・効果大）

### 目的

LofiFren ランチャから切り離し、Psephos 起動・終了で MicroPython を確実にクリーン状態へ戻す。

### 設計

`/main.py` または `/sd/py_scripts/psephos_launcher.py` 相当の最小ランチャを用意する。

```python
# psephos_launcher.py (案)
import sys
sys.path.insert(0, '/sd/py_scripts')

import psephos
psephos.main()

# 終了時のクリーンアップ
import machine
machine.soft_reset()
```

ポイント：
- `machine.soft_reset()` で **MicroPython 自体を再起動** → boot.py からやり直し → ヒープ完全クリーン
- 起動オーバーヘッドは boot.py 込みで ~2 秒程度（PicoCalc 実機実測：splash screen が出てから launcher 表示まで）
- 利用者から見た「ランチャに戻る」感覚は失われるが、Psephos が主用途のときは違和感ない

### 起動経路の選択肢

| 案 | 内容 | 長所 | 短所 |
|---|---|---|---|
| A | `boot.py` 末尾を変更して直接 `psephos.main()` 呼出 | ランチャ画面省略で起動最速 | 他アプリ利用時に boot.py 編集が必要 |
| B | `main.py` を独自版に差し替え | LofiFren `from py_run import main_menu` を呼ばずに Psephos 直接起動 | 既存ランチャは「main.py を別名退避」等で残せる |
| C | LofiFren ランチャから `psephos_launcher.py` を選択した時点で psephos.main()+soft_reset を実行 | LofiFren ランチャを残しつつ問題回避 | 初回はランチャ経由で 1 ホップかかる |

**推奨：案 B**。`main.py` を Psephos 直接起動版にし、`main_lofifren.py` 等で LofiFren を退避保存。Psephos 終了時に soft_reset → boot.py → main.py（Psephos）の自然なループ。

### 終了動作

Psephos の `main()` の終了パスで `machine.soft_reset()` を呼ぶ。ただし PC フォールバック (`_HW=False`) では何もしない。

```python
def main():
    try:
        _main_run()
    finally:
        if _HW:
            try:
                import os as _os
                _os.dupterm(_DUPTERM_PREV[0])
            except Exception:
                pass
            # 自前ランチャ運用時はここで soft_reset。LofiFren 経由時は呼ばない。
            # 環境変数や config フラグで切替えられるとよい。
            ...
```

### 課題

- ESC で「終了」したつもりが soft_reset で勝手に再起動されると初学者は驚く → 「Psephos: type 'exit' to shutdown」など明示ヘルプ
- soft_reset は PC からの mpremote セッションを切る → デバッグ時は別経路
- バッテリ運用時は再起動でちらつき発生 → 受容範囲か要確認

---

## 2. Phase 6-B: プリベイク bitmap フォント

### 目的

`_draw_text_2x` / `_draw_text_3x` / `_draw_text_hist` で毎回 framebuf に描画してピクセル走査する間接方式を、**事前にビットマップ化した文字データを直接 `fill_rect` または `pixel` 書込み**する方式に置き換える。

### 現状の問題

- ASCII 文字 1 文字描画につき：
  1. `bytearray((pad_w * 8) // 8)` 確保（または共有 tmp_fb 利用）
  2. `framebuf.text(s, 0, 0, 1)` でラスタライズ
  3. 8×len(s) ピクセル走査
  4. 各 set ピクセルにつき `_display.pixel` or `_display.fill_rect` 呼出
- 1 行（20 文字程度）描画で 100ms 級になることもある（実測ではないが、累計の遅延の主因）
- メモリ確保が頻発（共有 tmp_fb 化済みだが、関数 frame で大量の int を生成）

### 設計

各サイズごとに、ASCII 95 文字（0x20..0x7E）のビットマップを **モジュール定数として保持**：

```python
# 8x8 標準 ASCII を 1 度だけラスタライズして 8 byte/char で持つ
_FONT_8X8 = (
    # ' ' (0x20)
    b'\x00\x00\x00\x00\x00\x00\x00\x00',
    # '!' (0x21)
    b'\x18\x18\x18\x18\x18\x00\x18\x00',
    ...
)

# 描画は bytes と座標から直接 set ピクセル展開
def _draw_char_8x8(x, y, ch, color, scale=1):
    code = ord(ch)
    if code < 0x20 or code > 0x7E:
        return  # 非ASCII は無視
    bitmap = _FONT_8X8[code - 0x20]
    for row in range(8):
        byte = bitmap[row]
        for col in range(8):
            if byte & (0x80 >> col):
                if scale == 1:
                    _display.pixel(x + col, y + row, color)
                else:
                    _display.fill_rect(x + col*scale, y + row*scale, scale, scale, color)
```

### 必要サイズ

| 用途 | サイズ | scale | データ量 |
|---|---|---|---|
| 履歴 | 8×8 (or 6×8) | 1 | 95 × 8 = 760 byte |
| 入力行 / CAS テキスト | 16×16 | 2（同データから 2×2 ブロック展開） | 共用、追加ゼロ |
| 大きな結果 | 24×24 or 32×32 | 3 or 4（同データから N×N ブロック展開） | 共用、追加ゼロ |

つまり **1 つの 8×8 ビットマップから 3 サイズすべてを生成**できるので、データ量は 760 byte で済む。

### 6×8 ハードウェアフォントを使うか

LofiFren の `picocalcdisplay.drawTxt6x8` はハードウェア（C 実装）で高速。履歴で 1× サイズはこれを継続使用するのも選択肢。ただし scale 倍にすると C 関数を使えなくなる。

**推奨：8×8 統一**。MicroPython 組み込みの 8×8 フォント相当のビットマップを 1 セット用意し、scale で 16/24/32 を生成。`drawTxt6x8` は使わない。

### 課題

- 8×8 フォントの bitmap データを取得する手段：
  - MicroPython の framebuf 内部フォント（C 実装）をエクスポートしたい
  - 簡易：起動時に framebuf に各文字を 1 度だけ描いて bytes を抜き出して `_FONT_8X8` を構築（メモリは 760 byte で済む）
- 日本語フォントは別レイヤ（`memory:japanese-font-sources.md` 参照）

### 効果見込み

- 描画コール数を 1/3〜1/5 に削減（推測）
- framebuf 一時確保なし → メモリ断片化への寄与を完全排除
- ピクセル走査回数を 1/8 に（バイト単位で読み bit シフトのため）

---

## 2.5 Phase 6-C: chrome.bin 廃止して動的描画（最大効果）

### 目的

**chrome.bin (51,200 byte の連続領域確保) を完全に廃止**し、起動毎に `hline` + プリベイク bitmap フォントで chrome を描き直す。これがメモリ断片化問題の根本解決。

### 設計

chrome.bin の内容は実質：

- 上部 17 px：水平線（ACC 色、上端 1 px）+ "PSEPHOS - programmable scientific calculator" テキスト + 水平線（ACC 色、下端）
- 下部 4 px：水平線 1 本（ACC 色、下端 2 px）
- 中央 299 px：全て BG（黒）

すべて Python 側で表現可能：

```python
def _draw_chrome():
    if not _HW:
        return
    # 上部: 水平線 + テキスト + 水平線
    _display.hline(0, 0, SCREEN_W, COL_ACC)              # 上端
    _draw_text_8x8(2, 4, "PSEPHOS", COL_ACC)             # 56 px wide
    _draw_text_8x8(58, 4, " - programmable", COL_FG)
    _draw_text_8x8(58 + 15*8, 4, " sci calc", COL_FG)    # 行末調整
    _display.hline(0, 15, SCREEN_W, COL_ACC)             # テキスト下
    # 下部: 水平線
    _display.hline(0, SCREEN_H - 2, SCREEN_W, COL_ACC)
```

- 上記コード自体は 20〜30 行で済む
- データ量増加は **0 byte**（フォントは Phase 6-B で既にロード済）
- テーマ切替は `COL_ACC` / `COL_FG` の参照だけで済む（パレット計算不要）

### 効果

- 起動毎の `bytearray(51200)` 確保が消える → **断片化の最大原因が消える**
- SD I/O が初期化時と help 終了時に発生していたが不要に → 起動高速化
- `_maybe_load_chrome` / `_redraw_chrome` / `_chrome_buf` 周辺コードを大幅削除可能
- chrome.png / chrome.bin / 関連 design handoff は「将来オプション」として残しておく

### help 画像との関係

ヘルプ画面は日本語が含まれるため、引き続き PNG / bin 方式（`psephos_help_p1.bin` / `_p2.bin`）で運用。ただし：

- help コマンド実行時のみ 51 KB 確保 → help 終了時に即解放（gc.collect）
- 単発確保なので、chrome.bin と違い「常駐し続けて断片化を悪化させる」ことはない
- 日本語フォントをいずれ実装すれば、help も動的描画化可能（将来課題）

### Phase 6-C の課題

- "PSEPHOS - programmable scientific calculator" 全文を 8×8 = 8 px × 44 文字 = 352 px で書くと 320 px に収まらない。短縮形 or 6×6 用意 or 7×6 圧縮版が必要：
  - 案 1: `"PSEPHOS - prog. scientific calculator"` 短縮（41 文字 × 8 = 328、まだ超過）
  - 案 2: 6×8 フォントを別途プリベイクしてヘッダ専用に使う
  - 案 3: PSEPHOS + ロゴアイコン的な絵文字 + 短いサブタイトル
  - 案 4: 上部 17 px を 24 px に拡げて 16×16 フォントで "PSEPHOS" だけ大きく
- 既存 chrome.png のレイアウト感を踏襲するかは要検討（claude_design_handoff_chrome.md 参照）

---

## 3. 実装状況（2026-06-24 現在）

### 完了 ✅

- **Phase 6-C: chrome.bin 廃止 + 動的描画** — `_maybe_load_chrome` をレイアウト定数更新のみのスタブに、`_redraw_chrome` を `hline + _display.text` (6×8 hardware) に置換。51KB framebuf 確保ゼロ。
- **Phase 6-D: help bin 廃止 + 動的描画** — `_HELP_LINES` を `_HELP_SECTIONS = [("Functions:", [...lines...]), ...]` 形式に変更。`_show_help` はセクション見出し (ACC) + インデント本文 (FG) を 6×8 hardware で描画。51KB 確保なし。
- **Phase 6-B step 1: フォントロードインフラ** — `_FONT_CACHE` + `_get_font(name)` 遅延ロード + `_draw_text_bm(x, y, s, color, font_name)` + `_draw_text_p1/p2/p3` ラッパ追加。9 種の Terminus/Spleen bitmap モジュールを `/sd/py_scripts/` に配置済（変換器: `fonts/bdf_to_py.py`、サンプラ: `fonts/font_sampler.py` / `fonts/psephos_preview.py`）。
- **`_diag` ログ機構** — `picocalc.usb_debug` 優先 + SD フォールバック。メモリ確保失敗時も診断可能。

### 未統合（重要）

**Phase 6-B step 1 は infra のみで、実際の描画は依然として旧フォント** を使っている：

| 描画箇所 | 現状 | 意図 |
|---|---|---|
| chrome 上部ヘッダ | 6×8 hardware `_display.text` | Terminus 12×24 (Pattern 1) |
| 計算履歴 | 6×8 hardware | Terminus 8×16 (Pattern 2) |
| 入力欄 | 16×16 (8×8 → 2× soft scale) | Terminus 12×24 (Pattern 1) |
| CAS 数式テキスト | 16×16 (8×8 → 2× soft scale) | Terminus 12×24 (Pattern 1) |
| CAS 指数 | 8×8 framebuf | Terminus 8×16 (Pattern 2) |
| ヘルプ本文 | 6×8 hardware | Terminus 8×16 (Pattern 2) |
| big_calc 結果行 | 16×16 (8×8 → 2× soft scale) | Terminus 16×32 (Pattern 3) |
| Greek glyph | 16×16 → 32×32 custom render | 維持 (Pattern 3 と同サイズ) |

未統合の理由：起動シーケンス中に Terminus 12×24 (20 KB) / 16×32 (27 KB) を import すると、LofiFren ランチャの抱える状態 + psephos.py モジュールロード (~67 KB) と合算してメモリ断片化を悪化させ、起動失敗 (`MemoryError: 51200 byte`) を招くため。

### 残課題

LofiFren ランチャ経由で **複数回の計算を繰返すと途中でフリーズ** する症状あり（closure 累積・framebuf 一時バッファ・履歴データなどの蓄積が原因）。Phase 6-A の実装で根治予定。

## 4. 次セッション以降の実装順序（方針: Aで進める）

総司様の方針：**Phase 6-A を先に実装してメモリを確保してから、Phase 6-B 後半（Terminus 統合）を段階導入**する。

1. **Phase 6-A: 自前ランチャ + `machine.soft_reset()`** — 最優先
   - `/main.py` を Psephos 起動専用に書換え（現在の LofiFren `main.py` は `main_lofifren.py` 等で退避）
   - Psephos の `main()` 末尾 finally で `machine.soft_reset()` 呼出
   - 起動毎にヒープ完全クリア → 連続 51KB 確保問題と closure 累積を根治
   - 動作確認：5 回連続起動でメモリリーク無し、繰返し計算でフリーズしない

2. **Phase 6-B step 2: Terminus フォント 1 つずつ段階導入**
   - 例: chrome 上部ヘッダだけ Terminus 12×24 に切替 → 動作確認 → 次へ
   - 失敗したら 1 つ前のステップに戻す
   - 完成形：chrome / 入力 / CAS / 履歴 / 指数 / ヘルプ / big_calc が Terminus 各サイズで描画
   - フォント data は遅延ロード (`_get_font`) で必要時のみ heap 消費

3. **Phase 5: plot 実装の復元** — Phase 6-A 完了後
   - `git stash list` で stash@{0} を確認
   - `git stash pop stash@{0}` で復元 (conflict 解消必要かも)
   - Phase 6 完成形の上に重ねて動作確認

## 5. 既知の警告 / 注意

- `_FONT_CACHE` は無制限に成長する。Phase 6-A 実装で soft_reset により都度クリアされる前提。
- `_draw_text_p1/p2/p3` は `_get_font` 失敗時 (PC 環境や import エラー) は無音で何もしない。
- `picocalc.usb_debug` は boot.py で `_usb = sys.stdout` から束ねたもの。dupterm None 後も USB へ流れる。
- diag 関数の呼出はそのまま残置。本番運用前に grep して削除推奨（パフォーマンス影響あり）。

---

## 4. 関連ファイル

- `psephos.py`：本体（main / _main_run / _show_big_calc / _draw_text_*）
- `/modules/py_run.py`：LofiFren ランチャ。`exec(script_content, script_globals)` で起動。
- `/boot.py`：起動順序（PicoDisplay → PicoKeyboard → dupterm 登録 → main.py 呼出）。
- `/main.py`：現在は `from py_run import main_menu; main_menu()`。Phase 6-A で書換対象。

## 5. 参考メモリ

- `memory:lofifren-app-integration.md` — terminal.readinto / dupterm / 起動時クリア
- `memory:picocalc-mcp-pitfalls.md` §5 — 対話中 exec 禁止（断片化悪化）
- `memory:psephos-phase5-candidates.md` — Phase 5 の Plot 等候補

---

## 6. 受け入れ基準（Phase 6 完了時）

- [ ] Psephos を 5 回連続起動 / 終了してもメモリ断片化で起動失敗しない
- [ ] 各起動で chrome.bin (51 KB) が初回と同じ速度で読込める
- [ ] `_show_big_calc` の描画時間が 1 文字あたり 1ms 以下（プリベイクフォント効果）
- [ ] 既存の LofiFren ランチャは復元可能な形で退避されている
- [ ] DESIGN.md / README.md の起動方法説明を新方式に合わせて更新
