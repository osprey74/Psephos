# Psephos

> PicoCalc 向け関数電卓。ψῆφος（psephos = 計算に用いた小石）に由来。

## プロジェクト概要

ClockworkPi PicoCalc + Raspberry Pi Pico 2W で動作する MicroPython 製の関数電卓。Python 形式の数式（`sin(pi/6)+sqrt(2)` 等）を物理 QWERTY で直接入力し、結果と履歴を画面に積み、SD カード（`/sd/psephos_history.txt`）へ永続化する。

詳細仕様は [DESIGN.md](DESIGN.md)、実装ハンドオフは [HANDOFF_psephos.md](HANDOFF_psephos.md) を参照。両者が一次情報。

## ターゲットデバイス

- **本体**: ClockworkPi PicoCalc
- **MCU**: Raspberry Pi Pico 2W (RP2350)
- **画面**: 320×320 ILI9488、4bit LUT、6×8 フォント（COLS=53 / ROWS=40）
- **キーボード**: I2C MCU @ 0x1F
- **SD**: SPI0、FAT32、`/sd` マウント
- **ファーム**: LofiFren / zenodante 系 MicroPython

## リポジトリ構成

```
Psephos/
├── CLAUDE.md            # 本ファイル（プロジェクト設定）
├── DESIGN.md            # 設計の SSOT
├── HANDOFF_psephos.md   # 実装ハンドオフ（Phase 1 実機適合タスク）
├── README.md / README.ja.md
├── LICENSE              # MIT
└── psephos.py           # 本体（単一ファイル MVP）
```

> 単一ファイルアプリ。ハード依存は `_read_key()` と描画ヘルパ（`_draw_text/_clear/_show`）に集約し、ロジック層（`evaluate/History/_format`）はハード非依存に保つこと。

## 技術スタック

- **言語**: MicroPython（CPython 互換のサブセット）
- **依存**: `math`、`picocalc.display`（framebuf サブクラス）、`picocalc.keyboard` 相当
- **PC フォールバック**: `picocalc` が import できない環境では端末入出力で動作するため、ロジック検証は PC でも可能

## 開発コマンド

実機転送は `mpremote` または LofiFren Dashboard を使用：

```powershell
# REPL 接続
mpremote connect COM<n> repl

# ファイル転送（SD の py_scripts/ 配下に配置想定）
mpremote connect COM<n> cp psephos.py :/sd/py_scripts/psephos.py

# PC フォールバック実行（ロジック検証用）
python psephos.py
```

## アーキテクチャ原則

- **ハード抽象化の堅持**: 機種・ファーム差は `_read_key()` および描画ヘルパに閉じ込める。ロジック層に `import picocalc` を漏らさない。
- **eval セキュリティ維持**: `eval(expr, {"__builtins__": {}}, local)` の第2引数空 dict 化を**絶対に外さない**。関数追加は `_build_namespace()` への明示追加のみ。
- **例外で落とさない**: 評価・I/O は必ず捕捉し、画面メッセージに変換。クラッシュ＝バグ。
- **メモリ意識**: 履歴は `HISTORY_MAX`(=200) で上限。大きな中間リストを作らない。
- **MicroPython 互換**: CPython 専用機能（型ヒント実行時評価、特定の標準ライブラリ）を避ける。`str.format()` を基本とし、f-string は限定的に。

## Task Management

- **task_file**: [HANDOFF_psephos.md](HANDOFF_psephos.md) §3〜4 のフェーズ別タスク、および §7「受け入れ基準」のチェックボックス
- **done_marker**: `[x]`
- **progress_summary**: false

## Documentation

- **docs_to_update**: `README.md` / `README.ja.md`、`DESIGN.md`、`HANDOFF_psephos.md`
- **doc_pairs**: `README.md` ⇔ `README.ja.md`
- **primary_spec**: [DESIGN.md](DESIGN.md)（設計の SSOT、仕様変更時はこちらを正とする）

## Versioning

- **version_files**: `psephos.py` の先頭コメント（実装時に `__version__` を追加検討）
- **mono_version**: true（単一ファイルアプリのため）
- **cargo_lockfile**: false（Python プロジェクト）

## CI/CD

- **cicd**: false（未整備）
- **cicd_platform_candidate**: GitHub Actions（PC フォールバック上のロジック回帰テスト用途）

## SNS

- **sns_accounts**:
  - Bluesky: `@osprey74.bsky.social`（実機適合完了後にリリース告知予定）

## 制約事項・既知の未確認事項

HANDOFF_psephos.md §3 の Phase 1 タスクが未完了：

- `_read_key()` の実機 API 適合（`sys.stdin.read(1)` は仮置き）
- 色番号（`COL_FG=15 / COL_BG=0 / COL_DIM=8 / COL_ACC=11`）の LUT 検証
- `display.show()` の要否（Core1 常時リフレッシュ設計のため不要な可能性あり）
- SD `/sd/psephos_history.txt` への追記と起動時ロードの実機確認
- 数式に必要な記号（`+ - * / ( ) . , ** %`）の PicoCalc キーボードでの入力可否

> Phase 1 完了まで Phase 2 以降に着手しないこと（HANDOFF §0）。

## 参照ドキュメント

- [DESIGN.md](DESIGN.md) — 設計 SSOT
- [HANDOFF_psephos.md](HANDOFF_psephos.md) — 実装ハンドオフ
- [ClockworkPi PicoCalc 公式](https://www.clockworkpi.com/picocalc)
- [zenodante/PicoCalc-micropython-driver](https://github.com/zenodante/PicoCalc-micropython-driver)
- [LofiFren/PicoCalc](https://github.com/LofiFren/PicoCalc)
- [MicroPython `math`](https://docs.micropython.org/en/latest/library/math.html)
