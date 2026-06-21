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

    finally:
        shutil.rmtree(d, ignore_errors=True)

    print(f"\n结果: {ok} 通过, {fail} 失败")
    raise SystemExit(1 if fail else 0)


if __name__ == "__main__":
    main()
