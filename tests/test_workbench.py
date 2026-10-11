import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import live_tv
import workbench


class ShellTests(unittest.TestCase):
    def test_setup_actions_need_no_redundant_discovery_checkbox(self):
        html = (workbench.ASSETS / "index.html").read_text(encoding="utf-8")
        script = (workbench.ASSETS / "ui.js").read_text(encoding="utf-8")
        self.assertNotIn("sdk-grant", html)
        self.assertNotIn("sdk-grant", script)
        self.assertIn('operation, ...values, authorized: true', script)

    def test_record_route_reuses_authenticated_bounded_json_policy(self):
        app = live_tv.App(None, {"sources": []})
        app.record = lambda data: {"state": "fixture", "operation": data["operation"]}
        server = live_tv.BoundedServer(("127.0.0.1", 0), live_tv.handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for authenticated, expected in [(False, 403), (True, 200)]:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                headers = {"Content-Type": "application/json"}
                if authenticated:
                    headers["Authorization"] = "Bearer " + app.token
                connection.request("POST", "/api/record", '{"operation":"status"}', headers)
                response = connection.getresponse()
                self.assertEqual(response.status, expected)
                response.read(); connection.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_standalone_assets_and_frame_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            (assets / "index.html").write_text("standalone workbench")
            app = live_tv.App(None, {"sources": []})
            app.assets = assets
            app.frame_ancestors = "'none'"
            server = live_tv.BoundedServer(("127.0.0.1", 0), live_tv.handler(app))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/")
                response = connection.getresponse()
                self.assertIn("frame-ancestors 'none'", response.getheader("Content-Security-Policy"))
                self.assertEqual(response.read(), b"standalone workbench")
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_setup_is_authenticated_and_cross_origin_refused(self):
        app = workbench.Workbench()
        server = live_tv.BoundedServer(("127.0.0.1", 0), live_tv.handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for headers, status in [
                ({}, 403),
                ({"Authorization": "Bearer " + app.token, "Origin": "http://example.invalid"}, 403),
                ({"Authorization": "Bearer " + app.token}, 200),
            ]:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/api/setup", headers=headers)
                response = connection.getresponse()
                self.assertEqual(response.status, status)
                body = response.read()
                self.assertNotIn(app.token.encode(), body)
                connection.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.lane_patch = patch.object(workbench, "lane", return_value=self.root / "device.lock")
        self.lane_patch.start()
        self.calls = []
        self.mapping = ""
        def runner(tool, args):
            self.calls.append(args)
            if args == ["devices"]:
                return "127.0.0.1:26101 device TV\n"
            if "debug" in args:
                return "debug port: 42000"
            if args[-2:] == ["forward", "--list"]:
                return self.mapping
            if "forward" in args and "--remove" not in args:
                self.mapping = " ".join([args[1], args[-2], args[-1]])
            return ""
        self.app = workbench.Workbench(runner=runner, discover=lambda port: ["page-one", "page-two"])

    def tearDown(self):
        self.app.forward = None
        self.app.close_sources()
        self.lane_patch.stop()
        self.temporary.cleanup()

    def test_connect_requires_grant_and_confirms_ready_device(self):
        with self.assertRaises(live_tv.LiveError):
            self.app.setup({"operation": "connect-tv", "serial": "127.0.0.1:26101", "authorized": False})
        self.assertEqual(self.calls, [])
        self.app.setup({"operation": "connect-tv", "serial": "127.0.0.1:26101", "authorized": True})
        self.assertEqual(self.calls, [["connect", "127.0.0.1:26101"], ["devices"]])
        self.assertFalse((self.root / "device.lock").exists())

    def test_live_lane_never_broken(self):
        lock = self.root / "device.lock"
        lock.mkdir()
        with self.assertRaisesRegex(live_tv.LiveError, "already owned"):
            self.app.setup({"operation": "attach", "port": 12345, "authorized": True})
        self.assertTrue(lock.is_dir())
        self.assertEqual(self.calls, [])
        lock.rmdir()

    def test_existing_forward_attach_never_creates_or_removes_sdb_forward(self):
        self.app.setup({"operation": "attach", "port": 12345, "authorized": True})
        self.app.setup({"operation": "select", "page": 1, "safe_ids": []})
        config = self.app.sources["development-tv"].config
        self.assertEqual(config["target_id"], "page-two")
        self.assertEqual(config["endpoint"], "http://127.0.0.1:12345")
        self.app.close()
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / "device.lock").exists())

    def test_debug_stops_package_and_manages_only_owned_mapping(self):
        result = self.app.setup({"operation": "debug", "serial": "127.0.0.1:26101",
                                 "authorized": True, "app": "PACKAGEID0.Application", "legacy": True})
        self.assertTrue(result["forward_owned"])
        self.assertEqual(self.calls[:2], [
            ["-s", "127.0.0.1:26101", "shell", "0", "kill", "PACKAGEID0"],
            ["-s", "127.0.0.1:26101", "shell", "0", "debug", "PACKAGEID0.Application", "0"],
        ])
        local = self.app.forward[1]
        self.app.setup({"operation": "release", "authorized": True})
        self.assertEqual(self.calls[-1], ["-s", "127.0.0.1:26101", "forward", "--remove", f"tcp:{local}"])
        self.assertIsNone(self.app.forward)

    def test_changed_mapping_is_not_removed(self):
        self.app.forward = ("127.0.0.1:26101", 12345, 42000)
        self.mapping = "127.0.0.1:26101 tcp:12345 tcp:43000"
        with self.assertRaisesRegex(live_tv.LiveError, "closed elsewhere"):
            self.app.setup({"operation": "release", "authorized": True})
        self.assertFalse(any("--remove" in args for args in self.calls))
        self.assertIsNotNone(self.app.forward)
        self.assertFalse((self.root / "device.lock").exists())

    def test_failed_discovery_keeps_forward_available_for_explicit_cleanup(self):
        def unavailable(port):
            raise live_tv.LiveError("discovery", "No debug page")
        self.app.discover = unavailable
        with self.assertRaises(live_tv.LiveError):
            self.app.setup({"operation": "debug", "serial": "127.0.0.1:26101",
                           "authorized": True, "app": "PACKAGEID0.Application", "legacy": False})
        self.assertTrue(self.app.setup_status()["forward_owned"])
        self.assertFalse((self.root / "device.lock").exists())
        self.app.setup({"operation": "release", "authorized": True})

    def test_invalid_app_and_safe_ids_never_dispatch(self):
        for app in ["bad;echo", "short.App", "PACKAGEID0.App extra"]:
            with self.assertRaises(live_tv.LiveError):
                self.app.setup({"operation": "debug", "serial": "127.0.0.1:26101",
                               "authorized": True, "app": app, "legacy": False})
        self.assertEqual(self.calls, [])
        self.app.setup({"operation": "attach", "port": 12345, "authorized": True})
        with self.assertRaises(live_tv.LiveError):
            self.app.setup({"operation": "select", "page": 0, "safe_ids": ["not an ID"]})

    def test_select_is_refused_during_connected_preview(self):
        self.app.sources = {"test": SimpleNamespace(client=object())}
        try:
            with self.assertRaisesRegex(live_tv.LiveError, "Disconnect"):
                self.app.setup({"operation": "select", "page": 0, "safe_ids": []})
        finally:
            self.app.sources = {}

    def test_windows_sdb_path_with_spaces_uses_executable_suffix(self):
        fake_os = SimpleNamespace(name="nt", environ={"TIZEN_SDK_ROOT": str(self.root / "SDK with spaces")})
        target = self.root / "SDK with spaces/tools/sdb.exe"
        target.parent.mkdir(parents=True)
        target.touch()
        with patch.object(workbench, "os", fake_os), patch.object(workbench.shutil, "which", return_value=None):
            self.assertEqual(workbench.sdk_tool("sdb"), str(target))
            self.assertEqual(workbench.lane(), self.root / "device.lock")

    def test_sdb_process_output_is_bounded_and_errors_are_sanitized(self):
        self.assertEqual(workbench.run_sdb(sys.executable, ["-c", "print('ready')"]).strip(), "ready")
        with self.assertRaisesRegex(live_tv.LiveError, "unexpected amount"):
            workbench.run_sdb(sys.executable, ["-c", "print('x' * 300000)"])
        with self.assertRaises(live_tv.LiveError) as caught:
            workbench.run_sdb(sys.executable, ["-c", "import sys; print('PRIVATE-RESPONSE'); sys.exit(1)"])
        self.assertNotIn("PRIVATE-RESPONSE", str(caught.exception))

    def test_detects_connected_tv_and_installed_apps_without_manual_id(self):
        original = self.app.runner
        def runner(tool, args):
            if args[-3:] == ["shell", "0", "applist"]:
                self.calls.append(args)
                return "'PACKAGEID0.Application' 'Development app'\n"
            return original(tool, args)
        self.app.runner = runner
        result = self.app.setup({"operation": "discover", "serial": "", "authorized": True})
        self.assertEqual(result["devices"], ["127.0.0.1:26101"])
        self.assertEqual(result["apps"], ["PACKAGEID0.Application"])
        self.assertFalse((self.root / "device.lock").exists())

    def test_local_network_discovery_needs_no_extra_grant(self):
        result = {"devices": [{"ip": "192.168.0.10", "name": "Fixture TV", "model": "unknown",
                               "year": "unknown", "developer_mode": "unknown"}], "unconfirmed": 1}
        with patch.object(workbench, "discover_tvs", return_value=result):
            status = self.app.setup({"operation": "find-network", "authorized": True})
            self.assertEqual(status["network_devices"], result["devices"])
            self.assertIn("Found 1 TV", status["message"])
            self.assertFalse((self.root / "device.lock").exists())

    def test_find_tvs_works_while_device_lane_is_busy_and_never_blames_inspector(self):
        (self.root / "device.lock").mkdir()
        try:
            with patch.object(workbench, "discover_tvs", return_value={"devices": [], "unconfirmed": 0}):
                status = self.app.setup({"operation": "find-network", "authorized": True})
            self.assertIn("No TVs found", status["message"])
            with patch.object(workbench, "discover_tvs", side_effect=OSError(65, "No route to host")):
                with self.assertRaises(live_tv.LiveError) as caught:
                    self.app.setup({"operation": "find-network", "authorized": True})
            self.assertNotIn("nspector", str(caught.exception))
            self.assertIn("Wi", str(caught.exception))
            with patch.object(workbench, "confirm_tv", side_effect=OSError(61, "refused")):
                with self.assertRaises(live_tv.LiveError) as caught:
                    self.app.setup({"operation": "confirm-tv", "authorized": True, "ip": "192.168.0.10"})
            self.assertNotIn("nspector", str(caught.exception))
        finally:
            (self.root / "device.lock").rmdir()


