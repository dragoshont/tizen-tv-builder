"""TVs on this network: bounded read-only scan and stateless, user-operated connect.

state() feeds only the loopback page and CLI output of the person at this computer, so it
shows the TV's full address, DUID and app IDs. Errors, jobs and receipts are scrubbed of
addresses, and nothing here writes addresses or DUIDs to logs, caches or the repository.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import ipaddress
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import threading
import time
import webbrowser

import flows
import live_tv
import samsung_cert
from live_tv import LiveError, exact, fail
import network_tv
import appkit
import samsung_models
import tv_setup
from tizen import application_id
from workbench import sdk_tool

PORT = 26101
API_PORT = 8001
INFO_FIELDS = ("name", "model", "year", "developer_mode", "os", "firmware", "platform_year",
               "manufacturer", "resolution", "frame_tv")

DEV_LABELS = {"enabled": "Developer Mode on", "not-enabled": "Developer Mode off",
              "needs-config": "Host PC IP points elsewhere", "reachable": "Developer port open",
              "closed": "Developer port closed",
              "unknown": "Not reachable"}
# The only status words a TV card, list row or CLI line may show.
STATUSES = ("Checking…", "Not reachable", "Developer Mode off", "Ready to connect", "Connecting…",
            "Connected", "Connection problem")
STORE_APP = "Samsung doesn't allow debugging this store app. Only apps you install can be viewed."
MAX_TVS = 16
MAX_NETWORKS = 4
MAX_CERTIFICATE = 1024 * 1024
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
ALIAS = re.compile(r"tv-[0-9a-f]{6}")
DUID_EDGE = r"A-Za-z0-9_+/="

DEVELOPER_STEPS = (
    "On the TV remote, open Apps.",
    "Type 1 2 3 4 5 with the number keys (use the on-screen keypad if the remote has none).",
    "Switch Developer mode On.",
    "Enter this computer's address as Host PC IP: {host}.",
    "Choose OK, then restart the TV (hold Power until the logo appears).",
    "Come back here and choose Retry.",
)


REFUSED = re.compile(r"\bclosed\b|refused", re.IGNORECASE)


def scrub(text):
    """Remove network addresses from any message that may reach the UI, logs or receipts."""
    return IPV4.sub("[address]", str(text))[:400]


def redacted_address(host):
    if not host:
        return "not on this network"
    parts = host.split(".")
    return ".".join(parts[:2] + ["•", "•"])


def default_lease_root():
    return Path("/tmp") if os.name == "posix" else Path(tempfile.gettempdir())


def default_canonical():
    return default_lease_root() / "plx-samsung-device.lock"


def _key(path):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        value = path.read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            fail("invalid", "Alias key is damaged; delete it to create new TV aliases")
        return bytes.fromhex(value)
    value = secrets.token_hex(32)
    with os.fdopen(fd, "w") as stream:
        stream.write(value + "\n")
    return bytes.fromhex(value)


def _info(host):
    return network_tv.confirm(host, budget=1, identity=True)


def appkit_signed(path):
    """Structural check that the CLI produced both signatures; the TV verifies the digests."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False
    return "author-signature.xml" in names and "signature1.xml" in names


def _find_tool(name):
    try:
        return sdk_tool(name, None)
    except (LiveError, OSError, ValueError):
        return None


def default_data_root():
    return Path.home() / "tizen-studio-data"


def signing_profiles(data_root):
    """Profile names from Tizen Studio's profiles.xml. Names only; keys and passwords are never read."""
    path = Path(data_root) / "profile" / "profiles.xml"
    try:
        if path.is_symlink() or path.stat().st_size > MAX_CERTIFICATE:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    names = [p.get("name") for p in root.iter("profile") if p.get("name")]
    active = root.get("active") if root.get("active") in names else None
    distributors = {}
    for profile in root.iter("profile"):
        for item in profile.iter("profileitem"):
            # Only the key path is read; password attributes are never touched.
            if profile.get("name") and item.get("distributor") == "1" and item.get("key"):
                distributors[profile.get("name")] = item.get("key")
    return {"names": names, "active": active, "distributor": distributors.get(active),
            "distributors": distributors}


def _profile_devices(key):
    """(folder, DUIDs listed in the device-profile.xml next to a distributor key); None when unreadable."""
    folder = Path(key).expanduser().parent
    root = _safe_xml(folder / "device-profile.xml")
    return folder, None if root is None else [
        (d.text or "").strip() for d in root.iter("TestDevice") if (d.text or "").strip()]


def _safe_xml(path):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CERTIFICATE:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


def certificate_coverage(data_root, duid=None):
    """Which TVs the active signing profile's distributor certificate lists (device-profile.xml).

    Returns DUIDs and the profile name only; the profile's email and passwords are never read out."""
    profiles = signing_profiles(data_root)
    # Every other profile's device list too: a profile made elsewhere may already cover the TV.
    by_profile = {}
    for name, key in ((profiles or {}).get("distributors") or {}).items():
        devices = _profile_devices(key)[1]
        if name != (profiles or {}).get("active") and devices is not None:
            by_profile[name] = devices
    others = [name for name, devices in sorted(by_profile.items()) if duid and duid in devices]
    extra = {"duid": duid, "by_profile": by_profile, "others": others}
    if not profiles or not profiles["active"]:
        return dict(extra, state="no-profile", profile=None, covers=[], folder=None)
    key = profiles.get("distributor")
    if not key:
        # Tizen's built-in distributor certificate: Developer Mode TVs accept it without a DUID list.
        return dict(extra, state="default", profile=profiles["active"], covers=[], folder=None)
    folder, devices = _profile_devices(key)
    covers = devices or []
    if devices is None:
        state = "unreadable"
    elif not duid:
        state = "unknown-tv"
    else:
        state = "covered" if duid in covers else "not-covered"
    return dict(extra, state=state, profile=profiles["active"], covers=covers, folder=str(folder))


INSTALL_HELP = {
    "tizen-studio": "Install Tizen Studio with the Samsung TV extension (https://developer.samsung.com/smarttv), "
                    "or set TIZEN_SDK_ROOT to an existing installation",
    "profile": "Create a signing profile: for Developer Mode the default Tizen distributor certificate works on "
               "the 2017 and 2019 TVs tested. Run `tv cert add TV` to add a TV to a Samsung certificate",
    "openssl": "Needed for Samsung certificates. macOS and Linux include it; on Windows install Git for Windows",
    "samsung-ca": "Needed for Samsung certificates: install Tizen Studio's Certificate Manager with the Samsung TV "
                  "extension (Package Manager)",
}


def setup_report(sdb="auto", tizen="auto", data_root=None, which=shutil.which, cert_roots="auto", system=None):
    """What this computer has for the first-run journey. Detects only; never installs or runs anything."""
    sdb = _find_tool("sdb") if sdb == "auto" else sdb
    tizen = _find_tool("tizen") if tizen == "auto" else tizen
    profiles = signing_profiles(data_root or default_data_root())
    openssl = samsung_cert.find_openssl(which, system)
    ca = samsung_cert.find_ca(samsung_cert.sdk_roots(sdb, tizen) if cert_roots == "auto" else cert_roots)
    checks = [
        {"id": "sdb", "label": "sdb (connects to the TV)", "status": "found" if sdb else "missing",
         "next": None if sdb else INSTALL_HELP["tizen-studio"]},
        {"id": "tizen", "label": "tizen CLI (signs and installs)", "status": "found" if tizen else "missing",
         "next": None if tizen else INSTALL_HELP["tizen-studio"]},
        {"id": "profiles", "label": "Signing profiles", "status": "found" if profiles and profiles["names"] else "missing",
         "next": None if profiles and profiles["names"] else INSTALL_HELP["profile"]},
        {"id": "openssl", "label": "openssl (makes Samsung certificates)", "status": "found" if openssl else "missing",
         "next": None if openssl else INSTALL_HELP["openssl"]},
        {"id": "samsung-ca", "label": "Samsung certificate files (from Tizen Studio)",
         "status": "found" if ca else "missing", "next": None if ca else INSTALL_HELP["samsung-ca"]},
    ]
    ready = all(c["status"] == "found" for c in checks if c["id"] not in {"openssl", "samsung-ca"})
    return {"ready": ready, "checks": checks,
            "profiles": {"names": profiles["names"], "active": profiles["active"]} if profiles else
                        {"names": [], "active": None},
            "note": "Nothing was installed or changed; this only looks at what is already on this computer."}


DELETE_ON_TV = "Delete it on the TV: open Apps, select the gear (Settings), choose the app, then Delete"
APP_ID_TOKEN = re.compile(r"(?<![A-Za-z0-9.])[A-Za-z0-9]{10}\.[A-Za-z0-9]{1,52}(?![A-Za-z0-9.])")


