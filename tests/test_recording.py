import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch, Mock
import uuid
import ctypes
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import recording


def png(width=100, height=80):
    # Header-contract mock, not a device image or media-account interaction.
    return (b"\x89PNG\r\n\x1a\n" + struct.pack("!I", 13) + b"IHDR" +
            struct.pack("!II", width, height) + b"\x08\x06\0\0\0crc!")


class Source:
    def __init__(self):
        self.lock = threading.Lock()
        self.pixels_allowed = True
        self.streaming = False
        self.client = object()
        self.expires = time.monotonic() + 60
        self.stale = False
        self.cleanup = "snapshots released; LayerTree disabled"
        self.revision = None
        self.frames = []
        self.calls = 0
        self.reject = False
        self.entered = threading.Event()
        self.finish = None

    def operate(self, operation, data):
        assert operation == "capture" and data == {}
        with self.lock:
            self.calls += 1
            self.entered.set()
            if self.finish is not None:
                self.finish.wait(2)
            if self.reject:
                raise RuntimeError("PRIVATE transport address")
            raw = png()
            self.frames = [{"raw": raw, "width": 100, "height": 80},
                           {"raw": png(20, 20), "width": 20, "height": 20}]
            self.revision = hashlib.sha256(raw).hexdigest()
            return {"revision": self.revision}

    def disconnect(self):
        self.client = None
        self.pixels_allowed = False


class Encoder:
    def __init__(self, available=False):
        self.available = available
        self.args = None
        self.reject = False

    def summary(self):
        return {"available": self.available, "gif": self.available, "mp4": self.available,
                "webm": self.available, "webp": self.available, "png": True}

    def run(self, args, output_fd=None, limit=None, **kwargs):
        self.args = args
        if self.reject:
            raise recording.RecordingError("encoder", "Mock encoding failure")
        os.write(output_fd, b"encoded")