class RemoteTests(unittest.TestCase):
    def test_controller_rejects_competing_capture_and_stream_during_recording(self):
        config = {"id": "fixture", "kind": "tv", "metadata": {}, "safe_activation_ids": [],
                  "endpoint": "http://127.0.0.1:12345", "target_id": "page",
                  "lease_path": "/unused-host-fixture.lock", "lease_seconds": 10}
        source = workbench.RemoteSource(config)
        source._recording_owner = SimpleNamespace(thread=object())
        for operation, data in [("capture", {}), ("stream", {"enabled": True, "interval": 10})]:
            with self.assertRaisesRegex(live_tv.LiveError, "Recording owns capture"):
                source.operate(operation, data)

    def test_only_observed_optional_keys_are_enabled_and_require_app_action_grant(self):
        class Client:
            def __init__(self, port, target):
                self.calls = []
            def rpc(self, method, params=None, timeout=3):
                self.calls.append((method, params))
                if method == "Runtime.evaluate":
                    return {"result": {"value": [{"name": "MediaPlay", "code": 415}, {"name": "1", "code": 49}]}}
                return {}
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as temporary:
            config = {"id": "test", "kind": "tv", "metadata": {}, "safe_activation_ids": [],
                      "endpoint": "http://127.0.0.1:12345", "target_id": "page",
                      "lease_path": str(Path(temporary) / "lock"), "lease_seconds": 10}
            source = workbench.RemoteSource(config, Client)
            try:
                source.operate("connect", {"authorized": True, "input": True, "pixels": False})
                self.assertIn("play", source.summary()["remote_keys"])
                self.assertNotIn("power", source.summary()["remote_keys"])
                with self.assertRaises(live_tv.LiveError):
                    source.operate("key", {"key": "play"})
                source.disconnect()
                source.operate("connect", {"authorized": True, "input": True, "pixels": False, "extended_input": True})
                source.operate("key", {"key": "play"})
                events = [p for m, p in source.client.calls if m == "Input.dispatchKeyEvent"]
                self.assertEqual([e["type"] for e in events], ["keyDown", "keyUp"])
                self.assertEqual(events[0]["windowsVirtualKeyCode"], 415)
                with self.assertRaises(live_tv.LiveError):
                    source.operate("key", {"key": "power"})
                with self.assertRaises(live_tv.LiveError):
                    source.operate("key", {"key": "ok"})
            finally:
                source.disconnect()

    def test_full_remote_buttons_are_present(self):
        html = (workbench.ASSETS / "index.html").read_text(encoding="utf-8")
        for name in set(live_tv.KEYS) | set(workbench.REMOTE_NAMES):
            self.assertIn(f'data-key="{name}"', html)


