"""CryptoNote 桌面加密记事本（ttkbootstrap · Cosmo 浅色主题）。

启动后先显示解锁/创建界面，验证主密码后切换到主编辑界面。
解锁界面与主界面共用同一个窗口（切换 Frame），不使用隐藏主窗 + Toplevel 的组合，
以避免 Windows 上 withdraw 父窗 + transient 子窗导致窗口一闪而过的问题。

主界面：左侧卡片式笔记列表（标题 + 预览 + 时间），右侧标题 + 正文编辑器。

安全相关：
- 主密码只用于在内存中派生密钥，不写任何配置或临时文件。
- 空闲超时自动锁定，回到解锁界面，清掉内存中的 Vault 引用。
- 退出前对未保存改动给出提示。
"""

import os
import json
import tkinter as tk
from tkinter import font as tkfont
from datetime import datetime
from urllib.parse import urlparse

import ttkbootstrap as tb
import ttkbootstrap as ttk          # ttkbootstrap 顶层导出了大部分控件（带 bootstyle）
from ttkbootstrap.widgets.scrolled import ScrolledFrame
from ttkbootstrap.constants import (
    LEFT, RIGHT, TOP, BOTTOM, BOTH, X, Y, W, E, EW, END, WORD, FLAT,
    PRIMARY, SECONDARY, SUCCESS, DANGER, INFO, LINK,
)
from ttkbootstrap.dialogs import Messagebox
from tkinter import filedialog

from crypto import Vault, VaultError, WrongPassword, TITLE_MAX_LENGTH

APP_TITLE = "CryptoNote 加密记事本"
DEFAULT_VAULT = "notes.vault"
AUTO_LOCK_MS = 5 * 60 * 1000        # 5 分钟无操作自动锁定
THEME = "cosmo"                      # ttkbootstrap 浅色主题

# 富文本样式位（可叠加）。正文持久化为 {"_rt":RT_VERSION,"text":..,"spans":[[s,e,flags],..], "links":[[s,e,url],..]}。
RT_VERSION = 3
ST_BOLD = 1
ST_ITALIC = 2
ST_UNDERLINE = 4
ST_STRIKE = 8
ST_ALL = (ST_BOLD, ST_ITALIC, ST_UNDERLINE, ST_STRIKE)

BODY_FONT_DEFAULT = 11               # 正文默认字号
BODY_FONT_MIN = 8
BODY_FONT_MAX = 40
AUTOSAVE_MS = 30 * 1000              # 自动保存间隔
UNDO_COALESCE_MS = 600              # 打字快照去抖窗口
UNDO_MAX = 200                       # 撤销栈上限

FONT = "Microsoft YaHei UI"
F_HERO = (FONT, 22, "bold")
F_H1 = (FONT, 15, "bold")
F_TITLE = (FONT, 16, "bold")
F_BODY = (FONT, 11)
F_CARD_TITLE = (FONT, 11, "bold")
F_CARD_SUB = (FONT, 9)
F_SMALL = (FONT, 9)


def rt_loads(raw: str):
    """Parse decrypted body into (plain_text, spans, links).

    Backward compatible: old notes without rich text return empty spans/links.
    spans: [(start, end, flags), ...]
    links: [(start, end, url), ...]
    """
    if raw:
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and obj.get("_rt") in (2, 3):
                text = obj.get("text", "")
                spans = [tuple(s) for s in obj.get("spans", [])]
                links = [tuple(l) for l in obj.get("links", [])]
                return text, spans, links
        except (ValueError, TypeError):
            pass
    return raw, [], []


def rt_dumps(text: str, spans, links=None) -> str:
    """Serialize plain text + spans + links into rich text JSON."""
    if links is None:
        links = []
    return json.dumps({"_rt": RT_VERSION, "text": text,
                       "spans": [list(s) for s in spans],
                       "links": [list(l) for l in links]},
                      ensure_ascii=False, separators=(",", ":"))


def rt_plaintext(raw: str) -> str:
    """从（可能是富文本的）正文里取出纯文本，供列表预览使用。"""
    return rt_loads(raw)[0]


def is_safe_link_url(url) -> bool:
    """超链接白名单：只允许 http/https，避免 file:// 等 scheme 交给系统打开。"""
    if not isinstance(url, str):
        return False
    try:
        return urlparse(url.strip()).scheme.lower() in ("http", "https")
    except ValueError:
        return False



class UnlockFrame(ttk.Frame):
    """解锁 / 新建保险库界面。验证成功后调用 on_unlocked(vault, path)。"""

    def __init__(self, master, on_unlocked, initial_path=""):
        super().__init__(master, padding=0)
        self.on_unlocked = on_unlocked
        self._path = tk.StringVar(value=initial_path)
        self._build()

    def _build(self):
        card = ttk.Frame(self, padding=40)
        card.place(relx=0.5, rely=0.5, anchor="center")

        ttk.Label(card, text="🔐", font=(FONT, 36)).grid(
            row=0, column=0, columnspan=2, pady=(0, 4))
        ttk.Label(card, text="CryptoNote", font=F_HERO, bootstyle=PRIMARY).grid(
            row=1, column=0, columnspan=2)
        ttk.Label(card, text="加密笔记库 · 一个主密码管理所有笔记",
                  font=F_SMALL, bootstyle=SECONDARY).grid(
            row=2, column=0, columnspan=2, pady=(2, 28))

        ttk.Label(card, text="保险库文件", font=F_BODY).grid(row=3, column=0, sticky=W, pady=(0, 4))
        path_row = ttk.Frame(card)
        path_row.grid(row=4, column=0, columnspan=2, sticky=EW, pady=(0, 16))
        ttk.Entry(path_row, textvariable=self._path, width=34, font=F_BODY).pack(
            side=LEFT, fill=X, expand=True)
        ttk.Button(path_row, text="浏览", width=6, bootstyle="secondary-outline",
                   command=self._browse).pack(side=LEFT, padx=(8, 0))

        ttk.Label(card, text="主密码", font=F_BODY).grid(row=5, column=0, sticky=W, pady=(0, 4))
        self.ent_pw = ttk.Entry(card, show="●", width=42, font=F_BODY)
        self.ent_pw.grid(row=6, column=0, columnspan=2, sticky=EW, pady=(0, 14))

        ttk.Label(card, text="确认密码  （仅新建时需要）", font=F_SMALL,
                  bootstyle=SECONDARY).grid(row=7, column=0, columnspan=2, sticky=W, pady=(0, 4))
        self.ent_pw2 = ttk.Entry(card, show="●", width=42, font=F_BODY)
        self.ent_pw2.grid(row=8, column=0, columnspan=2, sticky=EW, pady=(0, 28))

        btns = ttk.Frame(card)
        btns.grid(row=9, column=0, columnspan=2, sticky=EW)
        ttk.Button(btns, text="打开保险库", bootstyle=PRIMARY, width=16,
                   command=self._open).pack(side=LEFT)
        ttk.Button(btns, text="新建", bootstyle="success-outline", width=8,
                   command=self._create).pack(side=LEFT, padx=8)
        ttk.Button(btns, text="退出", bootstyle="secondary-link",
                   command=self.master.destroy).pack(side=RIGHT)

        self.ent_pw.focus_set()
        self.ent_pw.bind("<Return>", lambda e: self._open())
        self.ent_pw2.bind("<Return>", lambda e: self._open())

    def _browse(self):
        path = filedialog.askopenfilename(
            title="选择保险库文件",
            filetypes=[("CryptoNote 保险库", "*.vault"), ("所有文件", "*.*")],
        )
        if path:
            self._path.set(path)

    def _open(self):
        path = self._path.get().strip()
        pw = self.ent_pw.get()
        if not path:
            Messagebox.show_warning("请先选择保险库文件", APP_TITLE, parent=self)
            return
        if not pw:
            Messagebox.show_warning("请输入主密码", APP_TITLE, parent=self)
            return
        try:
            vault = Vault.open(path, pw)
        except WrongPassword:
            Messagebox.show_error("主密码错误，或文件已被篡改", APP_TITLE, parent=self)
            self.ent_pw.delete(0, END)  # 清空密码框
            self.ent_pw.focus_set()
            return
        except VaultError as exc:
            Messagebox.show_error(f"打开失败：{exc}", APP_TITLE, parent=self)
            return
        self.ent_pw.delete(0, END)  # 解锁成功，清空密码框
        self.ent_pw2.delete(0, END)
        self.on_unlocked(vault, path)

    def _create(self):
        pw = self.ent_pw.get()
        pw2 = self.ent_pw2.get()
        if not pw:
            Messagebox.show_warning("请输入主密码", APP_TITLE, parent=self)
            return
        if pw != pw2:
            Messagebox.show_warning("两次输入的密码不一致", APP_TITLE, parent=self)
            self.ent_pw2.delete(0, END)
            self.ent_pw2.focus_set()
            return
        if len(pw) < 6:
            ans = Messagebox.yesno("密码偏短（少于 6 位），仍要继续？", APP_TITLE,
                                   parent=self, buttons=["否:secondary", "是:primary"],
                                   localize=False)
            if ans != "是":
                return
        path = self._path.get().strip()
        if not path:
            path = filedialog.asksaveasfilename(
                title="新建保险库",
                defaultextension=".vault",
                initialfile=DEFAULT_VAULT,
                filetypes=[("CryptoNote 保险库", "*.vault")],
            )
            if not path:
                return
        try:
            vault = Vault.create(path, pw)
        except VaultError as exc:
            Messagebox.show_error(f"新建失败：{exc}", APP_TITLE, parent=self)
            return
        self.ent_pw.delete(0, END)  # 新建成功，清空密码框
        self.ent_pw2.delete(0, END)
        self.on_unlocked(vault, path)


