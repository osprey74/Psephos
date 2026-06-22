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

ただしメモリ断片化の **根本原因（chrome.bin 51KB）には触れない**。Phase 6-A と組み合わせて初めて完成。

---

## 3. 実装順序（推奨）

1. **Phase 6-A: 自前ランチャ** — `main.py` を Psephos 起動専用に書換え、`machine.soft_reset()` で終了
   - 既存 `main.py`（`from py_run import main_menu`）は `main_lofifren.py` 等で退避
   - Psephos の `main()` 末尾 finally に `_os.dupterm` 復元の後に `machine.soft_reset()` 追加
   - 動作確認：LofiFren ランチャ経由ではなく直接起動するか、2 回起動 → メモリリーク無いか

2. **Phase 6-B: bitmap フォント** — `_FONT_8X8` を組み込み、`_draw_text_*` 系を置換
   - データ生成スクリプト（PC 側）を 1 つ作って `_FONT_8X8 = (...)` を psephos.py に埋め込む
   - `_draw_char_8x8(x, y, ch, color, scale)` を実装、`_draw_text_2x`/`_3x`/`_hist` から呼出
   - 共有 tmp framebuf は不要になるので削除
   - 動作確認：表示は変わらず、複雑式の描画が体感で速くなっているか

3. **Phase 5: plot 実装の復元** — 2026-06-22 セッションで `git stash@{0}` に避難済の plot 実装を取り出して適用
   - `git stash pop stash@{0}` で復元
   - Phase 6-A/B 上に重ねて動作確認

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
