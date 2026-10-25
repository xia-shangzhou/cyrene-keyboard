"""枚举本机 HID 设备（VID/PID/产品名/接口号/UsagePage），用于识别昔涟键盘主控方案。"""
import ctypes
from ctypes import wintypes

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]


GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10


def get_hid_guid():
    g = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(g))
    return g


def wide(buf):
    if not buf:
        return ""
    return buf.value if hasattr(buf, "value") else str(buf)


def enum_hid():
    guid = get_hid_guid()
    setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if hdev == INVALID_HANDLE_VALUE or hdev is None:
        print("SetupDiGetClassDevs 失败:", ctypes.get_last_error())
        return []

    results = []
    idx = 0
    while True:
        did = SP_DEVICE_INTERFACE_DATA()
        did.cbSize = ctypes.sizeof(did)
        ok = setupapi.SetupDiEnumDeviceInterfaces(
            wintypes.HANDLE(hdev), None, ctypes.byref(guid), idx, ctypes.byref(did)
        )
        if not ok:
            break
        idx += 1

        # 取所需缓冲区大小
        req = wintypes.DWORD(0)
        setupapi.SetupDiGetDeviceInterfaceDetailW(
            wintypes.HANDLE(hdev), ctypes.byref(did), None, 0, ctypes.byref(req), None
        )
        if req.value == 0:
            continue

        buf = ctypes.create_string_buffer(req.value)
        # cbSize 字段在 64 位下为 8
        ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 8 if ctypes.sizeof(
            ctypes.c_void_p
        ) == 8 else 6
        if not setupapi.SetupDiGetDeviceInterfaceDetailW(
            wintypes.HANDLE(hdev), ctypes.byref(did), buf, req.value, ctypes.byref(req), None
        ):
            continue

        path = ctypes.wstring_at(ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD) * 2)

        h = kernel32.CreateFileW(
            path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if h == INVALID_HANDLE_VALUE:
            h = kernel32.CreateFileW(
                path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
            )
        if h == INVALID_HANDLE_VALUE:
            continue

        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(attrs)
        if hid.HidD_GetAttributes(wintypes.HANDLE(h), ctypes.byref(attrs)):
            pbuf = ctypes.create_unicode_buffer(256)
            hid.HidD_GetProductString(wintypes.HANDLE(h), pbuf, 256 * 2)
            mbuf = ctypes.create_unicode_buffer(256)
            hid.HidD_GetManufacturerString(wintypes.HANDLE(h), mbuf, 256 * 2)
            results.append(
                {
                    "vid": attrs.VendorID,
                    "pid": attrs.ProductID,
                    "ver": attrs.VersionNumber,
                    "product": pbuf.value,
                    "mfr": mbuf.value,
                    "path": path,
                }
            )
        kernel32.CloseHandle(wintypes.HANDLE(h))

    setupapi.SetupDiDestroyDeviceInfoList(wintypes.HANDLE(hdev))
    return results


if __name__ == "__main__":
    devs = enum_hid()
    print(f"共 {len(devs)} 个 HID 接口\n")
    seen = set()
    for d in devs:
        key = (d["vid"], d["pid"], d["product"])
        tag = "" if key in seen else "  <-- 新设备"
        seen.add(key)
        if tag:
            print(
                f"VID_{d['vid']:04X} PID_{d['pid']:04X} v{d['ver']:04X}  "
                f"[{d['mfr']}] {d['product']}{tag}"
            )
    print("\n--- 全部接口路径（用于定位可写集合）---")
    for d in devs:
        if d["vid"] in (0x258A, 0x0C45, 0x05AC, 0x1E71) or "FL" in d["product"].upper():
            print(f"  {d['vid']:04X}:{d['pid']:04X}  {d['path']}")
