import re

s = open(r"C:\Users\shang\AppData\Local\Temp\iot.js", encoding="utf-8", errors="replace").read()

pids = {}
for m in re.finditer(r"vid:12625,pid:(\d+)", s):
    pid = int(m.group(1))
    seg = s[m.start(): m.start() + 420]
    nm = re.search(r'displayName:"([^"]*)"', seg)
    cp = re.search(r'company:"([^"]*)"', seg)
    fr = re.search(r"featureReportByteLength:(\d+)", seg)
    pids.setdefault(pid, []).append(
        (nm.group(1) if nm else "?", cp.group(1) if cp else "?", fr.group(1) if fr else "?")
    )

print("0x3151 平台已知 PID 数量:", len(pids))
for pid, v in sorted(pids.items()):
    mark = "   <<<<<< 你的键盘" if pid == 0x5052 else ""
    print(f"  PID 0x{pid:04X} ({pid:5d})  {v[0][0]:<26} company={v[0][1]:<12} featLen={v[0][2]}{mark}")

print("\n=== battery 关键字上下文（前 6 处）===")
for i, m in enumerate(re.finditer(r"[Bb]attery", s)):
    if i >= 6:
        break
    a = max(0, m.start() - 180)
    b = min(len(s), m.end() + 180)
    print(f"@{m.start()}: " + s[a:b].replace("\n", " "))
    print()
