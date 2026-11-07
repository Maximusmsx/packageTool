# -*- coding: utf-8 -*-
# 端到端功能测试：模拟两个业务目录，真实调用 run_pack 打包，验证结果后清理。
import glob
import io
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import main as m

RAR = r"C:\Program Files\WinRAR\Rar.exe"
assert os.path.isfile(RAR), "no rar"

# 1. 造测试目录
fixture = tempfile.mkdtemp(prefix="autotax_test_")
src1 = os.path.join(fixture, "TAX_CENTER_BS")
src2 = os.path.join(fixture, "OTHER_DB")
os.makedirs(os.path.join(src1, "2026-11-01"))
os.makedirs(os.path.join(src1, "2026-11-07"))  # 最新
os.makedirs(os.path.join(src1, "not-a-date"))   # 应被忽略
os.makedirs(os.path.join(src2, "2026-10-30"))
with open(os.path.join(src1, "2026-11-07", "data.txt"), "w") as f:
    f.write("hello bs")
with open(os.path.join(src1, "2026-11-01", "old.txt"), "w") as f:
    f.write("old")
with open(os.path.join(src2, "2026-10-30", "data.txt"), "w") as f:
    f.write("hello other")

logs = []
items = [
    {"dir": src1, "prefix": "TAX_CENTER_BS"},
    {"dir": src2, "prefix": "OTHER_DB"},
]

tmp_before = set(glob.glob(os.path.join(tempfile.gettempdir(), "autotaxpack_*")))

final = m.run_pack(items, "测试总包", RAR, log=logs.append, progress=lambda v: logs.append("progress %.0f" % v))
print("\n".join(logs))
print("FINAL:", final)

# 2. 验证总包存在且内容正确
assert os.path.isfile(final) and os.path.getsize(final) > 0, "final rar missing"
out = subprocess.run([RAR, "l", final], stdout=subprocess.PIPE).stdout.decode("mbcs", "replace")
print("----- final rar listing -----")
print(out)
assert "TAX_CENTER_BS2026-11-07.rar" in out, "sub rar 1 missing in final"
assert "OTHER_DB2026-10-30.rar" in out, "sub rar 2 missing in final"

# 验证子包内部保留了日期目录层级且只打了最新日期
tmp_list = tempfile.mkdtemp(prefix="autotax_list_")
sub1 = os.path.join(tmp_list, "TAX_CENTER_BS2026-11-07.rar")
subprocess.run([RAR, "x", "-o+", final, "TAX_CENTER_BS2026-11-07.rar", tmp_list + os.sep], check=True)
out1 = subprocess.run([RAR, "l", sub1], stdout=subprocess.PIPE).stdout.decode("mbcs", "replace")
print("----- sub rar listing -----")
print(out1)
assert "2026-11-07" in out1 and "data.txt" in out1, "sub rar content wrong"
assert "2026-11-01" not in out1, "old date dir should not be packed"

# 3. 验证临时目录已清理
tmp_after = set(glob.glob(os.path.join(tempfile.gettempdir(), "autotaxpack_*")))
assert tmp_after == tmp_before, "temp dirs leaked: %s" % (tmp_after - tmp_before)

# 4. 清理测试产物
os.remove(final)
shutil.rmtree(fixture, ignore_errors=True)
shutil.rmtree(tmp_list, ignore_errors=True)
print("ALL TESTS PASSED")
