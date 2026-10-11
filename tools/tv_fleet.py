"""TVs on this network: bounded read-only scan, per-TV leases and user-operated connect.

Addresses, serials and DUIDs stay in this process's memory. Everything returned by
state() uses keyed aliases and redacted details so it can reach the UI safely.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import ipaddress
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time

import flows
from live_tv import Lease, LiveError, exact, fail
import network_tv
from tizen import application_id
from workbench import sdk_tool

PORT = 26101
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


class Fleet:
    def __init__(self, sdb_path="auto", runner=flows.run_tool, interfaces=network_tv.interfaces,
                 sweep=network_tv.sweep, info=_info, key_path=None, lease_root=None, canonical=None,
                 certificate_duids=None, retail=flows.retail, names=None, save_names=None):
        if sdb_path == "auto":
            try:
                sdb_path = sdk_tool("sdb", None)
            except (LiveError, OSError, ValueError):
                sdb_path = None
        self.sdb = sdb_path
        self.runner = runner
        self.interfaces = interfaces
        self.sweep = sweep
        self.info = info
        self.key_path = Path(key_path or Path.home() / ".tizen-tv-builder" / "alias.key")
        self.lease_root = Path(lease_root or default_lease_root())
        self.canonical = Path(canonical or default_canonical())
        self.certificate_duids = certificate_duids
        self.retail = retail
        self.names = dict(names or {})
        self.save_names = save_names
        self.records = {}
        self.scan = {"status": "never", "networks": 0, "found": 0, "sdb": "unknown"}
        self.lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.threads = []
        self.secret = None

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

    def detect(self):
        if not self.scan_lock.acquire(blocking=False):
            fail("busy", "A scan is already running")
        try:
            with self.lock:
                self.scan = dict(self.scan, status="scanning", error=None)
            networks = [a for a in self.interfaces() if network_tv.local_address(a)][:MAX_NETWORKS]
            hosts = set()
            for address in networks:
                hosts.update(h for h in self.sweep(address, port=PORT, timeout=.4) if network_tv.local_address(h))
            devices = self._devices()
            entries = {}
            for host in sorted(hosts, key=ipaddress.IPv4Address):
                entries[f"{host}:{PORT}"] = {"host": host, "reachable": True, "sdb_state": None}
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
            with self.lock:
                seen = set()
                for serial in serials:
                    entry = entries[serial]
                    info = infos.get(serial) or {}
                    uid = info.get("uid")
                    alias = self.alias("uid:" + uid if uid else "serial:" + serial)
                    seen.add(alias)
                    record = self.records.setdefault(alias, {"alias": alias, "lease": None, "owned": False,
                                                             "identity": None, "certificate": "unknown",
                                                             "guidance": None, "job": None, "pending": None,
                                                             "error": None})
                    if record["lease"] is not None and record["serial"] != serial:
                        continue  # Never retarget a TV this portal is holding.
                    record.update(serial=serial, host=entry["host"], reachable=entry["reachable"],
                                  sdb_state=entry["sdb_state"], loopback=bool(entry.get("loopback")),
                                  info={k: info.get(k, "unknown") for k in ("name", "model", "year", "developer_mode")},
                                  missing=False)
                for alias, record in list(self.records.items()):
                    if alias not in seen:
                        if record["lease"] is None:
                            del self.records[alias]
                        else:
                            record["missing"] = True
                self.scan = {"status": "done", "networks": len(networks), "found": len(serials),
                             "sdb": "unavailable" if devices is None else "ok", "finished_at": int(time.time())}
            return self.state()
        except Exception as error:
            with self.lock:
                self.scan = dict(self.scan, status="failed",
                                 error=scrub(error) if isinstance(error, LiveError) else "Scan failed")
            raise
        finally:
            self.scan_lock.release()

    # ---- presentation ---------------------------------------------------
    def lease_path(self, alias):
        return self.lease_root / f"tizen-tv-{alias}.lock"

    def busy_reason(self, record):
        if record["lease"] is not None:
            return None
        if record.get("loopback"):
            return "Used by another tool on this computer"
        if self.lease_path(record["alias"]).exists():
            return "In use by another session"
        if record["sdb_state"] == "device" and self.canonical.exists():
            return "Connected to another session"
        return None

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

    PENDING = {"connect": "Connecting…", "identify": "Checking…", "disconnect": "Disconnecting…",
               "run": "Starting app…"}

    def headline(self, record, status, busy, reason):
        """One plain-language status line per card: (pill label, tone, sentence)."""
        if busy:
            return "In use", "busy", busy
        if record["pending"]:
            label = self.PENDING.get(record["pending"], "Working…")
            return label, "progress", label
        if record.get("missing"):
            return "Not found", "warn", "Not found in the last scan. Check that the TV is on, then scan again"
        if status == "connected":
            identity = record["identity"] or {}
            if not identity:
                return "Connected", "ok", "Connected. Identify it to check install readiness"
            tizen = identity.get("tizen")
            prefix = "Connected · Tizen " + tizen if tizen and tizen != "unknown" else "Connected"
            if record["certificate"] == "match":
                return "Connected", "ok", prefix + " · ready to install"
            return "Needs setup", "warn", "Connected · install blocked until the certificate includes this TV"
        if record["guidance"]:
            return "Needs setup", "warn", record["guidance"]["title"]
        if record["reachable"]:
            return "Ready to connect", "idle", reason or "Developer Mode port is open"
        return "Not reachable", "warn", reason or "Developer Mode port is closed or the TV is off"

    def summary(self, record, number):
        busy = self.busy_reason(record)
        connected = record["lease"] is not None or record["sdb_state"] == "device"
        status = "busy" if busy else "connected" if connected else "not-connected"
        info = record.get("info") or {}
        default = self.default_name(record, number)
        guidance = record["guidance"]
        if guidance and guidance.get("kind") in {"host-ip", "developer-mode"}:
            guidance = dict(guidance, host_ip=self.host_ip(record))
        if status == "not-connected" and not record["pending"]:
            reason = ("Developer Mode port is open; not connected yet" if record["reachable"] else
                      "Developer Mode port is closed or the TV is off")
        else:
            reason = None
        label, tone, headline = self.headline(record, status, busy, reason)
        return {"alias": record["alias"], "name": self.names.get(record["alias"], default),
                "label": label, "tone": tone, "headline": headline,
                "default_name": default, "renamed": record["alias"] in self.names,
                "status": status, "busy_reason": busy, "not_connected_reason": reason,
                "reachable": bool(record["reachable"]), "missing": record.get("missing", False),
                "developer_mode": info.get("developer_mode", "unknown"),
                "model": {"value": info.get("model", "unknown"),
                          "provenance": "observed" if info.get("model", "unknown") != "unknown" else "unknown"},
                "year": info.get("year", "unknown"),
                "owned": bool(record["owned"]), "held": record["lease"] is not None,
                "caution": bool(not busy and record["lease"] is None and self.canonical.exists()),
                "identity": record["identity"], "certificate": record["certificate"],
                "guidance": guidance, "pending": record["pending"], "error": record["error"],
                "job": record["job"],
                "details": {"network": redacted_address(record.get("host")),
                            "developer_port": "open" if record["reachable"] else "closed or unknown",
                            "sdk_link": record["sdb_state"] or "none",
                            "duid": "present (hidden)" if (record["identity"] or {}).get("duid_present") else "not read",
                            "lease": "held by this portal" if record["lease"] is not None else "not held"}}

    def state(self):
        with self.lock:
            ordered = sorted(self.records.values(), key=lambda r: (r.get("host") is None, r["alias"]))
            return {"scan": dict(self.scan), "sdk": "available" if self.sdb else "missing",
                    "tvs": [self.summary(r, n) for n, r in enumerate(ordered, 1)]}

    # ---- user operations --------------------------------------------------
    def _require_sdk(self):
        if not self.sdb:
            fail("sdk", "Tizen Studio's sdb was not found. Install Tizen Studio with the TV extension, then scan again")

    def _devices_ready(self, serial):
        return (self._devices() or {}).get(serial) == "device"

    def connect(self, alias, data):
        exact(data, {"confirm"})
        self._require_sdk()
        with self.lock:
            record = self.record(alias)
            busy = self.busy_reason(record)
            if busy:
                fail("busy", busy + "; it is shown read-only")
            if record["lease"] is not None:
                fail("busy", "This portal is already connected to that TV")
            if self.canonical.exists() and data["confirm"] != alias:
                fail("confirm", f"Another TV session is active. Type {alias} to confirm this is a different TV")
            if not record.get("host"):
                fail("invalid", "This TV has no network address to connect to")
            lease = Lease(self.lease_path(alias))
            lease.acquire()
            record["lease"], record["error"], record["guidance"] = lease, None, None
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
        return self.state()

    def identify(self, alias, data):
        exact(data, {})
        with self.lock:
            record = self.record(alias)
            if record["lease"] is None:
                fail("connect", "Connect to this TV first")
        try:
            public, duid = flows._identity(record["serial"], self.sdb, self.runner)
        except (LiveError, ValueError) as error:
            with self.lock:
                record["error"] = "Could not read the TV identity: " + scrub(error)
            fail("identity", record["error"])
        certificate = self._certificate(duid)
        del duid
        with self.lock:
            record["fingerprint"] = public["fingerprint"]
            record["identity"] = {"duid_present": True, "tizen": public["platform_version"],
                                  "profile": public["profile"], "tier": public["tier"]}
            record["certificate"] = certificate
            record["error"] = None
            record["guidance"] = None if certificate == "match" else {
                "kind": "certificate", "title": "Connected, install blocked: needs certificate update",
                "steps": ["Create or update the Samsung distributor certificate so it includes this TV's DUID.",
                          "Point the portal profile's certificate_duids at the new device-profile file.",
                          "Choose Identify again."]}
        return self.state()

    def _certificate(self, duid):
        if not self.certificate_duids:
            return "unconfigured"
        try:
            with open(self.certificate_duids, "r", encoding="utf-8", errors="replace") as stream:
                text = stream.read(MAX_CERTIFICATE + 1)
        except OSError:
            return "unreadable"
        if len(text) > MAX_CERTIFICATE:
            return "unreadable"
        pattern = rf"(?<![{DUID_EDGE}-]){re.escape(duid)}(?![{DUID_EDGE}-])"
        return "match" if re.search(pattern, text) else "missing"

    def _gate(self, record, data):
        if record["lease"] is None:
            fail("connect", "Connect to this TV first")
        if data["confirm"] != record["alias"]:
            fail("confirm", f"Type {record['alias']} to confirm installing on this TV")
        reasons = {"unknown": "identify the TV first", "unconfigured": "no certificate is configured",
                   "unreadable": "the certificate file could not be read",
                   "missing": "this TV is not in the certificate"}
        if record["certificate"] != "match":
            fail("certificate", "certificate lacks this DUID: " + reasons.get(record["certificate"], "unknown"))

    def run(self, alias, data):
        exact(data, {"confirm", "app", "package"})
        with self.lock:
            record = self.record(alias)
            self._gate(record, data)
            if record["job"] and record["job"]["status"] == "running":
                fail("busy", "A run is already in progress on this TV")
            try:
                app = application_id(data["app"]) if isinstance(data["app"], str) else application_id("")
            except ValueError as error:
                fail("invalid", str(error))
            package = data["package"]
            if not isinstance(package, str) or len(package) > 1024:
                fail("invalid", "Package must be a path or empty")
            record["job"] = {"op": "run", "status": "running", "passed": None, "failed_steps": []}
            lease, serial, target = record["lease"], record["serial"], record["fingerprint"]

        def work():
            try:
                receipt = self.retail(serial, target, app, package or None, lease=lease,
                                      sdb=self.sdb, runner=self.runner)
                job = {"op": "run", "status": "passed" if receipt.get("passed") else "failed",
                       "passed": bool(receipt.get("passed")),
                       "failed_steps": [scrub(s) for s in receipt.get("failed_steps", [])][:20]}
            except (LiveError, ValueError, OSError) as error:
                job = {"op": "run", "status": "error", "passed": False, "failed_steps": [],
                       "message": scrub(error) if isinstance(error, LiveError) else "Run failed"}
            with self.lock:
                record["job"] = job
        thread = threading.Thread(target=work, daemon=True)
        self.threads.append(thread)
        thread.start()
        return self.state()

    def disconnect(self, alias, data):
        exact(data, {})
        with self.lock:
            record = self.record(alias)
            if record["lease"] is None:
                fail("connect", "This portal is not connected to that TV")
            if record["job"] and record["job"]["status"] == "running":
                fail("busy", "Wait for the run to finish before disconnecting")
        self._release(record)
        return self.state()

    def _release(self, record):
        failed = None
        if record["owned"]:
            try:
                self.runner([self.sdb, "disconnect", record["serial"]], 20)
            except LiveError as error:
                failed = error
        with self.lock:
            if record["lease"] is not None:
                record["lease"].release()
            record.update(lease=None, owned=False, sdb_state=None if not failed else record["sdb_state"],
                          identity=None, certificate="unknown", guidance=None, fingerprint=None)
        if failed:
            fail("cleanup", "Disconnect failed: " + scrub(failed))

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
            with self.lock:
                record = self.record(alias)
                if record["pending"]:
                    fail("busy", "This TV is already busy with " + record["pending"])
                record["pending"] = operation
                record["error"] = None
            target = getattr(self, operation)
            args = (alias, data)

        def work():
            try:
                if operation == "connect":
                    target(*args)
                    try:
                        self.identify(alias, {})
                    except LiveError:
                        pass  # Recorded on the card; the connection itself stays.
                else:
                    target(*args)
            except LiveError as error:
                if operation != "detect":
                    with self.lock:
                        record = self.records.get(alias)
                        if record is not None and not record["error"]:
                            record["error"] = scrub(error)
            except Exception:
                if operation != "detect":
                    with self.lock:
                        if alias in self.records:
                            self.records[alias]["error"] = "Operation failed unexpectedly"
            finally:
                if operation != "detect":
                    with self.lock:
                        if alias in self.records:
                            self.records[alias]["pending"] = None
        thread = threading.Thread(target=work, daemon=True)
        self.threads.append(thread)
        thread.start()
        return self.state()

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
