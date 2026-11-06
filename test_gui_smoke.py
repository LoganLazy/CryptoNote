"""GUI 冒烟测试：应用启动、自动锁定、列表顺序、标题粘贴、链接白名单、改密码确认。

需要显示器：Linux 下用 xvfb-run -a python test_gui_smoke.py
不跑 mainloop，不依赖真实显示器。

注意：ttkbootstrap 只允许单 root，整个测试共用一个 root 窗口
（App 冒烟用它自己的 App 实例，之后各小节复用同一个 root）。
"""

import os
import sys
import tempfile
import shutil
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter.simpledialog as sd
import webbrowser as wb

import ttkbootstrap as tb

from crypto import Vault
from app import App, MainView, PasswordDialog, rt_dumps, rt_loads


def main():
    d = tempfile.mkdtemp(prefix="cryptonote_gui_")
    ok = 0
    fail = 0

    def check(name, cond):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  PASS  {name}")
        else:
            fail += 1
            print(f"  FAIL  {name}")

    root = None
    try:
        # 1. 应用启动：App 能实例化，解锁界面出现
        app = App()
        root = app.root
        app._show_unlock()
        root.update()
        check("App 实例化且解锁界面出现",
              app.unlock is not None and app.unlock.winfo_exists())
        app.root.destroy()
        root = None

        # 之后各小节共用一个 root，避免反复创建/销毁触发单例竞态
        root = tb.Window(themename="cosmo")
        root.withdraw()
        root.update()

        # 2. 自动锁定：有未保存改动也不崩溃、一定上锁、超链接不丢失
        path = os.path.join(d, "gui.vault")
        vault = Vault.create(path, "pw123456")
        body = rt_dumps("see https://example.com here", [],
                        [(4, 23, "https://example.com")])
        nid = vault.add_note("带链接", body)
        locked = []
        view = MainView(root, vault, "gui.vault", on_lock=lambda: locked.append(True))
        root.update()
        view.txt_body.insert("1.0", "edit ")
        root.update()
        check("改动后 dirty 为真", view._dirty)
        try:
            view._auto_lock()
            raised = None
        except Exception as exc:  # noqa: BLE001 - 这里要的就是捕获任何异常
            raised = exc
        check("自动锁定不抛异常", raised is None)
        check("自动锁定后 on_lock 被调用（确实上锁）", bool(locked))
        check("自动锁定后 dirty 已清（改动已保存）", not view._dirty)
        v2 = Vault.open(path, "pw123456")
        _, _, links = rt_loads(v2.get_body(nid))
        check("自动锁定保存后超链接还在",
              any(u == "https://example.com" for _, _, u in links))
        view.destroy()

        # 3. 列表顺序：编辑旧笔记保存后，卡片顺序跟上数据顺序
        path3 = os.path.join(d, "order.vault")
        vault3 = Vault.create(path3, "pw123456")
        id_a = vault3.add_note("Note A", "alpha")
        time.sleep(1.2)  # updated 是 1 秒精度，必须隔开才有先后
        id_b = vault3.add_note("Note B", "beta")
        view3 = MainView(root, vault3, "order.vault", on_lock=lambda: None)
        root.update()

        def pack_order(v):
            return [getattr(w, "note_id", None) for w in v.list_holder.pack_slaves()]

        check("初始 _notes 顺序 B 在前",
              [n["id"] for n in view3._notes] == [id_b, id_a])
        check("初始卡片顺序 B 在前", pack_order(view3) == [id_b, id_a])
        view3._load_note(id_a)
        view3.txt_body.insert("1.0", "touch ")
        view3.save_note()
        root.update()
        check("编辑 A 保存后 _notes 顺序 A 在前",
              [n["id"] for n in view3._notes] == [id_a, id_b])
        check("编辑 A 保存后卡片顺序 A 在前", pack_order(view3) == [id_a, id_b])
        view3.destroy()

        # 4. 标题粘贴：300 字 insert 应被 validatecommand 挡住
        path4 = os.path.join(d, "title.vault")
        vault4 = Vault.create(path4, "pw123456")
        vault4.add_note("短标题", "body")
        view4 = MainView(root, vault4, "title.vault", on_lock=lambda: None)
        root.update()
        view4.ent_title.delete(0, "end")
        view4.ent_title.insert(0, "x" * 256)
        root.update()
        check("256 字标题可正常输入", len(view4.ent_title.get()) == 256)
        view4.ent_title.delete(0, "end")
        view4.ent_title.insert(0, "y" * 300)
        root.update()
        check("300 字粘贴后标题仍在 256 字以内",
              len(view4.ent_title.get()) <= 256)
        check("300 字粘贴未被完整接受",
              view4.ent_title.get() != "y" * 300)
        view4.destroy()

        # 5. 超链接白名单：file:// 拒绝插入；点击 http 打开、file:// 不打开
        import app as app_module
        path5 = os.path.join(d, "link.vault")
        vault5 = Vault.create(path5, "pw123456")
        vault5.add_note("链接测试", "click here now")
        view5 = MainView(root, vault5, "link.vault", on_lock=lambda: None)
        # 要取文字屏幕坐标（bbox），窗口必须真正映射出来
        root.deiconify()
        root.geometry("900x600")
        view5.pack(fill="both", expand=True)  # 要测点击坐标，编辑器必须实际显示
        root.update()
        view5._load_note(view5._current_id)
        root.update()
        warnings = []
        orig_warn = app_module.Messagebox.show_warning
        orig_ask = sd.askstring
        try:
            app_module.Messagebox.show_warning = staticmethod(
                lambda *a, **k: warnings.append(a))
            # 选中一段文字，模拟插入 file:// 链接
            sd.askstring = lambda *a, **k: "file:///etc/passwd"
            view5.txt_body.tag_add("sel", "1.0", "1.0+5c")
            view5.insert_link()
            root.update()
            check("file:// 链接插入被拒绝（无 link tag 生成）",
                  not [t for t in view5.txt_body.tag_names()
                       if t.startswith("link")])
            check("file:// 拒绝时弹出提示", len(warnings) == 1)
            # 插入 https 链接应成功
            sd.askstring = lambda *a, **k: "https://example.org"
            view5.txt_body.tag_add("sel", "1.0", "1.0+5c")
            view5.insert_link()
            root.update()
            link_tags = [t for t in view5.txt_body.tag_names()
                         if t.startswith("link")]
            check("https 链接插入成功", len(link_tags) == 1)
            # 点击 https 链接应打开浏览器（直接调 handler 并传入合成事件：
            # event_generate 在本环境不触发 tag 绑定，见下方的绑定存在性断言）
            opened = []
            orig_open = wb.open
            wb.open = lambda url, *a, **k: opened.append(url)
            try:
                from types import SimpleNamespace as _NS
                box = view5.txt_body.bbox("1.0+2c")
                check("能取到链接文字坐标", box is not None)
                # tag 上确实注册了 <Button-1> 绑定（查 Tcl 绑定表）
                bound = view5.txt_body.tk.call(
                    view5.txt_body._w, "tag", "bind", link_tags[0], "<Button-1>")
                check("链接 tag 上注册了点击绑定", bool(bound))
                if box:
                    view5._on_link_click(
                        _NS(x=box[0] + 1, y=box[1] + 1))
                    root.update()
                check("点击 https 链接会打开浏览器",
                      opened == ["https://example.org"])
                # 手工挂一个 file:// 链接（模拟旧库里的非法链接），点击不应打开
                bad = view5._new_link_tag("file:///etc/passwd")
                view5.txt_body.tag_add(bad, "1.0+6c", "1.0+10c")
                root.update()
                box2 = view5.txt_body.bbox("1.0+7c")
                if box2:
                    view5._on_link_click(
                        _NS(x=box2[0] + 1, y=box2[1] + 1))
                    root.update()
                check("点击 file:// 链接不会打开浏览器",
                      opened == ["https://example.org"])
            finally:
                wb.open = orig_open
        finally:
            sd.askstring = orig_ask
            app_module.Messagebox.show_warning = orig_warn
        view5.destroy()

        # 6. 改密码短密码确认：少于 6 位先问，选否则不关闭
        orig_yesno = app_module.Messagebox.yesno
        try:
            answers = []
            app_module.Messagebox.yesno = staticmethod(
                lambda *a, **k: answers.pop(0) if answers else "否")
            dlg = PasswordDialog(root)
            root.update()
            dlg.ent1.insert(0, "abc")
            dlg.ent2.insert(0, "abc")
            answers.append("否")
            dlg._ok()
            root.update()
            check("短密码选否则不关闭弹窗",
                  dlg.result is None and dlg.winfo_exists())
            answers.append("是")
            dlg._ok()
            root.update()
            check("短密码选是则通过", dlg.result == "abc")
            if dlg.winfo_exists():
                dlg.destroy()
        finally:
            app_module.Messagebox.yesno = orig_yesno
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
        shutil.rmtree(d, ignore_errors=True)

    print(f"\n结果: {ok} 通过, {fail} 失败")
    raise SystemExit(1 if fail else 0)


if __name__ == "__main__":
    main()
