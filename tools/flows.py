"""Scripted install -> launch -> ready -> keys -> perf -> capture -> logs -> teardown flows.

Every external effect goes through an injectable runner, inspector factory and clock so the
flows are tested against fakes. Real emulator and TV runs are only claimed when observed.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time

from live_tv import CDP, KEYS, Lease, LiveError, fail
from tizen import application_id, device_serial, redact
from workbench import lane, pages, sdk_tool
import appkit

BUDGETS = Path(__file__).resolve().parents[1] / "docs" / "budgets.json"
PORT = re.compile(r"(?:port\s*[:=]?\s*)([0-9]{4,5})\b", re.IGNORECASE)
EMULATOR_SERIAL = re.compile(r"emulator-\d{4,5}")
READY = "document.readyState"
MARKS = ("(function(){var t=window.performance&&performance.timing,m=window.__tvMarks||{};"
         "return JSON.stringify({firstFrame:m.firstFrame,home:m.home,"
         "domContentLoaded:t&&t.domContentLoadedEventEnd?t.domContentLoadedEventEnd-t.navigationStart:null,"
         "load:t&&t.loadEventEnd?t.loadEventEnd-t.navigationStart:null});}())")


def run_tool(argv, timeout=60, limit=512 * 1024):
    """Cross-platform bounded child process; kills only the child it started."""
    if not argv[0]:
        fail("sdk", "SDK tool not found. Install Tizen Studio with the TV extension")
    try:
        done = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                              env=dict(os.environ, TERM=os.environ.get("TERM", "dumb")))
    except subprocess.TimeoutExpired:
        fail("sdk_timeout", "SDK command timed out; owned child stopped")
    except OSError:
        fail("sdk", "SDK tool could not start")
    output = done.stdout[-limit:].decode("utf-8", errors="replace")
    if done.returncode:
        fail("sdk", "SDK command failed: " + redact(output[-600:]).strip())
    return output


def debug_port(output):
    matches = set(PORT.findall(output))
    if len(matches) != 1 or not 1024 <= int(next(iter(matches))) <= 65535:
        fail("debug", "SDK did not return one debug port; relaunch after kill")
    return int(matches.pop())


def emulator_tool(explicit=None):
    if explicit:
        return explicit if Path(explicit).is_file() else None
    name = "em-cli.bat" if os.name == "nt" else "em-cli"
    roots = [Path(os.environ["TIZEN_SDK_ROOT"])] if os.environ.get("TIZEN_SDK_ROOT") else []
    roots += [Path.home() / "tizen-studio"] + ([Path("C:/tizen-studio")] if os.name == "nt" else [])
    return next((str(r / "tools/emulator/bin" / name) for r in roots if (r / "tools/emulator/bin" / name).is_file()), None)


def tier_for(platform_version):
    try:
        major, minor = (int(x) for x in (str(platform_version).split(".") + ["0"])[:2])
    except ValueError:
        return None
    if (major, minor) <= (3, 0):
        return "legacy"
    return "mid" if (major, minor) <= (5, 0) else "current"


def evaluate(metrics, tier, budgets=None):
    budgets = budgets or json.loads(BUDGETS.read_text())
    if tier not in budgets["tiers"]:
        fail("invalid", "Unknown budget tier")
    limits = budgets["tiers"][tier]["budgets_ms"]
    verdict = {}
    for name, limit in limits.items():
        value = metrics.get(name)
        verdict[name] = {"budget_ms": limit, "measured_ms": value,
                         "result": "unmeasured" if value is None else "pass" if value <= limit else "fail"}
    return {"tier": tier, "metrics": verdict,
            "passed": all(v["result"] == "pass" for v in verdict.values())}


def _median(values):
    values = sorted(v for v in values if isinstance(v, (int, float)))
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


class Session:
    """One app on one serial. Owns at most one forward and remembers only what it created."""

    def __init__(self, serial, app, sdb=None, tizen=None, runner=run_tool, discover=pages,
                 factory=CDP, clock=time.monotonic, sleep=time.sleep, legacy=False, output=None):
        self.serial, self.app = serial, application_id(app)
        self.sdb_path = sdk_tool("sdb", sdb)
        self.tizen_path = sdk_tool("tizen", tizen)
        self.runner, self.discover, self.factory = runner, discover, factory
        self.clock, self.sleep, self.legacy = clock, sleep, legacy
        self.output = Path(output) if output else None
        self.forward = None
        self.client = None
        self.receipt = {"serial_kind": "emulator" if serial.startswith("emulator-") else "network",
                        "app": self.app, "steps": []}

    def step(self, name, result="ok", **detail):
        self.receipt["steps"].append({"step": name, "result": result, **detail})

    def sdb(self, *args, timeout=30):
        return self.runner([self.sdb_path, "-s", self.serial, *args], timeout)

    def install(self, package):
        package = Path(package)
        report = appkit.check_package(package)
        if not report["passed"]:
            fail("package", "Package check failed; run `tizen.py check-package`")
        self.runner([self.tizen_path, "install", "-n", package.name, "-s", self.serial, "--",
                     str(package.resolve().parent)], 180)
        self.step("install", package_sha256=report["sha256"], signed=report["signed"])

    def kill(self):
        try:
            self.sdb("shell", "0", "kill", self.app.split(".")[0])
            self.step("kill")
        except LiveError:
            self.step("kill", "not-running")

    def launch(self):
        """Kill-before-debug, debug launch, owned forward and page readiness. Returns ms."""
        if self.forward:
            self.close()
        self.kill()
        started = self.clock()
        output = self.sdb("shell", "0", "debug", self.app, *(["0"] if self.legacy else []), timeout=60)
        remote = debug_port(output)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            local = reservation.getsockname()[1]
        if f"tcp:{local}" in self.sdb("forward", "--list"):
            fail("busy", "Local port already forwarded; nothing replaced")
        self.sdb("forward", f"tcp:{local}", f"tcp:{remote}")
        self.forward = (local, remote)
        if not any(line.split() == [self.serial, f"tcp:{local}", f"tcp:{remote}"]
                   for line in self.sdb("forward", "--list").splitlines()):
            fail("cleanup", "Created forward could not be confirmed")
        page = self.discover(local)[0]
        self.client = self.factory(local, page)
        deadline = started + 30
        while True:
            state = self.evaluate(READY)
            if state == "complete":
                break
            if self.clock() > deadline:
                fail("ready", "Page did not reach readyState complete within 30 s")
            self.sleep(0.25)
        elapsed = round((self.clock() - started) * 1000)
        self.step("launch", debug_port="observed", ready_ms=elapsed)
        return elapsed

    def evaluate(self, expression):
        result = self.client.rpc("Runtime.evaluate", {"expression": expression, "returnByValue": True}, timeout=5)
        return (result.get("result") or {}).get("value")

    def marks(self, wait=10):
        deadline = self.clock() + wait
        while True:
            try:
                values = json.loads(self.evaluate(MARKS) or "{}")
            except ValueError:
                values = {}
            if values.get("home") is not None or self.clock() > deadline:
                return values
            self.sleep(0.25)

    def keys(self, names):
        for name in names:
            if name not in KEYS:
                fail("invalid", "Unknown key " + str(name)[:20])
            code, key = KEYS[name]
            try:
                self.client.rpc("Input.dispatchKeyEvent", {"type": "keyDown", "key": key,
                                "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code})
            finally:
                self.client.rpc("Input.dispatchKeyEvent", {"type": "keyUp", "key": key,
                                "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code})
        self.step("keys", sent=list(names))

    def capture(self):
        """Page screenshot when the engine answers it; otherwise an explicit unsupported result."""
        try:
            data = base64.b64decode(self.client.rpc("Page.captureScreenshot", {"format": "png"}, timeout=15)["data"],
                                    validate=True)
        except LiveError as error:
            self.step("capture", "unsupported", reason=error.code)
            return None
        except (KeyError, ValueError, TypeError):
            self.step("capture", "fail", reason="malformed")
            return None
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            self.step("capture", "fail", reason="not-png")
            return None
        name = None
        if self.output:
            name = f"app-layer-{int(time.time())}.png"
            (self.output / name).write_bytes(data)
        self.step("capture", file=name, bytes=len(data), scope="renderer page only; no video plane")
        return data

    def logs(self, lines=80):
        try:
            text = self.sdb("dlog", "-d", timeout=20)
        except LiveError as error:
            self.step("logs", "unsupported", reason=error.code)
            return []
        tail = [redact(line)[:300] for line in text.splitlines()[-lines:]]
        if self.output:
            (self.output / "dlog-tail.txt").write_text("\n".join(tail) + "\n", encoding="utf-8")
        self.step("logs", lines=len(tail))
        return tail

    def close(self):
        if self.client is not None:
            try:
                self.client.close()
            finally:
                self.client = None
        if self.forward:
            local, remote = self.forward
            mapped = any(line.split() == [self.serial, f"tcp:{local}", f"tcp:{remote}"]
                         for line in self.sdb("forward", "--list").splitlines())
            if mapped:
                self.sdb("forward", "--remove", f"tcp:{local}")
                self.step("forward-removed")
            else:
                self.step("forward-removed", "absent", note="mapping changed; nothing deleted")
            self.forward = None

    def teardown(self, uninstall=False):
        errors = []
        for action in (self.close, self.kill) + ((self.uninstall,) if uninstall else ()):
            try:
                action()
            except LiveError as error:
                errors.append(error.code)
        if errors:
            self.step("teardown", "partial", errors=errors)
        return errors

    def uninstall(self):
        self.runner([self.tizen_path, "uninstall", "-p", self.app.split(".")[0], "-s", self.serial], 120)
        self.step("uninstall")


def perf(session, runs=1, tier=None):
    if not 1 <= runs <= 5:
        fail("invalid", "Runs must be 1-5")
    samples = []
    for _ in range(runs):
        cold = session.launch()
        marks = session.marks()
        samples.append({"coldStartMs": cold, "firstFrameMs": marks.get("firstFrame"),
                        "firstHomeMs": marks.get("home")})
    metrics = {key: _median([s[key] for s in samples]) for key in ("coldStartMs", "firstFrameMs", "firstHomeMs")}
    result = {"runs": runs, "samples": samples, "median": metrics,
              "method": "cold start = host clock from debug launch to readyState complete (includes inspector attach); "
                        "first frame/home = app marks relative to navigationStart"}
    if tier:
        result["budget"] = evaluate(metrics, tier)
    session.step("perf", **{k: v for k, v in metrics.items()})
    return result


def identify(serial, sdb=None, runner=run_tool):
    """Stable, non-reversible target fingerprint. Fails closed; the DUID itself is never returned."""
    tool = sdk_tool("sdb", sdb)
    capability = runner([tool, "-s", serial, "capability"], 20)
    fields = dict(line.split(":", 1) for line in capability.splitlines() if ":" in line)
    fields = {k.strip(): v.strip() for k, v in fields.items()}
    duid = runner([tool, "-s", serial, "shell", "0", "getduid"], 20).strip().splitlines()
    duid = duid[-1].strip() if duid else ""
    if not re.fullmatch(r"[A-Za-z0-9_+/=-]{8,128}", duid):
        fail("identity", "Target identity unreadable; nothing installed")
    platform = fields.get("platform_version", "")
    return {"fingerprint": "tv-" + hashlib.sha256(duid.encode()).hexdigest()[:16],
            "platform_version": platform if re.fullmatch(r"[0-9.]{1,12}", platform) else None,
            "profile": fields.get("profile_name") if re.fullmatch(r"[a-z]{1,16}", fields.get("profile_name", "")) else None,
            "tier": tier_for(platform)}


def _flow(session, package, keys, runs, tier, capture, logs, uninstall):
    try:
        if package:
            session.install(package)
        result = perf(session, runs, tier)
        if keys:
            session.keys(keys)
        if capture:
            session.capture()
        if logs:
            session.logs()
        return result
    finally:
        session.teardown(uninstall)


def retail(serial, target, app, package=None, keys=(), runs=1, tier=None, capture=True, uninstall=False,
           output=None, lease_path=None, **session_args):
    """Retail TV flow: holds the canonical lane throughout and refuses a different TV."""
    device_serial(serial)
    if not re.fullmatch(r"tv-[0-9a-f]{16}", target or ""):
        fail("invalid", "--target must be the fingerprint printed by `tizen.py identify`")
    if package and not appkit.check_package(package, require_signed=True)["passed"]:
        fail("package", "Retail install needs an author+distributor signed package that passes check-package")
    lease = Lease(lease_path or lane())
    lease.acquire()
    receipt = {"flow": "retail", "target": target}
    try:
        runner = session_args.get("runner", run_tool)
        tool = sdk_tool("sdb", session_args.get("sdb"))
        runner([tool, "connect", serial], 20)
        if not any(line.split()[:2] == [serial, "device"] for line in runner([tool, "devices"], 20).splitlines()):
            fail("sdk", "TV is not ready; check Developer Mode and Host PC IP")
        found = identify(serial, session_args.get("sdb"), runner)
        if found["fingerprint"] != target:
            fail("target", "Connected TV is not the named target; nothing installed")
        receipt["identity"] = {k: found[k] for k in ("platform_version", "profile", "tier")}
        session = Session(serial, app, output=output, **session_args)
        receipt["perf"] = _flow(session, package, keys, runs, tier or found["tier"], capture, False, uninstall)
        receipt.update(session.receipt)
        return receipt
    finally:
        lease.release()
        receipt["lane"] = "released"


def emulator(vm, app, package=None, keys=(), runs=1, tier="current", capture=True, logs=True,
             uninstall=True, output=None, em_cli=None, spawn=subprocess.Popen, ready_timeout=180, **session_args):
    """Emulator flow. Stops the emulator only when this flow launched it."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", vm or ""):
        fail("invalid", "VM name must be letters, digits, dot, dash or underscore")
    runner = session_args.get("runner", run_tool)
    clock = session_args.get("clock", time.monotonic)
    sleep = session_args.get("sleep", time.sleep)
    tool = emulator_tool(em_cli)
    if not tool:
        fail("sdk", "em-cli not found. Install the Tizen TV emulator with Package Manager")
    sdb = sdk_tool("sdb", session_args.get("sdb"))
    if vm not in runner([tool, "list-vm"], 30).split():
        fail("invalid", "VM not found; create it in Emulator Manager")

    def serials():
        return {line.split()[0] for line in runner([sdb, "devices"], 20).splitlines()
                if line.split()[:1] and EMULATOR_SERIAL.fullmatch(line.split()[0]) and line.split()[1:2] == ["device"]}

    before = serials()
    console = tempfile.TemporaryFile()
    process = spawn([tool, "launch", "-n", vm], stdout=console, stderr=subprocess.STDOUT,
                    env=dict(os.environ, TERM=os.environ.get("TERM", "dumb")))
    receipt = {"flow": "emulator", "vm": vm, "owned_emulator": True}
    serial = None
    try:
        deadline = clock() + ready_timeout
        while not serial:
            new = serials() - before
            if not new and process.poll() not in (None, 0):
                console.seek(0)
                said = [line for line in console.read()[-4096:].decode("utf-8", "replace").splitlines() if line.strip()]
                fail("emulator", "Emulator could not start: " + redact(" ".join(said[-3:]))[:300] +
                     ". The TV emulator needs Intel/AMD hardware virtualization (HAXM, KVM or Hyper-V/WHPX); "
                     "it does not run on Apple Silicon")
            if len(new) == 1:
                serial = new.pop()
            elif len(new) > 1:
                fail("target", "Several emulators appeared; stop the others and retry")
            elif clock() > deadline:
                fail("ready", "Emulator did not register with SDB in time")
            else:
                sleep(2)
        receipt["serial"] = serial
        session = Session(serial, app, output=output, **session_args)
        receipt["perf"] = _flow(session, package, keys, runs, tier, capture, logs, uninstall)
        receipt.update(session.receipt)
        return receipt
    finally:
        console.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
            receipt["emulator"] = "stopped (owned launcher)"
        else:
            receipt["emulator"] = "launcher exited; close the VM in Emulator Manager if still running"