class NoteCard(ttk.Frame):
    """单条笔记卡片：左侧选中色条 + 标题 + 正文预览 + 时间。整卡可点击。"""

    def __init__(self, master, note, preview, colors, on_click):
        super().__init__(master, padding=0)
        self.note_id = note["id"]
        self._colors = colors
        self._on_click = on_click
        self._selected = False

        # 左侧选中指示条
        self._bar = tk.Frame(self, width=4, background=colors.bg)
        self._bar.pack(side=LEFT, fill=Y)

        # 卡片内容容器（用原生 Frame 以便控制背景色）
        self._box = tk.Frame(self, background=colors.bg)
        self._box.pack(side=LEFT, fill=BOTH, expand=True)

        title = note["title"] or "（无标题）"
        updated = (note.get("updated") or "")[:16].replace("T", " ")
        # 限制标题长度避免卡片溢出（20 个中文字符或 40 个英文字符）
        if len(title) > 20:
            title = title[:17] + "…"

        self._lbl_title = tk.Label(
            self._box, text=title, font=F_CARD_TITLE, anchor=W,
            background=colors.bg, foreground=colors.fg)
        self._lbl_title.pack(fill=X, padx=12, pady=(10, 0))

        self._lbl_prev = tk.Label(
            self._box, text=preview or "（空）", font=F_CARD_SUB, anchor=W,
            background=colors.bg, foreground=colors.secondary)
        self._lbl_prev.pack(fill=X, padx=12, pady=(2, 0))

        self._lbl_time = tk.Label(
            self._box, text=updated, font=F_SMALL, anchor=W,
            background=colors.bg, foreground=colors.border)
        self._lbl_time.pack(fill=X, padx=12, pady=(2, 10))

        # 所有子件都绑定点击与悬停
        self._widgets = [self, self._box, self._lbl_title, self._lbl_prev, self._lbl_time]
        for w in self._widgets:
            w.bind("<Button-1>", lambda e: self._on_click(self.note_id))
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)

    def _paint(self, bg, bar):
        self._box.config(background=bg)
        self._bar.config(background=bar)
        for w in (self._lbl_title, self._lbl_prev, self._lbl_time):
            w.config(background=bg)

    def set_selected(self, selected):
        self._selected = selected
        if selected:
            self._paint(self._colors.light, self._colors.primary)
        else:
            self._paint(self._colors.bg, self._colors.bg)

    def _on_enter(self, _e):
        if not self._selected:
            self._paint(self._colors.active, self._colors.bg)

    def _on_leave(self, _e):
        if not self._selected:
            self._paint(self._colors.bg, self._colors.bg)


class PasswordDialog(tk.Toplevel):
    """带遮罩输入的改密码模态弹窗。两栏密码 + 确认，返回 None 或新密码。"""

    def __init__(self, master, title="设置新主密码"):
        super().__init__(master)
        self.result = None
        self.title(APP_TITLE)
        self.resizable(False, False)
        self.transient(master)

        card = ttk.Frame(self, padding=28)
        card.pack(fill=BOTH, expand=True)

        ttk.Label(card, text="🔑 " + title, font=F_H1, bootstyle=PRIMARY).pack(
            anchor=W, pady=(0, 18))

        ttk.Label(card, text="新主密码", font=F_BODY).pack(anchor=W, pady=(0, 4))
        self.ent1 = ttk.Entry(card, show="●", width=34, font=F_BODY)
        self.ent1.pack(fill=X, pady=(0, 12))

        ttk.Label(card, text="再次输入", font=F_BODY).pack(anchor=W, pady=(0, 4))
        self.ent2 = ttk.Entry(card, show="●", width=34, font=F_BODY)
        self.ent2.pack(fill=X, pady=(0, 8))

        self.hint = ttk.Label(card, text="", font=F_SMALL, bootstyle=DANGER)
        self.hint.pack(anchor=W, pady=(0, 16))

        btns = ttk.Frame(card)
        btns.pack(fill=X)
        ttk.Button(btns, text="确定", bootstyle=PRIMARY, width=10,
                   command=self._ok).pack(side=RIGHT)
        ttk.Button(btns, text="取消", bootstyle="secondary-link",
                   command=self._cancel).pack(side=RIGHT, padx=(0, 8))

        self.ent1.focus_set()
        self.ent1.bind("<Return>", lambda e: self.ent2.focus_set())
        self.ent2.bind("<Return>", lambda e: self._ok())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda e: self._cancel())

        self.update_idletasks()
        self._center_on(master)
        self.grab_set()

    def _center_on(self, master):
        self.update_idletasks()
        mx, my = master.winfo_rootx(), master.winfo_rooty()
        mw, mh = master.winfo_width(), master.winfo_height()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{mx + (mw - w) // 2}+{my + (mh - h) // 2}")

    def _ok(self):
        pw, pw2 = self.ent1.get(), self.ent2.get()
        if not pw:
            self.hint.config(text="密码不能为空")
            return
        if pw != pw2:
            self.hint.config(text="两次输入不一致")
            self.ent2.delete(0, END)
            self.ent2.focus_set()
            return
        if len(pw) < 6:
            ans = Messagebox.yesno("密码偏短（少于 6 位），仍要继续？", APP_TITLE,
                                   parent=self, buttons=["否:secondary", "是:primary"],
                                   localize=False)
            if ans != "是":
                return
        self.result = pw
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class FindDialog(tk.Toplevel):
    """非模态查找/替换浮窗，作用于给定的 Text 控件。"""

    def __init__(self, master, text_widget, colors, replace=False, on_change=None):
        super().__init__(master)
        self.txt = text_widget
        self._on_change = on_change
        self.title("查找 / 替换")
        self.resizable(False, False)
        self.transient(master)
        self._last_index = "1.0"
        self.txt.tag_configure("find_hit", background=colors.warning, foreground=colors.fg)

        card = ttk.Frame(self, padding=16)
        card.pack(fill=BOTH, expand=True)

        ttk.Label(card, text="查找", font=F_BODY).grid(row=0, column=0, sticky=W, pady=4)
        self.ent_find = ttk.Entry(card, width=30, font=F_BODY)
        self.ent_find.grid(row=0, column=1, columnspan=2, sticky=EW, pady=4, padx=(8, 0))

        ttk.Label(card, text="替换为", font=F_BODY).grid(row=1, column=0, sticky=W, pady=4)
        self.ent_repl = ttk.Entry(card, width=30, font=F_BODY)
        self.ent_repl.grid(row=1, column=1, columnspan=2, sticky=EW, pady=4, padx=(8, 0))

        self.var_case = tk.BooleanVar(value=False)
        ttk.Checkbutton(card, text="区分大小写", variable=self.var_case,
                        bootstyle="round-toggle").grid(row=2, column=0, columnspan=3,
                                                       sticky=W, pady=(4, 10))

        btns = ttk.Frame(card)
        btns.grid(row=3, column=0, columnspan=3, sticky=EW)
        ttk.Button(btns, text="查找下一个", bootstyle=PRIMARY,
                   command=self.find_next).pack(side=LEFT)
        ttk.Button(btns, text="替换", bootstyle="secondary-outline",
                   command=self.replace_one).pack(side=LEFT, padx=4)
        ttk.Button(btns, text="全部替换", bootstyle="secondary-outline",
                   command=self.replace_all).pack(side=LEFT)
        ttk.Button(btns, text="关闭", bootstyle="secondary-link",
                   command=self._close).pack(side=RIGHT)

        self.status = ttk.Label(card, text="", font=F_SMALL, bootstyle=SECONDARY)
        self.status.grid(row=4, column=0, columnspan=3, sticky=W, pady=(8, 0))

        card.columnconfigure(1, weight=1)
        self.ent_find.focus_set()
        self.ent_find.bind("<Return>", lambda e: self.find_next())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._close())
        self.update_idletasks()
        mx, my = master.winfo_rootx(), master.winfo_rooty()
        self.geometry(f"+{mx + 80}+{my + 80}")

    def _close(self):
        self.txt.tag_remove("find_hit", "1.0", END)
        self.destroy()

    def find_next(self):
        needle = self.ent_find.get()
        if not needle:
            return None
        self.txt.tag_remove("find_hit", "1.0", END)
        pos = self.txt.search(needle, self._last_index, nocase=not self.var_case.get(),
                              stopindex=END)
        if not pos:
            # 回到开头再找一次（循环查找）。
            pos = self.txt.search(needle, "1.0", nocase=not self.var_case.get(),
                                  stopindex=END)
            if not pos:
                self.status.config(text="未找到")
                return None
        end = f"{pos}+{len(needle)}c"
        self.txt.tag_add("find_hit", pos, end)
        self.txt.mark_set("insert", end)
        self.txt.see(pos)
        self._last_index = end
        self.status.config(text="")
        return pos, end

    def replace_one(self):
        hit = self.find_next()
        if not hit:
            return
        pos, end = hit
        self.txt.delete(pos, end)
        self.txt.insert(pos, self.ent_repl.get())
        self._last_index = f"{pos}+{len(self.ent_repl.get())}c"
        if self._on_change:
            self._on_change()

    def replace_all(self):
        needle = self.ent_find.get()
        if not needle:
            return
        repl = self.ent_repl.get()
        count = 0
        idx = "1.0"
        while True:
            pos = self.txt.search(needle, idx, nocase=not self.var_case.get(), stopindex=END)
            if not pos:
                break
            end = f"{pos}+{len(needle)}c"
            self.txt.delete(pos, end)
            self.txt.insert(pos, repl)
            idx = f"{pos}+{len(repl)}c"
            count += 1
        self.status.config(text=f"已替换 {count} 处")
        if count and self._on_change:
            self._on_change()


