# -*- coding: utf-8 -*-
# 一键打包脚本：python build.py
# 生成 dist\自动计税数据库打包工具.exe（单文件，内置 Rar.exe，运行不联网、无需安装依赖）

import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "自动计税数据库打包工具"


def find_rar():
    candidates = [
        r"C:\Program Files\WinRAR\Rar.exe",
        r"C:\Program Files (x86)\WinRAR\Rar.exe",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    hits = glob.glob(r"C:\Program Files*\WinRAR\Rar.exe")
    if hits:
        return hits[0]
    raise SystemExit("未找到 WinRAR 的 Rar.exe，请先安装 WinRAR。")


def main():
    rar = find_rar()
    icon = os.path.join(HERE, "app.ico")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", APP_NAME,
        "--add-binary", "%s%s." % (rar, os.pathsep),
        "--distpath", os.path.join(HERE, "dist"),
        "--workpath", os.path.join(HERE, "build"),
        "--specpath", os.path.join(HERE, "build"),
    ]
    if os.path.isfile(icon):
        cmd += ["--icon", icon]
    cmd.append(os.path.join(HERE, "main.py"))
    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=HERE)
    exe = os.path.join(HERE, "dist", APP_NAME + ".exe")
    print("\n生成完成：%s" % exe)


if __name__ == "__main__":
    main()
