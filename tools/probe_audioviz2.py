"""
音乐律动 0x0D 帧格式验证（第二版）。

第一版（probe_audioviz.py）为什么失败
------------------------------------
按猜的布局发：band 放 frame[2..17]、校验和放 frame[18]、且**没先切到音乐模式**。
线上文档（MonsGeek/Akko，同 YC3121 平台）给出的真实布局完全不同：

    payload 索引（不含 Report ID）       本代码索引（含 Report ID）
    byte 0        = 0x0D                 frame[1]
    byte 1..6     = 0 (padding)          frame[2..7]
    byte 7        = checksum Bit7        frame[8]      ← 只累加 byte0..6
    byte 8..23    = band[0..15], 各 0-6  frame[9..24]

也就是说：**校验和位置和 GET 命令一样在 frame[8]，数据从 frame[9] 开始**。
另有一条硬前提：**必须先用 SET_LEDPARAM 把模式切到 20 或 22**，
否则流式数据没有渲染器接手。

本探针要回答的两个问题
----------------------
Q1  正确格式下，灯光会不会真的跟着频段动？（肉眼 + 模式回读）
Q2  每帧 0x0D 会不会触发 `05 0F` flash 保存事件？（决定能不能长期跑）

安全设计
--------
- 全程 flash 写入只有 2 次（切模式 1 次 + 恢复 1 次），都是 SET_LEDPARAM
- 0x0D 只发固定批次数（默认 2 批 × 24 帧），不无限流
- 一旦在 0x0D 阶段看到 `05 0F`，立刻停止后续批次
- 默认 dry-run

    python tools/probe_audioviz2.py            # 只看帧与计划
    python tools/probe_audioviz2.py --confirm  # 执行
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

VID, PID = 0x38A9, 0x0017

FRAME_MS = 40            # 推送间隔 → 25Hz
BATCH_FRAMES = 24        # 每批帧数（≈1 秒）
BATCHES = 2              # 批次数
FLASH_WAIT = 1.4         # SET_LEDPARAM 之后等 flash 写完

MUSIC_MODES = (22, 20)   # 先试 22（音乐律动条），再试 20（音乐节奏）


def hexline(b: bytes, n: int = 28) -> str:
    return " ".join(f"{x:02X}" for x in b[:n])


# ------------------------------------------------------------------ 帧构造

def build_audio_viz_usb(bands: list[int]) -> bytes:
    """0x0D 有线(USB)格式。

    bands: 16 个 0-6 的整数（固件渲染器只认这个范围，相邻两段求和 clamp 到 8）
    """
    frame = bytearray(P.FRAME_LEN)
    frame[0] = 0                                  # Report ID
    frame[1] = P.Cmd.SET_AUDIO_VIZ                # 0x0D
    # frame[2..7] 保持 0（padding）
    for i, v in enumerate(bands[:16]):
        frame[9 + i] = max(0, min(6, int(v)))     # 数据从 frame[9] 开始
    # 校验和固定放 frame[8]，只累加 frame[0..7]（与 GET 命令同位置）
    frame[8] = 255 - (sum(frame[0:8]) & 0xFF)
    return bytes(frame)


def band_pattern(kind: str, t: int) -> list[int]:
    """生成测试用的 16 段电平。"""
    if kind == "sweep":
        # 单峰从左扫到右，最容易肉眼看出「键盘有没有跟着动」
        pos = t % 16
        return [6 if i == pos else (4 if abs(i - pos) == 1 else 0) for i in range(16)]
    if kind == "step":
        # 全部同步高低跳变，用来看整体亮度响应
        return [6] * 16 if (t // 4) % 2 == 0 else [0] * 16
    if kind == "ramp":
        return [min(6, i // 2) for i in range(16)]
    raise ValueError(kind)


# ------------------------------------------------------------------ 监听

class InputWatcher(threading.Thread):
    """后台监听厂商接口的 input report（只读），用来抓 `05 0F` 保存事件。"""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.events: list[tuple[float, list[int]]] = []
        self._stop = False
        self.devs: list = []

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
                    data = d.read(64, 100)
                except Exception:  # noqa: BLE001
                    continue
                if data:
                    self.events.append((time.monotonic(), list(data)))

    def stop(self) -> None:
        self._stop = True

    def after(self, t: float) -> list[list[int]]:
        return [e[1] for e in self.events if e[0] > t]


def save_events(packets: list[list[int]]) -> list[list[int]]:
    """挑出 `05 0F`（设置保存开始/完成）。"""
    return [p for p in packets if len(p) >= 3 and p[0] == 0x05 and p[1] == 0x0F]


# ------------------------------------------------------------------ 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true", help="真正发送（默认 dry-run）")
    ap.add_argument("--mode", type=int, default=MUSIC_MODES[0], help="先试哪个音乐模式，默认 22")
    ap.add_argument("--style", type=int, default=0, help="音乐样式 0-2（LEDPARAM option 高 nibble）")
    ap.add_argument("--pattern", default="sweep", choices=("sweep", "step", "ramp"))
    args = ap.parse_args()

    kb = Keyboard.open_any(readonly=False)

    # ---- 读当前灯效，作为恢复素材
    led = P.parse_ledparam(kb.query(P.Cmd.GET_LEDPARAM, None, bit8=True) or b"")
    if not led:
        print("读不到当前灯效，放弃（避免无法恢复原状态）")
        kb.close()
        return 2

    old_mode, old_b = led["mode"], led["brightness"]
    old_opts = int(led["options"], 16)
    rgb = tuple(led["rgb"])

    # 音乐模式：option 高 nibble = style，低 nibble 保持原编码
    music_opts = ((args.style & 0x0F) << 4) | (old_opts & 0x0F)

    def ledparam(mode: int, opts: int) -> bytes:
        return P.build_led_cmd(
            P.Cmd.SET_LEDPARAM,
            [mode & 0xFF, led["speed_raw"] & 0xFF, old_b & 0xFF, opts & 0xFF, *rgb],
        )

    f_switch = ledparam(args.mode, music_opts)
    f_restore = ledparam(old_mode, old_opts)

    print("=" * 78)
    print("音乐律动 0x0D 格式验证 — " + ("执行" if args.confirm else "DRY-RUN（不发送）"))
    print("=" * 78)
    print(f"当前状态：模式 {old_mode}({led['mode_name']})  亮度 {old_b}  option 0x{old_opts:02X}  RGB {rgb}")
    print(f"目标音乐模式：{args.mode}({P.LED_MODES.get(args.mode, '?')})  style={args.style}  "
          f"option 0x{music_opts:02X}")

    print("\n--- 帧预览 ---")
    print(f"① 切模式 SET_LEDPARAM : {hexline(f_switch, 14)}")
    for t in (0, 5, 10):
        b = band_pattern(args.pattern, t)
        print(f"② 0x0D t={t:<2} bands={b}")
        print(f"   {hexline(build_audio_viz_usb(b))}")
    print(f"③ 恢复 SET_LEDPARAM   : {hexline(f_restore, 14)}")

    # 自检：校验和位置
    f = build_audio_viz_usb([6] * 16)
    expect = 255 - ((0 + 0x0D) & 0xFF)
    assert f[8] == expect, f"校验和错误 {f[8]:02X} != {expect:02X}"
    assert all(f[2 + i] == 0 for i in range(6)), "padding 不为 0"
    print(f"\n自检：cmd=frame[1]=0x{f[1]:02X}  checksum=frame[8]=0x{f[8]:02X}  "
          f"bands=frame[9..24] 首值 {f[9]}")

    if not args.confirm:
        print("\nDRY-RUN 结束，未发送任何字节。加 --confirm 执行。")
        kb.close()
        return 0

    # ---- 开始
    w = InputWatcher()
    n = w.open()
    if n == 0:
        print("\n没有可监听的接口，无法判断 flash 写入，放弃")
        kb.close()
        return 2
    print(f"\n探针已挂上 {n} 个接口")
    w.start()

    viz_save_count = 0
    try:
        time.sleep(0.6)

        # ① 基线
        t0 = time.monotonic()
        time.sleep(1.0)
        base = w.after(t0)
        print(f"\n① 基线 1.0s          : {len(base)} 包, 含 05 0F = {bool(save_events(base))}")

        # ② 切到音乐模式（必然写 flash，作为探针有效性对照）
        t0 = time.monotonic()
        kb.send(f_switch)
        time.sleep(FLASH_WAIT)
        a_switch = w.after(t0)
        ev = save_events(a_switch)
        print(f"② 切模式 mode={args.mode}  : {len(a_switch)} 包, 含 05 0F = {bool(ev)}"
              f"  {[hexline(p, 4) for p in ev[:3]]}")

        after_mode = P.parse_ledparam(kb.query(P.Cmd.GET_LEDPARAM, None, bit8=True) or b"")
        ok_mode = after_mode.get("mode") == args.mode
        print(f"   回读模式 = {after_mode.get('mode')}({after_mode.get('mode_name')})  "
              f"{'✅ 已进入音乐模式' if ok_mode else '❌ 没进去，固件可能不支持该模式'}")

        # ③ 流式发 0x0D
        print(f"\n③ 推送 0x0D：{BATCHES} 批 × {BATCH_FRAMES} 帧 @ {FRAME_MS}ms")
        if not ok_mode:
            print("   ⚠ 没进音乐模式，此时观察重点变成「0x0D 是否会被拒绝/回显」")

        for bi in range(BATCHES):
            t0 = time.monotonic()
            for k in range(BATCH_FRAMES):
                kb.send(build_audio_viz_usb(band_pattern(args.pattern, bi * BATCH_FRAMES + k)))
                time.sleep(FRAME_MS / 1000.0)
            time.sleep(0.4)
            pk = w.after(t0)
            ev = save_events(pk)
            viz_save_count += len(ev)
            print(f"   批次 {bi + 1}: 发 {BATCH_FRAMES} 帧 → {len(pk)} 包, "
                  f"含 05 0F = {bool(ev)} {[hexline(p, 4) for p in ev[:4]]}")
            if ev:
                print("   🛑 0x0D 触发了 flash 保存 → 立即停止后续批次")
                break

        # ④ 推送后状态
        final = P.parse_ledparam(kb.query(P.Cmd.GET_LEDPARAM, None, bit8=True) or b"")
        print(f"\n④ 推送后模式 = {final.get('mode')}({final.get('mode_name')})  "
              f"亮度 {final.get('brightness')}")

        time.sleep(0.5)

        # ⑤ 恢复
        t0 = time.monotonic()
        kb.send(f_restore)
        time.sleep(FLASH_WAIT)
        a_res = w.after(t0)
        print(f"⑤ 恢复原灯效         : {len(a_res)} 包, 含 05 0F = {bool(save_events(a_res))}")
        back = P.parse_ledparam(kb.query(P.Cmd.GET_LEDPARAM, None, bit8=True) or b"")
        print(f"   回读 = {back.get('mode')}({back.get('mode_name')}) 亮度 {back.get('brightness')}")

        # ---- 结论
        print("\n" + "=" * 78)
        if ok_mode and viz_save_count == 0:
            print("结论：帧格式被接受 ✅，且 0x0D 不写 flash → **可以做，放心流式推**")
        elif ok_mode and viz_save_count > 0:
            print(f"结论：格式对了但 0x0D 触发了 {viz_save_count} 次 flash 保存 ❌ 不能长期跑")
        elif not ok_mode:
            print("结论：模式 20/22 在本固件上不存在 → 音乐律动这条路走不通，换 0x0E 屏幕同步或放弃")
        print("=" * 78)

    finally:
        w.stop()
        try:
            kb.send(f_restore)
        except Exception:  # noqa: BLE001
            pass
        kb.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