# Plain-language meaning of what the Tizen CLI prints. Order matters: the first match wins.
INSTALL_ERRORS = (
    ("author-mismatch", re.compile(r"-11\b|author certificate not match", re.I),
     "The TV already has this app signed by someone else (a different author certificate)",
     "Remove the other version first: tv uninstall TV APP_ID, then install again"),
    ("certificate-expired", re.compile(r"expired", re.I),
     "The signing certificate has expired",
     "Renew it with one Samsung sign-in: tv cert add TV, then install again"),
    ("certificate-chain", re.compile(r"-12\b|invalid certificate chain|check certificate error", re.I),
     "The certificate does not list this TV, so the TV rejects the app",
     "Add this TV to your certificate: tv cert add TV"),
    ("signing-password", re.compile(r"inva[il]{2}d password", re.I),
     "The signing profile's password could not be read (common without a desktop keyring)",
     "Re-create the profile, or store its passwords in profiles.xml; details are in "
     "~/tizen-studio-data/cli/logs/cli.log"),
)


def explain(output, fallback_code, fallback_message, fallback_next):
    for code, pattern, message, nxt in INSTALL_ERRORS:
        if pattern.search(output):
            return code, message, nxt
    return fallback_code, fallback_message, fallback_next


def copy_text(text, system=None, run=subprocess.run):
    system = system or platform.system()
    command = ["pbcopy"] if system == "Darwin" else ["clip"] if system == "Windows" else (
        ["xclip", "-selection", "clipboard"] if shutil.which("xclip") else None)
    if not command:
        return False
    try:
        run(command, input=text.encode(), check=True, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def choose_package(system=None, run=subprocess.run):
    """Native "choose a .wgt" dialog, because a browser page cannot learn a file's path. None = cancelled."""
    system = system or platform.system()
    if system == "Darwin":
        command = ["osascript", "-e", 'POSIX path of (choose file with prompt "Choose a Tizen app (.wgt)")']
    elif system == "Windows":
        command = ["powershell", "-NoProfile", "-STA", "-Command",
                   "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.OpenFileDialog; "
                   "$d.Filter = 'Tizen app (*.wgt)|*.wgt'; if ($d.ShowDialog() -eq 'OK') { $d.FileName }"]
    elif shutil.which("zenity"):
        command = ["zenity", "--file-selection", "--file-filter=*.wgt"]
    else:
        fail("unsupported", "No file dialog on this computer; paste the .wgt path instead")
    try:
        done = run(command, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as error:
        fail("unsupported", f"Could not open a file dialog ({type(error).__name__}); paste the .wgt path instead")
    path = (done.stdout or "").strip()
    if done.returncode != 0 or not path:
        return None
    if not path.lower().endswith(".wgt") or not Path(path).is_file():
        fail("invalid", f"{path} is not a .wgt file")
    return path


_Link = _HeldLease = live_tv.MemoryLink


PREVIEW_SECONDS = 900
OFFLINE = "Turn the TV on, then scan again"


class Fleet:
    def __init__(self, sdb_path="auto", runner=flows.run_tool, interfaces=network_tv.interfaces,
                 sweep=network_tv.sweep, info=_info, ssdp=network_tv.ssdp_hosts, key_path=None, lease_root=None, canonical=None,
                 certificate_duids=None, retail=flows.retail, names=None, save_names=None, known=None, save_known=None,
                 session=None, cdp=live_tv.CDP, discover=None, tizen_path="auto", data_root=None,
                 which=shutil.which, clipboard=None, pictures=None, clock=time.time,
                 chooser=None, browser=None, signin=None, opener=None, cert_home=None, openssl="auto", cert_system=None,
                 sdk_installer=None):
        if sdb_path == "auto":
            sdb_path = _find_tool("sdb")
        if tizen_path == "auto":
            tizen_path = _find_tool("tizen")
        self.sdb = sdb_path
        self.tizen = tizen_path
        self.data_root = Path(data_root) if data_root else default_data_root()
        self.which = which
        self.chooser = chooser or choose_package
        self.clipboard = clipboard or copy_text
        # Samsung certificate seams: the browser opener, sign-in listener and HTTP are replaceable in tests.
        self.browser = browser or webbrowser.open
        self.signin = signin or samsung_cert.SignIn
        self.opener = opener or samsung_cert.urllib_opener
        self.cert_home = Path(cert_home) if cert_home else Path.home() / "SamsungCertificate"
        self.openssl = samsung_cert.find_openssl(which) if openssl == "auto" else openssl
        self.cert_system = cert_system  # Tests pass "Linux" so the real Keychain is never touched.
        self.cert_running = None  # (alias, thread, job) while a certificate request waits for sign-in.
        self.pictures = Path(pictures) if pictures else Path.home() / "Pictures" / "TV Screenshots"
        self.clock = clock
        self.runner = runner
        self.interfaces = interfaces
        self.sweep = sweep
        self.info = info
        self.ssdp = ssdp
        self.key_path = Path(key_path or Path.home() / ".tizen-tv-builder" / "alias.key")
        self.lease_root = Path(lease_root or default_lease_root())
        self.canonical = Path(canonical or default_canonical())
        self.certificate_duids = certificate_duids
        self.retail = retail
        self.names = dict(names or {})
        self.save_names = save_names
        # Last-known facts per alias (private profile): a TV that is off keeps its name and card.
        self.known = {a: dict(v) for a, v in (known or {}).items()}
        self.save_known = save_known
        self.session = session or (lambda serial, app, legacy: flows.Session(
            serial, app, sdb=self.sdb, runner=self.runner, legacy=legacy))
        self.cdp = cdp
        self.discover = discover or flows.pages
        self.records = {}
        self.scan = {"status": "never", "networks": 0, "found": 0, "sdb": "unknown"}
        self.lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.threads = []
        self.secret = None
        # Guided setup: the Tizen Studio install job and the app each TV's setup installed.
        self.sdk_installer = sdk_installer or tv_setup.install_sdk
        self.find_tool = _find_tool
        self.sdk_job = None
        self.setup_apps = {}
        # A memory with nothing to show (no name, model or Device ID) would only be a nameless card.
        self.known = {a: f for a, f in self.known.items() if any(f.get(k) for k in ("name", "model", "duid"))}
        for alias, fact in self.known.items():
            self.records[alias] = self._blank(alias) | {
                "serial": None, "host": None, "reachable": False, "sdb_state": None, "loopback": False,
                "missing": True, "unchecked": True, "api": False, "duid": fact.get("duid"),
                "observed_tizen": fact.get("os"),
                "info": {k: "unknown" for k in INFO_FIELDS} | {
                    k: fact[k] for k in ("name", "model") if fact.get(k)}}

    @staticmethod
    def _blank(alias):
        return {"alias": alias, "lease": None, "owned": False, "identity": None, "certificate": "unknown",
                "guidance": None, "job": None, "pending": None, "error": None, "dev_proven": False,
                "dev_refused": False}

    def _remember(self, record, answered=True):
        """Persist last-known name/model/OS/DUID for this alias. Never the network address."""
        info = record.get("info") or {}
        fact = {k: info[k] for k in ("name", "model") if info.get(k, "unknown") != "unknown"}
        if record.get("observed_tizen"):
            fact["os"] = record["observed_tizen"]
        if record.get("duid"):
            fact["duid"] = record["duid"]
        if record.get("serial"):
            fact["serial_alias"] = self.alias("serial:" + record["serial"])
        old = self.known.get(record["alias"], {})
        fact = {k: v for k, v in old.items() if k != "last_seen"} | fact
        if not any(fact.get(k) for k in ("name", "model", "duid")):
            return  # Nothing worth remembering yet.
        # "Last seen" is when the TV actually answered, not when sdb merely still lists it.
        if answered:
            fact["last_seen"] = int(self.clock())
        elif old.get("last_seen"):
            fact["last_seen"] = old["last_seen"]
        if fact.get("serial_alias") and fact["serial_alias"] != record["alias"] and record["lease"] is None:
            # A serial-keyed memory of this same TV is superseded by its real identity.
            stale = self.records.get(fact["serial_alias"])
            if fact["serial_alias"] in self.known or (stale and stale["lease"] is None and stale.get("missing")):
                self.known.pop(fact["serial_alias"], None)
                if stale and stale["lease"] is None:
                    self.records.pop(fact["serial_alias"], None)
        self.known[record["alias"]] = fact
        stable = lambda f: {k: v for k, v in f.items() if k != "last_seen"}
        # Write when a fact changed, or at most once a minute for "last seen".
        if self.save_known and (stable(fact) != stable(old) or fact.get("last_seen", 0) - old.get("last_seen", 0) >= 60):
            try:
                self.save_known(dict(self.known))
            except (LiveError, OSError, ValueError):
                pass  # Memory still has it; a profile write failure must not fail a scan.

    # ---- identity -------------------------------------------------------
    def alias(self, identity):
        if self.secret is None:
            self.secret = _key(self.key_path)
        return "tv-" + hmac.new(self.secret, identity.encode(), hashlib.sha256).hexdigest()[:6]

    def record(self, alias):
        if not isinstance(alias, str) or not ALIAS.fullmatch(alias) or alias not in self.records:
            fail("route", "Unknown TV; scan the network again")
        return self.records[alias]

    # ---- scan -----------------------------------------------------------
    def _devices(self):
        if not self.sdb:
            return None
        try:
            output = self.runner([self.sdb, "devices"], 10)
        except LiveError:
            return None
        found = {}
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", parts[0]):
                found[parts[0]] = parts[1]
        return found

    def _device_name(self, serial):
        """The `tizen -t` target: the third column of `sdb devices` (the serial alone is ignored)."""
        output = self.runner([self.sdb, "devices"], 10)
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3 and parts[0] == serial and re.fullmatch(r"[A-Za-z0-9_.:<>-]{1,64}", parts[2]):
                return parts[2]
        fail("connect", "The TV is no longer connected; connect again")

    def detect(self):
        if not self.scan_lock.acquire(blocking=False):
            fail("busy", "A scan is already running")
        try:
            with self.lock:
                self.scan = dict(self.scan, status="scanning", error=None)
            networks = [a for a in self.interfaces() if network_tv.local_address(a)][:MAX_NETWORKS]
            # Three bounded, concurrent observations: the Developer Mode port, Samsung's read-only
            # API port (so a TV with Developer Mode off is still seen) and SSDP. Only the first
            # proves a developer port; the others are candidates until the API answers.
            with ThreadPoolExecutor(2 * len(networks) + 1) as pool:
                dev = [pool.submit(self.sweep, a, port=PORT, timeout=.4) for a in networks]
                api = [pool.submit(self.sweep, a, port=API_PORT, timeout=.4) for a in networks]
                search = pool.submit(self.ssdp) if networks else None
                hosts = {h for job in dev for h in job.result() if network_tv.local_address(h)}
                api_hosts = {h for job in api for h in job.result() if network_tv.local_address(h)}
                try:
                    api_hosts.update(h for h in (search.result() if search else ()) if network_tv.local_address(h))
                except OSError:
                    pass
            devices = self._devices()
            entries = {}
            for host in sorted(hosts, key=ipaddress.IPv4Address):
                entries[f"{host}:{PORT}"] = {"host": host, "reachable": True, "sdb_state": None}
            for host in sorted(api_hosts - hosts, key=ipaddress.IPv4Address):
                entries[f"{host}:{PORT}"] = {"host": host, "reachable": False, "sdb_state": None, "api_only": True}
            for serial, value in (devices or {}).items():
                if serial.startswith("emulator-"):
                    continue  # Emulators have their own card; they are not TVs on the network.
                host, _, port = serial.rpartition(":")
                try:
                    address = ipaddress.IPv4Address(host)
                except ipaddress.AddressValueError:
                    address = None
                entry = entries.setdefault(serial, {"host": None, "reachable": False, "sdb_state": None})
                entry["sdb_state"] = value
                if address is not None:
                    entry["host"] = host
                    entry["loopback"] = address.is_loopback
            serials = list(entries)[:MAX_TVS]
            lookups = [s for s in serials if entries[s]["host"] and not entries[s].get("loopback")]

            def lookup(serial):
                try:
                    return self.info(entries[serial]["host"])
                except Exception:  # Optional, best-effort metadata; the scan itself never fails on it.
                    return None
            with ThreadPoolExecutor(max(1, min(8, len(lookups)))) as pool:
                infos = dict(zip(lookups, pool.map(lookup, lookups)))
            # An open 8001 port alone is not a Samsung TV; keep it only if its API answered.
            serials = [s for s in serials if not entries[s].get("api_only") or infos.get(s)]
            with self.lock:
                seen = set()
                for serial in serials:
                    entry = entries[serial]
                    info = infos.get(serial) or {}
                    uid = info.get("uid")
                    alias = self.alias("uid:" + uid if uid else "serial:" + serial)
                    if not uid:
                        # Its info timed out this time: keep the card this TV already has, not a second one.
                        alias = next((a for a, r in self.records.items() if r.get("serial") == serial
                                      and a not in seen and r.get("api")), alias)
                        alias = next((a for a, f in self.known.items() if f.get("serial_alias") == alias
                                      and a not in seen), alias)
                    if alias in seen:
                        # The same physical TV seen twice (e.g. two addresses): merge, keep the best path.
                        record = self.records[alias]
                        if record["lease"] is None and (entry["reachable"] or entry["sdb_state"]) and not (
                                record["reachable"] or record["sdb_state"]):
                            record.update(serial=serial, host=entry["host"], loopback=bool(entry.get("loopback")))
                        record["reachable"] = record["reachable"] or entry["reachable"]
                        record["sdb_state"] = record["sdb_state"] or entry["sdb_state"]
                        record["api"] = record["api"] or bool(info)
                        continue
                    seen.add(alias)
                    record = self.records.setdefault(alias, self._blank(alias))
                    if record["lease"] is not None and record["serial"] != serial:
                        continue  # Never retarget a TV this portal is holding.
                    record.update(serial=serial, host=entry["host"], reachable=entry["reachable"],
                                  sdb_state=entry["sdb_state"], loopback=bool(entry.get("loopback")),
                                  missing=False, unchecked=False)
                    if info:
                        record.update(api=True, info={k: info.get(k, "unknown") for k in INFO_FIELDS})
                    elif not record.get("api"):
                        # Keep last-known name/model rather than replacing them with "unknown".
                        record.update(api=False, info={k: "unknown" for k in INFO_FIELDS} | {
                            k: v for k, v in (record.get("info") or {}).items() if v != "unknown"})
                    self._remember(record, answered=bool(info) or entry["reachable"] or entry["sdb_state"] == "device")
                for alias, record in self.records.items():
                    if alias not in seen:
                        # One missed scan must not make a known TV vanish: keep it, truthfully "Not reachable".
                        record.update(missing=True, unchecked=False)
                        if record["lease"] is None:
                            record.update(reachable=False, sdb_state=None)
                self.scan = {"status": "done", "networks": len(networks), "found": len(seen),
                             "sdb": "unavailable" if devices is None else "ok", "finished_at": int(time.time())}
            return self.state()
        except Exception as error:
            with self.lock:
                for record in self.records.values():
                    record["unchecked"] = False  # The scan's own error is shown instead of a spinner.
                self.scan = dict(self.scan, status="failed",
                                 error=scrub(error) if isinstance(error, LiveError) else "Scan failed")
            raise
        finally:
            self.scan_lock.release()

    # ---- presentation ---------------------------------------------------
    def lease_path(self, alias):
        return self.lease_root / f"tizen-tv-{alias}.lock"

    def host_ip(self, record):
        if not record.get("host"):
            return None
        network = ipaddress.ip_network(record["host"] + "/24", strict=False)
        return next((a for a in self.interfaces() if ipaddress.IPv4Address(a) in network), None)

    def default_name(self, record, number):
        info = record.get("info") or {}
        name, model = info.get("name", "unknown"), info.get("model", "unknown")
        name = name.removeprefix("[TV] ").strip() if name != "unknown" else ""
        if name and model != "unknown":
            return f"{name} · {model}"
        if name or model != "unknown":
            return name or f"Samsung TV · {model}"
        year = info.get("year", "unknown")
        return f"Samsung TV ({year}) #{number}" if re.fullmatch(r"20[0-9]{2}", year) else f"Samsung TV #{number}"

    PENDING = {"connect": "Connecting…", "identify": "Reading TV details…", "disconnect": "Disconnecting…",
               "preview": "Opening app screen (restarts the app)…", "apps": "Listing apps…",
               "install": "Installing…", "remove": "Uninstalling…", "launch": "Starting app…",
               "screenshot": "Saving screenshot…", "cert": "Preparing certificate…",
               "profile": "Changing signing profile…",
               "sample": "Installing the sample app…"}

    def headline(self, record, status, reason):
        """One status per TV, identical in the pill, the list row and the CLI: (label, tone, detail)."""
        pending = record["pending"]
        if pending == "connect":
            return "Connecting…", "progress", "Connecting…"
        if status == "connected":
            meta = self.metadata(record)
            parts = ["Connected"]
            if meta["os"]["version"]:
                parts.append(meta["os"]["text"])
            if meta["model"]:
                parts.append(meta["model"])
            if self.preview_ready(record):
                parts.append("Showing " + record["preview"]["app"])
            detail = " · ".join(parts)
            if pending:
                detail += " · " + self.PENDING.get(pending, "Working…")
            return "Connected", "ok", detail
        if status == "problem":
            return "Connection problem", "lost", record["error"] or "The developer link stopped working"
        if pending:
            return self.PENDING.get(pending, "Working…"), "progress", self.PENDING.get(pending, "Working…")
        if record.get("unchecked"):
            # Remembered from an earlier run and not scanned yet: we don't know, so don't say "off".
            return "Checking…", "progress", "Checking whether this TV is on…"
        if record.get("missing"):
            return "Not reachable", "warn", OFFLINE
        if record.get("dev_refused") or (record["error"] and record["reachable"]):
            return "Connection problem", "lost", record["error"] or (
                "The TV refused this computer. On the TV set Host PC IP to this computer, restart the TV, then Connect")
        if record["reachable"]:
            return "Ready to connect", "idle", reason or "Developer Mode port is open"
        if record.get("api"):
            return "Developer Mode off", "warn", reason or "The TV answers, but its Developer Mode port is closed"
        return "Not reachable", "warn", OFFLINE

    def dev_mode(self, record, status):
        """Developer readiness from observed evidence only; separate from connection status."""
        diagnosis = label = None
        if record["lease"] is not None or (record["sdb_state"] == "device" and record["reachable"]):
            state, why = "enabled", "This computer has a working developer connection to the TV"
            nxt = "Connect to use it" if status == "not-connected" else None
        elif record["reachable"] and record.get("dev_refused"):
            # Only an observed refusal means setup is needed; an untried port is not a failure.
            state = "needs-config"
            why = ("Developer Mode is on, but the TV refused this computer: its Host PC IP most likely "
                   "points to another computer")
            nxt = "On the TV set Host PC IP to this computer, restart the TV, then Retry"
            diagnosis = "host-ip-elsewhere"
        elif record.get("missing"):
            # Cached API answers from an earlier scan say nothing about the TV now.
            state, why = "unknown", "The TV did not answer the last scan; it may be off or on another network"
            nxt = None  # The status line already says it: "Turn the TV on, then scan again".
        elif record["reachable"]:
            state = "reachable"
            why = ("The Developer Mode port is open; this computer connected earlier this session but is not connected now"
                   if record.get("dev_proven") else
                   "The Developer Mode port is open; this computer has not connected yet")
            nxt = "Connect to check"
        elif record.get("api"):
            reported = (record.get("info") or {}).get("developer_mode")
            if reported in ("on", "1"):
                state, label = "needs-config", "Developer Mode on, port closed"
                why = "The TV reports Developer Mode on, but its Developer Mode port is closed"
                nxt = "Restart the TV (hold Power until the logo), then scan again"
            elif reported in ("off", "0"):
                state = "not-enabled"
                why = "Developer Mode is off: the TV reports Developer Mode off and its Developer Mode port is closed"
                diagnosis = "developer-mode-off"
                nxt = "On the TV open Apps, type 1 2 3 4 5, switch Developer mode On with this computer as Host PC IP, restart, then scan again"
            else:
                # A closed port alone is not proof: Developer Mode may be off, or something blocks the port.
                state = "closed"
                why = "The TV answers, but its Developer Mode port is closed (Developer Mode off or blocked)"
                nxt = "On the TV open Apps, type 1 2 3 4 5, switch Developer mode On with this computer as Host PC IP, restart, then scan again"
        else:
            state, why = "unknown", "Neither the Developer Mode port nor the TV's API answered"
            nxt = None
        return {"state": state, "label": label or DEV_LABELS[state], "explanation": why, "next": nxt, "diagnosis": diagnosis}

    @staticmethod
    def metadata(record):
        """One scan metadata schema shared by the portal and `tizen.py tv`. Unknown stays unknown."""
        info = dict.fromkeys(INFO_FIELDS, "unknown") | (record.get("info") or {})
        model = info["model"]
        parsed = samsung_models.model_year(model if model != "unknown" else "")
        reported = next((v for v in (info["year"], info["platform_year"]) if re.fullmatch(r"20[1-3][0-9]", str(v))), None)
        if reported:
            year = {"value": int(reported), "provenance": "reported by the TV"}
        elif parsed["year"]:
            year = {"value": parsed["year"], "provenance": "inferred from the model code"}
        else:
            year = {"value": None, "provenance": "unknown"}
        # Observed facts about the TV outlive the connection that read them.
        observed = (record.get("identity") or {}).get("tizen") or record.get("observed_tizen")
        os_name = info["os"] if info["os"] != "unknown" else ("Tizen" if observed or record.get("api") else "unknown")
        if observed and observed != "unknown":
            os_info = {"name": "Tizen", "version": observed, "provenance": "observed over the developer connection",
                       "text": "Tizen " + observed}
        else:
            os_info = {"name": os_name, "version": None, "provenance": "reported name only" if os_name != "unknown" else "unknown",
                       "text": None}  # A name without a version says nothing; unknown fields are omitted.
        baseline = samsung_models.BASELINE_TIZEN.get(year["value"])
        return {"manufacturer": info["manufacturer"] if info["manufacturer"] != "unknown" else
                ("Samsung" if record.get("api") or record["reachable"] or record["sdb_state"] else "unknown"),
                "friendly_name": info["name"].removeprefix("[TV] ").strip() if info["name"] != "unknown" else None,
                "model": model if model != "unknown" else None, "family": parsed["family"],
                "model_year": year, "manufacture_date": None, "os": os_info,
                "baseline": (f"Tizen {baseline} is the platform baseline for {year['value']} models, "
                             "not this TV's observed version") if baseline else None,
                "resolution": info["resolution"] if info["resolution"] != "unknown" else None,
                "frame_tv": info["frame_tv"] == "yes",
                "firmware": info["firmware"] if info["firmware"] != "unknown" else None}

    def summary(self, record, number):
        held = record["lease"] is not None
        # Connected = a working developer link. A picture is extra detail, not a different status.
        status = ("problem" if held and record["error"] else "connected" if held else "not-connected")
        info = record.get("info") or {}
        default = self.default_name(record, number)
        guidance = record["guidance"]
        if guidance and guidance.get("kind") in {"host-ip", "developer-mode"}:
            guidance = dict(guidance, host_ip=self.host_ip(record))
        if status == "not-connected" and not record["pending"]:
            reason = ("Developer Mode port is open" if record["reachable"] else
                      "The TV answers, but its Developer Mode port is closed" if record.get("api") else
                      "Developer Mode port is closed or the TV is off")
        else:
            reason = None
        label, tone, headline = self.headline(record, status, reason)
        meta = self.metadata(record)
        duid = record.get("duid")
        return {"alias": record["alias"], "name": self.names.get(record["alias"], default),
                "label": label, "tone": tone, "headline": headline,
                "default_name": default, "renamed": record["alias"] in self.names,
                # No name or model known: the portal and CLI title it by its address from details.
                "unnamed": record["alias"] not in self.names and not any(
                    info.get(k, "unknown") != "unknown" for k in ("name", "model")),
                "status": status, "not_connected_reason": reason,
                "last_seen": self.known.get(record["alias"], {}).get("last_seen"),
                "reachable": bool(record["reachable"]), "missing": record.get("missing", False),
                "developer_mode": info.get("developer_mode", "unknown"),
                "dev_mode": self.dev_mode(record, status),
                "model": {"value": info.get("model", "unknown"),
                          "provenance": "observed" if info.get("model", "unknown") != "unknown" else "unknown"},
                "year": info.get("year", "unknown"), "metadata": meta,
                "owned": bool(record["owned"]), "held": held,
                "preview": self.preview_summary(record),
                "apps": self.apps_summary(record),
                "identity": record["identity"], "certificate": self.coverage(duid, record["alias"]),
                "guidance": guidance, "pending": record["pending"], "error": record["error"],
                "job": record["job"],
                # Shown in full on this loopback page and CLI only; never logged or cached.
                "details": {"address": record.get("host"), "duid": duid,
                            "model": meta["model"], "os": meta["os"]["text"], "firmware": meta["firmware"],
                            "developer_port": "open" if record["reachable"] else "closed or unknown",
                            "sdk_link": record["sdb_state"] or "none", "host_ip": self.host_ip(record)}}

    def short_name(self, alias):
        """What people call this TV: their rename, else its own name, else its model."""
        if alias in self.names:
            return self.names[alias]
        record, fact = self.records.get(alias) or {}, self.known.get(alias, {})
        info = record.get("info") or {}
        for value in (info.get("name"), fact.get("name"), info.get("model"), fact.get("model")):
            if value and value != "unknown":
                return value.removeprefix("[TV] ").strip()
        return None

    def name_for_duid(self, duid):
        aliases = [a for a, r in self.records.items() if r.get("duid") == duid]
        aliases += [a for a, f in self.known.items() if f.get("duid") == duid and a not in aliases]
        return next((n for n in map(self.short_name, aliases) if n), duid)

    def coverage(self, duid, alias=None):
        """Certificate coverage of the active signing profile, cached briefly: state() is polled."""
        now = time.monotonic()
        cached = getattr(self, "_coverage", None)
        if cached is None or now - cached[0] > 5:
            cached = (now, certificate_coverage(self.data_root))
            self._coverage = cached
        base = dict(cached[1], duid=duid)
        by_profile = base.pop("by_profile", {})
        # Names of the other signing profiles that list this TV (no other profile's device IDs leave here).
        base["others"] = [name for name, devices in sorted(by_profile.items()) if duid and duid in devices]
        if base["state"] in {"unknown-tv", "covered", "not-covered"}:
            base["state"] = "unknown-tv" if not duid else "covered" if duid in base["covers"] else "not-covered"
        # TV names instead of bare device IDs; an ID no remembered TV has stays an ID.
        base["covers_names"] = [self.name_for_duid(d) for d in base["covers"]]
        base["tv_name"] = (self.short_name(alias) if alias else None) or "this TV"
        return base

    def state(self):
        with self.lock:
            ordered = sorted(self.records.values(), key=lambda r: (r.get("host") is None, r["alias"]))
            return {"scan": dict(self.scan), "sdk": "available" if self.sdb else "missing",
                    "tizen": "available" if self.tizen else "missing",
                    "signing_profiles": self.profile_names(),
                    "tvs": [self.summary(r, n) for n, r in enumerate(ordered, 1)]}

    def profile_names(self):
        # Names only, cached briefly: state() is polled every second.
        now = time.monotonic()
        cached = getattr(self, "_profiles", None)
        if cached is None or now - cached[0] > 10:
            cached = (now, list((signing_profiles(self.data_root) or {"names": []})["names"])[:32])
            self._profiles = cached
        return cached[1]

    @staticmethod
    def apps_summary(record):
        """Installed apps exactly as the TV listed them; never guessed from exit codes."""
        apps = record.get("apps")
        if record["lease"] is None:
            apps = None
        store = sorted(record.get("store_apps") or ())
        mine = sorted(record.get("installed_here") or ())
        if apps is None:
            state = "unreadable" if record["lease"] is not None and record.get("apps_unreadable") else "unknown"
            return {"state": state, "items": [], "store": store, "installed_here": mine}
        return {"state": "listed", "items": sorted(apps)[:500], "store": store, "installed_here": mine}

    # ---- user operations --------------------------------------------------
    def _require_sdk(self):
        if not self.sdb:
            fail("sdk", "Tizen Studio's sdb was not found. " + INSTALL_HELP["tizen-studio"] + ", then run `tv setup`")

    def _devices_ready(self, serial):
        return (self._devices() or {}).get(serial) == "device"

    def connect(self, alias, data):
        if not isinstance(data, dict) or not set(data) <= {"confirm"}:  # A legacy "confirm" is ignored.
            fail("invalid", "Request fields do not match this operation")
        self._require_sdk()
        with self.lock:
            record = self.record(alias)
            already = record["lease"] is not None
            if not already and not record.get("host"):
                fail("invalid", "This TV has no network address to connect to")
            if not already:
                record["lease"], record["error"], record["guidance"] = _Link(), None, None
        if already:
            return self.state()  # Already connected: Connect is a no-op, not an error.
        lease = record["lease"]
        serial = record["serial"]
        attempted = False
        try:
            if self._devices_ready(serial):
                owned = False
            else:
                attempted = True
                self.runner([self.sdb, "connect", serial], 20)
                if not self._devices_ready(serial):
                    fail("connect", "The TV did not accept the connection")
                owned = True
        except LiveError as error:
            if attempted:
                try:
                    self.runner([self.sdb, "disconnect", serial], 20)
                except LiveError:
                    pass
            with self.lock:
                lease.release()
                record["lease"] = None
                record["sdb_state"] = None
                record["dev_refused"] = bool(record["reachable"])
                if record["reachable"]:
                    record["guidance"] = {"kind": "host-ip", "title": "Check the TV's Host PC IP",
                                          "steps": list(DEVELOPER_STEPS)}
                    message = ("The TV's Developer Mode port answered but refused this computer. "
                               "Set Host PC IP to this computer, restart the TV, then Retry")
                else:
                    record["guidance"] = {"kind": "developer-mode", "title": "How to enable Developer Mode",
                                          "steps": list(DEVELOPER_STEPS)}
                    message = "Developer Mode port is closed or the TV is off. Enable Developer Mode, then Retry"
                record["error"] = message + " (" + scrub(error) + ")"
            fail("connect", record["error"])
        with self.lock:
            record["owned"] = owned
            record["sdb_state"] = "device"
            record["dev_proven"], record["dev_refused"] = True, False
        return self.state()

    def identify(self, alias, data):
        """Read the TV's identity over the developer link. Runs by itself on Connect and Scan."""
        exact(data, {})
        with self.lock:
            record = self.record(alias)
            if record["lease"] is None:
                fail("connect", "Connect to this TV first")
        try:
            public, duid = flows._identity(record["serial"], self.sdb, self.runner)
        except (LiveError, ValueError) as error:
            if isinstance(error, LiveError) and REFUSED.search(str(error)):
                self._refused(record, error)
            with self.lock:
                record["error"] = record["error"] or "Could not read the TV identity: " + scrub(error)
            fail("identity", record["error"])
        with self.lock:
            record["fingerprint"] = public["fingerprint"]
            record["duid"] = duid  # Memory only; shown on this local page, never logged or cached.
            if public["platform_version"] and public["platform_version"] != "unknown":
                record["observed_tizen"] = public["platform_version"]
            record["identity"] = {"duid_present": True, "tizen": public["platform_version"],
                                  "profile": public["profile"], "tier": public["tier"]}
            record["error"] = None
            record["guidance"] = None
            self._remember(record)
        return self.state()

    def _refused(self, record, error):
        """The TV accepted the link but refuses every command: not a connection; Host PC IP points elsewhere."""
        try:
            self._release(record)
        except LiveError:
            pass  # The refusal is the story; a failed cleanup is not a connection either.
        name = self.names.get(record["alias"]) or "The TV"
        with self.lock:
            record["dev_refused"], record["dev_proven"] = True, False
            record["guidance"] = {"kind": "host-ip", "title": "Check the TV's Host PC IP",
                                  "steps": list(DEVELOPER_STEPS)}
            record["error"] = (f"{name} accepted the connection but refused commands. On the TV set Host PC IP "
                               "to this computer, restart the TV, then Connect (" + scrub(error) + ")")

    def disconnect(self, alias, data):
        exact(data, {})
        with self.lock:
            record = self.record(alias)
            if record["lease"] is None:
                fail("connect", "This portal is not connected to that TV")
        self._release(record)
        return self.state()

    def _release(self, record):
        failed = None
        try:
            self._end_preview(record)
        except LiveError as error:
            failed = error
        if record["owned"]:
            try:
                self.runner([self.sdb, "disconnect", record["serial"]], 20)
            except LiveError as error:
                failed = error
        with self.lock:
            if record["lease"] is not None:
                record["lease"].release()
            record.update(lease=None, owned=False, sdb_state=None if not failed else record["sdb_state"],
                          identity=None, guidance=None, fingerprint=None, apps=None, apps_unreadable=False)
        if failed:
            fail("cleanup", "Disconnect failed: " + scrub(failed))

    # ---- app preview (the app's own drawing, never the whole TV) ---------------
    @staticmethod
    def preview_ready(record):
        preview = record.get("preview")
        if not preview:
            return False
        source = preview["source"]
        return (source.status in {"connected", "in-flight"} and bool(source.frames)
                and source.expires > time.monotonic())

    def preview_summary(self, record):
        preview = record.get("preview")
        if not preview:
            return None
        return dict(preview["source"].summary(), app=preview["app"], ready=self.preview_ready(record))

    def preview_source(self, alias):
        with self.lock:
            record = self.records.get(alias)
            preview = record.get("preview") if record else None
            return preview["source"] if preview else None

    def preview(self, alias, data):
        """Open any app's screen. This restarts the app; the button says so. The TV gives the real answer."""
        if not isinstance(data, dict) or not set(data) <= {"app", "restart"} or "app" not in data:
            fail("invalid", "Request fields do not match this operation")
        with self.lock:
            record = self.record(alias)
            if record["lease"] is None:
                fail("connect", "Connect to this TV first")
            app = self._app(data["app"])
            current = record.get("preview")
            serial = record["serial"]
            legacy = str(record.get("observed_tizen") or "").startswith("3.")
        if current and current["app"] == app and current["session"].forward:
            # Re-attach to the inspector this portal already opened; no restart.
            self._attach(record, current)
            return self.state()
        self._end_preview(record)
        session = self.session(serial, app, legacy)
        preview = {"app": app, "session": session, "source": None}
        try:
            session.launch()
            if session.client is not None:
                session.client.close()  # Legacy inspectors accept one client; the preview takes it.
                session.client = None
            self._attach(record, preview)
        except (LiveError, ValueError, OSError) as error:
            try:
                session.close()
            except LiveError:
                pass
            with self.lock:
                record["preview"] = None
                closed = isinstance(error, LiveError) and re.search(r"\bclosed\b", str(error), re.I)
                if closed and app not in (record.get("installed_here") or ()):
                    # Observed fact for this TV: the debug launch of this app was refused.
                    record.setdefault("store_apps", set()).add(app)
            if closed and app not in (record.get("installed_here") or ()):
                fail("preview", STORE_APP + " (" + app + ": " + scrub(error) + ")")
            fail("preview", "Could not open " + app + ": " + scrub(error))
        with self.lock:
            (record.get("store_apps") or set()).discard(app)
        return self.state()

    def _attach(self, record, preview):
        local = preview["session"].forward[0]
        page = flows.wait_pages(self.discover, local)[0]
        config = {"id": record["alias"], "kind": "tv", "endpoint": f"http://127.0.0.1:{local}",
                  "target_id": page, "lease_path": str(self.lease_path(record["alias"])),
                  "lease_seconds": PREVIEW_SECONDS, "metadata": {}, "safe_activation_ids": [],
                  "user_activation": True}
        old = preview.get("source")
        if old is not None:
            old.disconnect()
        source = live_tv.Source(config, self.cdp, lease=_HeldLease())
        try:
            source.operate("connect", {"authorized": True, "pixels": True, "input": True,
                                       "run_seconds": PREVIEW_SECONDS})
            source.operate("capture", {})
            if not source.frames:
                fail("capture", "The app answered but no picture was captured")
        except LiveError:
            source.disconnect()
            raise
        preview["source"] = source
        with self.lock:
            record["preview"] = preview

    def _end_preview(self, record):
        with self.lock:
            preview = record.get("preview")
            record["preview"] = None
        if not preview:
            return
        try:
            if preview["source"] is not None:
                preview["source"].disconnect()
        finally:
            preview["session"].close()  # Removes only the forward this portal created.

    def rename(self, alias, data):
        exact(data, {"name"})
        name = data["name"]
        if not isinstance(name, str) or len(name.strip()) > 60 or any(ord(c) < 32 for c in name):
            fail("invalid", "Name must be up to 60 plain characters")
        with self.lock:
            self.record(alias)
            names = dict(self.names)
            if name.strip():
                names[alias] = name.strip()
            else:
                names.pop(alias, None)
            if self.save_names:
                self.save_names(names)
            self.names = names
        return self.state()

    # ---- async wrapper for HTTP -------------------------------------------
    # ---- first run: DUID, signing, install, launch, remove --------------------
    def read_duid(self, alias):
        """The certificate DUID from `getduid` over the developer link. Never the REST API's `duid`."""
        with self.lock:
            record = self.record(alias)
            if record["lease"] is None:
                fail("connect", "Connect to this TV first")
        try:
            _, duid = flows._identity(record["serial"], self.sdb, self.runner)
        except (LiveError, ValueError) as error:
            fail("identity", "Could not read the TV's DUID: " + scrub(error))
        return duid

    def _change(self, alias, data, keys, optional=()):
        # No typed confirmations: the person clicking is a trusted developer. A legacy "confirm" is ignored.
        if not isinstance(data, dict) or not set(keys) <= set(data) <= set(keys) | set(optional) | {"confirm"}:
            fail("invalid", "Request fields do not match this operation")
        with self.lock:
            record = self.record(alias)
            if record["lease"] is None:
                fail("connect", "Connect to this TV first")
        if not self.tizen:
            fail("sdk", "Tizen Studio's tizen CLI was not found. " + INSTALL_HELP["tizen-studio"])
        return record

    def _finish(self, record, result):
        with self.lock:
            record["job"] = result
        return result

    def _app(self, value):
        try:
            return application_id(value if isinstance(value, str) else "")
        except ValueError as error:
            fail("invalid", str(error))

    def install(self, alias, data):
        """Re-sign a private copy with the user's profile, then install it. Errors in plain language."""
        record = self._change(alias, data, {"package"}, {"sign"})
        package = data["package"]
        sign = data.get("sign") or (signing_profiles(self.data_root) or {}).get("active")
        if not isinstance(package, str) or not package.lower().endswith(".wgt") or not Path(package).is_file():
            fail("invalid", "Package must be an existing .wgt file")
        if not sign:
            return self._finish(record, {"op": "install", "ok": False, "code": "no-profile",
                                         "message": "There is no active signing profile in Tizen Studio",
                                         "next": INSTALL_HELP["profile"]})
        if not isinstance(sign, str) or not re.fullmatch(r"[A-Za-z0-9 _.-]{1,64}", sign):
            fail("invalid", "Signing profile name is not valid")
        profiles = signing_profiles(self.data_root) or {"names": []}
        if sign not in profiles["names"]:
            names = ", ".join(profiles["names"]) or "none yet"
            return self._finish(record, {"op": "install", "ok": False, "code": "no-profile",
                                         "message": f"There is no signing profile called {sign}",
                                         "next": f"Use one of: {names}"})
        try:
            app = appkit.check_package(package)["app"]
        except (ValueError, OSError, KeyError, zipfile.BadZipFile):
            app = None
        if not app:
            fail("invalid", "That file is not a valid Tizen TV web app package (config.xml needs an app ID and the tv profile)")
        device = self._device_name(record["serial"])
        with tempfile.TemporaryDirectory(prefix="tizen-install-") as folder:
            source = Path(folder) / "in" / "app.wgt"  # Safe name: the CLI chokes on spaces.
            source.parent.mkdir()
            shutil.copyfile(package, source)
            out = Path(folder) / "out"
            out.mkdir()
            output = self._tizen(["package", "-t", "wgt", "-s", sign, "-o", str(out), "--", str(source)])
            built = sorted(out.glob("*.wgt"))
            if output[0] or len(built) != 1 or not appkit_signed(built[0]):
                code, message, nxt = explain(output[1], "signing-failed", "The package could not be signed",
                                             "Check the profile with tv setup, then try again")
                return self._finish(record, {"op": "install", "ok": False, "code": code, "message": message,
                                             "next": nxt.replace(" TV", " " + alias), "output": output[1][-400:],
                                             "app": app})
            safe = out / "app.wgt"  # The CLI names the output after <name>, which may hold spaces.
            built[0].replace(safe)
            failed, text = self._tizen(["install", "-n", safe.name, "-t", device, "--", str(out)], timeout=180)
        if failed or not re.search(r"successfully installed|install completed", text, re.I):
            code, message, nxt = explain(text, "install-failed", "The TV did not install the app",
                                         "Check the output below, then try again")
            return self._finish(record, {"op": "install", "ok": False, "code": code, "message": message,
                                         "next": nxt.replace("TV APP_ID", f"{alias} {app}").replace(
                                             "tv cert add TV", "tv cert add " + alias),
                                         "output": text[-400:], "app": app})
        # "Installed" only when the TV's own app list shows this exact ID afterwards.
        listed = self._installed_apps(record["serial"])
        self._remember_apps(record, listed)
        if listed is None:
            return self._finish(record, {"op": "install", "ok": False, "code": "install-unconfirmed",
                                         "message": "The TV reported an install, but its app list could not be read "
                                                    "to confirm " + app,
                                         "next": f"tv apps {alias}", "app": app})
        if app not in listed:
            return self._finish(record, {"op": "install", "ok": False, "code": "not-listed",
                                         "message": "The TV reported an install, but " + app + " is not in its app list",
                                         "next": f"tv apps {alias}", "app": app})
        with self.lock:
            record.setdefault("installed_here", set()).add(app)
            (record.get("store_apps") or set()).discard(app)
            self.setup_apps[alias] = app
        return self._finish(record, {"op": "install", "ok": True, "code": "installed",
                                     "message": "Installed " + app + " (confirmed by the TV's app list)",
                                     "next": f"tv open {alias} {app}", "app": app})

    def apps(self, alias, data):
        """Read-only: list the apps installed on a connected TV."""
        exact(data, {})
        with self.lock:
            record = self.record(alias)
            if record["lease"] is None:
                fail("connect", "Connect to this TV first")
            serial = record["serial"]
        listed = self._installed_apps(serial)
        self._remember_apps(record, listed)
        if listed is None:
            fail("apps", "The TV's app list could not be read; try Show apps again")
        return self.state()

    def app_info(self, alias, data):
        """Read-only: is one app (exact ID or exact name) installed, and which version the TV lists.

        installed is "yes", "no" or "unknown". The version is only what the TV's own app list
        shows; None when it shows none (the 2017 and 2019 TVs list IDs only).
        """
        exact(data, {"app"})
        query = data["app"]
        if not isinstance(query, str) or not 0 < len(query.strip()) <= 128 or not query.isprintable():
            fail("invalid", "Name the app by its ID or its name")
        query = query.strip()
        with self.lock:
            record = self.record(alias)
            if record["lease"] is None:
                fail("connect", "Connect to this TV first")
            serial = record["serial"]
        listing = self._app_listing(serial)
        self._remember_apps(record, None if listing is None else {r["id"] for r in listing})
        found = {"op": "app-info", "query": query, "installed": "unknown", "app": None, "title": None,
                 "version": None, "reason": None}
        if listing is None:
            return dict(found, reason="unreadable")
        match = [r for r in listing if r["id"] == query]
        if not match:
            match = [r for r in listing if (r.get("title") or "").casefold() == query.casefold()]
            if len(match) > 1:
                return dict(found, reason="ambiguous")
        if match:
            r = match[0]
            return dict(found, installed="yes", app=r["id"], title=r.get("title"), version=r.get("version"))
        if APP_ID_TOKEN.fullmatch(query) or all(r.get("title") for r in listing):
            return dict(found, installed="no")
        return dict(found, reason="names-not-listed")

    def _remember_apps(self, record, listed):
        with self.lock:
            if record["lease"] is not None:
                record["apps"] = None if listed is None else set(listed)
                record["apps_unreadable"] = listed is None

    def launch(self, alias, data):
        record = self._change(alias, data, {"app"})
        app = self._app(data["app"])
        failed, text = self._tizen(["run", "-p", app, "-t", self._device_name(record["serial"])])
        if failed or not re.search(r"successfully launched", text, re.I):
            return self._finish(record, {"op": "launch", "ok": False, "code": "launch-failed",
                                         "message": "The TV did not start " + app + " (is it installed?)",
                                         "output": text[-400:], "next": f"tv install {alias} APP.wgt", "app": app})
        return self._finish(record, {"op": "launch", "ok": True, "code": "launched",
                                     "message": "Started " + app, "next": None, "app": app})

    def remove(self, alias, data):
        """Uninstall, then re-list: on Tizen 5.0 `tizen uninstall` can exit 0 and change nothing."""
        record = self._change(alias, data, {"app"})
        app = self._app(data["app"])
        failed, text = self._tizen(["uninstall", "-p", app, "-t", self._device_name(record["serial"])])
        listed = self._installed_apps(record["serial"])
        self._remember_apps(record, listed)
        result = {"op": "remove", "app": app, "next": None}
        if listed is None:
            result.update(ok=False, code="remove-unconfirmed",
                          message="Sent the uninstall, but the TV's app list could not be read to confirm it",
                          next=DELETE_ON_TV)
        elif app in listed:
            result.update(ok=False, code="still-installed",
                          message=app + " is still installed: the TV ignored the uninstall command "
                                        "(observed on Tizen 5.0)", next=DELETE_ON_TV)
        elif failed or re.search(r"not exist", text, re.I):
            result.update(ok=False, code="not-installed", message=app + " is not installed on this TV")
        else:
            result.update(ok=True, code="removed", message="Removed " + app + " (confirmed by the TV's app list)")
            with self.lock:
                (record.get("installed_here") or set()).discard(app)
        return self._finish(record, result)

    def _installed_apps(self, serial):
        """App IDs the TV lists, or None when the list cannot be read. Never inferred from exit codes."""
        listing = self._app_listing(serial)
        return None if listing is None else {r["id"] for r in listing}

    def _app_listing(self, serial):
        """One record per listed app: id, plus title/version only when the TV prints them."""
        try:
            text = self.runner([self.sdb, "-s", serial, "shell", "0", "vd_applist"], 30)
        except LiveError:
            return None
        # Observed on the 2017 and 2019 TVs: "get app list...", "apps count: N", then N app_tizen_id lines.
        count = re.search(r"apps count:\s*(\d+)", text)
        listed = [line for line in text.splitlines() if "app_tizen_id" in line]
        apps = set(APP_ID_TOKEN.findall(text))
        if not apps or (count and listed and len(listed) < int(count.group(1))):
            return None
        records, current = {}, None
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            key, value = key.strip().lower(), value.strip()
            if not sep:
                continue
            if key == "app_tizen_id":
                current = records.setdefault(value, {"id": value}) if value in apps else None
            elif current is not None and value and key in {"app_title", "title"}:
                current["title"] = value[:128]
            elif current is not None and value and key in {"app_version", "version", "pkg_version"}:
                current["version"] = value[:32]
        return list(records.values()) + [{"id": a} for a in sorted(apps - set(records))]

    def _tizen(self, args, timeout=120):
        """(failed, scrubbed output). The Tizen CLI often exits 0 on failure, so callers read the text."""
        try:
            return False, scrub(self.runner([self.tizen] + args, timeout))
        except LiveError as error:
            return True, scrub(error)

    # ---- screenshot and certificate ------------------------------------------
    def screenshot(self, alias, data, folder=None):
        """Save the app picture as PNG. One file per captured paint layer; the TV's video plane is never in it."""
        if not isinstance(data, dict) or not set(data) <= {"app"}:
            fail("invalid", "Request fields do not match this operation")
        with self.lock:
            record = self.record(alias)
            if record["lease"] is None:
                fail("connect", "Connect to this TV first")
            current = record.get("preview")
        if "app" in data and (not current or current["app"] != self._app(data["app"])):
            self.preview(alias, {"app": data["app"]})
            with self.lock:
                current = record.get("preview")
        if not current or current.get("source") is None:
            fail("screenshot", "Open an app's screen first; a screenshot saves that picture")
        source = current["source"]
        try:
            source.operate("capture", {})
        except LiveError as error:
            if error.code != "busy":
                raise
        with source.lock:
            frames = [f["raw"] for f in source.frames]
        if not frames:
            fail("screenshot", "No picture was captured")
        target = Path(folder).expanduser() if folder else self.pictures
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", self.names.get(alias) or alias).strip("-")[:40] or alias
        stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(self.clock()))
        paths = []
        for index, raw in enumerate(frames):
            suffix = "" if len(frames) == 1 else f"-layer{index + 1}"
            path = target / f"{name}-{stamp}{suffix}.png"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # The picture may show private screens.
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
            paths.append(str(path))
        what = "Saved " + paths[0] if len(paths) == 1 else f"Saved {len(paths)} layers to {target}"
        return self._finish(record, {"op": "screenshot", "ok": True, "code": "saved", "message": what,
                                     "paths": paths, "app": current["app"], "next": None})

    def choose(self):
        return {"path": self.chooser()}

    def cert(self, alias, data):
        """Allow installing on this TV: one Samsung sign-in in the browser, everything else automatic.

        Keeps every TV the active certificate already lists, adds this one, and makes the resulting
        "tv-companion" profile active. Runs in the background; the job's steps show progress."""
        exact(data, {})
        with self.lock:
            record = self.record(alias)
            duid = record.get("duid")
            name = self.short_name(alias) or "this TV"
            running = self.cert_running
            if running and running[1].is_alive():
                if running[0] != alias:
                    fail("busy", f"Already waiting for Samsung sign-in for {self.short_name(running[0]) or 'another TV'}"
                                 "; finish that first")
                # Clicked again while waiting: bring the sign-in page back, keep the same job.
                record["job"] = running[2]
                self.browser(samsung_cert.SIGNIN_URL)
                return dict(running[2])
        if not duid:
            fail("connect", "Connect to this TV first so its Device ID can be read")
        self._coverage = None
        coverage = certificate_coverage(self.data_root, duid)
        if coverage["state"] == "covered":
            return self._finish(record, {"op": "cert", "ok": True, "code": "covered", "next": None,
                                         "message": f"You can install your apps on {name}."})
        if coverage["others"]:
            return self._finish(record, self._other_profile_job(coverage, name))
        duids = (coverage["covers"] if coverage["state"] == "not-covered" else []) + [duid]
        job = {"op": "cert", "ok": True, "code": "waiting", "duid": duid, "url": samsung_cert.SIGNIN_URL,
               "message": f"Allowing your apps on {name}", "steps": [],
               "next": "Sign in to Samsung in the browser window that opened"}
        thread = threading.Thread(target=self._cert_work, args=(record, name, duid, duids, job), daemon=True)
        with self.lock:
            self.cert_running = (alias, thread, job)
            record["job"] = job
        thread.start()
        return dict(job)

    def _cert_work(self, record, name, duid, duids, job):
        steps = {}

        def progress(step, text, done):
            with self.lock:
                steps[step] = {"text": text, "done": done}
                job["steps"] = list(steps.values())
                if step == "signin" and done:
                    job["next"] = "Nothing to do: the certificate is being made"
                record["job"] = dict(job)

        def end(update):
            with self.lock:
                job.update(update, steps=list(steps.values()))
                record["job"] = dict(job)
                self.cert_running = None
                self._coverage = self._profiles = None

        try:
            done = samsung_cert.add_tvs(
                duids, profiles_path=self.data_root / "profile" / "profiles.xml", cert_home=self.cert_home,
                openssl=self.openssl, ca=samsung_cert.find_ca(samsung_cert.sdk_roots(self.sdb, self.tizen)),
                progress=progress, browser=self.browser, signin=self.signin, opener=self.opener, clock=self.clock,
                system=self.cert_system)
        except samsung_cert.CertError as error:
            end({"ok": False, "code": error.code, "message": error.message, "next": error.next, "retry": True})
            return
        except Exception as error:  # A real error, shown as it is; never a silent success.
            end({"ok": False, "code": "cert-failed", "message": scrub(f"{type(error).__name__}: {error}"),
                 "next": "Try again", "retry": True})
            return
        coverage = certificate_coverage(self.data_root, duid)
        if coverage["state"] != "covered":
            end({"ok": False, "code": "unchanged", "retry": True, "backup": done["backup"],
                 "message": f"The certificate was saved, but installs still use one that doesn't list {name}",
                 "next": "Try again"})
            return
        end({"ok": True, "code": "covered", "backup": done["backup"], "url": None,
             "message": f"You can install your apps on {name}.",
             "note": "Apps installed with your old certificate need one reinstall." if done["reinstall"] else None,
             "next": None})

    def cert_result(self, alias):
        """The current certificate job for one TV (None while there is none)."""
        with self.lock:
            return self.record(alias)["job"]

    @staticmethod
    def _other_profile_job(coverage, name):
        other = coverage["others"][0]
        active = coverage["profile"] or "no profile"
        return {"op": "cert", "ok": False, "code": "other-profile", "use_profile": other,
                "others": coverage["others"], "duid": coverage["duid"],
                "message": f"Your profile {other} allows {name}, but installs use {active}, which doesn't.",
                "next": f"Use {other} for installs"}

    def profile(self, alias, data):
        """Make another signing profile the active one (`tizen security-profiles set-active`), then re-read it."""
        exact(data, {"profile"})
        name = data["profile"]
        profiles = signing_profiles(self.data_root) or {"names": []}
        if not isinstance(name, str) or name not in profiles["names"]:
            fail("invalid", "There is no signing profile with that name")
        with self.lock:
            record = self.record(alias)
        if not self.tizen:
            fail("sdk", "The tizen CLI from Tizen Studio is needed to change the active profile")
        failed, output = self._tizen(["security-profiles", "set-active", "-n", name], 60)
        self._coverage = self._profiles = None
        now = (signing_profiles(self.data_root) or {}).get("active")
        if now != name:
            return self._finish(record, {
                "op": "profile", "ok": False, "code": "profile-unchanged", "output": output[-2000:],
                "command": f"tizen security-profiles set-active -n {name}",
                "message": f"The active profile is still {now or 'none'}: the tizen CLI didn't switch to {name}.",
                "next": "See the output, or choose the profile in Tizen Studio's Certificate Manager"})
        duid = record.get("duid")
        covers = certificate_coverage(self.data_root, duid)
        return self._finish(record, {
            "op": "profile", "ok": True, "code": "profile-active", "profile": name,
            "message": f"Installs now use {name}" + (", which allows this TV" if covers["state"] == "covered" else ""),
            "next": None})

    # ---- guided setup (the same steps as `tv setup`) ------------------------------
    def sample(self, alias, data):
        """Install the sample app: packed from examples/hello-tv, then signed and installed like any .wgt."""
        exact(data, {})
        with tempfile.TemporaryDirectory(prefix="tizen-sample-") as folder:
            try:
                package = tv_setup.build_sample(folder)
            except (ValueError, OSError) as error:
                with self.lock:
                    record = self.record(alias)
                return self._finish(record, {"op": "sample", "ok": False, "code": "sample-failed",
                                             "message": f"The sample app could not be built: {error}", "next": None})
            return self.install(alias, {"package": str(package)})

    def setup_report(self):
        now = time.monotonic()
        cached = getattr(self, "_setup_report", None)
        if cached is None or now - cached[0] > 5:
            cached = (now, setup_report(sdb=self.sdb, tizen=self.tizen, data_root=self.data_root,
                                        which=self.which, system=self.cert_system))
            self._setup_report = cached
        return cached[1]

    def wizard(self, data):
        """Where the guided setup stands, in the words `tv setup` prints. Reads only."""
        if not isinstance(data, dict) or not set(data) <= {"tv"}:
            fail("invalid", "Request fields do not match this operation")
        alias = data.get("tv")
        if alias is not None and (not isinstance(alias, str) or not re.fullmatch(r"tv-[0-9a-f]{6}", alias)):
            fail("invalid", "Unknown TV")
        with self.lock:
            job = dict(self.sdk_job) if self.sdk_job else None
            apps = dict(self.setup_apps)
        return tv_setup.evaluate(self.setup_report(), self.state(), alias, tv_setup.lan_ip(self.interfaces),
                                 job, apps, self.cert_system)

    def install_sdk(self, data):
        """Install Tizen Studio's command-line tools in the background; progress appears in wizard()."""
        exact(data, {})
        with self.lock:
            if self.sdk_job and self.sdk_job.get("code") == "working":
                return dict(self.sdk_job)
            job = {"op": "install-sdk", "code": "working", "message": "Starting\u2026", "percent": None, "steps": []}
            self.sdk_job = job

        def progress(text, percent):
            with self.lock:
                job["message"], job["percent"] = text, percent
                if not job["steps"] or job["steps"][-1]["text"] != text:
                    if job["steps"]:
                        job["steps"][-1]["done"] = True
                    job["steps"].append({"text": text, "done": False})

        def work():
            try:
                self.sdk_installer(progress)
                result = {"code": "done", "message": "Tizen Studio's tools are installed"}
            except tv_setup.SetupError as error:
                result = {"code": "failed", "message": str(error), "output": error.output, "command": error.command}
            except Exception as error:  # The real cause, never a generic "failed".
                result = {"code": "failed", "message": f"{type(error).__name__}: {error}",
                          "command": tv_setup.install_command(self.cert_system)}
            with self.lock:
                if result["code"] == "done":
                    for step in job["steps"]:
                        step["done"] = True
                    self.sdb = self.sdb or self.find_tool("sdb")
                    self.tizen = self.tizen or self.find_tool("tizen")
                job.update(result)
                self._setup_report = None

        started = dict(job)
        thread = threading.Thread(target=work, daemon=True)
        self.threads.append(thread)
        thread.start()
        return started

    OPERATIONS = {"connect", "identify", "disconnect", "preview", "apps", "install", "remove",
                  "launch", "screenshot", "cert", "profile", "sample"}

    # Failures of these mean the developer link is broken; any other failure is that action's result only.
    LINK_OPS = {"connect", "identify", "disconnect"}

    def submit(self, operation, alias, data):
        """Start a slow operation in the background; progress and errors appear in state()."""
        if operation == "detect":
            exact(data, {})
            if self.scan_lock.locked():
                fail("busy", "A scan is already running")
            with self.lock:
                self.scan = dict(self.scan, status="scanning", error=None)
            target = self.detect
            args = ()
        else:
            if operation == "rename":
                return self.rename(alias, data)
            if operation not in self.OPERATIONS:
                fail("route", "Unknown operation")
            with self.lock:
                record = self.record(alias)
                if record["pending"]:
                    fail("busy", "This TV is already busy with " + record["pending"])
                record["pending"] = operation
                if operation in self.LINK_OPS:
                    record["error"] = None
                else:
                    record["job"] = None
            target = getattr(self, operation)
            args = (alias, data)

        def work():
            try:
                if operation == "detect":
                    target(*args)
                    # Scan also refreshes the details of TVs this portal is connected to.
                    for held in [a for a, r in self.records.items() if r["lease"] is not None and not r["pending"]]:
                        try:
                            self.identify(held, {})
                        except LiveError:
                            pass
                elif operation == "connect":
                    target(*args)
                    try:
                        self.identify(alias, {})
                    except LiveError:
                        pass  # Recorded on the card; the connection itself stays.
                    try:
                        if self.records[alias]["lease"] is not None:
                            self.apps(alias, {})  # Read-only list; changes nothing on the TV.
                    except Exception:
                        pass  # Shown as "app list unreadable"; Show apps retries.
                else:
                    target(*args)
            except Exception as error:
                if operation != "detect":
                    message = scrub(error) if isinstance(error, LiveError) else "Operation failed unexpectedly"
                    with self.lock:
                        record = self.records.get(alias)
                        if record is None:
                            pass
                        elif operation in self.LINK_OPS:
                            record["error"] = record["error"] or message
                        else:
                            record["job"] = {"op": operation, "ok": False, "code": "failed",
                                             "message": message, "next": None,
                                             "app": data.get("app") if isinstance(data, dict) else None}
            finally:
                if operation != "detect":
                    with self.lock:
                        if alias in self.records:
                            self.records[alias]["pending"] = None
        snapshot = self.state()  # Shows "pending" even when a fast operation finishes first.
        thread = threading.Thread(target=work, daemon=True)
        self.threads.append(thread)
        thread.start()
        return snapshot

    def wait(self, timeout=30):
        for thread in list(self.threads):
            thread.join(timeout)
        self.threads = [t for t in self.threads if t.is_alive()]

    def close(self):
        self.wait(60)
        failed = False
        for record in list(self.records.values()):
            if record["lease"] is not None:
                try:
                    self._release(record)
                except LiveError:
                    failed = True
        if failed:
            fail("device_lock_cleanup_failed", "At least one TV cleanup was refused; inspect manually")
