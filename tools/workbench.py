#!/usr/bin/env python3
"""TV Workbench: private browser onboarding, native paint preview and remote."""
import argparse
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import secrets
import threading
import time
import webbrowser

from live_tv import App, BoundedServer, CDP, KEYS, Lease, LiveError, Source, endpoint, exact, fail, handler
from tizen import _DeadlineConnection, application_id, device_serial
from network_tv import confirm as confirm_tv, discover as discover_tvs
from recording import Encoder, Recorder, RecordingError

ASSETS = Path(__file__).resolve().parent / "workbench"
REMOTE_NAMES = {
    "power": "Power", "source": "Source", "home": "Home", "menu": "Menu",
    "settings": "Tools", "info": "Info", "guide": "Guide", "exit": "Exit",
    "volume-up": "VolumeUp", "volume-down": "VolumeDown", "mute": "VolumeMute",
    "channel-up": "ChannelUp", "channel-down": "ChannelDown",
    "play": "MediaPlay", "pause": "MediaPause", "play-pause": "MediaPlayPause",
    "stop": "MediaStop", "rewind": "MediaRewind", "forward": "MediaFastForward",
    "red": "ColorF0Red", "green": "ColorF1Green", "yellow": "ColorF2Yellow", "blue": "ColorF3Blue",
    **{f"digit-{n}": str(n) for n in range(10)},
}
SUPPORTED_KEYS = ("(function(){if(typeof tizen==='undefined'||!tizen.tvinputdevice||"
                  "!tizen.tvinputdevice.getSupportedKeys)return null;"
                  "return tizen.tvinputdevice.getSupportedKeys().map(function(k){"
                  "return {name:k.name,code:k.code};});})()")


class RemoteSource(Source):
    def __init__(self, config, factory=CDP):
        super().__init__(config, factory)
        self.keymap = dict(KEYS)
        self.extended_input = False
        self.key_discovery = "Connect to discover app keys"

    def summary(self):
        result = super().summary()
        result.update({"remote_keys": list(self.keymap), "extended_input": self.extended_input,
                       "key_discovery": self.key_discovery})
        return result

    def operate(self, operation, data, stream_event=None):
        owner = getattr(self, "_recording_owner", None)
        if owner is not None and threading.current_thread() is not owner.thread:
            if operation in {"capture", "connect"} or (operation == "stream" and isinstance(data, dict) and data.get("enabled")):
                fail("busy", "Recording owns capture. Stop recording before changing the feed")
            if operation == "disconnect":
                try:
                    owner.stop()
                except RecordingError as error:
                    raise LiveError(error.code, str(error)) from None
        if operation == "key" and isinstance(data, dict) and data.get("key") not in KEYS:
            if not self.extended_input:
                fail("grant", "Enable app actions before using extra remote keys")
            if data.get("key") not in self.keymap:
                fail("unsupported", "This key is not exposed by the app's Tizen input API")
        extended = False
        if operation == "connect" and isinstance(data, dict):
            data = dict(data)
            extended = data.pop("extended_input", False)
            if type(extended) is not bool:
                fail("invalid", "App-action permission must be a boolean")
            self.keymap = dict(KEYS)
        result = super().operate(operation, data, stream_event)
        if operation == "connect":
            with self.lock:
                self.extended_input = extended
                try:
                    reply = self.client.rpc("Runtime.evaluate", {"expression": SUPPORTED_KEYS, "returnByValue": True})
                    keys = reply.get("result", {}).get("value")
                    if keys is None or "exceptionDetails" in reply:
                        self.key_discovery = "Extra app keys unavailable"
                    elif not isinstance(keys, list) or len(keys) > 256 or any(
                            not isinstance(k, dict) or not isinstance(k.get("name"), str)
                            or len(k["name"]) > 64 or type(k.get("code")) is not int
                            or not 1 <= k["code"] <= 65535 for k in keys):
                        fail("invalid", "Tizen key discovery was malformed")
                    else:
                        by_name = {key["name"]: key["code"] for key in keys}
                        self.keymap.update({key: (by_name[name], name) for key, name in REMOTE_NAMES.items() if name in by_name})
                        self.key_discovery = "App keys discovered"
                except (LiveError, OSError):
                    self.disconnect()
                    self.status = "error"
                    self.key_discovery = "Key discovery failed"
                    raise
            result = self.summary()
        return result


def lane():
    return Path(tempfile.gettempdir() if os.name == "nt" else "/tmp") / "plx-samsung-device.lock"