class RecordingTests(unittest.TestCase):
    def setUp(self):
        # A named test workspace, not the platform temporary directory.
        self.root = ROOT / (".recording-tests-" + uuid.uuid4().hex)
        self.root.mkdir(mode=0o700)
        self.recorders = []
        self.counter = 0

    def tearDown(self):
        for recorder in self.recorders:
            recorder.close()
        shutil.rmtree(self.root)

    def recorder(self, source=None, **kwargs):
        self.counter += 1
        path = self.root / f"app-layer-{kwargs.get('layer_index', 0)}-test{self.counter}"
        # Keep all test artifacts in the checkout; mock only the repository root policy.
        with patch.object(recording, "ROOT", self.root / "repo-policy-boundary"):
            result = recording.Recorder(source or Source(), path, duration=.03,
                                        encoder=kwargs.pop("encoder", Encoder()), **kwargs)
        self.recorders.append(result)
        return result

    def completed(self, **kwargs):
        result = self.recorder(**kwargs)
        result.start()
        result.thread.join(3)
        self.assertFalse(result.thread.is_alive())
        return result

    def test_genuine_selected_png_manifest_and_private_permissions(self):
        result = self.completed(layer_index=1)
        receipt = result.stop()
        self.assertEqual(receipt["state"], "stopped")
        self.assertEqual(receipt["frames"], 1)
        self.assertEqual((result.path / "frame-0000.png").read_bytes(), png(20, 20))
        manifest = json.loads((result.path / "manifest.json").read_bytes())
        self.assertEqual(manifest["frames"][0]["width"], 20)
        self.assertIn("not panel", manifest["limitations"])
        self.assertEqual(result.path.stat().st_mode & 0o777, 0o700)
        for path in result.path.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        public = json.dumps(result.status())
        self.assertNotIn(str(result.path), public)
        self.assertNotIn("sha256", public)

    def test_grant_disconnect_streaming_and_busy_rejected_before_capture(self):
        for attribute, value in (("pixels_allowed", False), ("client", None),
                                 ("streaming", True), ("expires", 0)):
            source = Source()
            setattr(source, attribute, value)
            result = self.recorder(source)
            with self.assertRaises(recording.RecordingError):
                result.start()
            self.assertEqual(source.calls, 0)
            self.assertFalse(result.path.exists())
        result = self.recorder()
        with result.source.lock:
            with self.assertRaises(recording.RecordingError):
                result.start()

    def test_overlapping_recorders_and_one_shot_rejected(self):
        source = Source()
        source.finish = threading.Event()
        first = self.recorder(source)
        first.start()
        self.assertTrue(source.entered.wait(1))
        second = self.recorder(source)
        with self.assertRaises(recording.RecordingError):
            second.start()
        with self.assertRaises(recording.RecordingError):
            first.start()
        source.finish.set()
        first.stop()
        self.assertIsNone(source._recording_owner)

    def test_capture_failure_disconnects_and_redacts_transport(self):
        source = Source()
        source.reject = True
        result = self.completed(source=source)
        self.assertEqual(result.status()["state"], "failed")
        self.assertIsNone(source.client)
        self.assertNotIn("PRIVATE", json.dumps(result.receipt()))

    def test_cleanup_failure_is_explicit(self):
        source = Source()
        source.cleanup = "snapshot release unconfirmed"
        result = self.completed(source=source)
        self.assertEqual(result.status()["state"], "failed")
        self.assertIsNone(source.client)
        self.assertEqual(result.status()["frames"], 0)

    def test_missing_layer_has_no_fallback(self):
        result = self.completed(layer_index=7)
        self.assertEqual(result.status()["frames"], 0)
        self.assertIn("absent", result.status()["error"])

    def test_total_frame_and_byte_bounds(self):
        with patch.object(recording, "MAX_FRAMES", 1):
            result = self.completed()
            self.assertEqual(result.status()["frames"], 1)
        with patch.object(recording, "MAX_BYTES", 65536):
            result = self.completed()
            self.assertEqual(result.status()["state"], "failed")
            self.assertEqual(result.status()["frames"], 0)
            self.assertLessEqual(result.bytes, 65536)

    def test_lease_budget_is_not_extended(self):
        source = Source()
        source.expires = time.monotonic() + .01
        expiry = source.expires
        result = self.completed(source=source)
        self.assertEqual(source.expires, expiry)
        self.assertLessEqual(source.calls, 1)

    def test_stop_does_not_leave_a_capture_thread(self):
        source = Source()
        source.finish = threading.Event()
        result = self.recorder(source)
        result.start()
        self.assertTrue(source.entered.wait(1))
        source.finish.set()
        receipt = result.stop()
        self.assertFalse(result.thread.is_alive())
        self.assertIn(receipt["state"], {"stopped", "failed"})

    def test_no_encoder_png_still_and_missing_encoder_explanation(self):
        result = self.completed()
        self.assertEqual(len(result.export("png", still=True)["paths"]), 1)
        with self.assertRaises(recording.RecordingError):
            result.export("gif")
        with patch.object(recording.shutil, "which", return_value=None):
            encoder = recording.Encoder()
        self.assertTrue(encoder.summary()["png"])
        self.assertIn("permission", encoder.summary()["setup"])

    def test_gif_palette_and_size_controls_no_overwrite(self):
        encoder = Encoder(True)
        result = self.completed(encoder=encoder)
        receipt = result.export("gif")
        self.assertIn(str(result.path / "animation.gif"), receipt["paths"])
        graph = encoder.args[encoder.args.index("-filter_complex") + 1]
        self.assertIn("palettegen", graph)
        self.assertIn("paletteuse", graph)
        self.assertIn("scale=960:540", graph)
        self.assertEqual(encoder.args[-1], "pipe:1")
        with self.assertRaises((recording.RecordingError, FileExistsError)):
            result.export("gif")

    def test_encoding_failures_remove_only_partial_owned_file(self):
        encoder = Encoder(True)
        encoder.reject = True
        result = self.completed(encoder=encoder)
        with self.assertRaises(recording.RecordingError):
            result.export("mp4")
        self.assertFalse((result.path / "animation.mp4").exists())
        self.assertTrue((result.path / "frame-0000.png").exists())

    def test_mp4_webm_webp_still_explicit_encoders(self):
        encoder = Encoder(True)
        result = self.completed(encoder=encoder)
        for format, codec in (("mp4", "libx264"), ("webm", "libvpx-vp9"), ("webp", "libwebp")):
            result.export(format, still=format == "webp")
            self.assertIn(codec, encoder.args)
        with self.assertRaises(recording.RecordingError):
            result.export("webp")

    def test_invalid_settings_and_output_paths(self):
        for kwargs in ({"fps": float("nan")}, {"fps": True}, {"duration": 121},
                       {"size": (1281, 720)}, {"layer_index": -1}):
            with self.assertRaises(recording.RecordingError):
                recording.Recorder(Source(), self.root / "app-layer-0-settings",
                                   encoder=Encoder(), **kwargs)
        for path in ("relative/app-layer-0-name", str(ROOT / "app-layer-0-name"),
                     str(self.root / "wrong-label")):
            with self.assertRaises(recording.RecordingError):
                recording.Recorder(Source(), path, encoder=Encoder())

    def test_symlink_parent_and_existing_folder_refused(self):
        link = self.root / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with patch.object(recording, "ROOT", self.root / "repo-policy-boundary"):
            result = recording.Recorder(Source(), link / "app-layer-0-link", encoder=Encoder())
        self.recorders.append(result)
        with self.assertRaises(recording.RecordingError):
            result.start()
        result = self.recorder()
        result.path.mkdir()
        with self.assertRaises(recording.RecordingError):
            result.start()

    def test_secure_encoder_input_cannot_follow_a_frame_symlink(self):
        result = self.completed(encoder=Encoder(True))
        frame = result.path / "frame-0000.png"
        frame.unlink()
        frame.symlink_to(result.path / "manifest.json")
        with self.assertRaises(recording.RecordingError):
            result.export("gif")
        with self.assertRaises(recording.RecordingError):
            result.export("png")

    def test_stop_timeout_is_explicit_and_keeps_source_owner(self):
        result = self.completed()
        original = result.thread
        result.thread = Mock()
        result.thread.is_alive.return_value = True
        result.source._recording_owner = result
        try:
            with self.assertRaises(recording.RecordingError) as caught:
                result.stop()
            self.assertEqual(caught.exception.code, "cleanup")
            result.thread.join.assert_called_once_with(recording.JOIN_SECONDS)
            self.assertIs(result.source._recording_owner, result)
            with self.assertRaises(recording.RecordingError):
                result.export("png")
        finally:
            result.thread = original
            result.source._recording_owner = None

    def test_changed_dimensions_and_invalid_png_stop_capture(self):
        for bad in (b"not png", png(101, 80)):
            source = Source()
            operate = source.operate
            def corrupt(operation, data):
                result = operate(operation, data)
                with source.lock:
                    source.frames[0]["raw"] = bad
                return result
            source.operate = corrupt
            result = self.completed(source=source)
            self.assertEqual(result.status()["state"], "failed")
            self.assertEqual(result.status()["frames"], 0)

    def test_encoder_subprocess_is_shell_free_and_output_bounded(self):
        with patch.object(recording.shutil, "which", return_value=None):
            encoder = recording.Encoder()
        encoder.path = "/installed/ffmpeg"
        process = Mock()
        process.stdout = io.BytesIO(b"12345")
        process.returncode = 0
        with patch.object(recording.subprocess, "Popen", return_value=process) as spawn:
            self.assertEqual(encoder.run(["-encoders"]), b"12345")
            self.assertFalse(spawn.call_args.kwargs["shell"])
            self.assertEqual(spawn.call_args.args[0][0], "/installed/ffmpeg")
        process.stdout = io.BytesIO(b"12345")
        with patch.object(recording.subprocess, "Popen", return_value=process):
            with self.assertRaises(recording.RecordingError):
                encoder.run(["-encoders"], limit=4)
        process.kill.assert_called()

    def test_encoder_timeout_kills_only_owned_process(self):
        with patch.object(recording.shutil, "which", return_value=None):
            encoder = recording.Encoder()
        encoder.path = "/installed/ffmpeg"
        process = Mock()
        process.stdout = io.BytesIO(b"")
        process.wait.side_effect = [subprocess.TimeoutExpired("ffmpeg", 5), 0]
        with patch.object(recording.subprocess, "Popen", return_value=process):
            with self.assertRaises(recording.RecordingError) as caught:
                encoder.run(["-encoders"])
        self.assertEqual(caught.exception.code, "timeout")
        process.kill.assert_called_once()

    def test_tampered_and_renamed_output_refused(self):
        result = self.completed(encoder=Encoder(True))
        (result.path / "frame-0000.png").write_bytes(png(20, 20))
        with self.assertRaises(recording.RecordingError):
            result.export("gif")
        moved = result.path.with_name(result.path.name + "-moved")
        result.path.rename(moved)
        result.path.mkdir()
        with self.assertRaises(recording.RecordingError):
            result.export("gif")

    def test_encoder_names_probe(self):
        self.assertEqual(recording.Encoder._names(b" V....D libx264 description\n", "encoder"), {"libx264"})
        self.assertEqual(recording.Encoder._names(b" ... palettegen V->V\n", "filter"), {"palettegen"})

    def test_installed_new_ffmpeg_filter_flags_and_hyphenated_encoder(self):
        self.assertEqual(recording.Encoder._names(b" .. paletteuse VV->V\n", "filter"), {"paletteuse"})
        self.assertEqual(recording.Encoder._names(b" V....D libvpx-vp9 description\n", "encoder"),
                         {"libvpx-vp9"})

    def test_windows_backend_dispatch_is_fail_closed_before_capture(self):
        result = self.recorder()
        with patch.object(recording.os, "name", "nt"), patch.object(
                recording, "WindowsDirectory", side_effect=recording.RecordingError(
                    "output", "Windows ACL failure")):
            with self.assertRaises(recording.RecordingError):
                result.start()
        self.assertEqual(result.source.calls, 0)
        self.assertIsNone(result.directory_fd)
        self.assertNotIn("output_path", result.status())

    def test_windows_backend_file_read_write_close_delegation(self):
        result = self.completed()
        backend = Mock()
        result.windows_directory = backend
        try:
            backend.file.return_value = 123
            self.assertEqual(result._file("still.png"), 123)
            backend.file.assert_called_with("still.png")
            result._unlink("still.png")
            backend.unlink.assert_called_once_with("still.png")
        finally:
            result.windows_directory = None
        result.close()
        result.windows_directory = backend
        result.directory_fd = -1
        result._close_directory()
        backend.close.assert_called_once()
        self.assertIsNone(result.directory_fd)

    def test_windows_sid_and_acl_tool_failure_close_handles(self):
        for responses in ([b"unrecognized user output"],
                          [b'"mock-user","S-1-5-21-100-200-300-1001"',
                           recording.RecordingError("output", "ACL denied")]):
            backend = Mock()
            with patch.object(ctypes, "WinDLL", return_value=backend, create=True), \
                    patch.object(recording.WindowsDirectory, "_configure"), \
                    patch.object(recording.WindowsDirectory, "_system_directory", return_value=self.root), \
                    patch.object(recording.WindowsDirectory, "_pin_parents"), \
                    patch.object(recording.WindowsDirectory, "_create"), \
                    patch.object(recording.WindowsDirectory, "_pin", return_value=1), \
                    patch.object(recording.WindowsDirectory, "_identity", return_value=(16, 1, 0, 2)), \
                    patch.object(recording.WindowsDirectory, "_verify_acl"), \
                    patch.object(recording, "_windows_command", side_effect=responses) as command:
                with self.assertRaises(recording.RecordingError):
                    recording.WindowsDirectory(self.root / "app-layer-0-winmock")
                self.assertIn("whoami.exe", command.call_args_list[0].args[0][0])
                if len(responses) == 2:
                    args = command.call_args_list[1].args[0]
                    self.assertIn("/inheritance:r", args)
                    self.assertIn("*S-1-5-21-100-200-300-1001:(OI)(CI)F", args)

    def test_windows_native_acl_verification_denies_other_owner_and_broad_acl(self):
        backend = recording.WindowsDirectory.__new__(recording.WindowsDirectory)
        backend.ctypes = ctypes
        backend.wintypes = SimpleNamespace(WORD=ctypes.c_uint16, DWORD=ctypes.c_uint32)
        backend.sid = "S-1-5-21-1001"
        backend.kernel = Mock()
        backend.advapi = Mock()
        acl = ctypes.create_string_buffer(b"\x02\x00\x08\x00\x01\x00\x00\x00")
        ace = ctypes.create_string_buffer(b"\x00\x03\x20\x00\xff\x01\x1f\x00" + b"\0" * 24)
        def security(handle, kind, flags, owner, group, dacl, sacl, descriptor):
            ctypes.cast(owner, ctypes.POINTER(ctypes.c_void_p))[0] = 1
            ctypes.cast(dacl, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(acl)
            ctypes.cast(descriptor, ctypes.POINTER(ctypes.c_void_p))[0] = 2
            return 0
        def control(descriptor, bits, revision):
            ctypes.cast(bits, ctypes.POINTER(ctypes.c_uint16))[0] = 0x1000
            return True
        def getace(pointer, index, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(ace)
            return True
        backend.advapi.GetSecurityInfo.side_effect = security
        backend.advapi.GetSecurityDescriptorControl.side_effect = control
        backend.advapi.GetAce.side_effect = getace
        backend._sid_text = Mock(return_value=backend.sid)
        backend._verify_acl(1, protected=True)
        backend._sid_text.return_value = "S-1-5-OTHER"
        with self.assertRaises(recording.RecordingError):
            backend._verify_acl(1, protected=True)
        backend._sid_text.return_value = backend.sid
        acl[4] = b"\x02"
        with self.assertRaises(recording.RecordingError):
            backend._verify_acl(1, protected=True)
        acl[4] = b"\x01"
        backend.advapi.GetSecurityDescriptorControl.side_effect = None
        with self.assertRaises(recording.RecordingError):
            backend._verify_acl(1, protected=True)

    def test_windows_reparse_parent_and_identity_refused(self):
        backend = recording.WindowsDirectory.__new__(recording.WindowsDirectory)
        backend.handles = []
        backend.ctypes = ctypes
        backend.kernel = Mock()
        backend.kernel.CreateFileW.return_value = 1
        backend._identity = Mock(return_value=(0x410, 1, 0, 2))
        with self.assertRaises(recording.RecordingError):
            backend._pin(self.root)
        backend.identity = (16, 1, 0, 2)
        backend._identity.return_value = (16, 1, 0, 3)
        with self.assertRaises(recording.RecordingError):
            backend.check()
        backend.close()
        backend.kernel.CloseHandle.assert_called_once_with(1)

    def test_windows_acl_command_timeout_and_output_are_bounded_private(self):
        process = Mock()
        process.stdout = io.BytesIO(b"PRIVATE" * 3000)
        process.returncode = 0
        with patch.object(recording.subprocess, "Popen", return_value=process) as spawn:
            with self.assertRaises(recording.RecordingError) as caught:
                recording._windows_command(["system/whoami.exe", "/user"])
            self.assertNotIn("PRIVATE", str(caught.exception))
            self.assertFalse(spawn.call_args.kwargs["shell"])
        process.kill.assert_called_once()
        process.stdout = io.BytesIO(b"")
        process.wait.side_effect = [subprocess.TimeoutExpired("whoami", 5), 0]
        with patch.object(recording.subprocess, "Popen", return_value=process):
            with self.assertRaises(recording.RecordingError) as caught:
                recording._windows_command(["system/whoami.exe", "/user"])
        self.assertIn("timed out", str(caught.exception))

    def test_export_failure_closes_owned_file_before_windows_style_unlink(self):
        encoder = Encoder(True)
        encoder.reject = True
        result = self.completed(encoder=encoder)
        original_file, original_unlink = result._file, result._unlink
        opened = []
        def file(name):
            fd = original_file(name)
            opened.append(fd)
            return fd
        def unlink(name):
            with self.assertRaises(OSError):
                os.fstat(opened[-1])
            original_unlink(name)
        result._file, result._unlink = file, unlink
        with self.assertRaises(recording.RecordingError):
            result.export("gif")


if __name__ == "__main__":
    unittest.main()
