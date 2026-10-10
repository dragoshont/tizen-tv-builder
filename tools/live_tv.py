#!/usr/bin/env python3
"""Private, user-operated loopback TV inspector. No SDB or device discovery."""
import argparse
import base64
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit

from tizen import _DeadlineConnection, _DeadlineSocket

ROOT = Path(__file__).resolve().parents[1]
ASSETS = (Path(__file__).resolve().parents[1] if Path(__file__).parent.name == "backend"
          else ROOT / ".github/extensions/tizen-live")
MAX_MESSAGE = 12 * 1024 * 1024
MAX_PIXELS = 8 * 1024 * 1024
GEOMETRY = "({width:window.innerWidth,height:window.innerHeight,scale:window.devicePixelRatio})"
KEYS = {"up": (38, "ArrowUp"), "down": (40, "ArrowDown"),
        "left": (37, "ArrowLeft"), "right": (39, "ArrowRight"),
        "ok": (13, "Enter"), "back": (10009, "Back")}


class LiveError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise LiveError(code, message)


def exact(data, keys):
    if not isinstance(data, dict) or set(data) != set(keys):
        fail("invalid", "Request fields do not match this operation")


def number(value, low, high):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high


def endpoint(value):
    if not isinstance(value, str) or not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{3,4}", value):
        fail("invalid", "Inspector endpoint must be explicit http://127.0.0.1:PORT")
    port = urlsplit(value).port
    if not 1024 <= port <= 65535:
        fail("invalid", "Inspector port is out of range")
    return port


def metadata(value):
    if not isinstance(value, dict) or set(value) - {"model", "year", "os_version", "firmware"}:
        fail("invalid", "Unknown TV metadata field")
    for name, item in value.items():
        exact(item, {"value", "provenance"})
        if not isinstance(item["provenance"], str) or item["provenance"] not in {"user-provided", "observed", "unknown"}:
            fail("invalid", "Metadata provenance must be user-provided, observed or unknown")
        text = item["value"]
        if not isinstance(text, str) or len(text) > 100 or any(ord(c) < 32 for c in text):
            fail("invalid", "Metadata must be short plain text")
        if name == "year" and text and not re.fullmatch(r"20[0-9]{2}", text):
            fail("invalid", "Year must be a four-digit year")
    return value


