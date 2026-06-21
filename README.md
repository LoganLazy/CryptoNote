# CryptoNote — Windows 桌面加密记事本

一个主密码管理多条笔记的加密笔记库。所有笔记加密后存进**单个库文件**，主密码不落盘，文件即使被人拷走也无法在不知道密码的情况下读取或篡改。

## 特性

- **加密笔记库**：一个主密码 + 一个 `.vault` 文件，左侧笔记列表，右侧标题/正文编辑。
- **强加密**：scrypt 派生密钥 + AES-256-GCM 认证加密，每条笔记独立 nonce。
- **防篡改**：GCM 自带完整性校验；用笔记 id 作为 AAD 绑定，防止条目被整体调换。
- **原子落盘**：先写临时文件再替换，写一半断电也不会损坏库文件。
- **空闲自动锁定**：5 分钟无操作自动回到解锁界面。
- **纯运行时无需编译**：只依赖已安装的 `pycryptodome` + Python 标准库，Windows ARM64 直接可用。

## 运行

```bash
cd ~/Desktop/CryptoNote
python app.py
```

首次使用：在解锁窗口点「新建」，选保存位置，设主密码即可。下次用「打开」选库文件并输入密码。

## 安全设计

| 环节 | 方案 | 说明 |
|------|------|------|
| 密钥派生 | scrypt (N=2¹⁵, r=8, p=1) | 抗暴力破解，标准库实现，无需 C/Rust 编译 |
| 加密算法 | AES-256-GCM | 认证加密，机密性 + 完整性一体 |
| nonce | 每条笔记每次保存随机生成 (96-bit) | 不复用 |
| 完整性绑定 | 笔记 id 作 AAD | 防止密文条目被调换 |
| 主密码 | 仅内存派生，永不落盘 | 不写配置/临时文件 |
| 落盘 | 临时文件 + `os.replace` 原子替换 | 防写入中断损坏 |

**主密码无法找回**：库文件用主密码加密，密码丢了等于数据丢了，没有后门。请自行备份密码和 `.vault` 文件。

## 文件结构

```
CryptoNote/
├── crypto.py        # 加密核心：Vault 类 + scrypt/AES-GCM
├── app.py           # Tkinter GUI
├── test_crypto.py   # 加密核心自测（13 项，全部通过）
└── README.md
```

## 测试

```bash
python test_crypto.py
```

覆盖：创建/读取、正确密码解密、错误密码拒绝、密文篡改检测、条目调换检测、改密码后新旧密码行为、原子落盘后重新打开。

## 库文件格式

JSON，仅含密文与公开的 KDF 参数（salt、scrypt 参数），不含任何明文笔记内容：

```json
{
  "version": 1,
  "kdf": { "algo": "scrypt", "salt": "...", "n": 32768, "r": 8, "p": 1, "dklen": 32 },
  "cipher": "AES-256-GCM",
  "check": { "nonce": "...", "ct": "...", "tag": "..." },
  "notes": {
    "<note-id>": {
      "title": { "nonce": "...", "ct": "...", "tag": "..." },
      "body":  { "nonce": "...", "ct": "...", "tag": "..." },
      "created": "...", "updated": "..."
    }
  }
}
```

## 打包成 exe（可选）

跑通后如需单文件 exe：

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name CryptoNote app.py
```

产物在 `dist/CryptoNote.exe`。注意 PyInstaller 在 ARM64 上会打出 ARM64 的 exe，只能在 ARM 设备上跑。