def sdk_tool(name, explicit=None):
    suffix = ".exe" if os.name == "nt" and name == "sdb" else ".bat" if os.name == "nt" else ""
    if explicit:
        candidate = Path(explicit)
        if not candidate.is_absolute() or not candidate.is_file():
            fail("sdk", "Tool override must be an absolute existing executable")
        return str(candidate)
    found = shutil.which(name + suffix)
    if found:
        return found
    roots = [Path(os.environ["TIZEN_SDK_ROOT"])] if os.environ.get("TIZEN_SDK_ROOT") else []
    roots += [Path.home() / "tizen-studio"]
    if os.name == "nt":
        roots.append(Path("C:/tizen-studio"))
    relative = Path("tools") / ("sdb" + suffix) if name == "sdb" else Path("tools/ide/bin") / ("tizen" + suffix)
    return next((str(root / relative) for root in roots if (root / relative).is_file()), None)


def run_sdb(tool, args):
    if not tool:
        fail("sdk", "SDB not found. Install Tizen Studio and Samsung TV Extension with owner permission")
    try:
        child = subprocess.Popen([tool, *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError:
        fail("sdk", "SDB could not start. Check executable and host permissions")
    output = bytearray()
    oversized = threading.Event()
    def collect():
        while True:
            chunk = child.stdout.read1(8192)
            if not chunk:
                return
            if len(output) + len(chunk) > 256 * 1024:
                oversized.set()
                child.kill()
                return
            output.extend(chunk)
    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    try:
        child.wait(timeout=20)
        reader.join(timeout=1)
        if reader.is_alive():
            fail("sdk", "The TV tools stopped responding. Try again; if it repeats, restart the TV")
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=3)
        fail("sdk_timeout", "The TV took too long to answer. Check it is on, then try again")
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
        child.stdout.close()
        reader.join(timeout=1)
    if oversized.is_set():
        fail("limit", "The TV sent an unexpected amount of output. Try again")
    if child.returncode:
        fail("sdk", "The TV refused that. Check Developer Mode is on and this computer's IP is set as Host PC IP")
    return output.decode("utf-8", errors="replace")


def _seen(data):
    """Describe a target list by known types and counts only; ids and URLs are not echoed."""
    if not data:
        return "the target list is empty"
    known = {"page", "background_page", "service_worker", "worker", "iframe", "other"}
    counts = {}
    for item in data:
        kind = item.get("type") if isinstance(item, dict) else None
        kind = kind if kind in known else "unknown"
        if kind == "page":
            kind = "page with an unusable id"
        counts[kind] = counts.get(kind, 0) + 1
    return "seen " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))


