# -*- coding: utf-8 -*-
"""
DriverTool - Εργαλείο εγκατάστασης οδηγών (drivers) για Windows
=================================================================
Υποστήριξη: Windows 7 SP1 / 8 / 8.1 / 10 / 11  (32 & 64-bit)

Πηγές drivers:
  * Windows Update / Microsoft Update (NVIDIA, AMD, Intel, Realtek, Broadcom ...)
  * Microsoft Update Catalog (επίσημοι υπογεγραμμένοι drivers, και για παλιό υλικό)
  * Επίσημες σελίδες κατασκευαστών
  * USB / φάκελος (και για υπολογιστές χωρίς internet)

Χρησιμοποιεί μόνο τη βασική βιβλιοθήκη της Python (3.6+), χωρίς εξωτερικά πακέτα.
Εκτέλεση:   python driver_tool.py      (ζητάει μόνο του δικαιώματα διαχειριστή)
Για .exe:   exe.bat
"""

from __future__ import print_function

import base64
import ctypes
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time
import webbrowser

try:
    from urllib.request import Request, urlopen
    from urllib.parse import quote, urlencode, urlparse
except ImportError:  # pragma: no cover
    print("Χρειάζεται Python 3.")
    sys.exit(1)

APP_NAME = "DriverTool"
APP_VERSION = "2.0"

IS_WINDOWS = os.name == "nt"

# ---------------------------------------------------------------------------
#  Φάκελος προγράμματος (εκεί μπαίνουν logs / αρχεία USB)
# ---------------------------------------------------------------------------
def _base_dir():
    env = os.environ.get("DRIVERTOOL_HOME")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    if getattr(sys, "frozen", False):            # PyInstaller .exe
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _base_dir()
LOG_DIR = os.path.join(BASE_DIR, "logs")
try:
    if not os.path.isdir(LOG_DIR):
        os.makedirs(LOG_DIR)
    _probe = os.path.join(LOG_DIR, ".probe")
    open(_probe, "w").close()
    os.remove(_probe)
except Exception:
    LOG_DIR = os.path.join(os.environ.get("TEMP", BASE_DIR), "DriverTool_logs")
    if not os.path.isdir(LOG_DIR):
        os.makedirs(LOG_DIR)

LOG_FILE = os.path.join(LOG_DIR, "log_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt")

DISPLAY_CLASS_GUID = "{4d36e968-e325-11ce-bfc1-08002be10318}"
MICROSOFT_UPDATE_ID = "7971f918-a847-4430-9279-4a52d1efe18d"
CATALOG = "https://www.catalog.update.microsoft.com"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DriverTool/" + APP_VERSION

VENDOR_IDS = {
    "10DE": "NVIDIA", "1002": "AMD", "1022": "AMD", "8086": "Intel", "8087": "Intel",
    "10EC": "Realtek", "0BDA": "Realtek", "14E4": "Broadcom", "0A5C": "Broadcom",
    "168C": "Qualcomm Atheros", "1969": "Qualcomm Atheros", "0CF3": "Qualcomm Atheros",
    "17CB": "Qualcomm", "1B21": "ASMedia", "1106": "VIA", "1039": "SiS", "5333": "S3",
    "102B": "Matrox", "1AF4": "Red Hat VirtIO", "15AD": "VMware", "80EE": "VirtualBox",
    "1814": "MediaTek/Ralink", "14C3": "MediaTek", "0E8D": "MediaTek", "1912": "Renesas",
    "197B": "JMicron", "1217": "O2 Micro", "1180": "Ricoh", "104C": "Texas Instruments",
    "11AB": "Marvell", "1B4B": "Marvell", "1D6A": "Aquantia/Marvell", "1095": "Silicon Image",
    "13F6": "C-Media", "1102": "Creative", "14F1": "Conexant", "111D": "IDT", "8384": "SigmaTel/IDT",
    "046D": "Logitech", "045E": "Microsoft", "04F2": "Chicony", "5986": "Bison",
    "138A": "Validity/Synaptics", "06CB": "Synaptics", "27C6": "Goodix", "0489": "Foxconn",
}

# ---------------------------------------------------------------------------
#  Έξοδος με χρώματα + log
# ---------------------------------------------------------------------------
COLORS = {
    None: 7, "gray": 8, "darkcyan": 3, "darkyellow": 6, "cyan": 11,
    "green": 10, "red": 12, "yellow": 14, "white": 15,
}

_console = None
if IS_WINDOWS:
    try:
        _console = ctypes.windll.kernel32.GetStdHandle(-11)
    except Exception:
        _console = None


def _log(text):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass


def out(text="", color=None):
    text = str(text)
    if _console:
        sys.stdout.flush()
        ctypes.windll.kernel32.SetConsoleTextAttribute(_console, COLORS.get(color, 7))
        try:
            print(text)
        finally:
            sys.stdout.flush()
            ctypes.windll.kernel32.SetConsoleTextAttribute(_console, 7)
    else:
        print(text)
    _log(text)


def ask(prompt):
    try:
        ans = input(prompt + " ")
    except EOFError:
        ans = ""
    _log(prompt + " " + ans)
    return ans.strip()


def pause():
    ask("\nΠατήστε Enter για συνέχεια...")


def clear():
    if IS_WINDOWS:
        os.system("cls")


