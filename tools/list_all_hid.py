import hid

devs = hid.enumerate()
print(f"在线 HID 接口总数：{len(devs)}\n")
for d in devs:
    vp = f"{d['vendor_id']:04X}:{d['product_id']:04X}"
    up = d.get("usage_page", 0)
    us = d.get("usage", 0)
    prod = d.get("product_string") or ""
    mfr = d.get("manufacturer_string") or ""
    itf = d.get("interface_number")
    print(f"{vp}  itf={itf:<3} up=0x{up:04X} u=0x{us:04X}  [{mfr}] {prod}")