def pages(port):
    connection = _DeadlineConnection("127.0.0.1", port, time.monotonic() + 4)
    try:
        connection.request("GET", "/json/list")
        response = connection.getresponse()
        if response.status != 200:
            fail("discovery", "Inspector discovery refused; no redirects followed")
        raw = response.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            fail("limit", "Inspector discovery exceeded its limit")
        data = json.loads(raw)
        if not isinstance(data, list) or len(data) > 100:
            fail("discovery", "Inspector target list is invalid")
        result = [p["id"] for p in data if isinstance(p, dict) and p.get("type") == "page"
                  and isinstance(p.get("id"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", p["id"])]
        if not result:
            fail("discovery", "No debug page target yet: " + _seen(data))
        return result
    finally:
        connection.close()


class Workbench(App):
    def __init__(self, sdb=None, runner=run_sdb, discover=pages, factory=CDP):
        super().__init__(None, {"sources": []}, factory)
        # The Workbench may be opened from any browser tab, so it keeps its URL-fragment token.
        self.token = secrets.token_urlsafe(32)
        self.assets = ASSETS
        self.frame_ancestors = "'none'"
        self.sdb = sdk_tool("sdb", sdb)
        self.runner, self.discover, self.factory = runner, discover, factory
        self.setup_lock = threading.Lock()
        self.forward = None
        self.target_pages = []
        self.inspector_port = None
        self.devices = []
        self.apps = []
        self.detected_serial = ""
        self.setup_message = ""
        self.network_devices = []
        self.encoder = None
        self.recorder = None
        self.record_lock = threading.Lock()

    def setup_status(self):
        return {"sdb": bool(self.sdb), "tizen": bool(sdk_tool("tizen")),
                "platform": sys.platform, "page_count": len(self.target_pages),
                "configured": bool(self.sources), "forward_owned": self.forward is not None,
                "devices": self.devices, "apps": self.apps, "serial": self.detected_serial,
                "message": self.setup_message, "network_devices": self.network_devices}

    def sdk(self, serial, args):
        return self.runner(self.sdb, ["-s", serial, *args])

    def setup(self, data):
        if not isinstance(data, dict):
            fail("invalid", "Setup requires an operation object")
        operation = data.get("operation")
        if not self.setup_lock.acquire(blocking=False):
            fail("busy", "Setup is already in flight; do not queue another operation")
        lease = Lease(lane())
        acquired = False
        try:
            if any(source.client for source in self.sources.values()):
                fail("busy", "Disconnect the preview before changing its setup")
            if operation in {"find-network", "confirm-tv"}:
                exact(data, {"operation", "authorized"} if operation == "find-network" else {"operation", "authorized", "ip"})
                try:
                    if operation == "find-network":
                        result = discover_tvs()
                        self.network_devices = result["devices"]
                        count = len(self.network_devices)
                        self.setup_message = (f"Found {count} TV{'s' if count != 1 else ''}. Pick one above." if count else
                                              "No TVs found. Turn the TV on, use the same Wi-Fi, or enter its IP in Add a TV.")
                    else:
                        self.network_devices = [confirm_tv(data["ip"])]
                        self.setup_message = "TV found. Pick it above."
                except ValueError as error:
                    fail("network", str(error) if operation == "confirm-tv" and "private" in str(error)
                         else "That address did not answer like a Samsung TV. Check the IP in the TV's network settings")
                except (OSError, http.client.HTTPException):
                    fail("network", "Can't reach the local network. Check Wi-Fi, and on macOS allow Local Network "
                                    "for your terminal in System Settings > Privacy & Security"
                         if operation == "find-network" else
                         "No answer from that IP. Make sure the TV is on and on the same Wi-Fi")
                return self.setup_status()
            if operation == "discover":
                exact(data, {"operation", "serial", "authorized"})
                if data["authorized"] is not True:
                    fail("grant", "Authorize device discovery first")
                if not isinstance(data["serial"], str):
                    fail("invalid", "Choose a detected TV or enter its serial")
                explicit = device_serial(data["serial"]) if data["serial"] else ""
                if self.forward:
                    fail("busy", "Release the previous forward in Advanced before changing TV setup")
                lease.acquire()
                acquired = True
                self.close_sources()
                self.target_pages = []
                self.inspector_port = None
                raw = self.runner(self.sdb, ["devices"])
                self.devices = []
                for line in raw.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[1] == "device":
                        try:
                            self.devices.append(device_serial(parts[0]))
                        except ValueError:
                            continue  # Non-TV/emulator serials are outside this explicit IPv4 workflow.
                self.apps = []
                self.detected_serial = ""
                if not self.devices:
                    fail("sdk", "No TV connected. Click Find TVs, or enter the TV's IP in Add a TV")
                selected = explicit or (self.devices[0] if len(self.devices) == 1 else "")
                if not selected:
                    self.setup_message = "Choose a TV, then find its apps"
                    return self.setup_status()
                if selected not in self.devices:
                    fail("sdk", "Selected TV is not connected")
                self.detected_serial = selected
                raw = self.sdk(selected, ["shell", "0", "applist"])
                self.apps = sorted(set(re.findall(r"(?<![A-Za-z0-9_])[A-Za-z0-9]{10}\.[A-Za-z0-9_]+", raw)))
                if len(self.apps) > 256:
                    fail("limit", "Installed app list exceeded its limit")
                self.setup_message = "Choose an app" if self.apps else "No debuggable apps found; enter the app ID in Advanced"
                return self.setup_status()
            if operation == "select":
                exact(data, {"operation", "page", "safe_ids"})
                index = data["page"]
                ids = data["safe_ids"]
                if type(index) is not int or not 0 <= index < len(self.target_pages):
                    fail("invalid", "Choose a page from the latest discovery")
                if not isinstance(ids, list) or len(ids) > 32 or any(
                        not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", value) for value in ids):
                    fail("invalid", "Enter at most 32 audited non-write DOM IDs")
                config = {"id": "development-tv", "kind": "tv",
                          "endpoint": f"http://127.0.0.1:{self.inspector_port}",
                          "target_id": self.target_pages[index], "lease_path": str(lane()),
                          "lease_seconds": 120, "metadata": {}, "safe_activation_ids": ids}
                self.close_sources()
                self.sources = {config["id"]: RemoteSource(config, self.factory)}
                return self.setup_status()
            if operation == "attach":
                exact(data, {"operation", "port", "authorized"})
                if data["authorized"] is not True or type(data["port"]) is not int:
                    fail("grant", "Authorize the existing forward and choose its explicit port")
                port = endpoint(f"http://127.0.0.1:{data['port']}")
                if self.forward:
                    fail("busy", "Release this workbench's owned forward before attaching another")
                lease.acquire()
                acquired = True
                targets = self.discover(port)
                self.close_sources()
                self.inspector_port, self.target_pages = port, targets
                return self.setup_status()
            if operation == "release":
                exact(data, {"operation", "authorized"})
                if data["authorized"] is not True:
                    fail("grant", "Authorize cleanup of this workbench's owned forward")
                lease.acquire()
                acquired = True
                self.remove_forward()
                self.close_sources()
                self.target_pages = []
                self.inspector_port = None
                return self.setup_status()
            if operation not in {"connect-tv", "debug"}:
                fail("invalid", "Unknown setup operation")
            exact(data, {"operation", "serial", "authorized"} if operation == "connect-tv" else
                  {"operation", "serial", "authorized", "app", "legacy"})
            if data["authorized"] is not True:
                fail("grant", "Explicit authorization for this TV operation is required")
            serial = device_serial(data["serial"])
            app_id = application_id(data["app"]) if operation == "debug" else None
            if operation == "debug" and type(data["legacy"]) is not bool:
                fail("invalid", "Legacy debug option must be a boolean")
            lease.acquire()
            acquired = True
            if operation == "connect-tv":
                if self.forward:
                    fail("busy", "Release the previous forward in Advanced before changing TV setup")
                self.close_sources()
                self.target_pages = []
                self.inspector_port = None
                self.runner(self.sdb, ["connect", serial])
                devices = self.runner(self.sdb, ["devices"])
                if not any(line.split()[:2] == [serial, "device"] for line in devices.splitlines()):
                    fail("sdk", "Selected TV is not ready. Check Developer Mode, pairing and host network permission")
                return self.setup_status()
            if self.forward:
                fail("busy", "Release the previous owned forward before relaunching")
            self.close_sources()
            self.target_pages = []
            self.sdk(serial, ["shell", "0", "kill", app_id.split(".")[0]])
            output = self.sdk(serial, ["shell", "0", "debug", app_id] + (["0"] if data["legacy"] else []))
            matches = re.findall(r"(?:port\s*[:=]?\s*)([0-9]{4,5})\b", output, re.IGNORECASE)
            if len(set(matches)) != 1 or not 1024 <= int(matches[0]) <= 65535:
                fail("debug", "The app didn't start in debug mode. Close it on the TV and click Connect again")
            remote_port = int(matches[0])
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                local_port = reservation.getsockname()[1]
            existing = self.sdk(serial, ["forward", "--list"])
            if f"tcp:{local_port}" in existing:
                fail("busy", "Chosen local port already has an SDB forward; nothing was replaced")
            self.sdk(serial, ["forward", f"tcp:{local_port}", f"tcp:{remote_port}"])
            self.forward = (serial, local_port, remote_port)
            mappings = self.sdk(serial, ["forward", "--list"])
            if not any(line.split() == [serial, f"tcp:{local_port}", f"tcp:{remote_port}"] for line in mappings.splitlines()):
                fail("cleanup", "Couldn't open the preview connection. Click Connect again")
            self.inspector_port = local_port
            self.target_pages = self.discover(local_port)
            return self.setup_status()
        except (ValueError, TypeError):
            raise LiveError("invalid", "Setup fields or inspector discovery were invalid") from None
        except (OSError, http.client.HTTPException):
            raise LiveError("discovery", "Couldn't reach the app on the TV. Make sure it's open, then click Connect again") from None
        finally:
            try:
                if acquired:
                    lease.release()
            finally:
                self.setup_lock.release()

    def remove_forward(self):
        if self.forward:
            serial, local, remote = self.forward
            mappings = self.sdk(serial, ["forward", "--list"])
            expected = [line.split() for line in mappings.splitlines()]
            if not any(parts == [serial, f"tcp:{local}", f"tcp:{remote}"] for parts in expected):
                fail("cleanup", "The preview connection was closed elsewhere. Click Connect again")
            self.sdk(serial, ["forward", "--remove", f"tcp:{local}"])
            self.forward = None

    def close_sources(self):
        if self.recorder is not None:
            try:
                self.recorder.stop()
            except RecordingError as error:
                raise LiveError(error.code, str(error)) from None
        super().close()
        self.sources = {}

    def record_status(self):
        if self.encoder is None:
            with self.record_lock:
                if self.encoder is None:
                    self.encoder = Encoder()
        parent = Path.home() / "Downloads"
        if not parent.is_dir():
            parent = Path.home()
        return {"recording": self.recorder.status() if self.recorder else None,
                "encoder": self.encoder.summary(), "default_parent": str(parent)}

    def record(self, data):
        if not self.record_lock.acquire(blocking=False):
            fail("busy", "A recording operation is already in flight")
        try:
            if not isinstance(data, dict):
                fail("invalid", "Recording requires an operation object")
            operation = data.get("operation")
            if operation == "start":
                exact(data, {"operation", "authorized_save", "output_parent", "fps", "width", "height", "duration", "layer"})
                if data["authorized_save"] is not True:
                    fail("grant", "Authorize saving private app-layer captures first")
                if len(self.sources) != 1:
                    fail("disconnected", "Connect one app preview before recording")
                if self.encoder is None:
                    self.encoder = Encoder()
                if not isinstance(data["output_parent"], str):
                    fail("invalid", "Output parent must be a private absolute folder")
                parent = Path(data["output_parent"])
                if not parent.is_absolute() or not parent.is_dir():
                    fail("invalid", "Choose an existing absolute output folder")
                name = f"app-layer-{data['layer']}-{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}"
                recorder = Recorder(next(iter(self.sources.values())), parent / name,
                                    fps=data["fps"], size=(data["width"], data["height"]),
                                    duration=data["duration"], layer_index=data["layer"], encoder=self.encoder)
                if self.recorder is not None:
                    self.recorder.close()
                self.recorder = recorder
                return self.recorder.start()
            if operation == "stop":
                exact(data, {"operation", "format"})
                if self.recorder is None:
                    fail("invalid", "No recording to stop")
                if data["format"] not in {"png", "gif", "mp4", "webm", "webp"}:
                    fail("invalid", "Choose PNG, GIF, MP4, WebM or WebP")
                receipt = self.recorder.stop()
                if receipt["state"] == "failed":
                    return receipt
                return self.recorder.export(data["format"], still=data["format"] == "webp")
            if operation == "export":
                exact(data, {"operation", "format", "still"})
                if self.recorder is None or type(data["still"]) is not bool or data["format"] not in {"png", "gif", "mp4", "webm", "webp"}:
                    fail("invalid", "Choose a recorded sequence and valid export format")
                return self.recorder.export(data["format"], still=data["still"])
            fail("invalid", "Unknown recording operation")
        except RecordingError as error:
            raise LiveError(error.code, str(error)) from None
        finally:
            self.record_lock.release()

    def save_metadata(self, alias, values):
        from live_tv import metadata
        metadata(values)
        source = self.sources[alias]
        if not source.lock.acquire(blocking=False):
            fail("busy", "Wait for the active command before editing metadata")
        try:
            source.config["metadata"] = values
        finally:
            source.lock.release()
        return self.state()

    def close(self):
        self.close_sources()
        if self.recorder is not None:
            try:
                self.recorder.close()
            except RecordingError as error:
                raise LiveError(error.code, str(error)) from None
        if self.forward:
            lease = Lease(lane())
            lease.acquire()
            try:
                self.remove_forward()
            finally:
                lease.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdb", help="Absolute installed sdb / sdb.exe path")
    parser.add_argument("--no-open", action="store_true", help="Print private URL without opening the browser")
    args = parser.parse_args()
    app = Workbench(args.sdb)
    server = BoundedServer(("127.0.0.1", 0), handler(app))
    url = f"http://127.0.0.1:{server.server_port}/#{app.token}"
    print("Private workbench URL (do not share): " + url, flush=True)
    if not args.no_open and not webbrowser.open(url):
        print("Browser did not open. Open the private URL locally.", file=sys.stderr)
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        with app.setup_lock:
            app.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LiveError, OSError) as error:
        print("ERROR: " + (str(error) if isinstance(error, LiveError) else "Local workbench operation failed"), file=sys.stderr)
        raise SystemExit(1)