class MainView(ttk.Frame):
    """主编辑界面：顶栏 + 左侧卡片列表 + 右侧标题/正文编辑器。"""

    def __init__(self, master, vault: Vault, vault_name, on_lock):
        super().__init__(master, padding=0)
        self.vault = vault
        self.vault_name = vault_name
        self.on_lock = on_lock
        self.colors = master.style.colors
        self._current_id = None
        self._dirty = False
        self._lock_after = None
        self._notes = []
        self._cards = {}                 # note_id -> NoteCard
        self._preview_cache = {}         # note_id -> preview，缓存预览文本
        # 富文本/编辑器状态
        self._typing_flags = 0           # 接下来输入应用的样式位（输入样式跟随）
        self._sticky_typing = False      # 无选区点 B/I/U 后置位，光标移动前不被覆盖
        self._body_size = BODY_FONT_DEFAULT
        self._undo_stack = []            # [(text, spans, cursor), ...]
        self._redo_stack = []
        self._undo_after = None          # 去抖快照定时器
        self._suppress_change = False    # 程序性改动时不触发快照/脏标记
        self._autosave_after = None
        self._autosave_on = tk.BooleanVar(value=True)
        self._wrap_on = tk.BooleanVar(value=True)
        self._links = []                 # [(start, end, url), ...] 当前笔记的超链接
        self._link_tags = {}             # {tag 名: url}，每条链接一个 tag，tag 随文字移动
        self._link_seq = 0               # 链接 tag 序号
        self._find_dialog = None
        self._filter_after = None        # 搜索框去抖定时器
        self._build()
        self._build_menubar()
        self._reload_list()
        self._select_first()            # 进库即选中第一条，让"当前笔记"有值
        self._reset_lock_timer()
        self._schedule_autosave()

    def _build(self):
        c = self.colors

        # ---------- 顶栏 ----------
        header = ttk.Frame(self, padding=(16, 12))
        header.pack(fill=X)
        ttk.Label(header, text="🔐 CryptoNote", font=F_H1, bootstyle=PRIMARY).pack(side=LEFT)
        ttk.Label(header, text=f"  ·  {self.vault_name}", font=F_SMALL,
                  bootstyle=SECONDARY).pack(side=LEFT, padx=(2, 0))
        ttk.Button(header, text="🔒 锁定", bootstyle="secondary-outline",
                   command=self.lock).pack(side=RIGHT)
        ttk.Button(header, text="🔑 改密码", bootstyle="link",
                   command=self.change_password).pack(side=RIGHT, padx=(0, 4))
        ttk.Separator(self, orient="horizontal").pack(fill=X)

        # ---------- 主体 ----------
        body = ttk.Frame(self)
        body.pack(fill=BOTH, expand=True)

        # 左栏：新建按钮 + 卡片列表
        left = ttk.Frame(body, padding=(12, 12, 6, 12))
        left.pack(side=LEFT, fill=Y)
        left.configure(width=260)
        left.pack_propagate(False)

        ttk.Button(left, text="＋  新建笔记", bootstyle=PRIMARY,
                   command=self.new_note).pack(fill=X, pady=(0, 8))

        # 搜索框：按标题实时过滤，纯前端，不触碰加密。
        self._filter_var = tk.StringVar()
        search = ttk.Entry(left, textvariable=self._filter_var, font=F_SMALL,
                           bootstyle="secondary")
        search.pack(fill=X, pady=(0, 10))
        self._filter_var.trace_add("write", lambda *_: self._schedule_filter_render())

        list_card = ttk.Frame(left, bootstyle="light")
        list_card.pack(fill=BOTH, expand=True)
        self.scroller = ScrolledFrame(list_card, autohide=True)
        self.scroller.pack(fill=BOTH, expand=True, padx=2, pady=2)
        self.list_holder = self.scroller

        ttk.Separator(body, orient="vertical").pack(side=LEFT, fill=Y)

        # 右栏：编辑器
        right = ttk.Frame(body, padding=(20, 16))
        right.pack(side=LEFT, fill=BOTH, expand=True)

        self.ent_title = ttk.Entry(right, font=F_TITLE, bootstyle="primary",
                                   validate="key",
                                   validatecommand=(self.register(self._validate_title), "%P"))
        self.ent_title.pack(side=TOP, fill=X)
        # 限制标题长度为 256 字符：KeyPress 检查 + validatecommand（挡粘贴）+ Vault 层兜底
        self.ent_title.bind("<KeyPress>", self._on_title_press)
        self.ent_title.bind("<KeyPress>", self._mark_dirty, add="+")
        ttk.Separator(right, orient="horizontal").pack(side=TOP, fill=X, pady=(10, 8))

        # 格式工具栏：B / I / U / S + 清除格式 / 列表 / 时间戳 / 查找。
        fmt = ttk.Frame(right)
        fmt.pack(side=TOP, fill=X, pady=(0, 10))
        ttk.Button(fmt, text="B", width=3, bootstyle="secondary-outline",
                   command=lambda: self._toggle_style(ST_BOLD)).pack(side=LEFT)
        ttk.Button(fmt, text="I", width=3, bootstyle="secondary-outline",
                   command=lambda: self._toggle_style(ST_ITALIC)).pack(side=LEFT, padx=(4, 0))
        ttk.Button(fmt, text="U", width=3, bootstyle="secondary-outline",
                   command=lambda: self._toggle_style(ST_UNDERLINE)).pack(side=LEFT, padx=(4, 0))
        ttk.Button(fmt, text="S", width=3, bootstyle="secondary-outline",
                   command=lambda: self._toggle_style(ST_STRIKE)).pack(side=LEFT, padx=(4, 0))
        ttk.Separator(fmt, orient="vertical").pack(side=LEFT, fill=Y, padx=8, pady=2)
        ttk.Button(fmt, text="清除格式", bootstyle="secondary-link",
                   command=self.clear_format).pack(side=LEFT)
        ttk.Button(fmt, text="• 列表", bootstyle="secondary-link",
                   command=lambda: self.toggle_list("• ")).pack(side=LEFT)
        ttk.Button(fmt, text="1. 编号", bootstyle="secondary-link",
                   command=lambda: self.toggle_list("number")).pack(side=LEFT)
        ttk.Button(fmt, text="🕒 时间", bootstyle="secondary-link",
                   command=self.insert_timestamp).pack(side=LEFT)
        ttk.Button(fmt, text="🔍 查找", bootstyle="secondary-link",
                   command=self.open_find).pack(side=LEFT)

        # 底部信息行：状态（左） + 字数行列统计（中） + 保存/删除（右）。
        actions = ttk.Frame(right, padding=(0, 12, 0, 0))
        actions.pack(side=BOTTOM, fill=X)
        self.status = ttk.Label(actions, text="", font=F_SMALL, bootstyle=SECONDARY)
        self.status.pack(side=LEFT)
        ttk.Button(actions, text="🗑 删除", bootstyle="danger-outline",
                   command=self.delete_note).pack(side=RIGHT, padx=(8, 0))
        ttk.Button(actions, text="💾 保存", bootstyle=SUCCESS,
                   command=self.save_note).pack(side=RIGHT)
        self.stat_label = ttk.Label(actions, text="", font=F_SMALL, bootstyle=SECONDARY)
        self.stat_label.pack(side=RIGHT, padx=(0, 14))

        text_wrap = tk.Frame(right, background=c.bg, highlightthickness=1,
                             highlightbackground=c.border, highlightcolor=c.primary)
        text_wrap.pack(side=TOP, fill=BOTH, expand=True)
        self._body_font = tkfont.Font(family=FONT, size=self._body_size)
        self.txt_body = tk.Text(
            text_wrap, wrap=WORD, undo=False, font=self._body_font, relief=FLAT,
            width=1, height=1,          # 初始尺寸给小，靠 expand 填充，避免索要 24 行撑爆布局
            borderwidth=0, highlightthickness=0, padx=14, pady=12,
            background=c.bg, foreground=c.fg, insertbackground=c.primary,
            selectbackground=c.primary, selectforeground=c.selectfg)
        tsb = ttk.Scrollbar(text_wrap, orient="vertical", command=self.txt_body.yview,
                            bootstyle="round")
        self._hsb = ttk.Scrollbar(text_wrap, orient="horizontal", command=self.txt_body.xview,
                                  bootstyle="round")
        self.txt_body.config(yscrollcommand=tsb.set, xscrollcommand=self._hsb.set)
        tsb.pack(side=RIGHT, fill=Y)
        # 横向滚动条仅在关闭自动换行时显示，这里先不 pack。
        self.txt_body.pack(side=LEFT, fill=BOTH, expand=True)
        self.txt_body.bind("<KeyPress>", self._mark_dirty)
        self._init_rich_fonts()
        self._init_editor_events()
        self._build_context_menu()

        for seq in ("<Key>", "<Button>"):
            self.bind_all(seq, lambda e: self._reset_lock_timer(), add="+")

        # 快捷键
        self.bind_all("<Control-s>", lambda e: (self.save_note(), "break")[1])
        self.bind_all("<Control-n>", lambda e: (self.new_note(), "break")[1])
        self.txt_body.bind("<Control-b>", lambda e: (self._toggle_style(ST_BOLD), "break")[1])
        self.txt_body.bind("<Control-i>", lambda e: (self._toggle_style(ST_ITALIC), "break")[1])
        self.txt_body.bind("<Control-u>", lambda e: (self._toggle_style(ST_UNDERLINE), "break")[1])
        self.txt_body.bind("<Control-z>", lambda e: (self.undo(), "break")[1])
        self.txt_body.bind("<Control-y>", lambda e: (self.redo(), "break")[1])
        self.txt_body.bind("<Control-Shift-Z>", lambda e: (self.redo(), "break")[1])
        self.txt_body.bind("<Control-f>", lambda e: (self.open_find(), "break")[1])
        self.txt_body.bind("<Control-h>", lambda e: (self.open_find(replace=True), "break")[1])
        self.txt_body.bind("<Control-k>", lambda e: (self.insert_link(), "break")[1])
        self.txt_body.bind("<Control-a>", lambda e: (self._select_all(), "break")[1])
        self.txt_body.bind("<Control-equal>", lambda e: (self.zoom(1), "break")[1])
        self.txt_body.bind("<Control-plus>", lambda e: (self.zoom(1), "break")[1])
        self.txt_body.bind("<Control-minus>", lambda e: (self.zoom(-1), "break")[1])
        self.txt_body.bind("<Control-0>", lambda e: (self.zoom(0), "break")[1])
        self.txt_body.bind("<Control-MouseWheel>",
                           lambda e: (self.zoom(1 if e.delta > 0 else -1), "break")[1])
        self.txt_body.bind("<F5>", lambda e: (self.insert_timestamp(), "break")[1])

    # ---------- 富文本（加粗/斜体/下划线/删除线） ----------

    def _init_rich_fonts(self):
        """预建 4 种字体组合 + 合成 tag 管理。每段文字只贴一个 rt 标签避免字体冲突。"""
        s = self._body_size
        self._rt_fonts = {
            0: self._body_font,
            ST_BOLD: tkfont.Font(family=FONT, size=s, weight="bold"),
            ST_ITALIC: tkfont.Font(family=FONT, size=s, slant="italic"),
            ST_BOLD | ST_ITALIC: tkfont.Font(family=FONT, size=s,
                                             weight="bold", slant="italic"),
        }
        self._rt_tags = set()

    def _configure_link_tag(self, name):
        """给一条链接 tag 配蓝色下划线 + 点击/悬停行为。"""
        self.txt_body.tag_configure(name, foreground='#0066cc', underline=True)
        self.txt_body.tag_bind(name, '<Button-1>', self._on_link_click)
        self.txt_body.tag_bind(name, '<Enter>', lambda e: self.txt_body.config(cursor='hand2'))
        self.txt_body.tag_bind(name, '<Leave>', lambda e: self.txt_body.config(cursor='arrow'))

    def _new_link_tag(self, url) -> str:
        """新建一个已配置的链接 tag 并记下它的 URL，返回 tag 名。"""
        name = f"link{self._link_seq}"
        self._link_seq += 1
        self._configure_link_tag(name)
        self._link_tags[name] = url
        return name

    def _clear_link_tags(self):
        """删掉全部链接 tag（加载另一条笔记前调用）。"""
        for name in [t for t in self.txt_body.tag_names() if t.startswith("link")]:
            try:
                self.txt_body.tag_delete(name)
            except tk.TclError:
                pass

    def _tag_for_flags(self, flags: int) -> str:
        """惰性创建并返回某样式组合对应的 tag 名（rt<flags>）。"""
        name = f"rt{flags}"
        if name not in self._rt_tags:
            self.txt_body.tag_configure(
                name,
                font=self._rt_fonts[flags & (ST_BOLD | ST_ITALIC)],
                underline=bool(flags & ST_UNDERLINE),
                overstrike=bool(flags & ST_STRIKE),
            )
            self._rt_tags.add(name)
        return name

    def _flags_at(self, index) -> int:
        """读取某字符位置当前的样式位（约定每字符至多一个 rt 标签）。"""
        for name in self.txt_body.tag_names(index):
            if name.startswith("rt"):
                try:
                    return int(name[2:])
                except ValueError:
                    pass
        return 0

    def _retag(self, start, end, flags: int):
        """把 [start,end) 重置为单一样式：先清掉所有 rt 标签，再贴新的。"""
        for name in self._rt_tags:
            self.txt_body.tag_remove(name, start, end)
        if flags:
            self.txt_body.tag_add(self._tag_for_flags(flags), start, end)

    def _toggle_style(self, bit: int):
        """Toggle style for selection or upcoming typing."""
        try:
            start = str(self.txt_body.index("sel.first"))
            end = str(self.txt_body.index("sel.last"))
        except tk.TclError:
            # No selection: toggle typing style
            self._typing_flags ^= bit
            self._sticky_typing = True
            on = "开" if (self._typing_flags & bit) else "关"
            self._set_status(f"接下来输入：{self._style_name(bit)} {on}")
            return
        self._begin_change(discrete=True)
        all_have = True
        idx = start
        while self.txt_body.compare(idx, "<", end):
            if not (self._flags_at(idx) & bit):
                all_have = False
                break
            idx = self.txt_body.index(f"{idx}+1c")
        add = not all_have
        # 逐字符重算样式位并贴标签。
        idx = start
        while self.txt_body.compare(idx, "<", end):
            nxt = self.txt_body.index(f"{idx}+1c")
            cur = self._flags_at(idx)
            new = (cur | bit) if add else (cur & ~bit)
            self._retag(idx, nxt, new)
            idx = nxt
        self._mark_dirty()
        self._update_stats()

    @staticmethod
    def _style_name(bit: int) -> str:
        return {ST_BOLD: "加粗", ST_ITALIC: "斜体",
                ST_UNDERLINE: "下划线", ST_STRIKE: "删除线"}.get(bit, "样式")

    def _apply_rich(self, text: str, spans):
        """加载笔记时：清掉旧标签，按 spans 铺样式。"""
        for name in self._rt_tags:
            self.txt_body.tag_remove(name, "1.0", END)
        for s, e, flags in spans:
            if flags:
                self.txt_body.tag_add(self._tag_for_flags(int(flags)),
                                      f"1.0+{int(s)}c", f"1.0+{int(e)}c")

    def _apply_links(self, text, links):
        """Apply link tags to text ranges.

        每条链接一个独立 tag（link<N>），URL 存在 _link_tags 里。
        tag 会随文字编辑自动移动，所以链接前面的文字被改动后，
        保存时仍能按 tag 的当前范围找回 URL，不会丢链接。
        """
        self._clear_link_tags()
        self._link_tags = {}
        self._link_seq = 0
        for s, e, url in links:
            name = self._new_link_tag(url)
            start = f"1.0+{int(s)}c"
            end = f"1.0+{int(e)}c"
            self.txt_body.tag_add(name, start, end)

    def _on_link_click(self, event):
        """Handle link click: open URL in browser."""
        idx = str(self.txt_body.index(f'@{event.x},{event.y}'))
        for name, url in list(getattr(self, '_link_tags', {}).items()):
            ranges = self.txt_body.tag_ranges(name)
            for k in range(0, len(ranges), 2):
                start = str(ranges[k])
                end = str(ranges[k + 1])
                if self.txt_body.compare(start, '<=', idx) and self.txt_body.compare(idx, '<', end):
                    if is_safe_link_url(url):
                        import webbrowser
                        webbrowser.open(url)
                    return

    def _collect_rich(self):
        """Collect text + styles + links for saving. Returns (text, spans, links)."""
        text = self.txt_body.get("1.0", "end-1c")
        n = len(text)
        if not n:
            return text, [], []
        flags = bytearray(n)
        for name in self._rt_tags:
            try:
                bits = int(name[2:])
            except ValueError:
                continue
            if not bits:
                continue
            ranges = self.txt_body.tag_ranges(name)
            for k in range(0, len(ranges), 2):
                a = self._char_index(ranges[k])
                b = self._char_index(ranges[k + 1])
                for j in range(a, min(b, n)):
                    flags[j] = bits
        spans = []
        i = 0
        while i < n:
            f = flags[i]
            j = i + 1
            while j < n and flags[j] == f:
                j += 1
            if f:
                spans.append((i, j, f))
            i = j

        # Collect links from per-link tags: tag 随文字移动，范围始终是
        # 当前的，所以链接前面的文字被改动后也不会丢 URL。
        links = []
        for name, url in getattr(self, '_link_tags', {}).items():
            ranges = self.txt_body.tag_ranges(name)
            for k in range(0, len(ranges), 2):
                a = self._char_index(str(ranges[k]))
                b = self._char_index(str(ranges[k + 1]))
                if a < b:
                    links.append((a, b, url))
        links.sort()

        return text, spans, links

    def _char_index(self, index) -> int:
        """把 Text 索引转成从开头算起的字符偏移。"""
        cnt = self.txt_body.count("1.0", index, "chars")
        if cnt is None:
            return 0
        return cnt[0] if isinstance(cnt, tuple) else int(cnt)

    # ---------- 输入样式跟随（widget proxy 拦截真实插入/删除） ----------

    def _init_editor_events(self):
        """安装 widget proxy：拦截底层 insert/delete，可靠捕获中文 IME 连续上屏、
        粘贴、程序性改动等所有文本变更，比按键事件更准。"""
        self._proxy_busy = False
        self._change_open = False       # 当前是否已为这串改动压过"前置快照"
        w = self.txt_body
        self._orig_text = str(w) + "_orig"
        w.tk.call("rename", str(w), self._orig_text)
        w.tk.createcommand(str(w), self._text_proxy)
        w.bind("<ButtonRelease-1>", self._on_cursor_move, add="+")
        w.bind("<KeyRelease>", lambda e: self._update_stats(), add="+")
        nav = ("Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next")
        for k in nav:
            w.bind(f"<KeyRelease-{k}>", self._on_cursor_move, add="+")

    def _text_proxy(self, *args):
        """Proxy Text commands: track insert/delete with style + dirty + snapshot."""
        w = self.txt_body
        cmd = args[0] if args else ""
        if self._suppress_change or self._proxy_busy:
            try:
                return w.tk.call((self._orig_text,) + args)
            except tk.TclError:
                return None

        start = None
        if cmd == "insert":
            try:
                start = w.tk.call(self._orig_text, "index", args[1])
            except tk.TclError:
                pass
            self._begin_change()
        elif cmd == "delete":
            self._begin_change()

        try:
            self._proxy_busy = True
            result = w.tk.call((self._orig_text,) + args)
        except tk.TclError as e:
            error_msg = str(e)
            if "text doesn't contain" in error_msg or "bad text index" in error_msg:
                return None
            raise
        finally:
            self._proxy_busy = False

        if cmd == "insert" and start:
            chars = "".join(args[i] for i in range(2, len(args), 2) if isinstance(args[i], str))
            if chars:
                try:
                    end = w.tk.call(self._orig_text, "index", f"{start}+{len(chars)}c")
                    self._retag(start, end, self._typing_flags)
                except tk.TclError:
                    pass
                self._mark_dirty()
                self._schedule_snapshot()
                self._update_stats()
        elif cmd == "delete":
            self._mark_dirty()
            self._schedule_snapshot()
            self._update_stats()
        return result

    def _on_cursor_move(self, _event=None):
        # 光标移动后，待输入样式跟随光标左侧字符（除非刚做过 sticky 切换）。
        if self._sticky_typing:
            self._sticky_typing = False
        else:
            try:
                self._typing_flags = self._flags_at("insert-1c") if self.txt_body.compare(
                    "insert", ">", "1.0") else 0
            except tk.TclError:
                self._typing_flags = 0
        self._update_stats()

    # ---------- 快照式撤销（文字 + 样式一起回滚） ----------

    def _begin_change(self, discrete=False):
        """在一串改动开始前压入"前置状态"快照（每串只压一次）。
        discrete=True 用于格式等离散操作，压完即结束、不与后续合并。"""
        if self._suppress_change:
            return
        if not self._change_open:
            self._snapshot_push()
            self._change_open = not discrete

    def _snapshot(self):
        """Capture current state for undo: text, spans, links, cursor position."""
        text, spans, links = self._collect_rich()
        return (text, spans, links, self.txt_body.index("insert"))

    def _snapshot_push(self):
        """Push current state to undo stack (call before operation)."""
        if self._suppress_change:
            return
        snap = self._snapshot()
        # Compare text, spans, links (skip cursor position)
        if self._undo_stack and self._undo_stack[-1][:3] == snap[:3]:
            return
        self._undo_stack.append(snap)
        if len(self._undo_stack) > UNDO_MAX:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _schedule_snapshot(self):
        """打字停顿 UNDO_COALESCE_MS 后，结束当前改动串（下次改动会重新压前置快照）。"""
        if self._undo_after is not None:
            self.after_cancel(self._undo_after)
        self._undo_after = self.after(UNDO_COALESCE_MS, self._close_change)

    def _close_change(self):
        self._change_open = False
        self._undo_after = None

    def _restore(self, snap):
        text, spans, links, cursor = snap
        self._suppress_change = True
        try:
            self.txt_body.delete("1.0", END)
            self.txt_body.insert("1.0", text)
            self._apply_rich(text, spans)
            self._apply_links(text, links)
            self._links = links
            try:
                self.txt_body.mark_set("insert", cursor)
            except tk.TclError:
                pass
        finally:
            self._suppress_change = False
        self._mark_dirty()
        self._update_stats()

    def undo(self):
        if self._undo_after is not None:
            self.after_cancel(self._undo_after)
            self._undo_after = None
        self._change_open = False
        if not self._undo_stack:
            self._set_status("没有可撤销的操作")
            return
        self._redo_stack.append(self._snapshot())
        self._restore(self._undo_stack.pop())
        self._set_status("已撤销")

    def redo(self):
        self._change_open = False
        if not self._redo_stack:
            self._set_status("没有可重做的操作")
            return
        self._undo_stack.append(self._snapshot())
        self._restore(self._redo_stack.pop())
        self._set_status("已重做")

    # ---------- 标题输入限制 ----------

    def _validate_title(self, proposed: str) -> bool:
        """标题 Entry 校验（validatecommand）：新值长度不得超过上限。

        validate="key" 下键盘输入、粘贴、程序性 insert 都会走这里；
        返回 False 则本次改动被拒绝，标题保持原值。
        """
        return len(proposed) <= TITLE_MAX_LENGTH

    def _on_title_press(self, event):
        """限制标题长度不超过 256 字符。"""
        if len(self.ent_title.get()) > 256:
            self.ent_title.delete(256, END)
            return "break"

    # ---------- 字数 / 行列统计 ----------

    def _update_stats(self, _event=None):
        if not hasattr(self, "stat_label"):
            return
        text = self.txt_body.get("1.0", "end-1c")
        chars = len(text)
        row, col = self.txt_body.index("insert").split(".")
        self.stat_label.config(text=f"字数 {chars} · 第 {row} 行 {int(col)+1} 列")

    # ---------- 字号缩放 ----------

    def zoom(self, direction: int):
        """direction: +1 放大，-1 缩小，0 还原默认。"""
        if direction == 0:
            self._body_size = BODY_FONT_DEFAULT
        else:
            self._body_size = max(BODY_FONT_MIN,
                                  min(BODY_FONT_MAX, self._body_size + direction))
        self._body_font.configure(size=self._body_size)
        for flags, f in self._rt_fonts.items():
            if flags != 0:
                f.configure(size=self._body_size)
        self._set_status(f"字号 {self._body_size}")

    # ---------- 清除格式 / 列表 / 时间戳 ----------

    def clear_format(self):
        try:
            start = self.txt_body.index("sel.first")
            end = self.txt_body.index("sel.last")
        except tk.TclError:
            start, end = "1.0", END
        self._begin_change(discrete=True)
        self._retag(start, end, 0)
        self._typing_flags = 0
        self._mark_dirty()
        self._set_status("已清除格式")

    def toggle_list(self, kind: str):
        """给选中行（无选区则当前行）加/去项目符号或编号前缀。"""
        self._begin_change(discrete=True)
        self._suppress_change = True    # 整批改动作为一步，前缀不继承待输入样式
        try:
            try:
                first = int(self.txt_body.index("sel.first").split(".")[0])
                last = int(self.txt_body.index("sel.last").split(".")[0])
            except tk.TclError:
                first = last = int(self.txt_body.index("insert").split(".")[0])
            # 判断是否已全部带前缀 -> 决定加还是去。
            lines = [self.txt_body.get(f"{r}.0", f"{r}.end") for r in range(first, last + 1)]
            bullet = "• "
            has_all = all(ln.startswith(bullet) or self._is_numbered(ln)
                          for ln in lines if ln.strip())
            num = 1
            for r in range(first, last + 1):
                ln = self.txt_body.get(f"{r}.0", f"{r}.end")
                if has_all:                 # 去前缀
                    new = self._strip_prefix(ln)
                elif not ln.strip():
                    new = ln                # 空行不加
                    continue
                else:
                    new = (bullet + ln) if kind == "• " else f"{num}. " + ln
                    num += 1
                self.txt_body.delete(f"{r}.0", f"{r}.end")
                self.txt_body.insert(f"{r}.0", new)
        finally:
            self._suppress_change = False
        self._mark_dirty()
        self._update_stats()

    @staticmethod
    def _is_numbered(line: str) -> bool:
        i = 0
        while i < len(line) and line[i].isdigit():
            i += 1
        return i > 0 and line[i:i + 2] == ". "

    def _strip_prefix(self, line: str) -> str:
        if line.startswith("• "):
            return line[2:]
        if self._is_numbered(line):
            return line[line.index(". ") + 2:]
        return line

    def insert_timestamp(self):
        # 插入走代理，自动压快照、套用待输入样式；这里只补一句状态。
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.txt_body.insert("insert", stamp)
        self._update_stats()

    def insert_link(self):
        """Insert hyperlink: prompt for URL and apply to selection or insert new text."""
        try:
            start = self.txt_body.index("sel.first")
            end = self.txt_body.index("sel.last")
            text = self.txt_body.get(start, end)
        except tk.TclError:
            text = None

        from tkinter.simpledialog import askstring
        url = askstring("Insert Link", "URL:", parent=self)
        if not url:
            return
        if not is_safe_link_url(url):
            Messagebox.show_warning("仅支持 http:// 或 https:// 链接", APP_TITLE, parent=self)
            return

        if text:
            # Apply link to selection
            self._begin_change(discrete=True)
            start_str = str(start)
            end_str = str(end)
            name = self._new_link_tag(url)
            self.txt_body.tag_add(name, start_str, end_str)
            s = int(self._char_index(start_str))
            e = int(self._char_index(end_str))
            self._links.append((s, e, url))
            self._mark_dirty()
        else:
            # Insert link text at cursor
            link_text = url.split('://')[-1].split('/')[0]
            self._begin_change(discrete=True)
            insert_idx = str(self.txt_body.index("insert"))
            self.txt_body.insert(insert_idx, link_text)
            end_idx = str(self.txt_body.index(f"{insert_idx}+{len(link_text)}c"))
            name = self._new_link_tag(url)
            self.txt_body.tag_add(name, insert_idx, end_idx)
            s = int(self._char_index(insert_idx))
            e = int(self._char_index(end_idx))
            self._links.append((s, e, url))
            self._mark_dirty()

    def _select_all(self):
        try:
            self.txt_body.tag_add("sel", "1.0", "end-1c")
        except tk.TclError:
            pass
        return "break"

    # ---------- 自动换行 / 自动保存 ----------

    def toggle_wrap(self):
        if self._wrap_on.get():
            self.txt_body.config(wrap=WORD)
            self._hsb.pack_forget()
        else:
            self.txt_body.config(wrap="none")
            self._hsb.pack(side=BOTTOM, fill=X)
        self._set_status("自动换行：" + ("开" if self._wrap_on.get() else "关"))

    def export_txt(self):
        """把当前笔记导出为 .txt 明文文件。导出即脱离加密保护，提示用户。"""
        if self._current_id is None:
            self._set_status("没有可导出的笔记")
            return
        title = self.ent_title.get().strip() or "未命名"
        default = "".join(ch for ch in title if ch not in '\\/:*?"<>|') + ".txt"
        path = filedialog.asksaveasfilename(
            title="导出为 txt（明文）", defaultextension=".txt",
            initialfile=default, filetypes=[("文本文件", "*.txt")])
        if not path:
            return
        text, _, _ = self._collect_rich()
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write((title + "\n\n" + text) if title else text)
        except OSError as exc:
            Messagebox.show_error(f"导出失败：{exc}", APP_TITLE, parent=self)
            return
        self._set_status(f"已导出明文：{os.path.basename(path)}")

    def _schedule_autosave(self):
        if self._autosave_after is not None:
            self.after_cancel(self._autosave_after)
        self._autosave_after = self.after(AUTOSAVE_MS, self._autosave_tick)

    def _autosave_tick(self):
        if self._autosave_on.get() and self._dirty and self._current_id is not None:
            title = self.ent_title.get()
            text, spans, links = self._collect_rich()
            try:
                self.vault.update_note(self._current_id, title, rt_dumps(text, spans, links))
                self._dirty = False
                self._set_status("已自动保存 ✓")
            except VaultError:
                pass
        self._schedule_autosave()

    # ---------- 右键菜单 ----------

    def _build_context_menu(self):
        m = tk.Menu(self.txt_body, tearoff=0)
        m.add_command(label="剪切", command=lambda: self.txt_body.event_generate("<<Cut>>"))
        m.add_command(label="复制", command=lambda: self.txt_body.event_generate("<<Copy>>"))
        m.add_command(label="粘贴", command=lambda: self.txt_body.event_generate("<<Paste>>"))
        m.add_command(label="全选", command=self._select_all)
        m.add_separator()
        m.add_command(label="加粗 (Ctrl+B)", command=lambda: self._toggle_style(ST_BOLD))
        m.add_command(label="斜体 (Ctrl+I)", command=lambda: self._toggle_style(ST_ITALIC))
        m.add_command(label="下划线 (Ctrl+U)", command=lambda: self._toggle_style(ST_UNDERLINE))
        m.add_command(label="删除线 (Ctrl+Shift+X)", command=lambda: self._toggle_style(ST_STRIKE))
        m.add_command(label="插入链接 (Ctrl+K)", command=self.insert_link)
        m.add_command(label="清除格式", command=self.clear_format)
        m.add_separator()
        m.add_command(label="插入时间 (F5)", command=self.insert_timestamp)
        self._ctx_menu = m
        self.txt_body.bind("<Button-3>", self._popup_context)

    def _popup_context(self, event):
        try:
            self._ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._ctx_menu.grab_release()

    # ---------- 查找 / 替换 ----------

    def open_find(self, replace=False):
        if self._find_dialog is not None and self._find_dialog.winfo_exists():
            self._find_dialog.lift()
            self._find_dialog.focus_set()
            return
        self._find_dialog = FindDialog(self.winfo_toplevel(), self.txt_body,
                                       self.colors, replace=replace,
                                       on_change=self._on_find_change)

    def _on_find_change(self):
        self._snapshot_push()
        self._mark_dirty()
        self._update_stats()

    # ---------- 菜单栏 ----------

    def _build_menubar(self):
        root = self.winfo_toplevel()
        bar = tk.Menu(root)

        m_note = tk.Menu(bar, tearoff=0)
        m_note.add_command(label="新建笔记  Ctrl+N", command=self.new_note)
        m_note.add_command(label="保存  Ctrl+S", command=self.save_note)
        m_note.add_command(label="导出为 txt…", command=self.export_txt)
        m_note.add_separator()
        m_note.add_command(label="删除当前笔记", command=self.delete_note)
        m_note.add_command(label="锁定", command=self.lock)
        bar.add_cascade(label="笔记", menu=m_note)

        m_edit = tk.Menu(bar, tearoff=0)
        m_edit.add_command(label="撤销  Ctrl+Z", command=self.undo)
        m_edit.add_command(label="重做  Ctrl+Y", command=self.redo)
        m_edit.add_separator()
        m_edit.add_command(label="全选  Ctrl+A", command=self._select_all)
        m_edit.add_command(label="查找/替换  Ctrl+F", command=lambda: self.open_find(replace=True))
        m_edit.add_command(label="插入时间  F5", command=self.insert_timestamp)
        bar.add_cascade(label="编辑", menu=m_edit)

        m_fmt = tk.Menu(bar, tearoff=0)
        m_fmt.add_command(label="加粗  Ctrl+B", command=lambda: self._toggle_style(ST_BOLD))
        m_fmt.add_command(label="斜体  Ctrl+I", command=lambda: self._toggle_style(ST_ITALIC))
        m_fmt.add_command(label="下划线  Ctrl+U", command=lambda: self._toggle_style(ST_UNDERLINE))
        m_fmt.add_command(label="删除线", command=lambda: self._toggle_style(ST_STRIKE))
        m_fmt.add_command(label="超链接  Ctrl+K", command=self.insert_link)
        m_fmt.add_separator()
        m_fmt.add_command(label="项目符号列表", command=lambda: self.toggle_list("• "))
        m_fmt.add_command(label="编号列表", command=lambda: self.toggle_list("number"))
        m_fmt.add_command(label="清除格式", command=self.clear_format)
        bar.add_cascade(label="格式", menu=m_fmt)

        m_view = tk.Menu(bar, tearoff=0)
        m_view.add_checkbutton(label="自动换行", variable=self._wrap_on,
                               command=self.toggle_wrap)
        m_view.add_checkbutton(label="自动保存", variable=self._autosave_on)
        m_view.add_separator()
        m_view.add_command(label="放大  Ctrl++", command=lambda: self.zoom(1))
        m_view.add_command(label="缩小  Ctrl+-", command=lambda: self.zoom(-1))
        m_view.add_command(label="还原字号  Ctrl+0", command=lambda: self.zoom(0))
        bar.add_cascade(label="视图", menu=m_view)

        root.config(menu=bar)
        self._menubar = bar

    # ---------- 列表渲染 ----------

    def _preview_of(self, note, max_chars=28):
        """取正文前若干字符做预览（一行）。用 note_id 缓存，避免重复解密。"""
        note_id = note["id"]
        if note_id in self._preview_cache:
            return self._preview_cache[note_id]
        try:
            body = self.vault.get_body(note_id)
        except VaultError:
            return ""
        line = rt_plaintext(body).strip().replace("\n", " ")
        preview = line[:max_chars] + ("…" if len(line) > max_chars else "")
        self._preview_cache[note_id] = preview
        return preview

    def _reload_list(self, select_id=None):
        self._notes = self.vault.list_notes()
        # 清掉已删除笔记的预览缓存，防止无限增长。
        live_ids = {n["id"] for n in self._notes}
        self._preview_cache = {k: v for k, v in self._preview_cache.items() if k in live_ids}
        self._render_cards(select_id=select_id)

    def _schedule_filter_render(self):
        """搜索框输入去抖：200ms 后才渲染卡片。"""
        if self._filter_after is not None:
            self.after_cancel(self._filter_after)
        self._filter_after = self.after(200, self._do_filter_render)

    def _do_filter_render(self):
        self._filter_after = None
        self._render_cards()

    def _render_cards(self, select_id=None):
        """按当前搜索关键字渲染卡片。数据来自 self._notes，不重新读库。"""
        kw = self._filter_var.get().strip().lower() if hasattr(self, "_filter_var") else ""
        if kw:
            shown = [n for n in self._notes if kw in (n["title"] or "").lower()]
        else:
            shown = self._notes

        # 计算要显示的卡片 id
        shown_ids = {n["id"] for n in shown}
        old_ids = set(self._cards.keys())

        # 增量更新：只删除不再显示的，只新增需要显示的
        to_remove = old_ids - shown_ids
        to_add = shown_ids - old_ids

        for nid in to_remove:
            self._cards[nid].destroy()
            del self._cards[nid]

        # 按顺序插入新卡片
        for n in shown:
            if n["id"] not in self._cards:
                card = NoteCard(self.list_holder, n, self._preview_of(n),
                                self.colors, on_click=self._select_note)
                card.pack(fill=X, pady=(0, 1))
                self._cards[n["id"]] = card

        # 按 shown 顺序重排已有卡片：pack_forget 后按顺序重新 pack，
        # 不销毁重建（保留选中高亮与滚动位置）。不重排会导致编辑旧笔记
        # 保存后数据已重排、界面卡片还停在老位置。
        for n in shown:
            card = self._cards.get(n["id"])
            if card is not None:
                card.pack_forget()
                card.pack(fill=X, pady=(0, 1))

        if select_id and select_id in self._cards:
            self._highlight(select_id)
        elif self._current_id in self._cards:
            self._highlight(self._current_id)
        if kw:
            self._set_status(f"匹配 {len(shown)} / 共 {len(self._notes)} 条")
        else:
            self._set_status(f"共 {len(self._notes)} 条笔记")

    def _highlight(self, note_id):
        for nid, card in self._cards.items():
            card.set_selected(nid == note_id)

    def _select_note(self, note_id):
        if note_id == self._current_id:
            return
        if not self._confirm_discard():
            return
        self._load_note(note_id)
        self._highlight(note_id)

    def _load_note(self, note_id):
        try:
            body = self.vault.get_body(note_id)
            note = next(n for n in self._notes if n["id"] == note_id)
        except (VaultError, StopIteration) as exc:
            Messagebox.show_error(f"读取失败：{exc}", APP_TITLE, parent=self)
            return
        self._current_id = note_id
        self.ent_title.delete(0, END)
        self.ent_title.insert(0, note["title"])
        self._suppress_change = True
        try:
            self.txt_body.delete("1.0", END)
            text, spans, links = rt_loads(body)
            self.txt_body.insert("1.0", text)
            self._apply_rich(text, spans)
            self._links = links
            self._apply_links(text, links)
        finally:
            self._suppress_change = False
        # 重置编辑器状态：撤销栈、待输入样式、统计。
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._typing_flags = 0
        self._sticky_typing = False
        self._dirty = False
        self._update_stats()
        self._set_status(f"已打开：{note['title'] or '（无标题）'}")

    def _select_first(self):
        """选中并打开列表里的第一条笔记；列表为空则清空编辑器。"""
        if self._notes:
            first_id = self._notes[0]["id"]
            self._current_id = first_id
            self._load_note(first_id)
            if first_id in self._cards:
                self._highlight(first_id)
        else:
            self._current_id = None
            self._dirty = False
            self._suppress_change = True
            try:
                self.ent_title.delete(0, END)
                self.txt_body.delete("1.0", END)
            finally:
                self._suppress_change = False
            self._set_status("没有笔记")

    # ---------- CRUD ----------

    def new_note(self):
        if not self._confirm_discard():
            return
        try:
            nid = self.vault.add_note("新笔记", "")
        except VaultError as exc:
            Messagebox.show_error(f"创建失败：{exc}", APP_TITLE, parent=self)
            return
        self._current_id = nid
        self._reload_list(select_id=nid)
        self._load_note(nid)
        self.ent_title.focus_set()
        self.ent_title.select_range(0, END)

    def save_note(self):
        if self._current_id is None:
            self._set_status("没有可保存的笔记")
            return
        title = self.ent_title.get()
        text, spans, links = self._collect_rich()
        body = rt_dumps(text, spans, links)
        try:
            self.vault.update_note(self._current_id, title, body)
        except VaultError as exc:
            Messagebox.show_error(f"保存失败：{exc}", APP_TITLE, parent=self)
            return
        self._dirty = False
        self._reload_list(select_id=self._current_id)
        self._set_status("已保存 ✓")

    def delete_note(self):
        if self._current_id is None:
            self._set_status("请先在左侧选中一条笔记")
            return
        ans = Messagebox.yesno("确定删除当前笔记？此操作不可恢复。", APP_TITLE,
                               parent=self, buttons=["否:secondary", "是:danger"],
                               localize=False)
        if ans != "是":
            return
        try:
            self.vault.delete_note(self._current_id)
        except VaultError as exc:
            Messagebox.show_error(f"删除失败：{exc}", APP_TITLE, parent=self)
            return
        self._current_id = None
        self._dirty = False
        self.ent_title.delete(0, END)
        self.txt_body.delete("1.0", END)
        self._reload_list()
        self._select_first()            # 自动选中剩余的第一条，没有则保持空
        self._set_status("已删除")

    def change_password(self):
        if self._dirty and not self._confirm_discard():
            return
        dlg = PasswordDialog(self.winfo_toplevel())
        self.wait_window(dlg)
        new_pw = dlg.result
        if not new_pw:
            return

        # 显示进度提示，改密码可能较慢
        self._set_status("⏳ 正在更新主密码...")
        self.update_idletasks()

        try:
            self.vault.change_password(new_pw)
        except VaultError as exc:
            Messagebox.show_error(f"改密码失败：{exc}", APP_TITLE, parent=self)
            return
        Messagebox.show_info("主密码已更新", APP_TITLE, parent=self)
        self._set_status("主密码已更新 ✓")

    # ---------- 脏标记 / 丢弃确认 ----------

    def _mark_dirty(self, _event=None):
        if not self._dirty and self._current_id is not None:
            self._dirty = True
            self._set_status("● 未保存")

    def _confirm_discard(self) -> bool:
        if not self._dirty or self._current_id is None:
            return True
        ans = Messagebox.yesnocancel("当前笔记有未保存的改动，是否保存？",
                                     APP_TITLE, parent=self,
                                     buttons=["取消:secondary", "否:secondary", "是:primary"],
                                     localize=False)
        if ans == "取消" or ans is None:
            return False
        if ans == "是":
            self.save_note()
        return True

    # ---------- 自动锁定 ----------

    def _reset_lock_timer(self):
        if self._lock_after is not None:
            self.after_cancel(self._lock_after)
        self._lock_after = self.after(AUTO_LOCK_MS, self._auto_lock)

    def _auto_lock(self):
        """空闲超时锁定：绝不弹框等待（人不在），有改动就静默保存后直接锁。

        保存失败也要锁（finally 保证 _do_lock 一定执行），绝不因保存
        异常让保险库停留在解锁状态。
        """
        try:
            if self._dirty and self._current_id is not None:
                try:
                    title = self.ent_title.get()
                    text, spans, links = self._collect_rich()
                    self.vault.update_note(self._current_id, title, rt_dumps(text, spans, links))
                    self._dirty = False
                except Exception:
                    pass                # 存不了也要锁，安全优先
            self._set_status("空闲超时，已锁定")
        finally:
            self._do_lock()

    def lock(self):
        # 手动锁定：仍尊重用户，对未保存改动给出确认。
        if not self._confirm_discard():
            return
        self._do_lock()

    def _do_lock(self):
        """实际执行锁定：清理定时器/浮窗/菜单栏，回到解锁界面。"""
        for attr in ("_lock_after", "_autosave_after", "_undo_after", "_filter_after"):
            tid = getattr(self, attr, None)
            if tid is not None:
                self.after_cancel(tid)
                setattr(self, attr, None)
        if self._find_dialog is not None and self._find_dialog.winfo_exists():
            self._find_dialog.destroy()
            self._find_dialog = None
        # 移除菜单栏，回到解锁界面时不残留。
        try:
            self.winfo_toplevel().config(menu="")
        except tk.TclError:
            pass
        self.unbind_all("<Key>")
        self.unbind_all("<Button>")
        self.unbind_all("<Control-s>")
        self.unbind_all("<Control-n>")
        self.on_lock()

    def _set_status(self, text):
        self.status.config(text=text)

    def can_close(self) -> bool:
        return self._confirm_discard()


