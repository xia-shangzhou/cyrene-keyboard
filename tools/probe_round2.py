"""
第二轮只读探测：扩大 GET 命令范围，重点找电量、验证读取稳定性。

仍然只发查询命令，SafetyPolicy 与 Keyboard 双重只读锁全程开启。

判断「真实响应」的方法：请求帧只有命令码和参数（其余全零），
如果响应和请求逐字节相同，说明固件不支持该命令、把请求原样回显；
只要有任何字节不同，就是真实数据。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb import device as D          # noqa: E402
from kb import protocol as P        # noqa: E402
from kb.safety import SafetyPolicy, UnsafeCommandError  # noqa: E402


def hexline(data: bytes, n: int = 20) -> str:
    return " ".join(f"{b:02X}" for b in data[:n])


def probe(pol, kb, name: str, cmd: int, params=None, bit8: bool = False) -> None:
    try:
        pol.assert_allowed(cmd)
        req = P.build(cmd, params, bit8=bit8)
        resp = kb.query(cmd, params, bit8=bit8)
        if resp is None:
            print(f"{name:<26} 0x{cmd:02X}  无响应")
            return
        echo = bytes(resp) == req
        tag = "回显(不支持)" if echo else "★真实数据"
        print(f"{name:<26} 0x{cmd:02X}  {tag}")
        print(f"    发 {hexline(req, 14)}")
        print(f"    收 {hexline(resp, 26)}")
    except UnsafeCommandError as e:
        print(f"{name:<26} 0x{cmd:02X}  已拦截：{e}")
    except Exception as e:  # noqa: BLE001
        print(f"{name:<26} 0x{cmd:02X}  异常：{type(e).__name__}: {e}")


def main() -> int:
    pol = SafetyPolicy(strict_readonly=True)
    print("=" * 78)
    print("第二轮只读探测（写命令本地已锁死）")
    print("=" * 78)
    kb = D.Keyboard.open_any(readonly=True)
    print(f"已连接 {kb.path.decode(errors='replace')[:70]}\n")

    print("--- A. 复读灯效参数，确认读取稳定（连读 3 次）---")
    for i in range(3):
        r = kb.query(P.Cmd.GET_LEDPARAM, None, bit8=True)
        print(f"  第{i + 1}次: {hexline(r, 12)}")
    print()

    print("--- B. 更多 GET 命令 ---")
    for name, cmd, prm in [
        ("GET_DEBOUNCE", 0x86, None),
        ("GET_USERPIC", 0x8C, None),
        ("GET_USERPIC(layer0)", 0x8C, [0]),
        ("GET_KEYMATRIX", 0x8A, None),
        ("GET_KEYMATRIX(layer0)", 0x8A, [0]),
        ("GET_FN", 0x90, None),
        ("GET_AUTOOS_EN", 0x97, None),
        ("GET_OLED_VERSION", 0xAD, None),
        ("GET_MLED_VERSION", 0xAE, None),
        ("GET_RF_INFO", 0xFB, None),
        ("GET_DONGLE_ID", 0xFD, None),
    ]:
        probe(pol, kb, name, cmd, prm)
        print()

    print("--- C. 0x83 换参数，找电量 ---")
    for prm in ([0], [1], [2], [3], [0x10], [0xFF]):
        probe(pol, kb, f"0x83 参数={prm[0]:#04x}", 0x83, prm)
        print()

    kb.close()
    print("=" * 78)
    print(f"安全统计：{pol.summary()}")
    print("全程未下发任何写入命令，键盘设置保持不变")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
