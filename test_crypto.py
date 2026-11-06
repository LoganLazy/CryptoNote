"""crypto.py 自测：验证加密核心的安全性质，不依赖 GUI。"""

import os
import json
import tempfile
import shutil

import crypto


def main():
    d = tempfile.mkdtemp(prefix="cryptonote_test_")
    path = os.path.join(d, "vault.cnote")
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

    try:
        # 1. 创建 + 增删改查
        v = crypto.Vault.create(path, "correct horse battery")
        nid = v.add_note("购物清单", "牛奶\n鸡蛋")
        nid2 = v.add_note("密码备忘", "wifi: 12345678")
        check("创建后能列出2条笔记", len(v.list_notes()) == 2)
        check("能读回正文", v.get_body(nid) == "牛奶\n鸡蛋")
        v.update_note(nid, "购物清单(改)", "牛奶\n鸡蛋\n面包")
        check("更新正文生效", v.get_body(nid) == "牛奶\n鸡蛋\n面包")
        v.delete_note(nid2)
        check("删除后剩1条", len(v.list_notes()) == 1)

        # 2. 重新打开（验证落盘 + 正确密码）
        v2 = crypto.Vault.open(path, "correct horse battery")
        check("重开后笔记还在", v2.get_body(nid) == "牛奶\n鸡蛋\n面包")
        check("标题正确解密", v2.list_notes()[0]["title"] == "购物清单(改)")

        # 3. 错误密码被拒
        try:
            crypto.Vault.open(path, "wrong password")
            check("错误密码应被拒绝", False)
        except crypto.WrongPassword:
            check("错误密码被拒绝", True)

        # 4. 明文不出现在磁盘文件里
        with open(path, "r", encoding="utf-8") as f:
            disk = f.read()
        check("正文未明文落盘", "面包" not in disk and "牛奶" not in disk)
        check("标题未明文落盘", "购物清单" not in disk)

        # 5. 篡改密文 -> 认证失败
        raw = json.loads(disk)
        some_id = next(iter(raw["notes"]))
        ct = raw["notes"][some_id]["body"]["ct"]
        # 翻转一个 base64 字符
        raw["notes"][some_id]["body"]["ct"] = ("A" if ct[0] != "A" else "B") + ct[1:]
        tampered = os.path.join(d, "tampered.cnote")
        with open(tampered, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        vt = crypto.Vault.open(tampered, "correct horse battery")
        try:
            vt.get_body(some_id)
            check("篡改正文应被检测", False)
        except crypto.WrongPassword:
            check("篡改正文被检测", True)

        # 6. 改密码
        v2.change_password("new master pw")
        try:
            crypto.Vault.open(path, "correct horse battery")
            check("旧密码改后应失效", False)
        except crypto.WrongPassword:
            check("旧密码改后失效", True)
        v3 = crypto.Vault.open(path, "new master pw")
        check("新密码可打开且数据完整", v3.get_body(nid) == "牛奶\n鸡蛋\n面包")

        # 7. 空主密码被拒
        try:
            crypto.Vault.create(os.path.join(d, "x.cnote"), "")
            check("空主密码应被拒绝", False)
        except crypto.VaultError:
            check("空主密码被拒绝", True)

        # 8. 条目调换检测（AAD 绑定）：整体互换两条笔记的密文应被发现
        spath = os.path.join(d, "swap.cnote")
        vs = crypto.Vault.create(spath, "swap test pw")
        sid1 = vs.add_note("AAA", "body A")
        sid2 = vs.add_note("BBB", "body B")
        sraw = json.load(open(spath, "r", encoding="utf-8"))
        # 互换 title 密文
        tpath = os.path.join(d, "swap_title.cnote")
        traw = json.loads(json.dumps(sraw))
        traw["notes"][sid1]["title"], traw["notes"][sid2]["title"] = \
            traw["notes"][sid2]["title"], traw["notes"][sid1]["title"]
        with open(tpath, "w", encoding="utf-8") as f:
            json.dump(traw, f)
        vt = crypto.Vault.open(tpath, "swap test pw")
        swapped_titles = [n["title"] for n in vt.list_notes()]
        check("互换标题密文后两条标题都不可解密",
              all(t == "（无法解密）" for t in swapped_titles))
        # 互换 body 密文
        bpath = os.path.join(d, "swap_body.cnote")
        braw = json.loads(json.dumps(sraw))
        braw["notes"][sid1]["body"], braw["notes"][sid2]["body"] = \
            braw["notes"][sid2]["body"], braw["notes"][sid1]["body"]
        with open(bpath, "w", encoding="utf-8") as f:
            json.dump(braw, f)
        vb = crypto.Vault.open(bpath, "swap test pw")
        try:
            vb.get_body(sid1)
            check("互换正文密文应被检测", False)
        except crypto.WrongPassword:
            check("互换正文密文被检测", True)

        # 9. 畸形文件：必须统一报 VaultError（或子类），不允许原始异常逃出
        def expect_vault_error(name, setup):
            p = os.path.join(d, "bad.cnote")
            setup(p)
            try:
                # 注意：主保险库在第 6 步已改过密码，这里必须用当前密码，
                # 否则 WrongPassword 会掩盖真正要测的畸形路径。
                vv = crypto.Vault.open(p, "new master pw")
                # 打开成功不代表没问题：缺 body 字段的笔记要在读正文时报错
                try:
                    for n in vv.list_notes():
                        vv.get_body(n["id"])
                except crypto.VaultError:
                    check(name, True)
                    return
                check(name, False)
            except crypto.VaultError:
                check(name, True)
            except Exception as exc:  # noqa: BLE001 - 这里要的就是捕获非预期异常
                check(f"{name}（逃出 {type(exc).__name__}，应为 VaultError）", False)

        base_raw = json.load(open(path, "r", encoding="utf-8"))
        expect_vault_error("截断 JSON 报 VaultError",
                           lambda p: open(p, "w", encoding="utf-8").write('{"version": 1, "kdf": '))
        expect_vault_error("JSON 数组报 VaultError",
                           lambda p: open(p, "w", encoding="utf-8").write('[1, 2, 3]'))
        expect_vault_error("空文件报 VaultError",
                           lambda p: open(p, "w", encoding="utf-8").write(''))

        def _no_check(p):
            raw = json.loads(json.dumps(base_raw))
            del raw["check"]
            with open(p, "w", encoding="utf-8") as f:
                json.dump(raw, f)
        expect_vault_error("缺 check 块报 VaultError", _no_check)

        def _no_body(p):
            raw = json.loads(json.dumps(base_raw))
            some = next(iter(raw["notes"]))
            del raw["notes"][some]["body"]
            with open(p, "w", encoding="utf-8") as f:
                json.dump(raw, f)
        expect_vault_error("笔记缺 body 字段报 VaultError", _no_body)

        # 10. 改密码时遇到被篡改的笔记：应抛 WrongPassword，原文件仍可用旧密码打开
        tpath2 = os.path.join(d, "tampered_pw.cnote")
        t2 = crypto.Vault.create(tpath2, "old pw 123")
        tid = t2.add_note("重要", "不要丢")
        traw2 = json.load(open(tpath2, "r", encoding="utf-8"))
        ct2 = traw2["notes"][tid]["body"]["ct"]
        traw2["notes"][tid]["body"]["ct"] = ("A" if ct2[0] != "A" else "B") + ct2[1:]
        with open(tpath2, "w", encoding="utf-8") as f:
            json.dump(traw2, f)
        vt2 = crypto.Vault.open(tpath2, "old pw 123")
        try:
            vt2.change_password("new pw 456")
            check("被篡改的笔记改密码应被拒绝", False)
        except crypto.WrongPassword:
            check("被篡改的笔记改密码被拒绝", True)
        v_old = crypto.Vault.open(tpath2, "old pw 123")
        check("改密码失败后原文件仍可用旧密码打开",
              v_old.list_notes()[0]["title"] == "重要")

        # 11. 标题超长被拒
        try:
            v3.add_note("x" * 300, "body")
            check("300 字标题 add_note 应被拒绝", False)
        except crypto.VaultError:
            check("300 字标题 add_note 被拒绝", True)
        try:
            v3.update_note(nid, "y" * 300, "body")
            check("300 字标题 update_note 应被拒绝", False)
        except crypto.VaultError:
            check("300 字标题 update_note 被拒绝", True)
        check("256 字标题可正常保存",
              v3.add_note("z" * 256, "body") is not None)

    finally:
        shutil.rmtree(d, ignore_errors=True)

    print(f"\n结果: {ok} 通过, {fail} 失败")
    raise SystemExit(1 if fail else 0)


if __name__ == "__main__":
    main()
