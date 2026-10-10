import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tizen", ROOT / "tools/tizen.py")
tizen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tizen)


class ToolkitTests(unittest.TestCase):
    def args(self, *arguments):
        return tizen.parser().parse_args(arguments)

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
        class Connection:
            sock = None
            def __init__(self, *args, **kwargs): self.reply = None
            def request(self, method, path):
                self.reply = Reply({"Browser": "Chrome/47", "Protocol-Version": "1.1"} if path.endswith("version")
                                   else [{"type": "page", "title": "private title", "url": "?token=secret"}])
            def getresponse(self): return self.reply
            def close(self): pass
        with patch.object(tizen.http.client, "HTTPConnection", Connection):
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
            def getresponse(self): return type("Reply", (), {"status": 302})()
            def close(self): pass
        with patch.object(tizen.http.client, "HTTPConnection", Connection), self.assertRaisesRegex(ValueError, "redirects"):
            tizen.inspect(12345)

    def test_inspector_full_body_limits_and_connection_cleanup(self):
        class Connection:
            sock = None
            closed = False
            def __init__(self, *args, **kwargs): pass
            def request(self, *args): pass
            def getresponse(self):
                return type("Reply", (), {"status": 200, "read1": lambda _, n: b"x" * 65536})()
            def close(self): Connection.closed = True
        with patch.object(tizen.http.client, "HTTPConnection", Connection):
            with self.assertRaisesRegex(ValueError, "one MiB"):
                tizen.inspect(12345)
            self.assertTrue(Connection.closed)
            Connection.closed = False
            with patch.object(tizen.time, "monotonic", side_effect=[0, 6]), self.assertRaisesRegex(ValueError, "deadline"):
                tizen.inspect(12345)
            self.assertTrue(Connection.closed)

    def test_sdk_failure_is_redacted_and_timeout_visible(self):
        reply = tizen.subprocess.CompletedProcess(["sdb"], 1, "", "token=secret 192.168.1.2 refused")
        with patch.object(tizen.subprocess, "run", return_value=reply):
            with self.assertRaises(ValueError) as error:
                tizen.execute(["sdb", "version"])
            self.assertNotIn("secret", str(error.exception))
            self.assertIn("refused", str(error.exception))
        with patch.object(tizen.subprocess, "run", side_effect=tizen.subprocess.TimeoutExpired(["sdb"], 5)):
            with self.assertRaises(tizen.subprocess.TimeoutExpired):
                tizen.execute(["sdb", "version"], 5)


if __name__ == "__main__":
    unittest.main()
