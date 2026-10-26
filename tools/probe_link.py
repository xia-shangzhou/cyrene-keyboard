"""
一条命令验当前连接方式能不能配置：USB / 2.4G / 蓝牙 通用。

不管你切到哪种模式，跑它就行。内部直接用 `kb.device.Keyboard.open_any()`，
和主程序走完全同一条代码路径 —— 这里能通，程序里就能用。

判据（和 kb/backend.py 保持一致）：
  · 命令码回显 + 参数非零 → ✅ 可以读写灯效
  · 全零帧               → 接收器/协议栈本地吞掉，没转发
  · 原样回显             → 帧到了设备，但固件没实现该命令
  · 无响应               → 这条链路不开放配置口

    python tools/probe_link.py
    python tools/probe_link.py --wait 0.6      # 蓝牙慢的话加大等待
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb import protocol as P          # noqa: E402
from kb.device import Keyboard        # noqa: E402


def hexline(b, n: int = 16) -> str:
    return " ".join(f"{x:02X}" for x in list(b)[:n]) if b else "（空）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=None,
                    help="查询后等多久再读（默认按连接方式自动选）")
    args = ap.parse_args()

    print("=" * 72)
    print("当前连接的配置能力探测")
    print("=" * 72)

    try:
        kb = Keyboard.open_any(readonly=True)
    except Exception as e:  # noqa: BLE001
        print(f"\n没找到键盘的厂商接口：{e}")
        print("请确认键盘已连上（USB 线 / 接收器 / 蓝牙配对）且处于开机状态。")
        return 2

    try:
        print(f"\n设备：0x{kb.vid:04X}:0x{kb.pid:04X} · {kb.link_name}"
              f"{'（BLE 传输层）' if kb.ble else ''}")
        print(f"路径：{kb.path.decode(errors='replace') if isinstance(kb.path, bytes) else kb.path}")

        alive = kb.config_alive()
        print(f"\n配置通道：{'✅ 可用' if alive else '❌ 不可用'}")

        req = P.build(P.Cmd.GET_LEDPARAM, None, bit8=True)
        try:
            resp = kb.query(P.Cmd.GET_LEDPARAM, bit8=True)
        except Exception as e:  # noqa: BLE001
            print(f"查询失败：{type(e).__name__}: {e}")
            return 1
        print(f"发：{hexline(req)}")
        print(f"收：{hexline(resp)}")

        if not resp:
            verdict = "❌ 无响应 —— 这条链路不开放配置口"
        elif bytes(resp) == req:
            # ★ 回显判定必须排在"通"前面：回显帧的 resp[1] 也是这个命令码、
            #   也不是全零，先判"通"就会把"假通道"报成可用（真踩过）。
            verdict = "⚠ 原样回显 —— 帧到了设备，但固件没实现（或通道不支持）"
        elif resp[1] == P.Cmd.GET_LEDPARAM and not all(b == 0 for b in resp):
            info = P.parse_ledparam(resp)
            verdict = (f"✅ 通！当前灯效 {info['mode_name']} / 速度 {info['speed_raw']} / "
                       f"亮度 {info['brightness']} / rgb {info['rgb']}")
        elif all(b == 0 for b in resp[:8]):
            verdict = "❌ 全零帧 —— 本地吞掉，没转发给键盘"
        else:
            verdict = "⚠ 不是这条命令的响应 —— 可能是缓存槽残留或事件帧"

        print(f"\n判定：{verdict}")
    finally:
        kb.close()

    print("\n" + "=" * 72)
    print("怎么用这个结果：")
    print("  · ✅ → 这条连接方式可以调灯效，直接启动主程序即可")
    print("  · ❌ → 厂商在这条链路上没做配置透传，需插 USB 线（Fn+5 长按 3 秒切有线）")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
