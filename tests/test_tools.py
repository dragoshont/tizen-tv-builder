import importlib.util
import http.server
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tizen", ROOT / "tools/tizen.py")
tizen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tizen)


class ToolkitTests(unittest.TestCase):
    def args(self, *arguments):
        return tizen.parser().parse_args(arguments)

    def assert_process_stopped(self, pid):
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            # An orphan killed on Linux may await init's reap; it cannot execute.
            status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                    capture_output=True, text=True, timeout=2).stdout.strip()
            if not status or status.startswith("Z"):
                return
            time.sleep(0.02)
        self.fail("SDK launcher left a running child")

    def test_manifest_and_identifier(self):
        self.assertEqual(tizen.validate(ROOT / "examples/hello-tv")["app"], "PlxHelloTv.Hello")
        for bad in ("app", "short.App", "1234567890.;rm", "1234567890.App\n"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tizen.application_id(bad)

    def test_serial_is_one_explicit_device(self):
        self.assertEqual(tizen.device_serial("127.0.0.1:26111"), "127.0.0.1:26111")
        for bad in ("host", "--all", "0.0.0.0:26101", "255.255.255.255:1",
                    "224.0.0.1:1", "127.0.0.1:0", "127.0.0.1:99999"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                tizen.device_serial(bad)

    def test_device_operations_fail_before_tool_resolution(self):
        with patch.object(tizen, "tool_path", side_effect=AssertionError("Should not invoke SDK")):
            with self.assertRaisesRegex(ValueError, "authorized"):
                tizen.run(self.args("run", "--app", "PlxHelloTv.Hello", "--serial", "127.0.0.1:26111"))

    def test_debug_timeout_argument_and_target(self):
        with patch.object(tizen, "tool_path", return_value="/sdk/sdb"), patch.object(tizen, "execute", return_value="port: 12345") as execute:
            tizen.run(self.args("debug", "--app", "PlxHelloTv.Hello", "--serial",
                               "127.0.0.1:26111", "--authorized", "--legacy-timeout", "0"))
            self.assertEqual(execute.call_args.args[0], ["/sdk/sdb", "-s", "127.0.0.1:26111",
                                                       "shell", "0", "debug", "PlxHelloTv.Hello", "0"])

    def test_cleanup_removes_only_named_forward(self):
        with patch.object(tizen, "tool_path", return_value="/sdk/sdb"), patch.object(tizen, "execute", return_value="") as execute:
            tizen.run(self.args("unforward", "--port", "12345", "--serial", "127.0.0.1:26111", "--authorized"))
            self.assertEqual(execute.call_args.args[0][-3:], ["forward", "--remove", "tcp:12345"])

    def test_stage_excludes_secrets_generated_and_hidden(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "source", root / "output"
            source.mkdir()
            for file in ("config.xml", "index.html"):
                (source / file).write_bytes((ROOT / "examples/hello-tv" / file).read_bytes())
            for file in ("key.p12", "private.json", "app.wgt", ".secret.js"):
                (source / file).write_text("private")
            (source / "private").mkdir()
            (source / "private/token.js").write_text("private")
            tizen.stage(source, output)
            self.assertEqual(sorted(p.name for p in output.iterdir()), ["config.xml", "index.html"])
            (source / "link.js").symlink_to(source / "index.html")
            with self.assertRaisesRegex(ValueError, "Symlinks"):
                tizen.stage(source, output)

    def test_manifest_rejects_traversal_and_entity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "index.html").write_text("hi")
            original = (ROOT / "examples/hello-tv/config.xml").read_text()
            for content in (original.replace('src="index.html"', 'src="../index.html"'),
                            original.replace("<widget ", '<!DOCTYPE widget SYSTEM "file:///private"><widget '),
                            original.replace('package="PlxHelloTv"', 'package="AnotherId0"')):
                (root / "config.xml").write_text(content)
                with self.assertRaises(ValueError):
                    tizen.validate(root)

    def test_unsigned_build_is_staged_and_non_device(self):
        with patch.object(tizen, "tool_path", return_value="/sdk/tizen"), patch.object(tizen, "execute", return_value="BUILD SUCCESSFUL") as execute:
            result = tizen.run(self.args("build", str(ROOT / "examples/hello-tv")))
            command = execute.call_args.args[0]
            self.assertEqual(command[:3], ["/sdk/tizen", "build-web", "--"])
            self.assertNotEqual(command[3], str(ROOT / "examples/hello-tv"))
            self.assertEqual(result["build"], "unsigned")
            self.assertFalse(Path(command[3]).exists(), "Temporary build cleaned")

    def test_build_refuses_a_failing_audit_before_the_sdk_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "app"
            shutil.copytree(ROOT / "examples/hello-tv", root)
            (root / "leak.js").write_text('var password = "hunter2hunter2";\n')
            with patch.object(tizen, "tool_path", return_value="/sdk/tizen"), patch.object(tizen, "execute") as execute:
                with self.assertRaisesRegex(ValueError, "audit"):
                    tizen.run(self.args("build", str(root)))
                execute.assert_not_called()

    def test_package_never_overwrites_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "existing.wgt"
            output.write_bytes(b"keep")
            with self.assertRaisesRegex(ValueError, "new .wgt"):
                tizen.run(self.args("package", str(ROOT / "examples/hello-tv"), "--profile", "test", "--output", str(output)))
            self.assertEqual(output.read_bytes(), b"keep")

    def test_signed_package_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "hello.wgt"
            def package(command, timeout=120):
                package_directory = Path(command[command.index("-o") + 1])
                (package_directory / "Hello TV.wgt").write_bytes(b"synthetic signed fixture")
                return ""
            with patch.object(tizen, "tool_path", return_value="/sdk/tizen"), patch.object(tizen, "execute", side_effect=package):
                result = tizen.run(self.args("package", str(ROOT / "examples/hello-tv"), "--profile", "test", "--output", str(output)))
            self.assertEqual(output.read_bytes(), b"synthetic signed fixture")
            self.assertIn("requested SDK profile", result["package"])

    def test_redacted_sdk_errors_and_missing_override(self):
        self.assertNotIn("secret", tizen.redact("token=secret password=secret duid=secret 192.168.1.2"))
        self.assertNotIn("192.168", tizen.redact("192.168.1.2"))
        with self.assertRaisesRegex(ValueError, "missing"):
            tizen.tool_path("sdb", "/definitely-not-installed/sdb")

    def test_inspector_shapes_and_privacy(self):
        class Reply:
            status = 200
            def __init__(self, data): self.data = json.dumps(data).encode()
            def read1(self, size):
                data, self.data = self.data[:size], self.data[size:]
                return data
            def close(self): pass
        class Connection:
            sock = None
            def __init__(self, *args, **kwargs): self.reply = None
            def request(self, method, path):
                self.reply = Reply({"Browser": "Chrome/47", "Protocol-Version": "1.1"} if path.endswith("version")
                                   else [{"type": "page", "title": "private title", "url": "?token=secret"}])
            def getresponse(self): return self.reply
            def close(self): pass
        with patch.object(tizen, "_DeadlineConnection", Connection):
            result = tizen.inspect(12345)
        self.assertEqual(result["target_count"], 1)
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(result["capture"], "not tested by HTTP discovery")

    def test_inspector_refuses_redirect_before_read(self):
        class Connection:
            sock = None
            def __init__(self, *args, **kwargs): pass
            def request(self, *args): pass
            def getresponse(self): return type("Reply", (), {"status": 302, "close": lambda _: None})()
            def close(self): pass
        with patch.object(tizen, "_DeadlineConnection", Connection), self.assertRaisesRegex(ValueError, "redirects"):
            tizen.inspect(12345)

    def test_inspector_full_body_limits_and_connection_cleanup(self):
        class Connection:
            sock = None
            closed = False
            def __init__(self, *args, **kwargs): pass
            def request(self, *args): pass
            def getresponse(self):
                return type("Reply", (), {"status": 200, "read1": lambda _, n: b"x" * 65536,
                                         "close": lambda _: None})()
            def close(self): Connection.closed = True
        with patch.object(tizen, "_DeadlineConnection", Connection):
            with self.assertRaisesRegex(ValueError, "one MiB"):
                tizen.inspect(12345)
            self.assertTrue(Connection.closed)
            Connection.closed = False
            with patch.object(tizen.time, "monotonic", side_effect=[0, 6]), self.assertRaisesRegex(ValueError, "deadline"):
                tizen.inspect(12345)
            self.assertTrue(Connection.closed)

    @unittest.skipUnless(os.name == "posix", "POSIX process ownership")
    def test_sdk_failure_is_redacted_and_timeout_visible(self):
        process = MagicMock()
        process.__enter__.return_value = process
        process.communicate.return_value = ("", "token=secret 192.168.1.2 refused")
        process.returncode = 1
        with patch.object(tizen.subprocess, "Popen", return_value=process):
            with self.assertRaises(ValueError) as error:
                tizen.execute(["sdb", "version"])
            self.assertNotIn("secret", str(error.exception))
            self.assertIn("refused", str(error.exception))
        process.communicate.side_effect = tizen.subprocess.TimeoutExpired(["sdb"], 5)
        process.pid = 12345
        with patch.object(tizen.subprocess, "Popen", return_value=process), patch.object(tizen.os, "killpg") as kill:
            with self.assertRaisesRegex(ValueError, "deadline"):
                tizen.execute(["sdb", "version"], 5)
            kill.assert_called_once_with(12345, tizen.signal.SIGKILL)
            process.wait.assert_called_once()

    @unittest.skipUnless(os.name == "posix", "POSIX process ownership")
    def test_sdk_cancellation_stops_group_and_reaps_launcher(self):
        process = MagicMock()
        process.__enter__.return_value = process
        process.communicate.side_effect = KeyboardInterrupt()
        process.pid = 12345
        with patch.object(tizen.subprocess, "Popen", return_value=process) as launch, \
                patch.object(tizen.os, "killpg") as kill:
            with self.assertRaises(KeyboardInterrupt):
                tizen.execute(["sdb", "version"], 5)
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        kill.assert_called_once_with(12345, tizen.signal.SIGKILL)
        process.wait.assert_called_once()

    def test_sdk_execution_refuses_unowned_platform_before_spawn(self):
        with patch.object(tizen.os, "name", "nt"), patch.object(tizen.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(ValueError, "POSIX"):
                tizen.execute(["sdb", "version"])
            launch.assert_not_called()

    def test_inspector_total_deadline_includes_trickled_status_and_headers(self):
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        class Trickle(socketserver.BaseRequestHandler):
            prefix = b""

            def handle(self):
                self.request.recv(4096)
                try:
                    self.request.sendall(self.prefix)
                    for _ in range(300):
                        self.request.sendall(b"x")
                        time.sleep(0.04)
                except OSError:
                    pass

        for prefix in (b"HTTP/1.1 200 ", b"HTTP/1.1 200 OK\r\nX-Trickle: "):
            with self.subTest(prefix=prefix):
                Trickle.prefix = prefix
                with Server(("127.0.0.1", 0), Trickle) as server:
                    worker = threading.Thread(target=server.serve_forever, daemon=True)
                    worker.start()
                    try:
                        started = time.monotonic()
                        result = subprocess.run(
                            [sys.executable, str(ROOT / "tools/tizen.py"), "inspect",
                             "--port", str(server.server_address[1])],
                            capture_output=True, text=True, timeout=6,
                        )
                        self.assertLess(time.monotonic() - started, 6)
                        self.assertEqual(result.returncode, 2)
                        self.assertIn("deadline", result.stderr)
                    finally:
                        server.shutdown()
                        worker.join()

    @unittest.skipUnless(os.name == "posix", "POSIX process ownership")
    def test_sdk_timeout_stops_owned_descendant(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "child.pid"
            child = "import time; time.sleep(30)"
            launcher = (
                "import subprocess, sys, time; from pathlib import Path; "
                "p = subprocess.Popen([sys.executable, '-c', sys.argv[2]]); "
                "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)"
            )
            child_pid = None
            unrelated = subprocess.Popen([sys.executable, "-c", child])
            try:
                with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                    tizen.execute([sys.executable, "-c", launcher, str(marker), child], 0.5)
                self.assertTrue(marker.exists(), "Fixture must spawn before deadline")
                child_pid = int(marker.read_text())
                self.assert_process_stopped(child_pid)
                self.assertIsNone(unrelated.poll(), "Cleanup must not stop unrelated processes")
            finally:
                unrelated.kill()
                unrelated.wait()
                if child_pid is not None:
                    try:
                        os.kill(child_pid, 9)
                    except ProcessLookupError:
                        pass

    @unittest.skipUnless(os.name == "posix", "POSIX process ownership")
    def test_cli_cancellation_stops_owned_descendant(self):
        for signum in (tizen.signal.SIGINT, tizen.signal.SIGTERM):
            with self.subTest(signum=signum), tempfile.TemporaryDirectory() as temporary:
                marker = Path(temporary) / "child.pid"
                sdk = Path(temporary) / "sdk"
                sdk.write_text(
                    "#!" + sys.executable + "\n"
                    "import subprocess, sys, time\n"
                    "from pathlib import Path\n"
                    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                    f"Path({str(marker)!r}).write_text(str(p.pid))\n"
                    "time.sleep(30)\n"
                )
                sdk.chmod(0o700)
                caller = subprocess.Popen(
                    [sys.executable, str(ROOT / "tools/tizen.py"),
                     "--sdb", str(sdk), "--tizen", str(sdk), "doctor"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                child_pid = None
                try:
                    deadline = time.monotonic() + 3
                    while not marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(marker.exists(), "Fixture must spawn before cancellation")
                    child_pid = int(marker.read_text())
                    caller.send_signal(signum)
                    _, stderr = caller.communicate(timeout=3)
                    self.assertEqual(caller.returncode, 128 + signum)
                    self.assertIn("cancelled", stderr)
                    self.assert_process_stopped(child_pid)
                finally:
                    if caller.poll() is None:
                        caller.kill()
                    caller.communicate()
                    if child_pid is not None:
                        try:
                            os.kill(child_pid, 9)
                        except ProcessLookupError:
                            pass

    def test_inspector_body_deadline_after_connection_close_header(self):
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        class Trickle(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.recv(4096)
                try:
                    self.request.sendall(
                        b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 300\r\n\r\n"
                    )
                    for _ in range(300):
                        self.request.sendall(b"x")
                        time.sleep(0.04)
                except OSError:
                    pass

        with Server(("127.0.0.1", 0), Trickle) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                result = subprocess.run(
                    [sys.executable, str(ROOT / "tools/tizen.py"), "inspect",
                     "--port", str(server.server_address[1])],
                    capture_output=True, text=True, timeout=6,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("deadline", result.stderr)
            finally:
                server.shutdown()
                worker.join()

    def test_inspector_live_http_discovery_retains_safe_output(self):
        paths = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                data = ({"Browser": "Chrome/47.0.2526.69", "Protocol-Version": "1.1"}
                        if self.path == "/json/version"
                        else [{"type": "page", "title": "private title", "url": "?token=fixture"}])
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                result = tizen.inspect(server.server_address[1])
                self.assertEqual(paths, ["/json/version", "/json/list"])
                self.assertEqual(result["browser"], "Chrome/47.0.2526.69")
                self.assertEqual(result["types"], ["page"])
                self.assertNotIn("private", json.dumps(result))
                self.assertNotIn("fixture", json.dumps(result))
            finally:
                server.shutdown()
                worker.join()


if __name__ == "__main__":
    unittest.main()