def parse_selection(ans, count):
    """'1,3 5' -> [0,2,4]. Κενό -> όλα. '0' -> None (ακύρωση)."""
    if not ans:
        return list(range(count))
    if ans.strip() == "0":
        return None
    sel = []
    for p in re.split(r"[,; ]+", ans):
        if "-" in p:
            a, _, b = p.partition("-")
            if a.isdigit() and b.isdigit():
                for n in range(int(a), int(b) + 1):
                    if 1 <= n <= count and (n - 1) not in sel:
                        sel.append(n - 1)
            continue
        if p.isdigit() and 1 <= int(p) <= count and (int(p) - 1) not in sel:
            sel.append(int(p) - 1)
    return sel


def is_yes(ans, default):
    if not ans:
        return default
    return ans.lower() in ("ν", "ναι", "n", "nai", "y", "yes")


def is_no(ans):
    return ans.lower() in ("ο", "όχι", "οχι", "o", "oxi", "no")


# ---------------------------------------------------------------------------
#  Σύστημα
# ---------------------------------------------------------------------------
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    """Επανεκκίνηση με δικαιώματα διαχειριστή (UAC). True αν ξεκίνησε."""
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, subprocess.list2cmdline(sys.argv[1:])
    else:
        exe = sys.executable
        params = subprocess.list2cmdline([os.path.abspath(__file__)] + sys.argv[1:])
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, BASE_DIR, 1)
        return rc > 32
    except Exception:
        return False


def require_admin():
    if not is_admin():
        out("Αυτή η λειτουργία χρειάζεται δικαιώματα διαχειριστή. "
            "Κλείστε και ανοίξτε ξανά το πρόγραμμα με «Ναι» στο παράθυρο UAC.", "red")
        return False
    return True


def sys_tool(name):
    """Διαδρομή εργαλείου System32 (64-bit και όταν η Python είναι 32-bit)."""
    win = os.environ.get("SystemRoot", r"C:\Windows")
    native = os.path.join(win, "Sysnative", name)
    if os.environ.get("PROCESSOR_ARCHITEW6432") and os.path.exists(native):
        return native
    return os.path.join(win, "System32", name)


def powershell_exe():
    p = sys_tool(os.path.join("WindowsPowerShell", "v1.0", "powershell.exe"))
    return p if os.path.exists(p) else "powershell.exe"


class OsInfo(object):
    def __init__(self):
        if IS_WINDOWS:
            v = sys.getwindowsversion()
            self.major, self.minor, self.build = v.major, v.minor, v.build
        else:
            self.major, self.minor, self.build = 10, 0, 19045
        a = (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or "").upper()
        self.arch = {"AMD64": "x64", "ARM64": "arm64", "X86": "x86"}.get(a, "x64")
        if (self.major, self.minor) == (6, 0):
            self.family = "Windows Vista"
        elif (self.major, self.minor) == (6, 1):
            self.family = "Windows 7"
        elif (self.major, self.minor) == (6, 2):
            self.family = "Windows 8"
        elif (self.major, self.minor) == (6, 3):
            self.family = "Windows 8.1"
        elif self.major >= 10:
            self.family = "Windows 11" if self.build >= 22000 else "Windows 10"
        else:
            self.family = "Windows"
        self.caption = self.family


OS = OsInfo()


