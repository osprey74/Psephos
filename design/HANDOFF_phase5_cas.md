# Phase 5: CAS（Computer Algebra System）視覚表示 — HANDOFF

> Psephos に **記号計算と数式の視覚レンダリング** 機能を追加するための実装計画。
>
> - 作成日: 2026-06-21 (JST)
> - 対象: PicoCalc + Pico 2W + MicroPython 1.25 (LofiFren ファーム)

---

## 0. 概要

通常入力（テキスト・数値計算）はそのまま維持し、ユーザが必要なときだけ **`cas` コマンド** で過去の式を呼び出して「典型的な数式記法（分数・√・指数等）」で表示する。

```
> sqrt(8-3)/2*9
= 10.0623...                ← 通常の数値計算結果
> cas ans                   ← 直近の式を CAS で表示
                            ← 画面全体が CAS 描画に切替
                            ← 任意キーで通常画面へ戻る
```

---

## 1. スコープ（3 Tier）

### Tier 1: 視覚レンダリング基盤（MVP）

- 入力式の文字列を AST（抽象構文木）に**パース**
- AST を **数式記法でレイアウト** （分数・√ 等の位置決め）
- framebuf に**ハイブリッド描画** （6×8 ASCII グリフ + 線描画）
- `cas <ref>` コマンドで履歴の式を呼び出して表示
- **記号簡約は行わない**（式そのものをそのまま視覚化）

例: `sqrt(8-3)/2*9` 入力 → CAS 表示は以下：

```
   _______
  √ 8 - 3
  ─────── × 9
     2
```

### Tier 2: 基本的記号簡約

- 算術定数畳み込み（`8 - 3` → `5`）
- sqrt の素因数分解（`sqrt(20)` → `2√5`）
- 分数の約分（`4/8` → `1/2`、GCD ベース）
- 定数のシンボリック保持（`pi`、`e` は値化しない）

Tier 2 適用後の例: `sqrt(8-3)/2*9` → `9√5 / 2`

### Tier 3: 拡張記号計算（将来課題）

- 多項式展開・因数分解
- 指数法則（`x^2 * x^3` → `x^5`）
- 三角関数恒等式
- 簡単な微積分

---

## 2. アーキテクチャ

### 2.1 モジュール構成

すべて `psephos.py` 内に実装（モノファイル方針維持）。論理的に以下 4 つの責務に分離：

| 領域 | 関数 / クラス | 責務 |
|---|---|---|
| Tokenizer | `_tokenize(expr) -> list[(kind, value)]` | 文字列 → トークン列 |
| Parser | `_parse_math(tokens) -> Node` | トークン列 → AST |
| Layout | `Node.layout() -> Box` | AST → bounding box ツリー |
| Renderer | `Box.render(x, y, color)` | bounding box → framebuf 描画 |

### 2.2 AST ノード型

```python
class _N:                          # base class
    pass

class Num(_N):                     # 数値リテラル
    def __init__(self, value): self.value = value

class Var(_N):                     # 識別子 (変数、定数 pi/e、ans 系)
    def __init__(self, name): self.name = name

class BinOp(_N):                   # 二項演算
    def __init__(self, op, l, r):
        self.op = op               # '+' '-' '*' '/' '**' '%'
        self.l = l
        self.r = r

class UnaryOp(_N):                 # 単項演算
    def __init__(self, op, x):
        self.op = op               # '+' '-'
        self.x = x

class Call(_N):                    # 関数呼び出し
    def __init__(self, name, args):
        self.name = name           # 'sin' 'sqrt' etc.
        self.args = args           # list[_N]
```

### 2.3 パーサ（再帰下降）

文法（既存 `_check_safe` で許可される範囲を踏襲）:

```
expr    -> add
add     -> mul (('+' | '-') mul)*
mul     -> pow (('*' | '/' | '%') pow)*
pow     -> unary ('**' pow)?              # 右結合
unary   -> ('+' | '-') unary | atom
atom    -> NUMBER
         | NAME
         | NAME '(' arglist? ')'
         | '(' expr ')'
arglist -> expr (',' expr)*
```

数値リテラル: 10進浮動小数 / 16進 `0x` / 2進 `0b` / 8進 `0o` / 指数 `1.5e-10` をサポート。

セキュリティ: パースは行うが評価はしない（`_check_safe` のような遮断は再ロジック不要、CAS 表示は計算しないので安全）。

### 2.4 レイアウトエンジン

各 AST ノードは `Box` を返す:

```python
class Box:
    def __init__(self, w, h, baseline):
        self.w = w                 # 幅 (px)
        self.h = h                 # 高さ (px)
        self.baseline = baseline   # ボックス内の「中心線」位置 (px from top)
                                    # 二項演算で上下行揃えに使う
    def render(self, x, y, color):
        # サブクラスごとに実装
        pass
```

