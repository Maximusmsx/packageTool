# -*- coding: utf-8 -*-
# 成品验证：预置 config.json -> 启动 exe -> 截图验证界面与配置自动加载 -> 清理
import glob
import io
import json
import os
import subprocess
import time

from PIL import ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
EXE = os.path.join(DIST, "自动计税数据库打包工具.exe")
CFG = os.path.join(DIST, "config.json")
SHOT = os.path.join(HERE, "verify_shot.png")

# 1. 预置配置（模拟老用户下次打开）
cfg = {
    "items": [
        {"dir": r"C:\Users\admin\Desktop\增值税发票\XCES_V2\TAX_CENTER_BS", "prefix": "TAX_CENTER_BS"},
        {"dir": r"D:\Data\OTHER_DB", "prefix": "OTHER_DB"},
    ],
    "pack_name": "自动计税数据库",
    "geometry": "880x420+200+120",
    "last_browse": r"C:\Users\admin\Desktop",
}
with io.open(CFG, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

before = set(glob.glob(os.path.join(os.environ["TEMP"], "_MEI*")))
proc = subprocess.Popen([EXE], cwd=DIST)
try:
    time.sleep(12)
    assert proc.poll() is None, "exe exited early"
    img = ImageGrab.grab()
    img.save(SHOT)
    print("screenshot saved:", SHOT, img.size)
    # 同时确认内置 Rar.exe
    new = set(glob.glob(os.path.join(os.environ["TEMP"], "_MEI*"))) - before
    assert any(os.path.isfile(os.path.join(d, "Rar.exe")) for d in new), "Rar.exe not bundled"
    print("Rar.exe bundled OK")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    time.sleep(1)
    if os.path.isfile(CFG):
        os.remove(CFG)
    for d in set(glob.glob(os.path.join(os.environ["TEMP"], "_MEI*"))) - before:
        subprocess.call(["cmd", "/c", "rd", "/s", "/q", d])
print("VERIFY DONE")