def run_ps(script, capture=True):
    """Εκτελεί PowerShell. Με capture επιστρέφει το κείμενο που έγραψε το script
    μέσω της συνάρτησης Emit (κωδικοποιημένο base64/UTF-8 ώστε να περνάνε σωστά τα ελληνικά)."""
    if capture:
        script = ("$__o = New-Object Text.StringBuilder\n"
                  "function Emit([string]$s) { [void]$__o.Append($s); [void]$__o.Append([char]10) }\n"
                  + script +
                  "\n[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($__o.ToString()))\n")
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    cmd = [powershell_exe(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc]
    if not capture:
        return subprocess.call(cmd)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    stdout, _ = p.communicate()
    lines = [l.strip() for l in stdout.decode("ascii", "replace").splitlines() if l.strip()]
    if not lines:
        return ""
    try:
        return base64.b64decode(lines[-1]).decode("utf-8", "replace")
    except Exception:
        return ""


SEP = "\x1f"

PS_DEVICES = r"""
foreach ($d in @(Get-WmiObject Win32_PnPEntity)) {
    $hw = ''
    if ($d.HardwareID) { $hw = [string]@($d.HardwareID)[0] }
    Emit ((@([string]$d.Name, [string]$d.DeviceID, $hw, [string][int]$d.ConfigManagerErrorCode, [string]$d.ClassGuid)) -join [char]31)
}
"""

PS_SYSINFO = r"""
$os = Get-WmiObject Win32_OperatingSystem
Emit ((@('OS', [string]$os.Caption, [string]$os.Version)) -join [char]31)
foreach ($c in @(Get-WmiObject Win32_Processor)) { Emit ((@('CPU', ([string]$c.Name).Trim())) -join [char]31) }
try { $mb = Get-WmiObject Win32_BaseBoard; Emit ((@('MB', [string]$mb.Manufacturer, [string]$mb.Product)) -join [char]31) } catch {}
try { $cs = Get-WmiObject Win32_ComputerSystem; Emit ((@('CS', [string]$cs.Manufacturer, [string]$cs.Model)) -join [char]31) } catch {}
foreach ($g in @(Get-WmiObject Win32_VideoController)) {
    $date = ''
    if ($g.DriverDate) { try { $date = ([Management.ManagementDateTimeConverter]::ToDateTime($g.DriverDate)).ToString('dd/MM/yyyy') } catch {} }
    Emit ((@('GPU', [string]$g.Name, [string]$g.DriverVersion, $date)) -join [char]31)
}
"""


class Device(object):
    def __init__(self, name, device_id, hwid, error, class_guid):
        self.name = name or "(Άγνωστη συσκευή)"
        self.device_id = device_id
        if not hwid and device_id:
            hwid = re.sub(r"\\[^\\]*$", "", device_id)
        self.hwid = hwid
        self.search_key = get_search_key(hwid)
        self.error = error
        self.class_guid = (class_guid or "").lower()
        self.vendor = get_vendor_name(hwid + " " + (device_id or ""))

    @property
    def real(self):
        return bool(re.match(r"^(PCI|USB|HDAUDIO|BTH|HID)\\", self.hwid, re.I) or
                    re.match(r"^ACPI\\(?!PNP)[A-Z]", self.hwid, re.I))


def get_vendor_name(text):
    m = re.search(r"(?:VEN_|VID_)([0-9A-Fa-f]{4})", text)
    if not m:
        return ""
    vid = m.group(1).upper()
    return VENDOR_IDS.get(vid, "Vendor " + vid)


def get_search_key(hwid):
    patterns = [
        r"^(PCI\\VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4})",
        r"^(USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4})",
        r"^(HDAUDIO\\FUNC_[0-9A-F]+&VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4})",
        r"^(BTH\\[^\\&]+)",
        r"^(ACPI\\[A-Z0-9_]+)",
        r"^(HID\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4})",
    ]
    for p in patterns:
        m = re.match(p, hwid or "", re.I)
        if m:
            return m.group(1).upper()
    return hwid or ""


def get_devices():
    devs = []
    for line in run_ps(PS_DEVICES).splitlines():
        f = line.split(SEP)
        if len(f) < 5:
            continue
        try:
            err = int(f[3])
        except ValueError:
            err = 0
        devs.append(Device(f[0], f[1], f[2], err, f[4]))
    return devs


def get_sysinfo():
    info = {"OS": [], "CPU": [], "MB": [], "CS": [], "GPU": []}
    for line in run_ps(PS_SYSINFO).splitlines():
        f = line.split(SEP)
        if f and f[0] in info:
            info[f[0]].append(f[1:])
    return info


def problem_devices(devs):
    return [d for d in devs if d.error != 0 and d.real]


def show_problem_devices(devs):
    bad = problem_devices(devs)
    if not bad:
        out("  Όλες οι συσκευές λειτουργούν κανονικά (δεν λείπουν drivers).", "green")
    for b in bad:
        reason = "σφάλμα %d" % b.error
        if b.error == 28:
            reason = "ΛΕΙΠΕΙ DRIVER"
        elif b.error == 22:
            reason = "απενεργοποιημένη"
        out("  [!] %s  [%s]  %s  (%s)" % (b.name, b.vendor, b.search_key, reason), "yellow")
    return bad


# ---------------------------------------------------------------------------
#  Επιλογή δίσκου / USB
# ---------------------------------------------------------------------------
def list_drives():
    """[(path, label)] για USB και άλλους δίσκους εκτός του δίσκου συστήματος."""
    res = []
    if not IS_WINDOWS:
        return res
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    sysdrive = os.environ.get("SystemDrive", "C:").upper()
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = "%s:\\" % chr(65 + i)
        dtype = k32.GetDriveTypeW(ctypes.c_wchar_p(root))
        if dtype not in (2, 3) or root[:2].upper() == sysdrive:
            continue
        free = ctypes.c_ulonglong(0)
        if not k32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(root), None, None, ctypes.byref(free)):
            continue  # π.χ. κενός αναγνώστης καρτών
        name = ctypes.create_unicode_buffer(261)
        k32.GetVolumeInformationW(ctypes.c_wchar_p(root), name, 261, None, None, None, None, 0)
        kind = "USB" if dtype == 2 else "Δίσκος"
        res.append((root, "%s %s  %s  (%.1f GB ελεύθερα)" % (kind, root, name.value, free.value / 1073741824.0)))
    return res


def select_target(prompt):
    out()
    out(prompt, "cyan")
    opts = [(BASE_DIR, "Φάκελος του προγράμματος (%s)" % BASE_DIR)] + list_drives()
    for i, (_, label) in enumerate(opts):
        out("  %d. %s" % (i + 1, label))
    out("  %d. Άλλος φάκελος (πληκτρολόγηση διαδρομής)" % (len(opts) + 1))
    ans = ask("Επιλογή [1]:") or "1"
    if not ans.isdigit():
        out("Μη έγκυρη επιλογή.", "red")
        return None
    n = int(ans)
    if 1 <= n <= len(opts):
        return opts[n - 1][0]
    if n == len(opts) + 1:
        p = ask("Διαδρομή φακέλου (π.χ. E:\\Drivers):").strip('" ')
        if not p:
            return None
        try:
            if not os.path.isdir(p):
                os.makedirs(p)
        except Exception:
            out("Μη έγκυρη διαδρομή.", "red")
            return None
        return p
    out("Μη έγκυρη επιλογή.", "red")
    return None


def safe_name(s, max_len):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", s).strip(" .")
    return (s[:max_len] or "device").rstrip(" .")


def run_tool(args):
    """Εκτελεί εξωτερικό εργαλείο δείχνοντας την έξοδό του. Επιστρέφει τον κωδικό εξόδου."""
    try:
        return subprocess.call(args)
    except OSError as e:
        out("  Αποτυχία εκτέλεσης %s: %s" % (args[0], e), "red")
        return -1