class RecordingIntegrationTests(unittest.TestCase):
    def test_invalid_new_output_preserves_previous_recording(self):
        app = workbench.Workbench()
        app.sources = {"fixture": SimpleNamespace()}
        app.encoder = SimpleNamespace()
        previous = SimpleNamespace(close=lambda: self.fail("Previous recording was closed"))
        app.recorder = previous
        with self.assertRaises(live_tv.LiveError):
            app.record({"operation": "start", "authorized_save": True,
                        "output_parent": "relative", "fps": 2, "width": 960,
                        "height": 540, "duration": 10, "layer": 0})
        self.assertIs(app.recorder, previous)

    def test_browser_record_deadline_covers_stop_and_encode_bounds(self):
        script = (workbench.ASSETS / "ui.js").read_text()
        self.assertIn('path === "/api/record" ? 90000 : 30000', script)

    def test_save_grant_constructor_and_stop_export_are_wired(self):
        app = workbench.Workbench()
        app.sources = {"fixture": SimpleNamespace()}
        app.encoder = SimpleNamespace(summary=lambda: {"png": True})
        created = []
        class FixtureRecorder:
            def __init__(self, source, path, **kwargs):
                created.append((path, kwargs))
            def start(self):
                return {"state": "recording", "frames": 0}
            def stop(self):
                return {"state": "stopped", "frames": 2}
            def export(self, format, still=False):
                return {"state": "stopped", "frames": 2, "format": format, "still": still}
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as temporary, patch.object(workbench, "Recorder", FixtureRecorder):
            values = {"operation": "start", "authorized_save": False, "output_parent": temporary,
                      "fps": 2, "width": 960, "height": 540, "duration": 10, "layer": 0}
            with self.assertRaises(live_tv.LiveError):
                app.record(values)
            self.assertEqual(created, [])
            values["authorized_save"] = True
            self.assertEqual(app.record(values)["state"], "recording")
            self.assertTrue(created[0][0].name.startswith("app-layer-0-"))
            self.assertEqual(created[0][1]["size"], (960, 540))
            self.assertEqual(app.record({"operation": "stop", "format": "png"})["format"], "png")
            self.assertTrue(app.record({"operation": "export", "format": "png", "still": True})["still"])