ノード種類ごとの `Box` 戻り値:

#### Num / Var

- `w = len(text) * CHAR_W`
- `h = CHAR_H`
- `baseline = CHAR_H // 2`
- `render`: `_display.text(text, x, y, color)`

#### BinOp `+ - * %`

- 子 = (lb, rb) = (l.layout(), r.layout())
- 演算子記号 + 周囲スペース = ~3 文字相当 (18 px)
- `w = lb.w + 18 + rb.w`
- `h = max(lb.h, rb.h)`
- `baseline = max(lb.baseline, rb.baseline)`
- `render`: 左 box → スペース → 演算子文字 → スペース → 右 box（baseline 揃え）

#### BinOp `/` （分数）

- 子 = (n, d) = (l.layout(), r.layout())
- 横棒の幅 = `max(n.w, d.w) + 4`
- `w = bar_w`
- `h = n.h + 1 + d.h`  # バー 1 px
- `baseline = n.h`     # バーの位置
- `render`:
  - num を中央寄せで上に
  - hline でバー
  - denom を中央寄せで下に

#### BinOp `**` （指数）

- 子 = (b, e) = (l.layout(), r.layout())
- 指数は **半文字分上にオフセット** （e は同じ 6×8 で描画）
- `w = b.w + e.w`
- `h = b.h + e.h // 2`
- `baseline = e.h // 2 + b.baseline`
- `render`:
  - b を下基準で描画
  - e を b の右上に半文字上オフセットで描画

#### UnaryOp

- 子 = (x,) = (x.layout(),)
- `w = CHAR_W + x.w`
- `h = x.h`
- `baseline = x.baseline`
- `render`: `op` 文字 + x

#### Call `sqrt(x)`

- 子 = (xb,) = (x.layout(),)
- 根号本体（√ シンボル）幅 = `SQRT_W` (6 px 程度)
- 上線 = xb の幅ぶん
- `w = SQRT_W + xb.w + 2`
- `h = xb.h + 2`  # オーバーライン 1 px + 余白
- `baseline = (h) // 2`
- `render`:
  - `(x, y) ~ (x + SQRT_W, y + h)` の範囲に √ グリフを線描画
  - `(x + SQRT_W, y)` から hline (xb.w + 2) ピクセル
  - xb を `(x + SQRT_W + 1, y + 2)` に描画

#### Call `abs(x)`

- 子 = (xb,) = (x.layout(),)
- `w = 2*ABS_W + xb.w`  # 縦棒 1 px + 余白 + xb + 余白 + 縦棒
- `h = xb.h`
- `baseline = xb.baseline`
- `render`:
  - 左端に vline
  - xb を間に
  - 右端に vline

#### Call 一般（sin, cos, log, ...）

- 引数 = ab1, ab2, ... = [a.layout() for a in args]
- 関数名文字数 + ( + 引数群 (カンマ区切り) + )
- `w = len(name) * CHAR_W + CHAR_W + sum(arg.w) + CHAR_W * (len(args)*2 - 1) + CHAR_W`
  - 例: `sin(x)` = "sin" + "(" + x.w + ")" = 3*6 + 6 + x.w + 6
- `h = max(child.h for child in ab)`
- `baseline = max(...)`
- `render`: 関数名文字列 + 開き括弧 + 引数群（区切り「, 」）+ 閉じ括弧

### 2.5 レンダラ補助

`framebuf` プリミティブで描く構造要素:

```python
# 分数バー
_display.hline(x, y, width, color)

# √ シンボル（簡略な描画例、最終調整必要）
def _draw_sqrt_glyph(x, y, height, color):
    # 左下から右上への対角線
    for i in range(SQRT_W):
        _display.pixel(x + i, y + height - 1 - i, color)
    # 右上から少し上に伸ばす
    _display.vline(x + SQRT_W - 1, y, 2, color)

# 絶対値の縦棒
_display.vline(x, y, height, color)
```

### 2.6 `cas` コマンドハンドラ

main() の Enter ハンドラ内（`help` `theme` `clear` と同列）:

```python
if expr == "cas" or expr.startswith("cas "):
    ref = expr[4:].strip() if expr.startswith("cas ") else "ans"
    # `cas ans` / `cas ans2` / 'cas ansN' を解決
    src = _resolve_history_ref(ref, history)
    if src is None:
        message = "Usage: cas ans[N]"
    else:
        _show_cas(src)               # 描画 + 任意キー待ち
        _redraw_chrome()             # 戻ったら chrome 復元
        message = ""
    buf = ""
    cursor = 0
    render(history, buf, cursor, message)
    continue
```

`cas` は予約名に追加（`_COMMANDS` に `"cas"` を追記）。