# ---------------------------------------------------------------------------
#  1. Σάρωση υλικού
# ---------------------------------------------------------------------------
def show_hardware_report():
    out("Σάρωση υλικού...", "cyan")
    info = get_sysinfo()
    lines = ["=== Σύστημα ==="]
    for o in info["OS"]:
        lines.append("  %s  (έκδοση %s, %s)" % (o[0], o[1] if len(o) > 1 else "", OS.arch))
    lines += ["", "=== Επεξεργαστής ==="]
    for c in info["CPU"]:
        lines.append("  " + re.sub(r"\s+", " ", c[0]))
    if info["CS"]:
        lines += ["", "=== Υπολογιστής ===", "  " + " ".join(info["CS"][0])]
    if info["MB"]:
        lines += ["", "=== Μητρική ===", "  " + " ".join(info["MB"][0])]
    lines += ["", "=== Κάρτα/ες γραφικών ==="]
    for g in info["GPU"]:
        g = g + ["", "", ""]
        lines.append("  %s  | driver %s  %s" % (g[0], g[1], g[2]))
    for l in lines:
        out(l)
    out()
    out("=== Συσκευές με πρόβλημα / χωρίς driver ===")
    devs = get_devices()
    show_problem_devices(devs)

    report = os.path.join(LOG_DIR, "hardware_%s.txt" % os.environ.get("COMPUTERNAME", "PC"))
    rows = lines + ["", "=== Όλες οι συσκευές ==="]
    for d in sorted([d for d in devs if d.real], key=lambda x: x.name.lower()):
        rows.append("%s\t%s\t%s\tκωδ.%d" % (d.name, d.vendor, d.hwid, d.error))
    with open(report, "w", encoding="utf-8-sig") as f:
        f.write("\r\n".join(rows))
    out()
    out("Η πλήρης αναφορά αποθηκεύτηκε στο: " + report, "gray")


# ---------------------------------------------------------------------------
#  2. Windows Update / Microsoft Update
# ---------------------------------------------------------------------------
PS_WINDOWS_UPDATE = r"""
$MicrosoftUpdateId = '%MUID%'
Write-Host 'Αναζήτηση drivers στο Windows Update... (μπορεί να πάρει μερικά λεπτά)' -ForegroundColor Cyan
$session = New-Object -ComObject Microsoft.Update.Session
$session.ClientApplicationID = 'DriverTool'
$res = $null
try {
    $sm = New-Object -ComObject Microsoft.Update.ServiceManager
    [void]$sm.AddService2($MicrosoftUpdateId, 7, '')
    $searcher = $session.CreateUpdateSearcher()
    $searcher.ServerSelection = 3
    $searcher.ServiceID = $MicrosoftUpdateId
    $res = $searcher.Search("IsInstalled=0 and Type='Driver' and IsHidden=0")
} catch {
    Write-Host '  (Το Microsoft Update δεν απάντησε, δοκιμή μέσω Windows Update...)' -ForegroundColor DarkGray
    try {
        $searcher = $session.CreateUpdateSearcher()
        $res = $searcher.Search("IsInstalled=0 and Type='Driver' and IsHidden=0")
    } catch {
        Write-Host ('Αποτυχία αναζήτησης: ' + $_.Exception.Message) -ForegroundColor Red
        Write-Host 'Δοκιμάστε τις επιλογές 3 ή 6/7 (Microsoft Update Catalog).' -ForegroundColor Yellow
        exit 2
    }
}
if ($res.Updates.Count -eq 0) {
    Write-Host 'Δεν βρέθηκαν νέοι drivers στο Windows Update για αυτόν τον υπολογιστή.' -ForegroundColor Green
    exit 0
}
Write-Host ''
Write-Host ('Βρέθηκαν {0} drivers:' -f $res.Updates.Count)
for ($i = 0; $i -lt $res.Updates.Count; $i++) {
    $u = $res.Updates.Item($i)
    $size = [math]::Round($u.MaxDownloadSize / 1MB, 1)
    Write-Host ('  {0,3}. {1}  ({2} MB)' -f ($i + 1), $u.Title, $size)
}
$ans = Read-Host "`nΠοιους να εγκαταστήσω; (Enter = όλους, π.χ. 1,3,5 , 0 = ακύρωση)"
$idx = @()
if (-not $ans) { for ($i = 0; $i -lt $res.Updates.Count; $i++) { $idx += $i } }
elseif ($ans.Trim() -eq '0') { exit 0 }
else {
    foreach ($p in ($ans -split '[,; ]+')) {
        $n = 0
        if ([int]::TryParse($p, [ref]$n) -and $n -ge 1 -and $n -le $res.Updates.Count) { $idx += ($n - 1) }
    }
}
if ($idx.Count -eq 0) { Write-Host 'Δεν επιλέχθηκε τίποτα.'; exit 0 }
$coll = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($i in $idx) {
    $u = $res.Updates.Item($i)
    if (-not $u.EulaAccepted) { try { $u.AcceptEula() } catch {} }
    [void]$coll.Add($u)
}
Write-Host 'Λήψη...' -ForegroundColor Cyan
$dl = $session.CreateUpdateDownloader()
$dl.Updates = $coll
try { [void]$dl.Download() } catch { Write-Host ('Σφάλμα λήψης: ' + $_.Exception.Message) -ForegroundColor Red; exit 3 }
Write-Host 'Εγκατάσταση...' -ForegroundColor Cyan
$inst = $session.CreateUpdateInstaller()
$inst.Updates = $coll
try { $r = $inst.Install() } catch { Write-Host ('Σφάλμα εγκατάστασης: ' + $_.Exception.Message) -ForegroundColor Red; exit 4 }
$codes = @{ 0 = 'Δεν ξεκίνησε'; 1 = 'Σε εξέλιξη'; 2 = 'ΟΚ'; 3 = 'ΟΚ με σφάλματα'; 4 = 'ΑΠΕΤΥΧΕ'; 5 = 'Ακυρώθηκε' }
for ($i = 0; $i -lt $coll.Count; $i++) {
    $rc = [int]$r.GetUpdateResult($i).ResultCode
    $color = 'Green'
    if ($rc -ge 4) { $color = 'Red' }
    Write-Host ('  [{0}] {1}' -f $codes[$rc], $coll.Item($i).Title) -ForegroundColor $color
}
if ($r.RebootRequired) { Write-Host "`nΧρειάζεται ΕΠΑΝΕΚΚΙΝΗΣΗ για να ολοκληρωθεί η εγκατάσταση." -ForegroundColor Yellow }
""".replace("%MUID%", MICROSOFT_UPDATE_ID)


