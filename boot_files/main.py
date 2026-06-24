# main.py — Psephos auto-launch (Phase 6-A 自前ランチャ)
#
# boot.py 完了直後にこの main.py が実行される。Psephos を直接起動し、
# 正常終了 (ESC) 後に machine.soft_reset() で MicroPython 自体を再起動
# してヒープを完全にクリアする。これにより:
#   - LofiFren ランチャ経由のメモリ汚染を完全に回避
#   - chrome.bin / help.bin / closure 累積による断片化を起動毎にリセット
#   - 51 KB の連続領域確保が常に成功
#
# 安全網: ブート直後 500 ms 以内に 'L' / 'l' キーを保持すると LofiFren に
# フォールバック (= /main_lofifren.py 相当を実行)。Psephos の不具合で
# 起動失敗が連続するときの脱出口として機能する。

import sys
import time
import gc
import picocalc


def _check_fallback():
    """ブート時 500 ms 以内に 'L' を検出したら True。

    キーボード MCU を直接 poll する (terminal が起動済かは関係なく動作)。
    """
    buf = bytearray(8)
    end = time.ticks_add(time.ticks_ms(), 500)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        try:
            n = picocalc.keyboard.readinto(buf)
        except OSError:
            n = None
        if n:
            for i in range(n):
                if buf[i] in (ord("l"), ord("L")):
                    return True
        time.sleep_ms(20)
    return False


def _show_splash():
    """ブート選択のヒントを 0.5 秒だけ表示。"""
    try:
        d = picocalc.display
        d.fill(0)
        d.text("Psephos", 132, 130, 15)
        d.text("Hold 'L' for LofiFren menu", 50, 160, 11)
        d.show()
    except Exception:
        pass


_show_splash()
_fallback = _check_fallback()

if _fallback:
    # LofiFren ランチャに戻す
    try:
        picocalc.terminal.wr("\x1b[2J\x1b[H")
        from py_run import main_menu
        main_menu()
    except Exception as e:
        sys.print_exception(e)
else:
    # Psephos を起動
    # SD マウント確認: /sd の statvfs サイズで判定 (内蔵フラッシュなら 2 MB、SD なら 数 GB)
    # マウント未完なら enhanced_sd.initsd() で再マウントを 3 回まで試す
    import os as _os
    sd_ok = False
    for _attempt in range(3):
        try:
            _sv = _os.statvfs("/sd")
            _mb = (_sv[0] * _sv[2]) // (1024 * 1024)
            if _mb >= 100:           # SD カードは 100 MB 以上、内蔵フラッシュは 2 MB
                sd_ok = True
                break
        except OSError:
            pass
        # マウント試行
        try:
            from enhanced_sd import initsd
            initsd(debug=False)
        except Exception:
            pass
        time.sleep_ms(300)
    if not sd_ok:
        try:
            picocalc.terminal.wr("\r\n[main.py] SD card mount failed. REPL accessible.\r\n")
        except Exception:
            pass
        # 中断 (REPL 残す)
        raise SystemExit()
    sys.path.insert(0, "/sd/py_scripts")
    gc.collect()
    _ok = False
    try:
        import psephos
        psephos.main()
        _ok = True
    except Exception as e:
        # 起動失敗時は soft_reset せず REPL に戻して traceback を保持
        sys.print_exception(e)
        try:
            picocalc.terminal.wr(
                "\r\n\r\n[main.py] Psephos crashed. "
                "Use REPL or hold 'L' next boot for LofiFren.\r\n"
            )
        except Exception:
            pass
    if _ok:
        # 正常終了 (ESC) 後にヒープ全クリアのため soft_reset
        try:
            picocalc.terminal.wr("\r\nRebooting...\r\n")
            time.sleep_ms(300)
        except Exception:
            pass
        import machine
        machine.soft_reset()