def read_profile(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        fail("invalid", "Profile must be an existing absolute private file, not a symlink")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r") as stream:
        stat = os.fstat(stream.fileno())
        if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            fail("invalid", "Profile must be owned by this user with mode 0600")
        if path.resolve().is_relative_to(ROOT) or stat.st_size > 65536:
            fail("invalid", "Profile must be small and outside the repository")
        raw = stream.read(65537)
        if len(raw) > 65536:
            fail("invalid", "Profile exceeds its size limit")
        data = json.loads(raw)
    exact(data, {"sources"})
    sources = data["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 2:
        fail("invalid", "Configure one TV, or a TV and an explicit emulator comparison")
    ids = set()
    kinds = set()
    for source in sources:
        exact(source, {"id", "kind", "endpoint", "target_id", "lease_path", "lease_seconds", "metadata",
                       "safe_activation_ids"})
        if not isinstance(source["id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", source["id"]):
            fail("invalid", "Source needs a stable non-secret alias")
        if source["id"] in ids or not isinstance(source["kind"], str) or source["kind"] not in {"tv", "emulator"} or source["kind"] in kinds:
            fail("invalid", "Source aliases and kinds must be unique")
        ids.add(source["id"])
        kinds.add(source["kind"])
        endpoint(source["endpoint"])
        if not isinstance(source["target_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source["target_id"]):
            fail("invalid", "Choose an explicit inspector page target ID")
        if not isinstance(source["lease_path"], str):
            fail("invalid", "Lease path must be a string")
        lease = Path(source["lease_path"])
        if not lease.is_absolute() or lease.is_symlink() or lease.resolve().is_relative_to(ROOT):
            fail("invalid", "Lease path must be an absolute private canonical lane outside the repository")
        if source["kind"] == "tv" and str(lease) != "/tmp/plx-samsung-device.lock":
            fail("invalid", "Native TV requires the canonical /tmp/plx-samsung-device.lock lane")
        if not number(source["lease_seconds"], 10, 120):
            fail("invalid", "Bounded run duration must be 10 to 120 seconds; this is not lease TTL")
        metadata(source["metadata"])
        safe_ids = source["safe_activation_ids"]
        if not isinstance(safe_ids, list) or len(safe_ids) > 32 or any(
                not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", value) for value in safe_ids):
            fail("invalid", "Safe activation IDs must be an audited list of non-write controls")
    return path, data


class Lease:
    def __init__(self, path):
        self.path = path
        self.identity = None
        self.token = None

    def acquire(self):
        path = Path(self.path)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            fail("device_lock_held", "Samsung lane is already owned. Disconnect its current worker first")
        stat = path.lstat()
        self.identity = (stat.st_dev, stat.st_ino)
        self.token = str(uuid.uuid4())
        try:
            fd = os.open(path / "owner.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump({"pid": os.getpid(), "token": self.token}, stream)
        except BaseException:
            # A failed owner write is not a valid lease; remove only our created file.
            stat = path.lstat()
            if (stat.st_dev, stat.st_ino) != self.identity:
                fail("device_lock_cleanup_failed", "Lease ownership changed during acquisition; delete nothing")
            if "fd" in locals():
                (path / "owner.json").unlink()
            try:
                path.rmdir()
            except OSError:
                fail("device_lock_cleanup_failed", "Lease acquisition cleanup failed; inspect manually")
            self.identity = None
            self.token = None
            raise

    def release(self):
        if self.identity is not None:
            path = Path(self.path)
            try:
                stat = path.lstat()
                owner = path / "owner.json"
                if owner.is_symlink() or (stat.st_dev, stat.st_ino) != self.identity:
                    fail("device_lock_cleanup_failed", "Lease ownership changed; cleanup refused")
                if json.loads(owner.read_text()).get("token") != self.token:
                    fail("device_lock_cleanup_failed", "Lease token changed; cleanup refused")
                owner.unlink()
                path.rmdir()
            except (OSError, ValueError):
                fail("device_lock_cleanup_failed", "Lease cleanup failed; inspect ownership manually, do not break the lane")
            self.identity = None
            self.token = None


class CDP:
    """Small RFC6455 client: fixed loopback endpoint, no extensions/compression."""
    def __init__(self, port, target):
        self.sock = None
        self.next_id = 0
        self.layers = None
        deadline = time.monotonic() + 4
        connection = _DeadlineConnection("127.0.0.1", port, deadline)
        response = None
        try:
            connection.request("GET", "/json/list")
            response = connection.getresponse()
            if response.status != 200:
                fail("discovery", "Inspector target discovery refused; redirects are forbidden")
            raw = bytearray()
            while True:
                chunk = response.read1(65536)
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > 1024 * 1024:
                    fail("limit", "Inspector discovery exceeds one MiB")
            pages = json.loads(raw)
            if not isinstance(pages, list):
                fail("discovery", "Inspector target list is malformed")
            matches = [p for p in pages if isinstance(p, dict) and p.get("id") == target and p.get("type") == "page"]
            if len(matches) != 1:
                fail("target", "The explicitly selected page target is missing or ambiguous")
            address = matches[0].get("webSocketDebuggerUrl")
            parsed = urlsplit(address) if isinstance(address, str) else None
            if (parsed is None or parsed.scheme != "ws" or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.path != f"/devtools/page/{target}"):
                fail("target", "Debugger URL must name the explicitly selected page without credentials")
            # Inspector discovery may advertise its device address. Never connect to it:
            # use only the authorized loopback port and validated target path below.
        finally:
            if response:
                response.close()
            connection.close()
        sock = socket.create_connection(("127.0.0.1", port), timeout=4)
        self.sock = sock
        try:
            self.deadline = time.monotonic() + 4
            key = base64.b64encode(secrets.token_bytes(16)).decode()
            request = (f"GET /devtools/page/{target} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                       f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                       "Sec-WebSocket-Version: 13\r\n\r\n")
            sock.sendall(request.encode())
            headers = bytearray()
            while not headers.endswith(b"\r\n\r\n"):
                headers.extend(self.read(1))
                if len(headers) > 16384:
                    fail("limit", "WebSocket handshake is too large")
            lines = headers.decode("ascii").split("\r\n")
            values = {}
            for line in lines[1:]:
                if line:
                    name, value = line.split(":", 1)
                    if name.lower() in values:
                        fail("protocol", "Duplicate handshake header")
                    values[name.lower()] = value.strip()
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            if (not re.fullmatch(r"HTTP/1\.[01] 101(?: .*)?", lines[0])
                    or values.get("sec-websocket-accept") != accept
                    or values.get("upgrade", "").lower() != "websocket"
                    or "upgrade" not in [v.strip() for v in values.get("connection", "").lower().split(",")]
                    or "sec-websocket-extensions" in values):
                fail("protocol", "WebSocket handshake rejected or attempted compression")
        except BaseException:
            self.close()
            raise

    def read(self, size):
        data = bytearray()
        while len(data) < size:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                fail("timeout", "Inspector RPC total deadline exceeded")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                fail("disconnected", "Inspector disconnected")
            data.extend(chunk)
        return bytes(data)

    def send(self, payload, opcode=1):
        mask = secrets.token_bytes(4)
        length = len(payload)
        head = bytes([0x80 | opcode])
        head += bytes([0x80 | length]) if length < 126 else (
            b"\xfe" + struct.pack("!H", length) if length <= 65535 else b"\xff" + struct.pack("!Q", length))
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            fail("timeout", "Inspector RPC deadline exceeded before send")
        self.sock.settimeout(remaining)
        self.sock.sendall(head + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload)))

    def message(self):
        data = bytearray()
        started = False
        while True:
            first, second = self.read(2)
            final, opcode = bool(first & 0x80), first & 15
            if first & 0x70 or second & 0x80:
                fail("protocol", "Compressed/reserved or masked server frame is forbidden")
            length = second & 127
            if length == 126:
                length = struct.unpack("!H", self.read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.read(8))[0]
            if length + len(data) > MAX_MESSAGE:
                fail("limit", "Inspector message exceeds bounded limit")
            if opcode >= 8 and (not final or length > 125):
                fail("protocol", "Malformed WebSocket control frame")
            payload = self.read(length)
            if opcode == 8:
                fail("disconnected", "Inspector closed its WebSocket")
            if opcode == 9:
                self.send(payload, 10)
                continue
            if opcode == 10:
                continue
            if opcode == 1 and not started:
                started = True
            elif opcode != 0 or not started:
                fail("protocol", "Only UTF-8 text messages are supported")
            data.extend(payload)
            if final:
                value = json.loads(data.decode("utf-8"))
                if not isinstance(value, dict):
                    fail("protocol", "Inspector message must be an object")
                return value

    def receive(self):
        value = self.message()
        if value.get("method") == "LayerTree.layerTreeDidChange":
            params = value.get("params")
            layers = params.get("layers") if isinstance(params, dict) else None
            if isinstance(layers, list):
                self.layers = layers
        return value

    def rpc(self, method, params=None, timeout=3):
        self.next_id += 1
        call_id = self.next_id
        self.deadline = time.monotonic() + timeout
        self.send(json.dumps({"id": call_id, "method": method, "params": params or {}}).encode())
        while True:
            value = self.receive()
            if value.get("id") != call_id:
                continue
            if "error" in value:
                code = value["error"].get("code") if isinstance(value["error"], dict) else None
                fail("unsupported" if code == -32601 else "rpc",
                     method + (" is unsupported" if code == -32601 else " was rejected by this target"))
            result = value.get("result")
            if not isinstance(result, dict):
                fail("protocol", "Inspector result is malformed")
            return result

    def layer_list(self):
        self.deadline = time.monotonic() + 3
        while self.layers is None:
            self.receive()
        return self.layers

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None


def png(data):
    if not isinstance(data, str) or not data.startswith("data:image/png;base64,") or len(data) > MAX_MESSAGE:
        fail("capture", "Replay did not return a bounded native PNG")
    raw = base64.b64decode(data.split(",", 1)[1], validate=True)
    if (len(raw) > MAX_PIXELS or len(raw) < 33 or raw[:8] != b"\x89PNG\r\n\x1a\n"
            or raw[12:16] != b"IHDR"):
        fail("capture", "Replay PNG header is invalid or exceeds eight MiB")
    width, height = struct.unpack("!II", raw[16:24])
    if not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > 16_777_216:
        fail("capture", "Replay PNG dimensions exceed limits")
    return raw, width, height


class Source:
    def __init__(self, config, factory=CDP):
        self.config = config
        self.factory = factory
        self.lock = threading.Lock()
        self.lease = Lease(config["lease_path"])
        self.client = None
        self.expires = 0
        self.frames = []
        self.geometry = None
        self.captured = 0
        self.revision = None
        self.input_allowed = False
        self.pixels_allowed = False
        self.status = "disconnected"
        self.error = None
        self.timer = None
        self.cleanup = "not needed"
        self.stale = True
        self.command_ms = None
        self.capture_ms = None
        self.streaming = False
        self.interval = 10
        self.stop_stream = threading.Event()

    def summary(self):
        return {"id": self.config["id"], "kind": self.config["kind"],
                "metadata": self.config["metadata"], "status": self.status,
                "error": self.error, "expires_in": max(0, int(self.expires - time.monotonic())),
                "input_allowed": self.input_allowed, "pixels_allowed": self.pixels_allowed,
                "geometry": self.geometry, "revision": self.revision,
                "captured_at": self.captured, "cleanup": self.cleanup,
                "capture_age_seconds": max(0, round(time.time() - self.captured)) if self.captured else None,
                "stale": self.stale, "command_ms": self.command_ms,
                "capture_ms": self.capture_ms, "streaming": self.streaming,
                "interval": self.interval, "activation_allowed": bool(self.config["safe_activation_ids"]),
                "run_limit_seconds": self.config["lease_seconds"],
                "input_fidelity": "CDP synthesized; physical remote parity unverified",
                "capabilities": {"capture": "LayerTree protocol probe required",
                                 "input": "guarded CDP probe required", "recording": False},
                "frames": [{"width": f["width"], "height": f["height"], "clickable": f["clickable"]}
                           for f in self.frames],
                "provenance": ("native TV renderer paint-layer replay" if self.config["kind"] == "tv"
                               else "explicit emulator renderer paint-layer replay"),
                "limitations": "Discrete paint layers; not full panel, video plane, HDR/audio or a recording"}

    def discard(self, erase=False):
        self.stale = True
        if not erase:
            return
        self.frames = []
        self.revision = None
        self.geometry = None

    def disconnect(self):
        self.streaming = False
        self.stop_stream.set()
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.client:
            self.client.close()
            self.client = None
        self.expires = 0
        self.input_allowed = False
        self.pixels_allowed = False
        self.discard()
        self.status = "disconnected"
        try:
            self.lease.release()
        except LiveError:
            self.cleanup = "device lane cleanup unconfirmed; ownership check refused deletion"
            self.status = "error"
            raise

    def expire(self, expected=None):
        with self.lock:
            if expected is not None and expected != self.expires:
                return
            try:
                self.disconnect()
                self.status = "expired"
                self.error = "Bounded run ended; lease released. Reauthorize to reconnect (no lease TTL)"
            except LiveError as error:
                self.error = str(error)

    def guard(self):
        if not self.client or time.monotonic() >= self.expires:
            self.disconnect()
            fail("disconnected", "Source disconnected or bounded run ended")

    def operate(self, operation, data, stream_event=None):
        if operation == "stream" and isinstance(data, dict) and data.get("enabled") is False:
            exact(data, {"enabled", "interval"})
            if not number(data["interval"], 10, 60):
                fail("invalid", "Stream interval must be 10 to 60 seconds")
            if not self.client or time.monotonic() >= self.expires:
                fail("disconnected", "Source disconnected or bounded run ended")
            # Pause signals scheduling, not CDP: it must work during an owned capture.
            self.stop_stream.set()
            self.streaming = False
            return self.summary()
        if not self.lock.acquire(blocking=False):
            fail("busy", "A command is already in flight; do not retry it")
        try:
            if stream_event is not None and stream_event.is_set():
                return self.summary()
            started = time.monotonic()
            self.status = "in-flight"
            self.error = None
            if operation == "connect":
                if "run_seconds" in data:
                    exact(data, {"authorized", "pixels", "input", "run_seconds"})
                    duration = data["run_seconds"]
                else:
                    exact(data, {"authorized", "pixels", "input"})
                    duration = self.config["lease_seconds"]
                if not number(duration, 10, self.config["lease_seconds"]):
                    fail("invalid", "Run duration must be 10 seconds up to the profile cap")
                if data["authorized"] is not True or type(data["pixels"]) is not bool or type(data["input"]) is not bool:
                    fail("grant", "Explicit target authorization and separate pixel/input choices are required")
                self.disconnect()
                self.lease.acquire()
                try:
                    self.client = self.factory(endpoint(self.config["endpoint"]), self.config["target_id"])
                except BaseException:
                    self.lease.release()
                    raise
                self.input_allowed = data["input"]
                self.pixels_allowed = data["pixels"]
                self.expires = time.monotonic() + duration
                self.timer = threading.Timer(duration, self.expire, args=(self.expires,))
                self.timer.daemon = True
                self.timer.start()
                self.status = "connected"
            elif operation == "disconnect":
                exact(data, {})
                self.disconnect()
            elif operation == "capture":
                exact(data, {})
                self.guard()
                if not self.pixels_allowed:
                    fail("grant", "Private pixel display was not authorized")
                self.capture()
                self.status = "connected"
            elif operation == "stream":
                exact(data, {"enabled", "interval"})
                self.guard()
                if type(data["enabled"]) is not bool or not number(data["interval"], 10, 60):
                    fail("invalid", "Stream interval must be 10 to 60 seconds")
                if not self.pixels_allowed:
                    fail("grant", "Private pixels must be authorized before streaming")
                if self.streaming and data["enabled"]:
                    fail("busy", "Pause the existing stream before starting another")
                self.interval = data["interval"]
                self.streaming = data["enabled"]
                if self.streaming:
                    self.stop_stream = threading.Event()
                    threading.Thread(target=self.stream_loop, args=(self.stop_stream,), daemon=True).start()
                else:
                    self.stop_stream.set()
                self.status = "connected"
            elif operation in {"key", "click"}:
                self.guard()
                if not self.input_allowed:
                    fail("grant", "User-controlled input was not authorized")
                if operation == "key":
                    exact(data, {"key"})
                    if not isinstance(data["key"], str) or data["key"] not in KEYS:
                        fail("invalid", "Only D-pad, OK and Back keys are permitted")
                    code, key = KEYS[data["key"]]
                    if data["key"] == "ok":
                        self.activation()
                    self.discard()
                    try:
                        self.client.rpc("Input.dispatchKeyEvent", {
                            "type": "keyDown", "key": key, "windowsVirtualKeyCode": code,
                            "nativeVirtualKeyCode": code})
                    finally:
                        self.client.rpc("Input.dispatchKeyEvent", {
                            "type": "keyUp", "key": key, "windowsVirtualKeyCode": code,
                            "nativeVirtualKeyCode": code})
                else:
                    self.click(data)
                self.status = "connected"
            else:
                fail("route", "Unknown operation")
            if operation == "capture":
                self.capture_ms = round((time.monotonic() - started) * 1000)
            else:
                self.command_ms = round((time.monotonic() - started) * 1000)
            return self.summary()
        except (LiveError, OSError, ValueError, http.client.HTTPException) as error:
            code = error.code if isinstance(error, LiveError) else "transport"
            message = str(error) if isinstance(error, LiveError) else "Inspector transport or response was invalid"
            self.error = message
            self.discard()
            if code not in {"invalid", "grant", "guard", "busy", "device_lock_held"}:
                self.disconnect()
            self.status = "connected" if self.client else "error"
            raise LiveError(code, message) from None
        finally:
            self.lock.release()

    def stream_loop(self, stop):
        # One bounded capture at a time, no queue and no fake/interpolated frames.
        while not stop.wait(self.interval):
            try:
                self.operate("capture", {}, stream_event=stop)
            except LiveError as error:
                if error.code == "busy":
                    continue
                self.streaming = False
                return

    def activation(self, x=None, y=None):
        ids = self.config["safe_activation_ids"]
        if not ids:
            fail("guard", "OK/click require audited safe activation IDs; household writes are not authorized")
        selected = ("document.activeElement" if x is None else
                    f"document.elementFromPoint({json.dumps(x)},{json.dumps(y)})")
        expression = f"(function(){{var e={selected};return !!e&&{json.dumps(ids)}.indexOf(e.id)!==-1;}})()"
        result = self.client.rpc("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        if result.get("result", {}).get("value") is not True or "exceptionDetails" in result:
            fail("guard", "This control is not on the audited non-write activation allowlist")

    def capture(self):
        self.discard()
        client = self.client
        started = time.monotonic()
        deadline = min(started + 15, self.expires)
        def rpc(method, params=None):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail("timeout", "Native capture total budget exceeded")
            return client.rpc(method, params, timeout=min(3, remaining))
        geometry = rpc("Runtime.evaluate", {"expression": GEOMETRY, "returnByValue": True})
        if "exceptionDetails" in geometry:
            fail("capture", "Viewport evaluation failed")
        self.geometry = geometry.get("result", {}).get("value")
        if not isinstance(self.geometry, dict) or any(
                not number(self.geometry.get(k), 1, limit) for k, limit in (("width", 8192), ("height", 8192), ("scale", 8))):
            fail("capture", "Viewport geometry is missing or invalid")
        enabled = False
        frames = []
        total = 0
        released = True
        try:
            client.layers = None
            rpc("LayerTree.enable")
            enabled = True
            layers = client.layer_list()
            paint = [layer for layer in layers if isinstance(layer, dict) and layer.get("drawsContent") is True]
            if not 1 <= len(paint) <= 8:
                fail("limit", "Expected one to eight paint layers; incomplete over-limit captures are refused")
            for layer in paint:
                if time.monotonic() - started > 15 or time.monotonic() >= self.expires:
                    fail("timeout", "Native capture total budget exceeded")
                layer_id = layer.get("layerId")
                if not isinstance(layer_id, str) or len(layer_id) > 100:
                    fail("capture", "Paint layer identity is invalid")
                snapshot = rpc("LayerTree.makeSnapshot", {"layerId": layer_id}).get("snapshotId")
                if not isinstance(snapshot, str) or not snapshot or len(snapshot) > 200:
                    fail("capture", "Snapshot identity is invalid")
                try:
                    replay = rpc("LayerTree.replaySnapshot", {"snapshotId": snapshot, "scale": 1})
                    raw, width, height = png(replay.get("dataURL"))
                    total += len(raw)
                    if total > MAX_PIXELS:
                        fail("limit", "Capture exceeds eight MiB across all layers")
                    clickable = (width == self.geometry["width"] and height == self.geometry["height"]
                                 and layer.get("offsetX") == 0 and layer.get("offsetY") == 0
                                 and not layer.get("transform"))
                    frames.append({"raw": raw, "width": width, "height": height, "clickable": clickable})
                finally:
                    try:
                        client.rpc("LayerTree.releaseSnapshot", {"snapshotId": snapshot})
                    except (LiveError, OSError, ValueError):
                        released = False
                        raise
        finally:
            if enabled:
                try:
                    client.rpc("LayerTree.disable")
                    self.cleanup = ("snapshots released; LayerTree disabled" if released else
                                    "snapshot release unconfirmed; LayerTree disabled")
                except (LiveError, OSError, ValueError):
                    self.cleanup = "LayerTree cleanup unconfirmed; disconnected, no automatic retry"
                    raise
        self.frames = frames
        self.revision = hashlib.sha256(b"".join(f["raw"] for f in frames)).hexdigest()[:24]
        self.captured = time.time()
        self.stale = False

    def click(self, data):
        exact(data, {"frame", "revision", "x", "y"})
        index = data["frame"]
        if type(index) is not int or not 0 <= index < len(self.frames):
            fail("stale", "Selected capture is missing; refresh before clicking")
        frame = self.frames[index]
        if (data["revision"] != self.revision or self.stale or time.time() - self.captured > 10
                or not frame["clickable"]):
            fail("stale", "Click needs a fresh viewport-aligned untransformed capture")
        if not number(data["x"], 0, frame["width"] - 1) or not number(data["y"], 0, frame["height"] - 1):
            fail("invalid", "Click is outside viewport bounds")
        params = {"x": data["x"], "y": data["y"], "button": "left", "clickCount": 1}
        self.activation(data["x"], data["y"])
        self.discard()
        try:
            self.client.rpc("Input.dispatchMouseEvent", {"type": "mousePressed", **params})
        finally:
            self.client.rpc("Input.dispatchMouseEvent", {"type": "mouseReleased", **params})


class App:
    def __init__(self, path, config, factory=CDP):
        self.path = path
        self.config = config
        self.sources = {s["id"]: Source(s, factory) for s in config["sources"]}
        self.token = secrets.token_urlsafe(32)
        self.persist_lock = threading.Lock()

    def state(self):
        return {"sources": [source.summary() for source in self.sources.values()]}

    def save_metadata(self, alias, values):
        metadata(values)
        with self.persist_lock:
            source = self.sources[alias]
            if not source.lock.acquire(blocking=False):
                fail("busy", "Wait for the active command before editing this profile")
            try:
                # Revalidate path permissions and merge only metadata, never connection/grants.
                _, current = read_profile(self.path)
                entry = next(s for s in current["sources"] if s["id"] == alias)
                entry["metadata"] = values
                temporary = self.path.with_name(self.path.name + "." + secrets.token_hex(8))
                try:
                    with temporary.open("x") as stream:
                        os.chmod(temporary, 0o600)
                        stream.write(json.dumps(current, indent=2) + "\n")
                    temporary.replace(self.path)
                finally:
                    if temporary.exists():
                        temporary.unlink()
                source.config["metadata"] = values
            finally:
                source.lock.release()
        return self.state()

    def close(self):
        failed = False
        for source in self.sources.values():
            with source.lock:
                try:
                    source.disconnect()
                except LiveError:
                    failed = True
        if failed:
            fail("device_lock_cleanup_failed", "At least one device lane cleanup was refused; inspect ownership manually")


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_error(self, code, message=None, explain=None):
            self.reply(code, {"error": "http", "message": "HTTP method or request was refused"})

        def reply(self, code, body, content_type="application/json"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            if isinstance(self.connection, _DeadlineSocket):
                self.connection.deadline = time.monotonic() + 4
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "img-src 'self' blob:; connect-src 'self'; base-uri 'none'; form-action 'none'")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def check(self, private=False, public_static=False):
            self.connection.settimeout(3)
            host = f"127.0.0.1:{self.server.server_port}"
            for key in ("Host", "Origin", "Authorization", "Content-Length", "Transfer-Encoding", "Content-Type"):
                if len(self.headers.get_all(key, [])) > 1:
                    fail("invalid", "Duplicate security/framing headers are forbidden")
            if self.headers.get("Host") != host:
                fail("origin", "Host does not match this loopback service")
            origin = self.headers.get("Origin")
            if not public_static and origin and origin != "http://" + host:
                fail("origin", "Cross-origin requests are forbidden")
            if not public_static and self.headers.get("Sec-Fetch-Site") == "cross-site":
                fail("origin", "Cross-site requests are forbidden")
            if private and not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + app.token):
                fail("auth", "Private canvas authentication is required")

        def do_GET(self):
            try:
                static = {"/": ("index.html", "text/html; charset=utf-8"),
                          "/ui.js": ("ui.js", "text/javascript"),
                          "/controls.mjs": ("controls.mjs", "text/javascript"),
                          "/ui.css": ("ui.css", "text/css")}
                if self.path in static:
                    # The host embeds this public shell cross-site. It contains no
                    # profile, token or pixels; authenticated API routes stay strict.
                    self.check(public_static=True)
                    name, mime = static[self.path]
                    self.reply(200, (ASSETS / name).read_bytes(), mime)
                    return
                self.check(private=True)
                if self.path == "/api/state":
                    self.reply(200, app.state())
                    return
                match = re.fullmatch(r"/api/frame/([a-z][a-z0-9-]{0,31})/([0-7])/([a-f0-9]{24})", self.path)
                if not match or match[1] not in app.sources:
                    fail("route", "Unknown route")
                source = app.sources[match[1]]
                with source.lock:
                    if source.revision != match[3] or int(match[2]) >= len(source.frames):
                        fail("stale", "Capture no longer exists; refresh")
                    self.reply(200, source.frames[int(match[2])]["raw"], "image/png")
            except (LiveError, OSError) as error:
                self.report(error)

        def do_POST(self):
            try:
                self.check(private=True)
                if self.headers.get("Content-Type") != "application/json" or self.headers.get("Transfer-Encoding"):
                    fail("invalid", "Only bounded JSON requests are accepted")
                length = self.headers.get("Content-Length", "")
                if not length.isdigit() or not 1 <= int(length) <= 4096:
                    fail("limit", "Request body must be one to 4096 bytes")
                # Absolute deadline includes trickled bodies.
                end = time.monotonic() + 3
                raw = bytearray()
                while len(raw) < int(length):
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        fail("timeout", "Request body deadline exceeded")
                    self.connection.settimeout(remaining)
                    chunk = self.rfile.read1(int(length) - len(raw))
                    if not chunk:
                        fail("invalid", "Request body was truncated")
                    raw.extend(chunk)
                data = json.loads(raw)
                match = re.fullmatch(r"/api/(connect|disconnect|capture|key|click|metadata|stream)/([a-z][a-z0-9-]{0,31})", self.path)
                if not match or match[2] not in app.sources:
                    fail("route", "Unknown route")
                if match[1] == "metadata":
                    result = app.save_metadata(match[2], data)
                else:
                    result = app.sources[match[2]].operate(match[1], data)
                self.reply(200, result)
            except (LiveError, OSError, ValueError, TypeError) as error:
                self.report(error)

        def report(self, error):
            code = error.code if isinstance(error, LiveError) else "invalid"
            message = str(error) if isinstance(error, LiveError) else "Request or transport was invalid"
            status = 403 if code in {"auth", "origin", "grant"} else 409 if code in {"busy", "stale", "device_lock_held"} else 400
            try:
                self.reply(status, {"error": code, "message": message})
            except OSError:
                pass

        def do_OPTIONS(self):
            self.reply(405, {"error": "method", "message": "Preflight and cross-origin access are forbidden"})

    return Handler


def serve(profile, owner_stdin=False):
    path, config = read_profile(profile)
    app = App(path, config)
    server = BoundedServer(("127.0.0.1", 0), handler(app))
    server.daemon_threads = True
    server.timeout = 0.2
    server.request_queue_size = 8
    # The URL contains private authentication in its fragment, never query/log/HTTP referrer.
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/#{app.token}"}), flush=True)
    if owner_stdin:
        def monitor_owner():
            sys.stdin.buffer.read()  # EOF when the SDK provider exits, even if it is killed.
            server.shutdown()
        threading.Thread(target=monitor_owner, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        try:
            app.close()
        finally:
            server.server_close()


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(*args, **kwargs)

    def get_request(self):
        connected, address = super().get_request()
        return _DeadlineSocket(connected, time.monotonic() + 3), address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--profile", type=Path, required=True)
    cli.add_argument("--owner-stdin", action="store_true", help="Disconnect and stop on owning provider pipe EOF")
    args = cli.parse_args()
    import signal
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        serve(args.profile, args.owner_stdin)
    except KeyboardInterrupt:
        return 0
    except (LiveError, OSError, ValueError) as error:
        print("ERROR: " + (str(error) if isinstance(error, LiveError) else "Invalid profile or local service failure"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