def install_from_windows_update():
    if not require_admin():
        return
    rc = run_ps(PS_WINDOWS_UPDATE, capture=False)
    _log("Windows Update: κωδικός εξόδου %s" % rc)


# ---------------------------------------------------------------------------
#  3. Επίσημες σελίδες κατασκευαστών
# ---------------------------------------------------------------------------
def open_vendor_pages():
    out("Ανίχνευση εξοπλισμού...", "cyan")
    info = get_sysinfo()
    devs = get_devices()
    cpu = info["CPU"][0][0] if info["CPU"] else ""
    vendors = set(d.vendor for d in devs if d.vendor and d.real)
    if "Intel" in cpu:
        vendors.add("Intel")
    if "AMD" in cpu:
        vendors.add("AMD")

    pages = []
    if "NVIDIA" in vendors:
        pages.append(("NVIDIA - drivers κάρτας γραφικών (και παλιές/legacy κάρτες)",
                      "https://www.nvidia.com/en-us/drivers/"))
    if "AMD" in vendors:
        pages.append(("AMD - drivers γραφικών & chipset (Ryzen, Radeon, παλιά μοντέλα)",
                      "https://www.amd.com/en/support/download/drivers.html"))
    if "Intel" in vendors:
        pages.append(("Intel - Driver & Support Assistant (αυτόματη ανίχνευση)",
                      "https://www.intel.com/content/www/us/en/support/detect.html"))
        pages.append(("Intel - Download Center (χειροκίνητα, και παλιά προϊόντα)",
                      "https://www.intel.com/content/www/us/en/download-center/home.html"))
    if "Realtek" in vendors:
        pages.append(("Realtek - ήχος / δίκτυο", "https://www.realtek.com/Download/Index"))
    model = None
    if info["CS"] and info["CS"][0][0] and "System manufacturer" not in info["CS"][0][0] \
            and "To be filled" not in info["CS"][0][0]:
        model = " ".join(info["CS"][0])
    elif info["MB"] and info["MB"][0][0]:
        model = " ".join(info["MB"][0])
    if model:
        pages.append(("Σελίδα υποστήριξης μητρικής/λάπτοπ: " + model,
                      "https://www.bing.com/search?q=" + quote(model + " drivers support")))
    for b in problem_devices(devs)[:8]:
        pages.append(("Microsoft Update Catalog για: " + b.name,
                      CATALOG + "/Search.aspx?q=" + quote(b.search_key)))

    out("Εντοπισμένος εξοπλισμός:", "cyan")
    out("  CPU: " + cpu)
    for g in info["GPU"]:
        out("  GPU: " + g[0])
    out()
    if not pages:
        out("Δεν βρέθηκαν γνωστοί κατασκευαστές.")
        return
    for i, (label, _) in enumerate(pages):
        out("  %d. %s" % (i + 1, label))
    sel = parse_selection(ask("\nΠοιες σελίδες να ανοίξω; (Enter = όλες, π.χ. 1,2 , 0 = καμία):"), len(pages))
    if not sel:
        return
    for i in sel:
        webbrowser.open(pages[i][1])
        time.sleep(0.4)
    out("Οι σελίδες άνοιξαν στον browser.", "green")


# ---------------------------------------------------------------------------
#  4. Αντίγραφο ασφαλείας drivers
# ---------------------------------------------------------------------------
def count_infs(folder):
    n = 0
    for _, _, files in os.walk(folder):
        n += sum(1 for f in files if f.lower().endswith(".inf"))
    return n