class App:
    """应用控制器：在同一窗口内切换解锁界面与主界面。"""

    def __init__(self):
        self.root = tb.Window(themename=THEME)
        self.root.title(APP_TITLE)
        self.root.geometry("960x640")
        self.root.minsize(760, 480)
        self.main = None
        self.unlock = None
        self._last_path = ""            # 锁定后预填，免得重新浏览找文件
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self):
        self._show_unlock()
        self.root.mainloop()

    def _show_unlock(self):
        self.root.title(APP_TITLE)
        self.unlock = UnlockFrame(self.root, on_unlocked=self._enter_main,
                                  initial_path=self._last_path)
        self.unlock.pack(fill=BOTH, expand=True)

    def _enter_main(self, vault, path):
        if self.unlock is not None:
            self.unlock.destroy()
            self.unlock = None
        self._last_path = path
        name = os.path.basename(path)
        self.root.title(f"{APP_TITLE} — {name}")
        self.main = MainView(self.root, vault, name, on_lock=self._lock)
        self.main.pack(fill=BOTH, expand=True)

    def _lock(self):
        if self.main is not None:
            self.main.destroy()
            self.main = None
        self._show_unlock()

    def _on_close(self):
        if self.main is not None and not self.main.can_close():
            return
        self.root.destroy()


def main():
    """Entry point for CryptoNote."""
    App().run()


if __name__ == "__main__":
    main()

