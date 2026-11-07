# -*- coding: utf-8 -*-
# exe 冒烟测试：启动 -> 确认窗口进程存活且 _MEI 运行时目录内含 Rar.exe -> 结束进程
import glob
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "dist", "自动计税数据库打包工具.exe")

before = set(glob.glob(os.path.join(os.environ["TEMP"], "_MEI*")))
proc = subprocess.Popen([EXE])
try:
    time.sleep(12)
    rc = proc.poll()
    assert rc is None, "exe exited early with code %s" % rc
    after = set(glob.glob(os.path.join(os.environ["TEMP"], "_MEI*")))
    new = after - before
    print("new runtime dirs:", new)
    found = False
    for d in new:
        rar = os.path.join(d, "Rar.exe")
        if os.path.isfile(rar):
            found = True
            print("Rar.exe bundled at:", rar, os.path.getsize(rar), "bytes")
    assert found, "Rar.exe NOT found in bundled runtime dir"
    print("SMOKE TEST PASSED")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