def backup_drivers():
    if not require_admin():
        return
    dest = select_target("Πού να αποθηκευτούν οι drivers αυτού του υπολογιστή;")
    if not dest:
        return
    folder = os.path.join(dest, "DriverBackup_%s_%s" % (
        os.environ.get("COMPUTERNAME", "PC"), datetime.date.today().strftime("%Y%m%d")))
    if not os.path.isdir(folder):
        os.makedirs(folder)
    out("Εξαγωγή drivers στο %s ..." % folder, "cyan")
    ok = False
    if OS.major >= 10:
        ok = run_tool([sys_tool("pnputil.exe"), "/export-driver", "*", folder]) == 0
    if not ok and (OS.major >= 10 or (OS.major, OS.minor) >= (6, 3)):
        ok = run_tool([sys_tool("dism.exe"), "/online", "/export-driver", "/destination:" + folder]) == 0
    if not ok and OS.major < 10:
        out("Windows 7/8: αντιγραφή ολόκληρου του DriverStore (μπορεί να είναι 1-3 GB)...", "yellow")
        src = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "DriverStore", "FileRepository")
        run_tool([sys_tool("robocopy.exe"), src, folder, "/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH"])
    out("Ολοκληρώθηκε: %d πακέτα drivers αποθηκεύτηκαν." % count_infs(folder), "green")
    out("Για επαναφορά (π.χ. μετά από φορμάτ), τρέξτε το εργαλείο από το USB και διαλέξτε την επιλογή 5.")


