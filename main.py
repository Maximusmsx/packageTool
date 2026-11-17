# -*- coding: utf-8 -*-
"""自动计税数据库打包工具

功能：
1. 配置多个「打包目录 + 打包名前缀」，保存在 config.json（与 exe 同目录）。
2. 打包前弹窗勾选本次要打包的目录（默认全选，支持全选/反选）。
3. 每个打包目录下找形如 yyyy-mm-dd 的子目录，取日期最大的那个，压缩为 <前缀><日期>.rar。
4. 全部子包完成后，把子包一起压缩成「桌面\\<打包名>.rar」，并清理中间临时文件。
5. 打包进度显示在主界面内嵌进度条上，不弹进度窗口。
"""

import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "自动计税数据库打包工具"
CONFIG_FILE = "config.json"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GEOMETRY_RE = re.compile(r"^\d{3,4}x\d{3,4}[+-]-?\d+[+-]-?\d+$")
INVALID_NAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t]')
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# --------------------------------------------------------------------------
# 路径 / 配置
# --------------------------------------------------------------------------
def app_dir():
    """程序所在目录（打包成 exe 后为 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test_%d" % os.getpid())
        with open(probe, "w") as f:
            f.write("1")
        os.remove(probe)
        return True
    except Exception:
        return False


def config_path():
    """配置优先放在程序目录（绿色便携），不可写时退回 %APPDATA%。"""
    base = app_dir()
    if _writable(base):
        return os.path.join(base, CONFIG_FILE)
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    fallback = os.path.join(appdata, "AutoTaxPackTool")
    os.makedirs(fallback, exist_ok=True)
    return os.path.join(fallback, CONFIG_FILE)


def load_config(path):
    data = {}
    if os.path.isfile(path):
        try:
            with io.open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
    items = []
    for it in data.get("items") or []:
        if isinstance(it, dict) and it.get("dir"):
            items.append({
                "dir": str(it["dir"]),
                "prefix": str(it.get("prefix") or ""),
            })
    return {
        "items": items,
        "pack_name": str(data.get("pack_name") or ""),
        "geometry": str(data.get("geometry") or ""),
        "last_browse": str(data.get("last_browse") or ""),
    }


def save_config(path, cfg):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # os.replace 本身即原子覆盖，无需先删除旧文件


# --------------------------------------------------------------------------
# 外部工具 / 系统目录
# --------------------------------------------------------------------------
def _winrar_from_registry():
    try:
        import winreg
    except Exception:
        return None
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WinRAR"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\WinRAR"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\WinRAR"),
    ]
    for root, sub in keys:
        try:
            with winreg.OpenKey(root, sub) as k:
                for value in ("exe64", "exe32"):
                    try:
                        exe, _ = winreg.QueryValueEx(k, value)
                    except OSError:
                        continue
                    cand = os.path.join(os.path.dirname(exe), "Rar.exe")
                    if os.path.isfile(cand):
                        return cand
        except OSError:
            continue
    return None


def find_rar():
    """定位 Rar.exe：优先 exe 内置 -> 程序目录 -> 注册表 -> 常见安装目录 -> PATH。"""
    candidates = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "Rar.exe"))
    candidates.append(os.path.join(app_dir(), "Rar.exe"))

    reg = _winrar_from_registry()
    if reg:
        candidates.append(reg)

    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env)
        if base:
            candidates.append(os.path.join(base, "WinRAR", "Rar.exe"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "Programs", "WinRAR", "Rar.exe"))

    found = shutil.which("rar")
    if found:
        candidates.append(found)

    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def desktop_dir():
    if os.name == "nt":
        try:
            import winreg
            key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                value, _ = winreg.QueryValueEx(k, "Desktop")
            value = os.path.expandvars(value)
            if os.path.isdir(value):
                return value
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


# --------------------------------------------------------------------------
# 打包逻辑
# --------------------------------------------------------------------------
def find_latest_date_dir(base_dir):
    """返回 base_dir 下 yyyy-mm-dd 目录中日期最大者，没有则返回 None。"""
    best = None
    try:
        names = os.listdir(base_dir)
    except OSError:
        return None
    for name in names:
        if not DATE_RE.match(name):
            continue
        path = os.path.join(base_dir, name)
        if not os.path.isdir(path):
            continue
        try:
            d = datetime.strptime(name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if best is None or d > best[0]:
            best = (d, name)
    return best[1] if best else None


def _run_rar(rar, args, cwd=None):
    cmd = [rar] + args
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )
    out = (proc.stdout or b"").decode("mbcs", "replace") if proc.stdout else ""
    if proc.returncode >= 2:
        raise RuntimeError("Rar 执行失败（返回码 %d）：\n%s" % (proc.returncode, out.strip()))
    return out


def normalize_pack_name(name):
    name = (name or "").strip()
    if name.lower().endswith(".rar"):
        name = name[:-4]
    return name.strip()


def run_pack(items, pack_name, rar_exe, log, progress=None, cancel=None):
    """执行完整打包流程，返回最终 rar 路径。log/progress 会回调到界面线程。"""
    if not rar_exe:
        raise RuntimeError("未找到 Rar.exe，无法生成 rar 压缩包。请安装 WinRAR 后重试。")

    final_name = normalize_pack_name(pack_name)
    if not final_name:
        raise RuntimeError("打包名不能为空。")
    if INVALID_NAME_RE.search(final_name):
        raise RuntimeError("打包名包含非法字符：\\ / : * ? \" < > |")

    final_path = os.path.join(desktop_dir(), final_name + ".rar")
    tmp_dir = tempfile.mkdtemp(prefix="autotaxpack_")
    produced = []
    total = len(items) + 1

    try:
        for idx, item in enumerate(items, 1):
            if cancel and cancel.is_set():
                raise RuntimeError("用户已取消。")
            base = item["dir"]
            prefix = (item.get("prefix") or "").strip()
            log("[%d/%d] %s" % (idx, len(items), base))
            if not os.path.isdir(base):
                raise RuntimeError("打包目录不存在：%s" % base)
            if not prefix:
                raise RuntimeError("打包名前缀为空：%s" % base)
            if INVALID_NAME_RE.search(prefix):
                raise RuntimeError("打包名前缀包含非法字符：%s" % prefix)

            date_name = find_latest_date_dir(base)
            if not date_name:
                raise RuntimeError("未找到 yyyy-mm-dd 格式的日期目录：%s" % base)
            log("       最新日期目录：%s" % date_name)

            arc_name = prefix + date_name + ".rar"
            if arc_name in produced:
                raise RuntimeError("打包文件名重复：%s\n请检查多个目录是否配置了相同的打包名前缀/日期目录。" % arc_name)
            arc_path = os.path.join(tmp_dir, arc_name)
            log("       正在压缩 -> %s" % arc_name)
            _run_rar(rar_exe, ["a", "-r", "-m3", "-y", "-idq", arc_path, date_name], cwd=base)
            if not os.path.isfile(arc_path) or os.path.getsize(arc_path) == 0:
                raise RuntimeError("压缩失败，未生成有效的压缩包：%s" % arc_name)
            produced.append(arc_name)
            if progress:
                progress(idx * 100.0 / total)

        if not produced:
            raise RuntimeError("没有需要打包的内容。")

        if cancel and cancel.is_set():
            raise RuntimeError("用户已取消。")

        log("[合并] %d 个压缩包 -> %s" % (len(produced), final_path))
        if os.path.exists(final_path):
            os.remove(final_path)
        _run_rar(rar_exe, ["a", "-r", "-m3", "-y", "-idq", final_path] + produced, cwd=tmp_dir)
        if not os.path.isfile(final_path) or os.path.getsize(final_path) == 0:
            raise RuntimeError("总压缩包生成失败：%s" % final_path)
        if progress:
            progress(100)
        log("       已生成：%s" % final_path)
        return final_path
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# --------------------------------------------------------------------------
# 界面
# --------------------------------------------------------------------------
class SelectDirsDialog(tk.Toplevel):
    """打包前勾选本次要打包的目录，默认全选。"""

    CHECKED = "☑"
    UNCHECKED = "☐"

    def __init__(self, master, items):
        tk.Toplevel.__init__(self, master)
        self.title("选择要打包的目录")
        self.resizable(True, True)
        self.minsize(640, 320)
        self.result = None  # None 表示取消，否则为勾选的配置项列表
        self.items = list(items)
        self.checked = [True] * len(self.items)

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)

        head = ttk.Frame(body)
        head.pack(fill="x")
        ttk.Label(head, text="单击行可勾选/取消，默认全选：").pack(side="left")
        self.count_var = tk.StringVar()
        ttk.Label(head, textvariable=self.count_var).pack(side="right")

        table = ttk.Frame(body)
        table.pack(fill="both", expand=True, pady=(6, 8))

        columns = ("chk", "idx", "dir", "prefix")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("chk", text="选择")
        self.tree.heading("idx", text="序号")
        self.tree.heading("dir", text="打包目录")
        self.tree.heading("prefix", text="打包名前缀")
        self.tree.column("chk", width=56, anchor="center", stretch=False)
        self.tree.column("idx", width=56, anchor="center", stretch=False)
        self.tree.column("dir", width=420, anchor="w")
        self.tree.column("prefix", width=180, anchor="w", stretch=False)

        vsb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for i, item in enumerate(self.items):
            self.tree.insert("", "end", iid=str(i),
                             values=(self.CHECKED, i + 1, item["dir"], item["prefix"]))

        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", lambda e: self._toggle_many(self.tree.selection()))
        self.tree.bind("<Double-1>", self._on_double_click)

        btns = ttk.Frame(body)
        btns.pack(fill="x")
        ttk.Button(btns, text="全选", width=10, command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(btns, text="反选", width=10, command=self._invert).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="确定", width=10, command=self.on_ok).pack(side="right")
        ttk.Button(btns, text="取消", width=10, command=self.destroy).pack(side="right", padx=(0, 8))

        self.bind("<Return>", lambda e: self.on_ok())
        self.bind("<Escape>", lambda e: self.destroy())

        self._update_count()
        self.transient(master)
        self.grab_set()
        self.geometry("760x420")
        self.update_idletasks()
        self._center(master)
        self.after(50, lambda: self.focus_force())

    def _center(self, master):
        w, h = self.winfo_width(), self.winfo_height()
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 3
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))

    # ---------------- 勾选 ----------------
    def _on_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.tree.identify_row(event.y)
        if row:
            self._toggle(row)

    def _on_double_click(self, event):
        return "break"

    def _toggle(self, iid):
        try:
            i = int(iid)
        except (TypeError, ValueError):
            return
        if 0 <= i < len(self.checked):
            self.checked[i] = not self.checked[i]
            self._paint(i)

    def _toggle_many(self, iids):
        for iid in iids:
            self._toggle(iid)

    def _paint(self, i):
        self.tree.set(str(i), "chk", self.CHECKED if self.checked[i] else self.UNCHECKED)
        self._update_count()

    def _set_all(self, value):
        self.checked = [value] * len(self.items)
        for i in range(len(self.items)):
            self.tree.set(str(i), "chk", self.CHECKED if value else self.UNCHECKED)
        self._update_count()

    def _invert(self):
        self.checked = [not c for c in self.checked]
        for i in range(len(self.items)):
            self.tree.set(str(i), "chk", self.CHECKED if self.checked[i] else self.UNCHECKED)
        self._update_count()

    def _update_count(self):
        self.count_var.set("已选 %d / %d" % (sum(self.checked), len(self.checked)))

    def on_ok(self):
        chosen = [item for item, c in zip(self.items, self.checked) if c]
        if not chosen:
            messagebox.showwarning("提示", "请至少勾选一个打包目录。", parent=self)
            return
        self.result = chosen
        self.destroy()


class AddDialog(tk.Toplevel):
    """添加 / 编辑一条打包目录配置。"""

    def __init__(self, master, item=None, last_browse=None):
        tk.Toplevel.__init__(self, master)
        self.title("添加打包目录" if item is None else "编辑打包目录")
        self.resizable(True, False)
        self.result = None

        frame = ttk.LabelFrame(self, text=("添加打包目录" if item is None else "编辑打包目录"), padding=12)
        frame.pack(fill="both", expand=True, padx=10, pady=10)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="1. 打包目录选择").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self.dir_var = tk.StringVar(value=(item or {}).get("dir", ""))
        ttk.Entry(frame, textvariable=self.dir_var, width=60).grid(row=1, column=0, columnspan=2, sticky="we")
        ttk.Button(frame, text="浏览", width=8, command=self.browse).grid(row=1, column=2, padx=(6, 0))
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="2. 打包名前缀").grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 4))
        self.prefix_var = tk.StringVar(value=(item or {}).get("prefix", ""))
        ttk.Entry(frame, textvariable=self.prefix_var, width=60).grid(row=3, column=0, columnspan=2, sticky="we")
        ttk.Button(frame, text="保存", width=10, command=self.on_save).grid(row=4, column=0, sticky="w", pady=(16, 0))

        self.last_browse = last_browse
        self._has_prefix = bool((item or {}).get("prefix"))

        self.bind("<Return>", lambda e: self.on_save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.transient(master)
        self.grab_set()
        self.after(50, lambda: self.focus_force())
        self.update_idletasks()
        self._center(master)

    def _center(self, master):
        w, h = self.winfo_width(), self.winfo_height()
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 3
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))

    def browse(self):
        start = self.dir_var.get().strip() or self.last_browse or ""
        if start and not os.path.isdir(start):
            start = os.path.dirname(start)
        chosen = filedialog.askdirectory(parent=self, title="选择打包目录", initialdir=start or None)
        if chosen:
            chosen = os.path.normpath(chosen)
            self.dir_var.set(chosen)
            if not self.prefix_var.get().strip() and not self._has_prefix:
                self.prefix_var.set(os.path.basename(chosen) or "")

    def on_save(self):
        path = self.dir_var.get().strip().strip('"')
        prefix = self.prefix_var.get().strip()
        if not path:
            messagebox.showwarning("提示", "请选择打包目录。", parent=self)
            return
        if not os.path.isdir(path):
            messagebox.showwarning("提示", "目录不存在：\n%s" % path, parent=self)
            return
        if not prefix:
            messagebox.showwarning("提示", "请填写打包名前缀。", parent=self)
            return
        if INVALID_NAME_RE.search(prefix):
            messagebox.showwarning("提示", "打包名前缀不能包含 \\ / : * ? \" < > | 等字符。", parent=self)
            return
        self.result = {"dir": path, "prefix": prefix}
        self.destroy()


class App(object):
    def __init__(self, root):
        self.root = root
        self.cfg_path = config_path()
        cfg = load_config(self.cfg_path)
        self.items = list(cfg["items"])
        self.last_browse = cfg["last_browse"]
        self.geometry = cfg["geometry"]
        self.msg_queue = queue.Queue()
        self.worker = None
        self.cancel_flag = threading.Event()

        self.root.title(APP_TITLE)
        self.root.geometry(self.geometry if GEOMETRY_RE.match(self.geometry) else "880x460")
        self.root.minsize(720, 380)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self.pack_name_var.set(cfg["pack_name"])
        self.refresh_list()
        self.root.after(200, self._poll_queue)

    # ---------------- UI ----------------
    def _build_ui(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except Exception:
            pass
        default_font = ("Microsoft YaHei UI", 9)
        self.root.option_add("*Font", default_font)
        style.configure("Treeview", font=default_font, rowheight=24)
        style.configure("Treeview.Heading", font=default_font)

        self.pack_name_var = tk.StringVar()

        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        # 上部：打包目录选择
        top = ttk.LabelFrame(outer, text="打包目录选择", padding=8)
        top.pack(fill="both", expand=True)

        head = ttk.Frame(top)
        head.pack(fill="x")
        ttk.Label(head, text="打包目录列表").pack(side="left")
        ttk.Button(head, text="+添加打包目录", command=self.add_item).pack(side="right")

        table = ttk.Frame(top)
        table.pack(fill="both", expand=True, pady=(6, 0))

        columns = ("idx", "dir", "prefix")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("idx", text="序号")
        self.tree.heading("dir", text="打包目录")
        self.tree.heading("prefix", text="打包名前缀")
        self.tree.column("idx", width=60, anchor="center", stretch=False)
        self.tree.column("dir", width=560, anchor="w")
        self.tree.column("prefix", width=200, anchor="w", stretch=False)

        vsb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda e: self.edit_item())
        self.tree.bind("<Delete>", lambda e: self.delete_item())
        self.tree.bind("<Button-3>", self._popup_menu)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="编辑", command=self.edit_item)
        self.menu.add_command(label="删除", command=self.delete_item)
        self.menu.add_command(label="打开目录", command=self.open_dir)

        # 下部：打包名 + 开始打包
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Label(bottom, text="打包名").pack(side="left")
        self.name_entry = ttk.Entry(bottom, textvariable=self.pack_name_var)
        self.name_entry.pack(side="left", fill="x", expand=True, padx=(8, 10))
        self.name_entry.bind("<FocusOut>", lambda e: self.save())
        self.start_btn = ttk.Button(bottom, text="开始打包", width=14, command=self.start_pack)
        self.start_btn.pack(side="right")

        # 进度区：直接嵌在主界面，不弹窗
        prog = ttk.Frame(outer)
        prog.pack(fill="x", pady=(10, 0))
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_bar = ttk.Progressbar(prog, mode="determinate", maximum=100,
                                            variable=self.progress_var)
        self.progress_bar.pack(side="left", fill="x", expand=True)
        self.pct_var = tk.StringVar(value="")
        ttk.Label(prog, textvariable=self.pct_var, width=6, anchor="e").pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(outer, textvariable=self.status_var, foreground="#555555").pack(fill="x", pady=(6, 0))

        self.refresh_list()

    def _popup_menu(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.menu.tk_popup(event.x_root, event.y_root)

    def refresh_list(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for i, item in enumerate(self.items, 1):
            self.tree.insert("", "end", values=(i, item["dir"], item["prefix"]))

    # ---------------- 配置 ----------------
    def save(self):
        geo = self.root.geometry()
        if not GEOMETRY_RE.match(geo):
            geo = self.geometry
        cfg = {
            "items": self.items,
            "pack_name": self.pack_name_var.get() if hasattr(self, "pack_name_var") else "",
            "geometry": geo,
            "last_browse": self.last_browse,
        }
        try:
            save_config(self.cfg_path, cfg)
        except Exception as exc:
            self.status_var.set("配置保存失败：%s" % exc)

    def selected_index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        idx = self.tree.index(sel[0])
        if 0 <= idx < len(self.items):
            return idx
        return None

    # ---------------- 条目操作 ----------------
    def add_item(self):
        dlg = AddDialog(self.root, None, self.last_browse)
        self.root.wait_window(dlg)
        if dlg.result:
            self.items.append(dlg.result)
            self.last_browse = os.path.dirname(dlg.result["dir"]) or self.last_browse
            self.refresh_list()
            self.save()
            self.status_var.set("已添加：%s" % dlg.result["dir"])

    def edit_item(self):
        idx = self.selected_index()
        if idx is None:
            return
        dlg = AddDialog(self.root, self.items[idx], self.last_browse)
        self.root.wait_window(dlg)
        if dlg.result:
            self.items[idx] = dlg.result
            self.refresh_list()
            self.save()

    def delete_item(self):
        idx = self.selected_index()
        if idx is None:
            return
        item = self.items[idx]
        if messagebox.askyesno("确认", "确定删除这条配置吗？\n%s" % item["dir"], parent=self.root):
            del self.items[idx]
            self.refresh_list()
            self.save()

    def open_dir(self):
        idx = self.selected_index()
        if idx is None:
            return
        path = self.items[idx]["dir"]
        if os.path.isdir(path):
            os.startfile(path)
        else:
            messagebox.showwarning("提示", "目录不存在：\n%s" % path, parent=self.root)

    # ---------------- 打包 ----------------
    def start_pack(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.items:
            messagebox.showwarning("提示", "请先添加打包目录。", parent=self.root)
            return
        name = normalize_pack_name(self.pack_name_var.get())
        if not name:
            messagebox.showwarning("提示", "请填写打包名。", parent=self.root)
            self.name_entry.focus_set()
            return
        if INVALID_NAME_RE.search(name):
            messagebox.showwarning("提示", "打包名不能包含 \\ / : * ? \" < > | 等字符。", parent=self.root)
            return

        rar_exe = find_rar()
        if not rar_exe:
            messagebox.showerror("缺少组件", "未找到 Rar.exe，无法生成 rar 压缩包。\n请安装 WinRAR 后重试。",
                                 parent=self.root)
            return

        # 勾选本次要打包的目录（默认全选）
        dlg = SelectDirsDialog(self.root, self.items)
        self.root.wait_window(dlg)
        if dlg.result is None:
            return
        chosen = dlg.result

        # 预检查：目录、日期目录
        problems = []
        valid_items = []
        for item in chosen:
            base = item["dir"]
            if not os.path.isdir(base):
                problems.append("%s\n    目录不存在" % base)
                continue
            latest = find_latest_date_dir(base)
            if not latest:
                problems.append("%s\n    没有 yyyy-mm-dd 格式的日期目录" % base)
                continue
            valid_items.append(item)

        if problems:
            text = "以下目录无法打包：\n\n" + "\n\n".join(problems)
            if not messagebox.askyesno("部分目录有问题", text + "\n\n是否跳过它们继续打包？", parent=self.root):
                return

        if not valid_items:
            messagebox.showwarning("提示", "没有可打包的目录。", parent=self.root)
            return

        final_path = os.path.join(desktop_dir(), name + ".rar")
        if os.path.exists(final_path):
            if not messagebox.askyesno("确认覆盖", "桌面已存在同名文件：\n%s\n\n是否覆盖？" % final_path,
                                       parent=self.root):
                return

        self.save()
        self.cancel_flag.clear()
        self.start_btn.configure(state="disabled")
        self.progress_var.set(0)
        self.pct_var.set("0%")
        self.status_var.set("开始打包，共 %d 个目录…" % len(valid_items))

        def worker():
            try:
                result = run_pack(
                    valid_items, name, rar_exe,
                    log=lambda s: self.msg_queue.put(("log", s)),
                    progress=lambda v: self.msg_queue.put(("progress", v)),
                    cancel=self.cancel_flag,
                )
                self.msg_queue.put(("done", result))
            except Exception as exc:
                self.msg_queue.put(("error", str(exc)))

        self.worker = threading.Thread(target=worker)
        self.worker.daemon = True
        self.worker.start()

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self.status_var.set(str(payload).strip())
                elif kind == "progress":
                    value = max(0.0, min(100.0, float(payload)))
                    self.progress_var.set(value)
                    self.pct_var.set("%d%%" % round(value))
                elif kind == "done":
                    self.progress_var.set(100)
                    self.pct_var.set("100%")
                    self.status_var.set("打包完成：%s" % payload)
                    self.start_btn.configure(state="normal")
                    self.root.bell()
                    messagebox.showinfo("完成", "打包完成！\n\n%s" % payload, parent=self.root)
                elif kind == "error":
                    self.status_var.set("打包失败：%s" % str(payload).strip().splitlines()[0])
                    self.start_btn.configure(state="normal")
                    messagebox.showerror("失败", payload, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_queue)

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("确认", "正在打包中，确定要退出吗？", parent=self.root):
                return
            self.cancel_flag.set()
        self.save()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
