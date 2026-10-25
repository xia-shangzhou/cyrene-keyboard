"""探测昔涟键盘（0x3151:0x5052）的 HID 接口与厂商通道，只读不写。"""
import hid

VID, PID = 0x3151, 0x5052

devs = hid.enumerate()
targets = [d for d in devs if d["vendor_id"] == VID and d["product_id"] == PID]
print(f"匹配 {VID:04X}:{PID:04X} 的接口：{len(targets)} 个\n")

for i, d in enumerate(targets):
    print(f"[{i}] interface={d.get('interface_number')}  "
          f"usagePage=0x{d.get('usage_page', 0):04X}  usage=0x{d.get('usage', 0):04X}")
    print(f"    product   : {d.get('product_string')!r}")
    print(f"    mfr       : {d.get('manufacturer_string')!r}")
    print(f"    serial    : {d.get('serial_number')!r}")
    print(f"    release   : 0x{d.get('release_number', 0):04X}")
    print(f"    path      : {d['path']}")

vent = [d for d in targets if d.get("usage_page", 0) >= 0xFF00]
print(f"\n厂商自定义接口（usagePage>=0xFF00）：{len(vent)} 个")

for d in vent:
    print(f"\n>>> 尝试打开 usagePage=0x{d.get('usage_page',0):04X} usage=0x{d.get('usage',0):04X}")
    dev = hid.device()
    try:
        dev.open_path(d["path"])
    except Exception as e:
        print("    打开失败:", e)
        continue
    name = dev.get_product_string() or ""
    print(f"    已打开：{name!r}")
    for rid in (0,):
        for ln in (65, 64):
            try:
                data = dev.get_feature_report(rid, ln)
                hexs = " ".join(f"{b:02X}" for b in data[:ln])
                print(f"    get_feature_report(id={rid}, len={ln}) -> {len(data)} 字节")
                print(f"      {hexs}")
            except Exception as e:
                print(f"    get_feature_report(id={rid}, len={ln}) 失败: {e}")
    dev.close()
