#!/usr/bin/env python3
"""从文本备份还原 Windows 桌面图标（仅标准库）。

仓库用 base64 文本保存 ICO，便于纯文本方式分发。克隆后运行一次：

    python tools/restore_icon.py

即可得到 assets/token-live.ico，安装脚本会用它作为桌面按钮图标。
不运行也不影响使用：install.py 检测不到 ICO 时会自动回退到系统默认图标。
"""
from __future__ import annotations

import base64
import binascii
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
B64 = ROOT / "assets" / "token-live.ico.b64"
ICO = ROOT / "assets" / "token-live.ico"


def main() -> int:
    if ICO.is_file():
        print("already present:", ICO)
        return 0
    if not B64.is_file():
        print("missing:", B64)
        return 1
    try:
        data = base64.b64decode(B64.read_text(encoding="ascii").strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        print("invalid base64:", exc)
        return 1
    if not data.startswith(b"\x00\x00\x01\x00"):
        print("unexpected ICO header")
        return 1
    ICO.write_bytes(data)
    print("restored:", ICO, len(data), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
