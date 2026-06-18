# Psephos

> ClockworkPi PicoCalc 向け関数電卓。名称はギリシャ語の **ψῆφος**（psephos = 計算に用いた小石）に由来します。

[English README](README.md)

- **対象ハード**: ClockworkPi PicoCalc + Raspberry Pi Pico 2W (RP2350)
- **実行環境**: MicroPython（LofiFren / zenodante 系ファームウェア）
- **ライセンス**: MIT
- **状態**: MVP — PC フォールバック上でコアロジック検証済み、実機適合は未完了

## 何ができるか

PicoCalc の物理 QWERTY とレトロな画面を、プログラマブル関数電卓に変えます。Python 形式の数式を直接打ち込めます。

```
> sin(pi/6) + sqrt(2)
> 2 ** 10
> degrees(pi)
> ans * 1.5
```

差別化ポイントは **計算履歴が画面に積み上がり、SD カードへ永続化される** ことです。`/sd/psephos_history.txt` に追記され、電源を切っても残り、起動時に復元されます。

## 対応関数・定数

`_build_namespace()` で明示的に許可された名前のみ呼び出せます。`eval` は `{"__builtins__": {}}` でサンドボックス化しています。

- **三角関数**: `sin cos tan asin acos atan atan2`
- **指数・対数**: `exp log log10 sqrt pow`
- **端数・絶対値**: `floor ceil fabs abs round`
- **角度変換**: `radians degrees`
- **定数**: `pi e tau`
- **ユーティリティ**: `min max ans`

## ドキュメント

- [DESIGN.md](DESIGN.md) — 設計の SSOT（アーキテクチャ、セキュリティ、画面レイアウト、データ設計）
- [HANDOFF_psephos.md](HANDOFF_psephos.md) — 実装ハンドオフ（Phase 1 実機適合タスク）

## ロードマップ

- **Phase 1**（現在）— 実機適合: キーボード API、LUT 色番号、SD 永続化の検証
- **Phase 2** — 上下キーで履歴呼び出し・再編集、左右キーでカーソル編集
- **Phase 3** — ユーザ定義変数、16進/2進の入力・表示
- **Phase 4** — テーマ切替、関数リファレンス画面、設定ファイル、履歴ローテーション

## 参考

- [ClockworkPi PicoCalc 公式](https://www.clockworkpi.com/picocalc)
- [zenodante/PicoCalc-micropython-driver](https://github.com/zenodante/PicoCalc-micropython-driver)
- [LofiFren/PicoCalc](https://github.com/LofiFren/PicoCalc)
- [MicroPython `math` モジュール](https://docs.micropython.org/en/latest/library/math.html)