# ---------------------------------------------------------------------------
#  5. Εγκατάσταση από USB / φάκελο
# ---------------------------------------------------------------------------
def install_from_folder():
    if not require_admin():
        return
    src = select_target("Από πού να εγκατασταθούν drivers; (θα ψαχτούν όλοι οι υποφάκελοι)")
    if not src:
        return
    out("Αναζήτηση αρχείων drivers...", "cyan")
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d.lower() not in ("$recycle.bin", "system volume information")]
        for f in files:
            if not f.lower().endswith(".cab"):
                continue
            cab = os.path.join(root, f)
            target = os.path.join(root, os.path.splitext(f)[0] + "_extracted")
            if os.path.isdir(target):
                continue
            out("Αποσυμπίεση " + f + " ...")
            os.makedirs(target)
            subprocess.call([sys_tool("expand.exe"), "-F:*", cab, target],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    infs = []
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d.lower() not in ("$recycle.bin", "system volume information")]
        infs += [os.path.join(root, f) for f in files if f.lower().endswith(".inf")]
    if not infs:
        out("Δεν βρέθηκαν drivers (.inf) σε αυτή τη θέση.", "red")
        return
    if is_no(ask("Βρέθηκαν %d αρχεία drivers. Να γίνει εγκατάσταση; (Ν/ο):" % len(infs))):
        return

    pnp = sys_tool("pnputil.exe")
    if OS.major >= 10:
        run_tool([pnp, "/add-driver", os.path.join(src, "*.inf"), "/subdirs", "/install"])
        subprocess.call([pnp, "/scan-devices"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        for i, inf in enumerate(infs, 1):
            out("[%d/%d] %s" % (i, len(infs), inf))
            subprocess.call([pnp, "-i", "-a", inf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out()
    out("Κατάσταση συσκευών μετά την εγκατάσταση:", "cyan")
    show_problem_devices(get_devices())
    out("Αν κάποια συσκευή δεν ενημερώθηκε, κάντε επανεκκίνηση.", "gray")


# ---------------------------------------------------------------------------
#  6. Καταγραφή υλικού σε USB (για PC χωρίς internet)
# ---------------------------------------------------------------------------
def export_hardware_list():
    dest = select_target("Πού να αποθηκευτεί η λίστα υλικού; (διαλέξτε το USB)")
    if not dest:
        return
    out("  1. Μόνο συσκευές χωρίς driver + κάρτες γραφικών (προτείνεται)")
    out("  2. Όλες οι συσκευές (για ενημέρωση όλων των drivers)")
    mode = ask("Επιλογή [1]:")
    out("Σάρωση υλικού...", "cyan")
    devs = [d for d in get_devices() if d.real]
    if mode != "2":
        devs = [d for d in devs if d.error != 0 or d.class_guid == DISPLAY_CLASS_GUID]
    pc = os.environ.get("COMPUTERNAME", "PC")
    lines = ["#OS=%s|%s|%d" % (OS.family, OS.arch, OS.build), "#PC=" + pc]
    seen = set()
    for d in devs:
        if d.search_key in seen:
            continue
        seen.add(d.search_key)
        lines.append("%s\t%s\t%s" % (d.name, d.search_key, d.hwid))
    path = os.path.join(dest, "hwids_%s.txt" % safe_name(pc, 40))
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write("\r\n".join(lines) + "\r\n")
    out("Καταγράφηκαν %d συσκευές στο %s" % (len(seen), path), "green")
    out("Τώρα πάρτε το USB σε υπολογιστή με internet, τρέξτε εκεί το εργαλείο και διαλέξτε την επιλογή 7.")


# ---------------------------------------------------------------------------
#  7. Λήψη drivers από Microsoft Update Catalog
# ---------------------------------------------------------------------------
def http(url, data=None, timeout=60):
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        data = urlencode(data).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    return urlopen(Request(url, data=data, headers=headers), timeout=timeout)


def clean_text(html):
    t = re.sub(r"<[^>]+>", " ", html)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


class CatalogItem(object):
    def __init__(self, uid, title, products, date, version):
        self.id, self.title, self.products, self.date, self.version = uid, title, products, date, version


def parse_catalog(html):
    items = []
    for m in re.finditer(r'<tr[^>]*id="([0-9a-fA-F\-]{36})_R\d+"[^>]*>(.*?)</tr>', html, re.S):
        uid, body = m.group(1), m.group(2)
        cells = {}
        for c in re.finditer(r'<td[^>]*id="[0-9a-fA-F\-]{36}_C(\d+)_R\d+"[^>]*>(.*?)</td>', body, re.S):
            cells[int(c.group(1))] = clean_text(c.group(2))
        tm = re.search(r'_link"[^>]*>(.*?)</a>', body, re.S)
        title = clean_text(tm.group(1)) if tm else cells.get(1, "")
        products = cells.get(2, "")
        date_s = cells.get(4, "")
        version = cells.get(5, "")
        if not cells:  # εναλλακτική ανάγνωση χωρίς ids στήλων
            for c in [clean_text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)]:
                if re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", c):
                    date_s = c
                elif not products and "Windows" in c and c != title:
                    products = c
                elif re.match(r"^\d+(\.\d+){2,}$", c):
                    version = c
        try:
            date = datetime.datetime.strptime(date_s, "%m/%d/%Y")
        except ValueError:
            date = datetime.datetime(1900, 1, 1)
        items.append(CatalogItem(uid, title, products, date, version))
    return items


def search_catalog(query):
    try:
        html = http(CATALOG + "/Search.aspx?q=" + quote(query)).read().decode("utf-8", "replace")
    except Exception as e:
        out("   Σφάλμα σύνδεσης: %s" % e, "red")
        return []
    return parse_catalog(html)


def catalog_download_urls(uid):
    payload = json.dumps([{"size": 0, "languages": "", "uidInfo": uid, "updateID": uid}])
    try:
        resp = http(CATALOG + "/DownloadDialog.aspx", {"updateIDs": payload}).read().decode("utf-8", "replace")
    except Exception as e:
        out("   Σφάλμα σύνδεσης: %s" % e, "red")
        return []
    urls = []
    for u in re.findall(r"\.url\s*=\s*'([^']+)'", resp):
        if u not in urls:
            urls.append(u)
    return urls


def os_matches(item, family, arch):
    pattern = {
        "Windows 11": r"Windows 11|Windows 10",
        "Windows 10": r"Windows 10",
        "Windows 8.1": r"Windows 8\.1|Windows 8 ",
        "Windows 8": r"Windows 8",
        "Windows 7": r"Windows 7",
        "Windows Vista": r"Vista",
    }.get(family, r"Windows 10")
    if not re.search(pattern, item.products):
        return False
    t = item.title
    if arch == "x64" and (re.search(r"ARM64", t) or (re.search(r"\bx86\b", t) and not re.search(r"x64|amd64", t, re.I))):
        return False
    if arch == "x86" and re.search(r"x64|amd64|ARM64", t, re.I):
        return False
    if arch == "arm64" and re.search(r"x64|amd64|\bx86\b", t, re.I) and "ARM64" not in t:
        return False
    return True


def download_file(url, target):
    tmp = target + ".part"
    resp = http(url, timeout=120)
    total = int(resp.headers.get("Content-Length") or 0)
    got = 0
    last = 0
    with open(tmp, "wb") as f:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total and time.time() - last > 0.3:
                last = time.time()
                sys.stdout.write("\r   %5.1f%%  (%.1f / %.1f MB)" % (got * 100.0 / total, got / 1048576.0, total / 1048576.0))
                sys.stdout.flush()
    if total:
        sys.stdout.write("\r" + " " * 50 + "\r")
    if os.path.exists(target):
        os.remove(target)
    os.rename(tmp, target)


def read_hwids_file(path):
    family, arch, pc, entries = "Windows 10", "x64", "PC", []
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            m = re.match(r"^#OS=([^|]+)\|([^|]+)", line)
            if m:
                family, arch = m.group(1), m.group(2)
                continue
            if line.startswith("#PC="):
                pc = line[4:].strip() or pc
                continue
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1]:
                entries.append((parts[0], parts[1]))
    return family, arch, pc, entries


def download_from_catalog():
    src = select_target("Πού βρίσκεται το αρχείο hwids_*.txt; (διαλέξτε το USB)")
    if not src:
        return
    files = sorted(glob.glob(os.path.join(src, "hwids_*.txt")))
    if not files:
        for root, dirs, fs in os.walk(src):
            dirs[:] = [d for d in dirs if d.lower() not in ("$recycle.bin", "system volume information")]
            files += [os.path.join(root, f) for f in fs if re.match(r"(?i)^hwids_.*\.txt$", f)]
            if len(files) >= 10:
                break
    if not files:
        out("Δεν βρέθηκε αρχείο hwids_*.txt. Τρέξτε πρώτα την επιλογή 6 στον υπολογιστή χωρίς internet.", "yellow")
        if not is_yes(ask("Να κατεβάσω drivers για ΑΥΤΟΝ τον υπολογιστή; (ν/Ο):"), False):
            return
        out("Σάρωση υλικού...", "cyan")
        entries, seen = [], set()
        for d in get_devices():
            if d.real and (d.error != 0 or d.class_guid == DISPLAY_CLASS_GUID) and d.search_key not in seen:
                seen.add(d.search_key)
                entries.append((d.name, d.search_key))
        family, arch, pc, out_base = OS.family, OS.arch, os.environ.get("COMPUTERNAME", "PC"), src
    else:
        path = files[0]
        if len(files) > 1:
            for i, f in enumerate(files):
                out("  %d. %s" % (i + 1, f))
            a = ask("Ποιο αρχείο; [1]:")
            if a.isdigit() and 1 <= int(a) <= len(files):
                path = files[int(a) - 1]
        family, arch, pc, entries = read_hwids_file(path)
        out_base = os.path.dirname(path)

    if not entries:
        out("Η λίστα είναι κενή - δεν χρειάζονται drivers.", "green")
        return
    out("Στόχος: %s  (%s, %s) - %d συσκευές" % (pc, family, arch, len(entries)), "cyan")
    per = ask("Πόσα πακέτα (τα νεότερα) ανά συσκευή; [1]:")
    per = int(per) if per.isdigit() and 1 <= int(per) <= 5 else 1

    out_root = os.path.join(out_base, "Drivers_" + safe_name(pc, 40))
    if not os.path.isdir(out_root):
        os.makedirs(out_root)
    ok, not_found = 0, []
    for name, key in entries:
        out()
        out(">> %s  [%s]" % (name, key), "white")
        res = search_catalog(key)
        if not res:
            out("   Δεν βρέθηκε στο Catalog.", "darkyellow")
            not_found.append((name, key))
            continue
        match = sorted([r for r in res if os_matches(r, family, arch)], key=lambda r: r.date, reverse=True)
        if not match:
            out("   Δεν υπάρχει έκδοση για %s - κατεβάζω την πιο κοντινή διαθέσιμη." % family, "darkyellow")
            match = sorted(res, key=lambda r: r.date, reverse=True)
        dev_folder = os.path.join(out_root, safe_name(name, 60))
        if not os.path.isdir(dev_folder):
            os.makedirs(dev_folder)
        for item in match[:per]:
            out("   %s  (%s)" % (item.title, item.date.strftime("%d/%m/%Y")))
            for u in catalog_download_urls(item.id):
                fname = os.path.basename(urlparse(u).path) or (item.id + ".cab")
                target = os.path.join(dev_folder, fname)
                if os.path.exists(target):
                    out("   (υπάρχει ήδη)", "gray")
                    ok += 1
                    continue
                try:
                    download_file(u, target)
                    out("   Κατέβηκε: " + fname, "green")
                    ok += 1
                except Exception as e:
                    out("   Αποτυχία λήψης: %s" % e, "red")
    out()
    out("Ολοκληρώθηκε: %d αρχεία στο %s" % (ok, out_root), "green")
    if not_found:
        out("Για αυτές τις συσκευές δεν βρέθηκε driver στο Catalog (δοκιμάστε την επιλογή 3 / σελίδα κατασκευαστή):", "yellow")
        for name, key in not_found:
            out("   - %s  [%s]" % (name, key))
    out("Πάρτε το USB στον υπολογιστή-στόχο, τρέξτε το εργαλείο και διαλέξτε την επιλογή 5.")


# ---------------------------------------------------------------------------
#  Κύριο μενού
# ---------------------------------------------------------------------------
MENU = [
    ("1", "Σάρωση υλικού (επεξεργαστής, κάρτα γραφικών, συσκευές χωρίς driver)", show_hardware_report),
    ("2", "Αυτόματη εγκατάσταση drivers από Windows Update (όλοι οι κατασκευαστές)", install_from_windows_update),
    ("3", "Επίσημες σελίδες κατασκευαστών (NVIDIA, AMD, Intel, Realtek...)", open_vendor_pages),
    ("4", "Αντίγραφο ασφαλείας των drivers αυτού του PC σε USB/φάκελο", backup_drivers),
    ("5", "Εγκατάσταση drivers από USB/φάκελο", install_from_folder),
    ("6", "Βήμα 1 - στο PC χωρίς internet: καταγραφή υλικού στο USB", export_hardware_list),
    ("7", "Βήμα 2 - σε PC με internet: λήψη drivers στο USB (Microsoft Update Catalog)", download_from_catalog),
]


def main():
    if not IS_WINDOWS:
        print("Το DriverTool λειτουργεί μόνο σε Windows.")
        return 1
    if not is_admin() and "--no-elevate" not in sys.argv:
        if relaunch_as_admin():
            return 0
    try:
        ctypes.windll.kernel32.SetConsoleTitleW("DriverTool - Εγκατάσταση drivers")
    except Exception:
        pass
    actions = dict((k, f) for k, _, f in MENU)
    while True:
        clear()
        out("=" * 72, "darkcyan")
        out("   DriverTool %s - Εγκατάσταση drivers (CPU, GPU, chipset, ήχος...)" % APP_VERSION, "cyan")
        out("=" * 72, "darkcyan")
        adm = "ΝΑΙ" if is_admin() else "ΟΧΙ (οι εγκαταστάσεις δεν θα λειτουργήσουν)"
        out("   %s | build %d | %s | Διαχειριστής: %s" % (OS.family, OS.build, OS.arch, adm), "gray")
        out()
        for key, label, _ in MENU[:5]:
            out("   %s. %s" % (key, label))
        out()
        out("   --- Υπολογιστής ΧΩΡΙΣ internet (μέσω USB) ---", "darkyellow")
        for key, label, _ in MENU[5:]:
            out("   %s. %s" % (key, label))
        out("      Βήμα 3 - πίσω στο PC χωρίς internet: επιλογή 5")
        out()
        out("   0. Έξοδος")
        out()
        choice = ask("Επιλογή:")
        out()
        if choice == "0":
            return 0
        fn = actions.get(choice)
        if not fn:
            continue
        try:
            fn()
        except KeyboardInterrupt:
            out("\nΑκυρώθηκε.", "yellow")
        except Exception as e:  # δεν κλείνει το πρόγραμμα σε απρόβλεπτο σφάλμα
            import traceback
            _log(traceback.format_exc())
            out("Απρόβλεπτο σφάλμα: %s (λεπτομέρειες στο %s)" % (e, LOG_FILE), "red")
        pause()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
