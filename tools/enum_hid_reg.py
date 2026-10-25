"""从注册表枚举 HID/USB 设备，识别键盘 VID/PID 与方案。纯 winreg，无需管理员。"""
import re
import winreg

ROOTS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\HID", "HID"),
    (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB", "USB"),
]

PAT = re.compile(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})")


def dev_desc(path):
    """读取 DeviceDesc / FriendlyName，返回可读名称。"""
    names = []
    for prop in ("FriendlyName", "DeviceDesc"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
                v, _ = winreg.QueryValueEx(k, prop)
                # 形如 "@keyboard.inf,%hid.devicedesc%;HID Keyboard Device"
                seg = v.split(";")[-1] if ";" in v else v
                names.append(seg.strip())
        except OSError:
            continue
    return names[0] if names else ""


def walk(root, subkey, tag):
    found = []
    try:
        with winreg.OpenKey(root, subkey) as k:
            n = winreg.QueryInfoKey(k)[0]
            children = [winreg.EnumKey(k, i) for i in range(n)]
    except OSError as e:
        print(f"[跳过] {subkey}: {e}")
        return found

    for child in children:
        m = PAT.search(child)
        if not m:
            continue
        vid, pid = int(m.group(1), 16), int(m.group(2), 16)
        # 再下一层是实例
        base = f"{subkey}\\{child}"
        insts = []
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                n = winreg.QueryInfoKey(k)[0]
                insts = [winreg.EnumKey(k, i) for i in range(n)]
        except OSError:
            pass
        for inst in insts:
            name = dev_desc(f"{base}\\{inst}")
            found.append((tag, vid, pid, name, f"{child}\\{inst[:24]}"))
    return found


def main():
    rows = []
    for root, sub, tag in ROOTS:
        rows += walk(root, sub, tag)

    rows.sort(key=lambda r: (r[2], r[1], r[3]))
    print(f"{'总线':<4}{'VID:PID':<11}{'名称'}")
    print("-" * 88)
    for tag, vid, pid, name, inst in rows:
        print(f"{tag:<4}{vid:04X}:{pid:04X}   {name or '(无名称)'}")

    print("\n=== 疑似外设主控（键盘常见 VID）===")
    KNOWN = {
        0x258A: "SinoWealth 中颖电子（最常见国产三模键盘方案）",
        0x05AC: "Apple",
        0x0C45: "Sonix 松翰",
        0x1E71: "NZXT",
        0x0B05: "ASUS",
        0x046D: "Logitech",
        0x1532: "Razer",
        0x320F: "BY Technology / 贝盈",
        0x372D: "Compupack",
        0x3151: "BY Tech 变体",
        0x1BCF: "Sunplus 凌阳",
        0x04D9: "Holtek 盛群",
        0x0416: "Winbond 华邦",
        0x2EA3: "WCH 沁恒",
    }
    seen = set()
    for tag, vid, pid, name, inst in rows:
        if vid in KNOWN and (vid, pid) not in seen:
            seen.add((vid, pid))
            print(f"  VID_{vid:04X} PID_{pid:04X}  {KNOWN[vid]}")


if __name__ == "__main__":
    main()
