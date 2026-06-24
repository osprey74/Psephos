# main_lofifren.py — LofiFren ランチャを起動する旧 main.py の退避版。
# Phase 6-A 移行後は /main.py が Psephos 自動起動になっているため、
# LofiFren ランチャを使いたいときはブート時に 'L' を 500ms 保持するか、
# REPL から `from main_lofifren import *` で起動できる。

from py_run import main_menu

main_menu()
