import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
import subprocess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("live_tv", ROOT / "tools/live_tv.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def source_config(root, alias="sample", kind="tv"):
    return {"id": alias, "kind": kind, "endpoint": "http://127.0.0.1:12345",
            "target_id": "test-page", "lease_path": str(root / (alias + ".lock")),
            "lease_seconds": 10, "metadata": {}, "safe_activation_ids": []}


def native_fixture(width=100, height=80):
    # PNG header contract fixture only; not TV evidence or a rendered substitute.
    return b"\x89PNG\r\n\x1a\n" + struct.pack("!I", 13) + b"IHDR" + struct.pack("!II", width, height) + b"\x08\x06\0\0\0" + b"crc!"


class FakeCDP:
    def __init__(self, port, target):
        self.calls = []
        self.layers = None
        self.closed = False
        self.reject = None

    def rpc(self, method, params=None, timeout=3):
        self.calls.append((method, params))
        if method == self.reject:
            raise live.LiveError("unsupported", method + " is unsupported")
        if method == "Runtime.evaluate":
            if params["expression"] == live.GEOMETRY:
                return {"result": {"value": {"width": 100, "height": 80, "scale": 1}}}
            return {"result": {"value": True}}
        if method == "LayerTree.makeSnapshot":
            return {"snapshotId": "snapshot-test"}
        if method == "LayerTree.replaySnapshot":
            return {"dataURL": "data:image/png;base64," + base64.b64encode(native_fixture()).decode()}
        return {}

    def layer_list(self):
        return [{"layerId": "layer", "drawsContent": True, "offsetX": 0, "offsetY": 0}]

    def close(self):
        self.closed = True


class Chrome47Inspector:
    """Raw-socket fake shaped like the Tizen 3.0 Chrome 47 inspector (protocol 1.1): plain HTTP/1.1
    discovery, an uncompressed RFC6455 upgrade, events before replies. Host fixture, not TV evidence."""

    def __init__(self, silent=(), garbage=()):
        self.silent, self.garbage, self.methods = set(silent), set(garbage), []
        self.server = socket.socket(); self.server.bind(("127.0.0.1", 0)); self.server.listen(4)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

    @staticmethod
    def frame(payload):
        n = len(payload)
        head = bytes([0x81, n]) if n < 126 else (bytes([0x81, 126]) + struct.pack("!H", n) if n < 65536
                                                  else bytes([0x81, 127]) + struct.pack("!Q", n))
        return head + payload

    def serve(self, conn):
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            head = data.split(b"\r\n\r\n")[0].decode()
            if head.startswith("GET /json/list"):
                body = json.dumps([{"description": "", "devtoolsFrontendUrl": "/devtools/inspector.html?ws=x",
                                    "id": "chrome47-page", "title": "Hello", "type": "page", "url": "file:///index.html",
                                    "webSocketDebuggerUrl": "ws://192.0.2.10:7011/devtools/page/chrome47-page"}]).encode()
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type:application/json; charset=UTF-8\r\nContent-Length:"
                             + str(len(body)).encode() + b"\r\n\r\n" + body)
                return
            key = [l.split(":", 1)[1].strip() for l in head.split("\r\n") if l.lower().startswith("sec-websocket-key")][0]
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            conn.sendall(("HTTP/1.1 101 WebSocket Protocol Handshake\r\nUpgrade: WebSocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            buffer = b""
            while True:
                while len(buffer) < 2:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buffer += chunk
                n, offset = buffer[1] & 127, 2
                if n == 126:
                    n, offset = struct.unpack("!H", buffer[2:4])[0], 4
                while len(buffer) < offset + 4 + n:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buffer += chunk
                mask, body = buffer[offset:offset + 4], buffer[offset + 4:offset + 4 + n]
                buffer = buffer[offset + 4 + n:]
                call = json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(body)))
                method = call["method"]; self.methods.append(method)
                if method in self.silent:
                    continue  # Like Page.captureScreenshot on this engine: recognized, never answered.
                if method in self.garbage:
                    conn.sendall(self.frame(b"\xff not json")); continue
                result = {}
                if method == "Runtime.evaluate":
                    result = {"result": {"type": "object", "value": {"width": 100, "height": 80, "scale": 1}}, "wasThrown": False}
                elif method == "LayerTree.enable":
                    conn.sendall(self.frame(json.dumps({"method": "LayerTree.layerTreeDidChange", "params": {"layers": [
                        {"layerId": "7", "drawsContent": True, "offsetX": 0, "offsetY": 0, "width": 100, "height": 80},
                        {"layerId": "8", "drawsContent": False}]}}).encode()))
                elif method == "LayerTree.makeSnapshot":
                    conn.sendall(self.frame(b'{"method":"LayerTree.layerPainted","params":{"layerId":"7","clip":{}}}'))
                    result = {"snapshotId": "1"}
                elif method == "LayerTree.replaySnapshot":
                    result = {"dataURL": "data:image/png;base64," + base64.b64encode(native_fixture()).decode()}
                conn.sendall(self.frame(json.dumps({"id": call["id"], "result": result}).encode()))

    def close(self):
        self.server.close()