`_resolve_history_ref(ref, history)`:
- `ref == "ans"` または `"ans1"` → 最新の history.items[-1][0]
- `"ans2".."ans10"` → history.items[-N][0]
- それ以外 → None

### 2.7 `_show_cas(expr_str)`

```python
def _show_cas(expr_str):
    try:
        node = _parse_math(expr_str)
    except Exception as e:
        # パースエラー表示
        return
    box = node.layout()
    # 動的領域に中央配置
    _clear_active()
    cx = (SCREEN_W - box.w) // 2
    cy = (_ACTIVE_TOP + _ACTIVE_BOTTOM - box.h) // 2
    box.render(cx, cy, COL_FG)
    # 元式テキストも下部に薄く表示（リファレンス）
    _draw_text(0, _ACTIVE_BOTTOM - CHAR_H, expr_str[:COLS], COL_DIM)
    _show()
    # 任意キーで戻る
    while True:
        k = _read_key()
        if isinstance(k, tuple):
            continue
        break
```

---

## 3. 実装フェーズ

### Phase 5a — Tier 1 MVP（本 HANDOFF の対象）

1. `_tokenize` 実装
2. AST 5 ノード型 + `_parse_math` 実装
3. `Box` クラス + 各ノードの `layout()` 実装（分数 / √ 中心）
4. `Box.render()` 実装（hline / vline / text / 線描画ヘルパ）
5. `cas` コマンドを main() に追加
6. `_show_cas` 実装
7. 予約名追加 (`_COMMANDS` に `"cas"`)
8. 実機検証

### Phase 5b — Tier 2 記号簡約

- `_simplify(node) -> node` 関数
- 算術畳み込み・分数約分・sqrt 素因数分解
- パースの後、レイアウト前に簡約を適用

### Phase 5c — Tier 3 拡張 CAS

- 多項式・展開・因数分解等
- 別フェーズで個別検討

---

## 4. レンダリング対応要素一覧（Tier 1）

| 要素 | 構文例 | 視覚表現 |
|---|---|---|
| 数値 | `42` `1.5e-10` `0xFF` | `42` `1.5e-10` `0xFF` |
| 変数 / 定数 | `x` `pi` `ans` | そのまま |
| 加減 | `a + b` `a - b` | 通常の中置記法 |
| 乗算 | `a * b` | `a × b`（記号は `*` 代替で `×`） |
| 除算（分数） | `a / b` | 上下分割 + 横棒 |
| べき | `a ** b` | a の右上に b を半文字上オフセット |
| 剰余 | `a % b` | `a mod b` または `a % b` |
| 単項 | `-x` `+x` | そのまま |
| 平方根 | `sqrt(x)` | √ + オーバーライン |
| 絶対値 | `abs(x)` | `|x|` |
| 三角・指数対数等 | `sin(x)` `log(x)` 等 | `sin(x)` のまま（記号化なし） |
| 整数化 | `int(x)` `floor(x)` 等 | 関数呼び出し記法 |
| カンマ引数 | `atan2(y, x)` | `atan2(y, x)` |
| 括弧 | `(a)` | 必要に応じて `(` `)` を文字で描画 |

---

## 5. 制約・既知の課題

### 5.1 フォントサイズ

- 単一 6×8 フォントのみ使用（指数も同サイズ、半文字オフセット）
- 複雑なネストで表示が窮屈になる可能性
- 将来的に小フォント（4×6 等）を自作する余地あり

### 5.2 √ グリフ

- 線描画で簡略表現
- 美しさより視認性優先

### 5.3 画面範囲

- CAS 表示は **動的領域内** (chrome を維持)
- 中央配置、はみ出す場合は折り返しまたは「式が長すぎます」エラー

### 5.4 Tier 1 では簡約なし

- `sqrt(8-3)` は `sqrt(8-3)` のまま視覚表示
- 簡約後の `√5` 表示は Tier 2 の機能

---

## 6. 受け入れ基準（Phase 5a / Tier 1）

- [ ] `cas ans` で直近履歴の式が視覚表示される
- [ ] `cas ans2` ... `cas ans10` で N 番目過去の式が表示される
- [ ] 分数（`a/b`）が上下に並んで表示される
- [ ] `sqrt(x)` が √ + オーバーラインで表示される
- [ ] 括弧の優先順位が AST に正しく反映される
- [ ] 数値リテラル（10進・16進・2進・8進・指数）すべて受理する
- [ ] 任意キーで CAS 表示から戻れる
- [ ] 戻った後、chrome が復元される
- [ ] パースエラー時、エラーメッセージを表示しクラッシュしない
- [ ] `cas` を予約名として代入 LHS が拒否される

---

## 7. 参照

- DESIGN.md §6 アーキテクチャ
- HANDOFF_psephos.md §5 コーディング規約
- `psephos.py` の `_check_safe` / `_extract_names` （既存のトークン抽出ロジック、参考になる）
