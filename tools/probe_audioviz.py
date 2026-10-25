"""
音乐律动（SET_AUDIO_VIZ, 0x0D）是否每帧写 flash 的探测。

为什么必须测
------------
灯效类 SET 命令会触发固件把参数保存到 flash（`05 0F` 事件）。音乐律动是
**每秒几十帧的持续数据流**，如果每帧都写 flash，磨损完全不可接受 —— 那就该直接放弃。

判断方法（带对照组）
--------------------
1. 基线：什么都不发，监听 1.5 秒 → 确认监听通道本身是否工作
2. 对照组：发一帧 SET_LEDPARAM（已知会写 flash）→ 看有没有 `05 0F`
3. 实验组：发一帧 SET_AUDIO_VIZ 0x0D → 看有没有 `05 0F`

- 对照有、实验没有  → 0x0D 不写 flash，功能可做 ✅
- 两组都有          → 每帧写 flash，磨损不可接受，放弃 ❌
- 两组都没有        → 固件不发这个事件，无法判断，需换思路

限流
----
全程只发 3 帧（对照 2 帧含恢复 + 实验 1 帧），每帧之间等 2 秒。
默认 dry-run，加 --confirm 才真正发送。

    python tools/probe_audioviz.py            # 只看计划
    python tools/probe_audioviz.py --confirm  # 执行
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid  # noqa: E402

from kb import protocol as P            # noqa: E402
from kb.device import Keyboard          # noqa: E402
from kb.safety import SafetyPolicy      # noqa: E402

VID, PID = 0x38A9, 0x0017
WAIT = 2.0          # 每帧之后等多久（flash 写入约 1 秒，留余量）


def hexline(b, n: int = 20) -> str:
    return " ".join(f"{x:02X}" for x in b[:n])


class InputWatcher(threading.Thread):
    """后台监听厂商接口的 input report（只读）。"""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.events: list[tuple[float, list[int]]] = []
        self._stop = False
        self.devs = []

    def open(self) -> int:
        for d in hid.enumerate():
            if d["vendor_id"] != VID or d["product_id"] != PID:
                continue
            if d.get("usage_page", 0) < 0xFF00:
                continue
            try:
                dev = hid.device()
                dev.open_path(d["path"])
                dev.set_nonblocking(0)
                self.devs.append(dev)
            except Exception:  # noqa: BLE001
                pass
        return len(self.devs)

    def run(self) -> None:
        while not self._stop:
            for d in self.devs:
                try:
                    data = d.read(64, 120)
                except Exception:  # noqa: BLE001
                    continue
                if data:
                    self.events.append((time.monotonic(), list(data)))

    def stop(self) -> None:
        self._stop = True

    def after(self, t: float) -> list[list[int]]:
        return [e[1] for e in self.events if e[0] > t]


def build_audio_viz(levels: list[int]) -> bytes:
    """构造 0x0D 帧。格式未公开，按「16 频段 + 末尾校验」这一最可能的布局试。"""
    frame = bytearray(P.FRAME_LEN)
    frame[0] = 0
    frame[1] = 0x0D
    for i, v in enumerate(levels[:16]):
        frame[2 + i] = max(0, min(255, v))
    frame[18] = 255 - (sum(frame[1:18]) & 0xFF)
    return bytes(frame)


def has_save_ack(packets: list[list[int]]) -> bool:
    """查找 05 0F（设置保存开始/完成）事件。"""
    return any(len(p) >= 3 and p[0] == 0x05 and p[1] == 0x0F for p in packets)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()

    pol = SafetyPolicy(strict_readonly=False)
    kb = Keyboard.open_any(readonly=False)

    print("=" * 74)
    print("音乐律动 0x0D 是否写 flash — " + ("执行" if args.confirm else "DRY-RUN（不发送）"))
    print("=" * 74)

    # 读当前灯效，作为对照帧与恢复帧的素材
    led_resp = kb.query(P.Cmd.GET_LEDPARAM, None, bit8=True)
    info = P.parse_ledparam(led_resp)
    old_b = info["brightness"]
    test_b = old_b + 1 if old_b < 4 else old_b - 1   # 往亮里调，比调成 0（关灯）更温和
    rgb = tuple(info["rgb"])
    opts = int(info["options"], 16)

    ctrl = P.build_led_cmd(P.Cmd.SET_LEDPARAM,
                           [info["mode"], info["speed_raw"], test_b, opts, *rgb])
    restore = P.build_led_cmd(P.Cmd.SET_LEDPARAM,
                              [info["mode"], info["speed_raw"], old_b, opts, *rgb])
    viz = build_audio_viz([0x20 + i * 0x08 for i in range(16)])

    print(f"当前灯效: {info['mode_name']} 亮度{old_b}")
    print(f"\n对照帧 (SET_LEDPARAM 亮度{old_b}→{test_b}): {hexline(ctrl)}")
    print(f"恢复帧 (SET_LEDPARAM 亮度{old_b})    : {hexline(restore)}")
    print(f"实验帧 (0x0D 16频段)                 : {hexline(viz)}")

    if not args.confirm:
        print("\nDRY-RUN 结束，未发送任何字节。")
        kb.close()
        return 0

    w = InputWatcher()
    n = w.open()
    print(f"\n已打开 {n} 个监听接口")
    if n == 0:
        print("⚠ 没有可监听的接口，本方法失效")
        kb.close()
        return 2
    w.start()

    try:
        # ① 基线
        t = time.monotonic()
        time.sleep(1.5)
        base = w.after(t)
        print(f"\n① 基线（不发任何东西）: 收到 {len(base)} 个包")

        # ② 对照组
        t = time.monotonic()
        kb.send(ctrl)
        time.sleep(WAIT)
        ack_ctrl = w.after(t)
        print(f"② 对照组 SET_LEDPARAM : 收到 {len(ack_ctrl)} 个包, "
              f"含 05 0F = {has_save_ack(ack_ctrl)}")
        for p in ack_ctrl[:4]:
            print(f"     {hexline(p, 8)}")

        # 恢复
        kb.send(restore)
        time.sleep(WAIT)

        # ③ 实验组
        t = time.monotonic()
        kb.send(viz)
        time.sleep(WAIT)
        ack_viz = w.after(t)
        print(f"③ 实验组 0x0D         : 收到 {len(ack_viz)} 个包, "
              f"含 05 0F = {has_save_ack(ack_viz)}")
        for p in ack_viz[:4]:
            print(f"     {hexline(p, 8)}")

        # 看看 0x0D 有没有把模式切成音乐模式
        after = P.parse_ledparam(kb.query(P.Cmd.GET_LEDPARAM, None, bit8=True))
        print(f"\n④ 发送后灯效模式: {after['mode_name']}（原为 {info['mode_name']}）")
        if after["mode"] != info["mode"]:
            print("   → 固件接受了 0x0D 并切换了模式，说明帧格式可能正确")
            # 恢复原模式
            kb.send(restore)
            time.sleep(WAIT)

        print("\n" + "=" * 74)
        c = has_save_ack(ack_ctrl)
        v = has_save_ack(ack_viz)
        if c and not v:
            print("结论：对照组写 flash、0x0D 不写 → **音乐律动可以安全实现**")
        elif c and v:
            print("结论：两组都写 flash → 音乐律动会持续磨损，**建议放弃**")
        elif not c and not v:
            print("结论：这个固件不发 05 0F 事件，本方法无法判断。")
            print("      需要改用「测响应耗时」或「看 flash 写指示灯」等别的思路。")
        else:
            print("结论：结果异常（对照组无事件、实验组有），需要复核。")

    finally:
        w.stop()
        kb.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