class LiveTests(unittest.TestCase):
    def test_pause_during_capture_stops_next_capture_without_waiting(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = live.Source(source_config(Path(temporary)), FakeCDP)
            source.operate("connect", {"authorized": True, "pixels": True, "input": False})
            entered, finish = threading.Event(), threading.Event()
            original = source.client.rpc
            def delayed(method, params=None, timeout=3):
                if method == "LayerTree.replaySnapshot":
                    entered.set()
                    if not finish.wait(2):
                        raise AssertionError("Pause waited on in-flight capture")
                return original(method, params, timeout)
            source.client.rpc = delayed
            source.streaming = True
            worker = threading.Thread(target=lambda: source.operate("capture", {}))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                started = time.monotonic()
                source.operate("stream", {"enabled": False, "interval": 10})
                self.assertLess(time.monotonic() - started, .1)
                self.assertTrue(source.stop_stream.is_set())
                self.assertFalse(source.streaming)
                finish.set(); worker.join(2)
                self.assertFalse(worker.is_alive())
                source.stream_loop(source.stop_stream)
                self.assertEqual(sum(m == "LayerTree.replaySnapshot" for m, _ in source.client.calls), 1)
            finally:
                finish.set(); worker.join(2); source.disconnect()

    def test_stream_three_real_host_cycles_pause_and_unchanged_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = source_config(Path(temporary)); config["lease_seconds"] = 40
            source = live.Source(config, FakeCDP)
            try:
                source.operate("connect", {"authorized": True, "pixels": True, "input": False})
                source.operate("stream", {"enabled": True, "interval": 10})
                end = time.monotonic() + 32
                while time.monotonic() < end:
                    if sum(m == "LayerTree.replaySnapshot" for m, _ in source.client.calls) >= 3:
                        break
                    time.sleep(.05)
                self.assertEqual(sum(m == "LayerTree.replaySnapshot" for m, _ in source.client.calls), 3)
                revision = source.revision
                source.operate("stream", {"enabled": False, "interval": 10})
                time.sleep(.1)
                self.assertFalse(source.streaming)
                self.assertEqual(source.revision, revision)
                self.assertEqual(sum(m == "LayerTree.replaySnapshot" for m, _ in source.client.calls), 3)
            finally:
                source.disconnect()

    @unittest.skipUnless(os.name == "posix", "Canvas private profiles use POSIX ownership; Windows uses workbench")
    def test_owner_pipe_eof_stops_service_without_touching_device(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); profile = root / "profile.json"
            config = {"sources": [source_config(root, kind="emulator")]}
            profile.write_text(json.dumps(config)); profile.chmod(0o600)
            child = subprocess.Popen([sys.executable, str(ROOT / "tools/live_tv.py"),
                                      "--profile", str(profile), "--owner-stdin"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                result = json.loads(child.stdout.readline())
                self.assertTrue(result["url"].startswith("http://127.0.0.1:"))
                child.stdin.close()
                self.assertEqual(child.wait(timeout=5), 0)
                self.assertFalse(Path(config["sources"][0]["lease_path"]).exists())
            finally:
                if child.poll() is None:
                    child.kill(); child.wait()
                child.stdout.close(); child.stderr.close()

    def chrome47_source(self, inspector):
        config = source_config(Path(tempfile.gettempdir()), alias="chrome47")
        config.update(endpoint=f"http://127.0.0.1:{inspector.port}", target_id="chrome47-page", user_activation=True)
        return live.Source(config, live.CDP)

    def test_chrome47_style_inspector_captures_paint_layers_end_to_end(self):
        inspector = Chrome47Inspector()
        try:
            source = self.chrome47_source(inspector)
            source.operate("connect", {"authorized": True, "pixels": True, "input": True, "run_seconds": 10})
            summary = source.operate("capture", {})
            self.assertEqual([(f["width"], f["height"]) for f in summary["frames"]], [(100, 80)])
            self.assertEqual(inspector.methods, ["Runtime.evaluate", "LayerTree.enable", "LayerTree.makeSnapshot",
                                                 "LayerTree.replaySnapshot", "LayerTree.releaseSnapshot", "LayerTree.disable"])
            source.disconnect()
        finally:
            inspector.close()

    def test_inspector_failures_name_the_step_and_the_real_cause(self):
        for kwargs, expected in (({"silent": {"LayerTree.makeSnapshot"}}, "LayerTree.makeSnapshot reply"),
                                 ({"garbage": {"Runtime.evaluate"}}, "not valid JSON/UTF-8 .*Runtime.evaluate")):
            inspector = Chrome47Inspector(**kwargs)
            try:
                source = self.chrome47_source(inspector)
                source.operate("connect", {"authorized": True, "pixels": True, "input": True, "run_seconds": 10})
                with self.assertRaisesRegex(live.LiveError, expected) as caught:
                    source.operate("capture", {})
                self.assertNotIn("transport or response was invalid", str(caught.exception))
                self.assertEqual(source.error, str(caught.exception))
            finally:
                inspector.close()
        message = live.inspector_failure(ConnectionResetError(54, "Connection reset by peer"),
                                         type("C", (), {"phase": "WebSocket handshake"})())
        self.assertEqual(message, "Inspector failed during WebSocket handshake: ConnectionResetError: "
                                  "[Errno 54] Connection reset by peer")

    def test_real_discovery_handshake_uses_only_loopback_without_compression(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        requests = []
        class Inspector(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                requests.append((self.path, dict(self.headers)))
                if self.path == "/json/list":
                    body = json.dumps([{"id": "test-page", "type": "page",
                                        "webSocketDebuggerUrl": "ws://192.0.2.10:45678/devtools/page/test-page"}]).encode()
                    self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                else:
                    self.send_response(101)
                    self.send_header("Upgrade", "websocket")
                    self.send_header("Connection", "Upgrade")
                    key = self.headers["Sec-WebSocket-Key"]
                    self.send_header("Sec-WebSocket-Accept", base64.b64encode(hashlib.sha1(
                        (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode())
                    self.end_headers()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Inspector)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            client = live.CDP(server.server_port, "test-page"); client.close()
            self.assertEqual([p for p, _ in requests], ["/json/list", "/devtools/page/test-page"])
            self.assertNotIn("Sec-WebSocket-Extensions", requests[1][1])
            with self.assertRaisesRegex(live.LiveError, "missing"):
                live.CDP(server.server_port, "missing")
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_parenthesized_page_id_uses_the_literal_advertised_socket_path(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        page = "(0123456789abcdef0123456789ABCDEF)"
        advertised = {"path": "/devtools/page/" + page}
        requests = []
        class Inspector(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                requests.append(self.path)
                if self.path == "/json/list":
                    body = json.dumps([{"id": page, "type": "page",
                                        "webSocketDebuggerUrl": "ws://192.0.2.10:45678" + advertised["path"]}]).encode()
                    self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                else:
                    self.send_response(101)
                    self.send_header("Upgrade", "websocket")
                    self.send_header("Connection", "Upgrade")
                    key = self.headers["Sec-WebSocket-Key"]
                    self.send_header("Sec-WebSocket-Accept", base64.b64encode(hashlib.sha1(
                        (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode())
                    self.end_headers()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Inspector)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            live.CDP(server.server_port, page).close()
            self.assertEqual(requests, ["/json/list", "/devtools/page/" + page])
            advertised["path"] = "/devtools/page/%280123456789abcdef0123456789ABCDEF%29"
            with self.assertRaisesRegex(live.LiveError, "Debugger URL"):
                live.CDP(server.server_port, page)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_target_id_validator_is_exact_on_every_platform(self):
        for value in ("(0123456789abcdef0123456789ABCDEF)", "test-page", "A1_b2"):
            self.assertTrue(live.target_id_ok(value), value)
        for value in ("(0123456789abcdef0123456789ABCDEF)/x", "(0123456789abcdef0123456789ABCDE)",
                      "(0123456789abcdef0123456789ABCDEG)", "((0123456789abcdef0123456789ABCDEF))",
                      "(test-page)", "page?q", "page\r\n", "a/b", "", None, 7):
            self.assertFalse(live.target_id_ok(value), repr(value))

    @unittest.skipUnless(os.name == "posix", "Canvas private profiles use POSIX ownership; Windows uses workbench")
    def test_source_accepts_only_the_exact_parenthesized_hex_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "profile.json"
            config = {"sources": [source_config(root)]}
            config["sources"][0]["lease_path"] = "/tmp/plx-samsung-device.lock"
            for value, ok in (("(0123456789abcdef0123456789ABCDEF)", True),
                              ("(0123456789abcdef0123456789ABCDEF)/x", False),
                              ("(0123456789abcdef0123456789ABCDE)", False)):
                config["sources"][0]["target_id"] = value
                profile.write_text(json.dumps(config)); profile.chmod(0o600)
                if ok:
                    self.assertEqual(live.read_profile(profile)[1]["sources"][0]["target_id"], value)
                else:
                    with self.assertRaisesRegex(live.LiveError, "target ID"):
                        live.read_profile(profile)

    def test_refused_operation_keeps_connected_controls_available(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = live.Source(source_config(Path(temporary)), FakeCDP)
            try:
                source.operate("connect", {"authorized": True, "pixels": False, "input": True})
                with self.assertRaises(live.LiveError):
                    source.operate("key", {"key": "ok"})
                self.assertEqual(source.status, "connected")
            finally:
                source.disconnect()

    def test_cleanup_failure_revokes_input_and_keeps_truthful_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            class FailingLink(live.MemoryLink):
                armed = False
                def release(self):
                    if self.armed:
                        raise live.LiveError("cleanup", "release refused")
            link = FailingLink()
            source = live.Source(source_config(Path(temporary)), FakeCDP, lease=link)
            source.operate("connect", {"authorized": True, "pixels": True, "input": True})
            source.timer.cancel()
            link.armed = True
            with self.assertRaises(live.LiveError):
                source.disconnect()
            self.assertFalse(source.input_allowed)
            self.assertFalse(source.pixels_allowed)
            self.assertIn("unconfirmed", source.cleanup)

    def test_snapshot_release_failure_is_not_reported_as_released(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = live.Source(source_config(Path(temporary)), FakeCDP)
            try:
                source.operate("connect", {"authorized": True, "pixels": True, "input": False})
                source.client.reject = "LayerTree.releaseSnapshot"
                with self.assertRaises(live.LiveError):
                    source.operate("capture", {})
                self.assertIn("unconfirmed", source.cleanup)
            finally:
                source.disconnect()

    @unittest.skipUnless(os.name == "posix", "Canvas private profiles use POSIX ownership; Windows uses workbench")
    def test_profile_optional_metadata_and_no_inferred_hardware(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "profile.json"
            config = {"sources": [source_config(root)]}
            config["sources"][0]["lease_path"] = "/tmp/plx-samsung-device.lock"
            profile.write_text(json.dumps(config)); profile.chmod(0o600)
            _, result = live.read_profile(profile)
            self.assertEqual(result["sources"][0]["metadata"], {})
            config["sources"][0]["metadata"] = {"model": {"value": "Example TV", "provenance": "user-provided"}}
            profile.write_text(json.dumps(config))
            self.assertEqual(live.read_profile(profile)[1]["sources"][0]["metadata"]["model"]["provenance"], "user-provided")
            profile.chmod(0o644)
            with self.assertRaisesRegex(live.LiveError, "0600"):
                live.read_profile(profile)

    @unittest.skipUnless(os.name == "posix", "Canvas private profiles use POSIX ownership; Windows uses workbench")
    def test_profile_allows_scan_only_multiple_tvs_and_private_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "profile.json"
            def check(config):
                profile.write_text(json.dumps(config)); profile.chmod(0o600)
                return live.read_profile(profile)[1]
            self.assertEqual(check({"sources": []})["sources"], [])
            two = [source_config(root, "living-room"), source_config(root, "bedroom")]
            two[0]["lease_path"] = "/tmp/plx-samsung-device.lock"
            two[1]["lease_path"] = "/tmp/tizen-tv-bedroom.lock"
            self.assertEqual(len(check({"sources": two})["sources"]), 2)
            names = {"tv-0a1b2c": "Bedroom TV"}
            self.assertEqual(check({"sources": [], "tv_names": names, "certificate_duids": str(root / "p.xml")})["tv_names"], names)
            bad = [{"sources": two, "extra": 1},
                   {"sources": [source_config(root, "a", "emulator"), source_config(root, "b", "emulator")]},
                   {"sources": [source_config(root, "a"), source_config(root, "a")]},
                   {"sources": [dict(two[1], lease_path="/tmp/other.lock")]},
                   {"sources": [], "tv_names": {"192.0.2.1": "x"}},
                   {"sources": [], "tv_names": {"tv-0a1b2c": "x" * 61}},
                   {"sources": [], "certificate_duids": "relative.xml"},
                   {"sources": [], "certificate_duids": str(ROOT / "p.xml")},
                   {"sources": [source_config(root, f"tv{n}") for n in range(9)]}]
            for config in bad:
                with self.subTest(config=list(config)), self.assertRaises(live.LiveError):
                    check(config)

    @unittest.skipUnless(os.name == "posix", "Canvas profile persistence uses POSIX ownership")
    def test_scan_and_tv_routes_are_authenticated_and_names_persist_privately(self):
        class FakeFleet:
            def __init__(self):
                self.calls = []; self.closed = False
            def state(self):
                return {"scan": {"status": "done"}, "sdk": "available",
                        "tvs": [{"alias": "tv-0a1b2c", "name": "Bedroom TV", "status": "not-connected"}]}
            def submit(self, operation, alias, data):
                self.calls.append((operation, alias, data))
                if alias == "tv-ffffff":
                    live.fail("route", "Unknown TV; scan the network again")
                if operation == "rename":
                    app.save_names({alias: data["name"]})
                return self.state()
            def wizard(self, data):
                self.calls.append(("wizard", None, data))
                return {"steps": [], "current": "computer"}
            def install_sdk(self, data):
                self.calls.append(("install-sdk", None, data))
                return {"code": "working"}
            def close(self):
                self.closed = True
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / "profile.json"
            config = {"sources": []}
            path.write_text(json.dumps(config)); path.chmod(0o600)
            fleet = FakeFleet()
            app = live.App(path, config, FakeCDP, fleet=fleet)
            server = live.BoundedServer(("127.0.0.1", 0), live.handler(app))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            import http.client
            def request(method, route, data=None, **headers):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request(method, route, None if data is None else json.dumps(data),
                                   {"Content-Type": "application/json", **headers})
                reply = connection.getresponse(); raw = reply.read(); connection.close()
                return reply.status, json.loads(raw)
            # The portal is unauthenticated by user decision; same-origin proof replaces the token.
            auth = {"Origin": f"http://127.0.0.1:{server.server_port}"}
            try:
                self.assertFalse(hasattr(app, "token"))
                self.assertEqual(request("POST", "/api/detect", {})[0], 403)
                self.assertEqual(request("POST", "/api/detect", {}, Origin="https://untrusted.example")[0], 403)
                self.assertEqual(request("POST", "/api/detect", {}, Origin="http://127.0.0.1:1")[0], 403)
                self.assertEqual(request("POST", "/api/tv/connect/tv-0a1b2c", {"confirm": ""})[0], 403)
                self.assertEqual(request("GET", "/api/state", **{"Sec-Fetch-Site": "same-site"})[0], 403)
                self.assertEqual(request("GET", "/api/state", Host="localhost:%d" % server.server_port)[0], 403)
                self.assertEqual(request("GET", "/api/state")[0], 200)
                self.assertEqual(request("GET", "/api/state", **{"Sec-Fetch-Site": "same-origin"})[0], 200)
                self.assertEqual(fleet.calls, [])
                status, state = request("GET", "/api/state", **auth)
                self.assertEqual((status, state["tvs"][0]["name"], state["sources"]), (200, "Bedroom TV", []))
                self.assertEqual(request("POST", "/api/detect", {}, **auth)[0], 200)
                self.assertEqual(request("POST", "/api/tv/connect/tv-0a1b2c", {"confirm": ""}, **auth)[0], 200)
                self.assertEqual(request("POST", "/api/tv/connect/tv-ffffff", {"confirm": ""}, **auth)[0], 400)
                self.assertEqual(request("POST", "/api/tv/format/tv-0a1b2c", {}, **auth)[0], 400)
                self.assertEqual(request("POST", "/api/tv/connect/192.0.2.1", {}, **auth)[0], 400)
                self.assertEqual(request("POST", "/api/tv/rename/tv-0a1b2c", {"name": "Den"}, **auth)[0], 200)
                self.assertEqual(json.loads(path.read_text()), {"sources": [], "tv_names": {"tv-0a1b2c": "Den"}})
                self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
                self.assertEqual(request("POST", "/api/wizard", {})[0], 403)
                self.assertEqual(request("POST", "/api/install-sdk", {})[0], 403)
                self.assertEqual(request("POST", "/api/wizard", {"tv": "tv-0a1b2c"}, **auth), (200, {"steps": [], "current": "computer"}))
                self.assertEqual(request("POST", "/api/install-sdk", {}, **auth), (200, {"code": "working"}))
                self.assertEqual(request("POST", "/api/tv/sample/tv-0a1b2c", {}, **auth)[0], 200)
                self.assertEqual([c[0] for c in fleet.calls], ["detect", "connect", "connect", "rename", "wizard",
                                                               "install-sdk", "sample"])
            finally:
                app.close(); server.shutdown(); server.server_close(); thread.join()
            self.assertTrue(fleet.closed)

    def test_portal_ui_leads_with_scan_and_plain_language(self):
        canvas = Path(__file__).resolve().parents[1] / ".github" / "extensions" / "tizen-live"
        html = (canvas / "index.html").read_text()
        script = (canvas / "ui.js").read_text()
        style = (canvas / "ui.css").read_text()
        self.assertLess(html.index('id="scan"'), html.index("<main"))
        self.assertRegex(html, r'<button id="scan" class="primary[^"]*"[^>]*>Scan network for TVs</button>')
        self.assertNotIn('type="checkbox"', html)
        for text in (html, script):
            for jargon in ("CDP", "Active source", "lane", "Before connecting", "Explicit emulator"):
                self.assertNotIn(jargon, text)
        # No consent dialogs: the restart is stated on the button itself.
        self.assertIn("Open screen (restarts app)", script)
        self.assertIn("restart: true", script)
        self.assertNotIn("confirm(", script)
        for removed in ("No app open", "Preview not connected", "Dev mode enabled", "Developer Mode ready", "Identify"):
            self.assertNotIn(removed, script)
        self.assertIn("authorized: true, pixels: true, input: true", script)
        self.assertIn('"/api/detect"', script)
        for op in ("connect", "disconnect", "rename", "preview", "apps", "install", "remove", "screenshot", "cert"):
            self.assertIn(f'tvCommand("{op}"', script)
        self.assertIn('"Remote"', script)
        self.assertIn('"Emulator"', script)
        self.assertIn(":focus-visible", style)
        for text in (html, script, style):
            self.assertNotRegex(text, r"https?://|@import|fonts\.googleapis")

    def test_selected_tv_has_one_connect_and_shows_shared_metadata_and_dev_label(self):
        canvas = Path(__file__).resolve().parents[1] / ".github" / "extensions" / "tizen-live"
        script = (canvas / "ui.js").read_text()
        card = script[script.index("function tvCard("):script.index("function updateTv(")]
        self.assertEqual(card.count('"Connect")'), 1)
        self.assertNotIn("screenAction", script)
        for field in ("tv.dev_mode", "tv.metadata", "meta.model_year", "meta.os?.text", '"Model year"'):
            self.assertIn(field, script)
        self.assertNotIn("Developer Mode reachable", script)

    def test_connected_pane_hides_certificate_machinery_and_never_renders_null(self):
        script = (Path(__file__).resolve().parents[1] / ".github" / "extensions" / "tizen-live" / "ui.js").read_text()
        update = script[script.index("function updateTv("):script.index("function sourceCard(")]
        # replaceChildren(null) renders a literal "null" line; children must be filtered first.
        self.assertIn("].filter(Boolean));", update)
        self.assertNotIn("tizen.py live", script)
        card = script[script.index("function tvCard("):script.index("function updateTv(")]
        # Main card order: status, screen, apps, install; details last and collapsible.
        self.assertIn('el("div", { class: "connected-body" }, screen, apps, installSection)', card)
        self.assertIn('job, connected, details),', card)
        self.assertIn('el("summary", {}, "TV details")', card)
        self.assertNotIn('"Advanced"', card)

    def test_unreachable_card_offers_only_scan_and_hints_need_a_picture(self):
        script = (Path(__file__).resolve().parents[1] / ".github" / "extensions" / "tizen-live" / "ui.js").read_text()
        update = script[script.index("function updateTv("):script.index("function sourceCard(")]
        self.assertIn('const unreachable = !tv.held && tv.label === "Not reachable";', update)
        self.assertIn('["Ready to connect", "Connection problem"].includes(tv.label)', update)
        self.assertIn("card.rescan.hidden = !unreachable;", update)
        # No guidance or "Next:" for a TV that is off; a last-seen time instead of a stale address.
        self.assertIn("|| unreachable ? null : tv.guidance", update)
        self.assertIn("!unreachable && tv.label", update)
        self.assertIn("unreachable ? \"\" : d.address, seen]", update)
        self.assertIn("card.hint.hidden = !live;", update)
        self.assertNotIn("version unavailable", script)

    def test_certificate_card_speaks_in_tv_names(self):
        script = (Path(__file__).resolve().parents[1] / ".github" / "extensions" / "tizen-live" / "ui.js").read_text(encoding="utf-8")
        for text in ("`You can't install your apps on ${c.tv_name || \"this TV\"} yet: ${certText(c)}`",
                     "`your certificate only allows ${(c.covers_names", '"Allow installing my apps on this TV"',
                     "Samsung sign-in opens in your browser; nothing else to type.", 'el("ul", { class: "checklist" }',
                     'el("summary", {}, "TV details"), list, disconnect)', '"Copy device ID"', '["Certificate", c.profile ? `${c.profile} · covers ${(c.covers'):
            self.assertIn(text, script)
        self.assertNotIn("This TV is ${d.duid}", script)
        self.assertIn("el(\"div\", { class: \"row\" }, connect, rescan), guide", script)

    def test_page_scans_once_on_open_when_nothing_was_checked(self):
        script = (Path(__file__).resolve().parents[1] / ".github" / "extensions" / "tizen-live" / "ui.js").read_text()
        refresh = script[script.index("async function refresh("):script.index('$("scan").addEventListener')]
        self.assertIn('if (!offline && !autoScanned && state.scan?.status === "never") { autoScanned = true; scan(); }',
                      refresh)

    def test_unnamed_tv_card_is_titled_by_its_address(self):
        script = (Path(__file__).resolve().parents[1] / ".github" / "extensions" / "tizen-live" / "ui.js").read_text()
        self.assertIn("return tv.unnamed && address ? `TV at ${address}` : tv.name;", script)
        update = script[script.index("function updateTv("):script.index("function sourceCard(")]
        self.assertIn("card.name.textContent = tvTitle(tv);", update)
        self.assertNotIn("card.name.textContent = tv.name;", script)

    def test_endpoint_and_profile_do_not_allow_network_redirection(self):
        for bad in ("http://localhost:12345", "http://192.0.2.1:12345", "https://127.0.0.1:12345",
                    "http://127.0.0.1:12345/path", "http://127.0.0.1:12345?token=fixture",
                    "http://127.0.0.1:99999", "http://127.0.0.1:01234"):
            with self.subTest(value=bad), self.assertRaises((live.LiveError, ValueError)):
                live.endpoint(bad)

    def test_directory_lease_interoperates_and_never_breaks_another_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "device.lock"
            first = live.Lease(path); first.acquire()
            self.assertTrue(path.is_dir())
            self.assertEqual(json.loads((path / "owner.json").read_text())["pid"], os.getpid())
            with self.assertRaisesRegex(live.LiveError, "already owned"):
                live.Lease(path).acquire()
            first.release()
            self.assertFalse(path.exists())
            first.acquire()
            (path / "owner.json").write_text('{"token":"different-owner"}')
            with self.assertRaisesRegex(live.LiveError, "token changed"):
                first.release()
            self.assertTrue((path / "owner.json").exists())

    def test_no_device_calls_before_user_connect_and_separate_grants(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = source_config(Path(temporary))
            factory = lambda *_: self.fail("Unapproved connection")
            source = live.Source(config, factory)
            self.assertEqual(source.summary()["status"], "disconnected")
            with self.assertRaises(live.LiveError):
                source.operate("connect", {"authorized": False, "pixels": True, "input": True})
            self.assertFalse(Path(config["lease_path"]).exists())

    def test_capture_native_provenance_cleanup_and_stale_preservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = live.Source(source_config(Path(temporary)), FakeCDP)
            try:
                source.operate("connect", {"authorized": True, "pixels": True, "input": False})
                result = source.operate("capture", {})
                self.assertEqual(result["provenance"], "native TV renderer paint-layer replay")
                self.assertEqual(result["frames"][0]["width"], 100)
                self.assertTrue(result["frames"][0]["clickable"])
                methods = [method for method, _ in source.client.calls]
                self.assertIn("LayerTree.releaseSnapshot", methods)
                self.assertEqual(methods[-1], "LayerTree.disable")
                original = result["revision"]
                self.assertEqual(source.operate("capture", {})["revision"], original, "Unchanged pixels have stable identity")
                client = source.client
                client.reject = "LayerTree.replaySnapshot"
                with self.assertRaisesRegex(live.LiveError, "unsupported"):
                    source.operate("capture", {})
                self.assertTrue(source.stale)
                self.assertEqual(source.revision, original)
                self.assertEqual(len(source.frames), 1, "Failure preserves last real frame, marked stale")
                self.assertTrue(client.closed)
                self.assertFalse(Path(source.config["lease_path"]).exists())
                methods = [m for m, _ in client.calls]
                self.assertEqual(methods[-2:], ["LayerTree.releaseSnapshot", "LayerTree.disable"])
            finally:
                source.disconnect()

    def test_guarded_input_and_no_capture_before_key_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = live.Source(source_config(Path(temporary)), FakeCDP)
            try:
                source.operate("connect", {"authorized": True, "pixels": False, "input": True})
                source.operate("key", {"key": "right"})
                self.assertEqual([m for m, _ in source.client.calls], ["Input.dispatchKeyEvent", "Input.dispatchKeyEvent"])
                self.assertEqual(source.client.calls[0][1]["windowsVirtualKeyCode"], 39)
                self.assertEqual(source.client.calls[1][1]["type"], "keyUp")
                self.assertIsNotNone(source.command_ms)
                before = len(source.client.calls)
                with self.assertRaisesRegex(live.LiveError, "audited"):
                    source.operate("key", {"key": "ok"})
                self.assertEqual(len(source.client.calls), before)
                self.assertIsNotNone(source.client, "Guard refusal should not detach the safe session")
                with self.assertRaises(live.LiveError):
                    source.operate("key", {"key": "Home"})
            finally:
                source.disconnect()

    def test_click_mapping_refuses_stale_small_transformed_and_unapproved(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = source_config(Path(temporary)); config["safe_activation_ids"] = ["safe-button"]
            source = live.Source(config, FakeCDP)
            try:
                source.operate("connect", {"authorized": True, "pixels": True, "input": True})
                source.operate("capture", {})
                revision = source.revision
                source.operate("click", {"frame": 0, "revision": revision, "x": 99, "y": 79})
                self.assertEqual([m for m, _ in source.client.calls][-2:],
                                 ["Input.dispatchMouseEvent", "Input.dispatchMouseEvent"])
                source.operate("capture", {})
                source.frames[0]["clickable"] = False
                with self.assertRaisesRegex(live.LiveError, "fresh"):
                    source.operate("click", {"frame": 0, "revision": source.revision, "x": 0, "y": 0})
            finally:
                source.disconnect()

    def test_busy_backpressure_has_no_queue_or_second_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = live.Source(source_config(Path(temporary)), FakeCDP)
            source.lock.acquire()
            try:
                started = time.monotonic()
                with self.assertRaisesRegex(live.LiveError, "in flight"):
                    source.operate("key", {"key": "up"})
                self.assertLess(time.monotonic() - started, .1)
            finally:
                source.lock.release()

    def test_expiry_releases_only_owned_lane_and_disables_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = live.Source(source_config(Path(temporary)), FakeCDP)
            source.operate("connect", {"authorized": True, "pixels": True, "input": True})
            client = source.client
            source.expire()
            self.assertEqual(source.status, "expired")
            self.assertFalse(source.input_allowed)
            self.assertTrue(client.closed)
            self.assertFalse(Path(source.config["lease_path"]).exists())

    def test_explicit_emulator_label_and_comparison_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {"sources": [source_config(root), source_config(root, "emulated", "emulator")]}
            app = live.App(root / "profile.json", config, FakeCDP)
            self.assertIn("native TV", app.state()["sources"][0]["provenance"])
            self.assertIn("emulator", app.state()["sources"][1]["provenance"])
            self.assertFalse(app.sources["sample"].frames)

    def test_png_limits_and_malformed_replays(self):
        for value in ("data:image/svg+xml,<script>", "data:image/png;base64,invalid",
                      "data:image/png;base64," + base64.b64encode(native_fixture(9000)).decode()):
            with self.subTest(value=value[:40]), self.assertRaises((live.LiveError, ValueError)):
                live.png(value)

    @unittest.skipUnless(os.name == "posix", "Canvas profile persistence uses POSIX ownership; workbench routes tested separately")
    def test_http_auth_origin_route_metadata_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / "profile.json"
            config = {"sources": [source_config(root, kind="emulator")]}
            path.write_text(json.dumps(config)); path.chmod(0o600)
            app = live.App(path, config, FakeCDP)
            server = live.BoundedServer(("127.0.0.1", 0), live.handler(app))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            import http.client
            def request(method, route, data=None, **headers):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                body = None if data is None else json.dumps(data)
                supplied = {"Content-Type": "application/json", **headers}
                connection.request(method, route, body, supplied)
                reply = connection.getresponse(); raw = reply.read(); connection.close()
                return reply.status, json.loads(raw) if reply.getheader("Content-Type") == "application/json" else raw
            auth = {"Origin": f"http://127.0.0.1:{server.server_port}"}
            try:
                embedded = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Dest": "iframe"}
                for route in ("/", "/ui.js", "/ui.css", "/controls.mjs"):
                    status, public = request("GET", route, **embedded)
                    self.assertEqual(status, 200, "Embedded host must load public renderer files")
                    self.assertNotIn(b"Authorization", public)
                self.assertEqual(request("GET", "/", **embedded, Host="untrusted.example")[0], 403)
                self.assertEqual(request("GET", "/api/state", **auth, **embedded)[0], 403)
                self.assertEqual(request("POST", "/api/connect/sample", {}, **auth, **embedded)[0], 403)
                self.assertEqual(request("GET", "/ui.js?private=1", **embedded)[0], 403)
                self.assertIsNone(app.sources["sample"].client)
                self.assertEqual(request("POST", "/api/connect/sample", {"authorized": True, "pixels": True, "input": False})[0], 403)
                self.assertEqual(request("GET", "/api/state", Origin="https://untrusted.example")[0], 403)
                self.assertEqual(request("GET", "/api/state", Host="untrusted.example")[0], 403)
                self.assertEqual(request("POST", "/api/connect/sample", {}, Origin="null")[0], 403)
                self.assertEqual(request("OPTIONS", "/api/state", **auth)[0], 405)
                status, refused = request("<script>", "/api/state", **auth)
                self.assertEqual(status, 501)
                self.assertNotIn("<script>", json.dumps(refused))
                state = request("GET", "/api/state", **auth)[1]
                self.assertNotIn("target_id", json.dumps(state))
                self.assertNotIn("12345", json.dumps(state))
                self.assertEqual(request("POST", "/api/evaluate/sample", {"expression": "1"}, **auth)[0], 400)
                self.assertEqual(request("POST", "/api/connect/sample", {"authorized": True, "pixels": True, "input": False}, **auth)[0], 200)
                self.assertEqual(request("POST", "/api/key/sample", {"key": "ok"}, **auth)[0], 403)
                self.assertEqual(request("POST", "/api/capture/sample", {}, **auth)[0], 200)
                revision = app.sources["sample"].revision
                self.assertEqual(request("GET", f"/api/frame/sample/0/{revision}", **{"Sec-Fetch-Site": "same-site"})[0], 403)
                self.assertEqual(request("GET", f"/api/frame/sample/0/{revision}", Origin="http://127.0.0.1:1")[0], 403)
                self.assertEqual(request("GET", f"/api/frame/sample/0/{revision}", **auth)[1], native_fixture())
                values = {"model": {"value": "Sample model", "provenance": "user-provided"}}
                self.assertEqual(request("POST", "/api/metadata/sample", values, **auth)[0], 200)
                self.assertEqual(json.loads(path.read_text())["sources"][0]["metadata"], values)
                self.assertEqual(request("GET", "/api/state?token=anything", **auth)[0], 400)
            finally:
                app.close(); server.shutdown(); server.server_close(); thread.join()
            self.assertFalse(Path(config["sources"][0]["lease_path"]).exists())

    def test_rpc_fragmentation_ping_notifications_and_unsupported(self):
        client_sock, peer = socket.socketpair()
        client = object.__new__(live.CDP); client.sock = client_sock
        client.next_id = 0; client.layers = None
        def frame(raw, opcode=1, final=True):
            first = (0x80 if final else 0) | opcode
            return bytes([first, len(raw)]) + raw
        peer.sendall(frame(b"ping", 9) + frame(b'{"method":"LayerTree.layerTreeDidChange","params":{"layers":[]}}') +
                     frame(b'{"id":1,', final=False) + frame(b'"result":{}}', opcode=0))
        self.assertEqual(client.rpc("Page.enable"), {})
        self.assertEqual(client.layers, [])
        peer.sendall(frame(b'{"id":2,"error":{"code":-32601,"message":"private"}}'))
        with self.assertRaisesRegex(live.LiveError, "unsupported") as error:
            client.rpc("LayerTree.enable")
        self.assertNotIn("private", str(error.exception))
        client.close(); peer.close()

    def test_rpc_deadline_and_frame_length_limits(self):
        for wire in (b"", b"\x81\x7f" + struct.pack("!Q", live.MAX_MESSAGE + 1)):
            client_sock, peer = socket.socketpair()
            client = object.__new__(live.CDP); client.sock = client_sock
            client.deadline = time.monotonic() + .03
            try:
                if wire: peer.sendall(wire)
                with self.assertRaises((live.LiveError, TimeoutError)):
                    client.message()
            finally:
                client.close(); peer.close()


if __name__ == "__main__":
    unittest.main()
