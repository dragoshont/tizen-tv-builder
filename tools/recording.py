"""Bounded, private, discrete native paint-layer recording; no device CLI."""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import threading
import time
import stat


def _windows_command(args):
    """Only fixed Windows system executables, bounded output; never print it."""
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, shell=False)
    data = bytearray()
    overflow = []
    def drain():
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                return
            if len(data) + len(chunk) > 16384:
                overflow.append(True)
                process.kill()
                return
            data.extend(chunk)
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        fail("output", "Windows private-output ACL command timed out")
    finally:
        reader.join(5)
        process.stdout.close()
    if reader.is_alive() or overflow or process.returncode:
        fail("output", "Windows private-output ACL command failed")
    return bytes(data)


class WindowsDirectory:
    """Protected creation DACL, pinned non-reparse ancestors and verified ACLs."""

    def __init__(self, path):
        self.path = path
        self.handles = []
        self.identity = None
        self.sid = None
        try:
            import ctypes
            from ctypes import wintypes
            self.ctypes = ctypes
            self.wintypes = wintypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
            self._configure()
            system = self._system_directory()
            # Never resolve whoami/icacls from a project or user-controlled PATH.
            observed = _windows_command([str(system / "whoami.exe"), "/user", "/fo", "csv", "/nh"])
            matches = re.findall(rb"S-1-\d+(?:-\d+)+", observed)
            if len(matches) != 1:
                fail("output", "Windows current-user SID could not be verified")
            self.sid = matches[0].decode("ascii")
            self._pin_parents()
            self._create()
            handle = self._pin(path)
            self.identity = self._identity(handle)
            self._verify_acl(handle, protected=True)
            _windows_command([str(system / "icacls.exe"), str(path),
                              "/inheritance:r", "/grant:r", f"*{self.sid}:(OI)(CI)F", "/q"])
            self._verify_acl(handle, protected=True)
        except RecordingError:
            self.close()
            raise
        except Exception:
            self.close()
            fail("output", "Windows private-output API unavailable or ownership/ACL verification failed")

    def _configure(self):
        c, w = self.ctypes, self.wintypes
        signatures = [
            (self.kernel, "CreateFileW", w.HANDLE,
             [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]),
            (self.kernel, "CloseHandle", w.BOOL, [w.HANDLE]),
            (self.kernel, "LocalFree", c.c_void_p, [c.c_void_p]),
            (self.kernel, "CreateDirectoryW", w.BOOL, [w.LPCWSTR, c.c_void_p]),
            (self.kernel, "GetDriveTypeW", w.UINT, [w.LPCWSTR]),
            (self.kernel, "GetSystemDirectoryW", w.UINT, [w.LPWSTR, w.UINT]),
            (self.kernel, "GetFileInformationByHandle", w.BOOL, [w.HANDLE, c.c_void_p]),
            (self.advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW", w.BOOL,
             [w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.c_void_p]),
            (self.advapi, "GetSecurityInfo", w.DWORD,
             [w.HANDLE, w.DWORD, w.DWORD, c.POINTER(c.c_void_p), c.c_void_p,
              c.POINTER(c.c_void_p), c.c_void_p, c.POINTER(c.c_void_p)]),
            (self.advapi, "ConvertSidToStringSidW", w.BOOL,
             [c.c_void_p, c.POINTER(c.c_void_p)]),
            (self.advapi, "GetSecurityDescriptorControl", w.BOOL,
             [c.c_void_p, c.POINTER(w.WORD), c.POINTER(w.DWORD)]),
            (self.advapi, "GetAce", w.BOOL, [c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)]),
        ]
        for library, name, restype, argtypes in signatures:
            function = getattr(library, name)
            function.restype, function.argtypes = restype, argtypes

    def _system_directory(self):
        buffer = self.ctypes.create_unicode_buffer(32768)
        length = self.kernel.GetSystemDirectoryW(buffer, len(buffer))
        if not 0 < length < len(buffer):
            fail("output", "Windows trusted system directory could not be located")
        return Path(buffer.value)

    def _pin(self, path):
        # READ_CONTROL + FILE_READ_ATTRIBUTES; deny delete/rename and writes.
        handle = self.kernel.CreateFileW(str(path), 0x20080, 1, None, 3, 0x02200000, None)
        if handle in (None, self.ctypes.c_void_p(-1).value):
            fail("output", "Windows output parent cannot be pinned securely")
        self.handles.append(handle)
        information = self._identity(handle)
        if information[0] & 0x400:
            fail("output", "Windows output cannot traverse a reparse point")
        return handle

    def _identity(self, handle):
        # BY_HANDLE_FILE_INFORMATION: 13 DWORDs, including attributes/volume/file ID.
        words = (self.wintypes.DWORD * 13)()
        if not self.kernel.GetFileInformationByHandle(handle, self.ctypes.byref(words)):
            fail("output", "Windows output identity verification failed")
        return words[0], words[7], words[11], words[12]

    def _pin_parents(self):
        if not self.path.drive or str(self.path).startswith(("\\\\", "\\\\?\\")):
            fail("output", "Windows recording requires a local absolute drive path, not UNC/device paths")
        current = Path(self.path.anchor)
        if self.kernel.GetDriveTypeW(str(current)) != 3:
            fail("output", "Windows recording requires a fixed local ACL-capable drive")
        self._pin(current)
        for part in self.path.parent.parts[1:]:
            current /= part
            handle = self._pin(current)
            if not self._identity(handle)[0] & 0x10:
                fail("output", "Windows output parent is not a directory")

    def _create(self):
        c, w = self.ctypes, self.wintypes
        descriptor = c.c_void_p()
        sddl = f"O:{self.sid}D:P(A;OICI;FA;;;{self.sid})"
        if not self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, c.byref(descriptor), None):
            fail("output", "Windows private creation ACL could not be built")
        class SecurityAttributes(c.Structure):
            _fields_ = [("length", w.DWORD), ("descriptor", c.c_void_p), ("inherit", w.BOOL)]
        attributes = SecurityAttributes(c.sizeof(SecurityAttributes), descriptor, False)
        try:
            if not self.kernel.CreateDirectoryW(str(self.path), c.byref(attributes)):
                fail("output", "Windows output folder must be new; private exclusive creation failed")
        finally:
            self.kernel.LocalFree(descriptor)

    def _sid_text(self, pointer):
        text = self.ctypes.c_void_p()
        if not self.advapi.ConvertSidToStringSidW(pointer, self.ctypes.byref(text)):
            fail("output", "Windows ACL SID verification failed")
        try:
            return self.ctypes.wstring_at(text)
        finally:
            self.kernel.LocalFree(text)

    def _verify_acl(self, handle, protected=False):
        c, w = self.ctypes, self.wintypes
        owner, acl, descriptor = c.c_void_p(), c.c_void_p(), c.c_void_p()
        if self.advapi.GetSecurityInfo(handle, 1, 5, c.byref(owner), None,
                                       c.byref(acl), None, c.byref(descriptor)):
            fail("output", "Windows output owner/DACL could not be read")
        try:
            if self._sid_text(owner) != self.sid or not acl.value:
                fail("output", "Windows output ownership or private DACL mismatch")
            control, revision = w.WORD(), w.DWORD()
            if not self.advapi.GetSecurityDescriptorControl(descriptor, c.byref(control), c.byref(revision)):
                fail("output", "Windows ACL protection could not be checked")
            if protected and not control.value & 0x1000:
                fail("output", "Windows folder ACL inheritance was not removed")
            # ACL header: revision, padding, size, ACE count, padding.
            count = c.cast(acl, c.POINTER(w.WORD))[2]
            if count != 1:
                fail("output", "Windows output DACL is not current-user-only")
            ace = c.c_void_p()
            if not self.advapi.GetAce(acl, 0, c.byref(ace)):
                fail("output", "Windows output ACL entry could not be checked")
            header = c.string_at(ace, 8)
            mask = struct.unpack("<I", header[4:8])[0]
            if (header[0] != 0 or mask != 0x1F01FF
                    or (protected and header[1] & 3 != 3)
                    or self._sid_text(c.c_void_p(ace.value + 8)) != self.sid):
                fail("output", "Windows private full-control ACL entry mismatch")
        finally:
            self.kernel.LocalFree(descriptor)

    def check(self):
        handle = self.handles[-1]
        if self._identity(handle) != self.identity:
            fail("output", "Windows output directory identity changed")
        info = self.path.lstat()
        expected_inode = (self.identity[2] << 32) | self.identity[3]
        if info.st_file_attributes & 0x400 or info.st_ino != expected_inode:
            fail("output", "Windows output pathname identity or reparse status changed")
        self._verify_acl(handle, protected=True)

    def file(self, name, read=False):
        self.check()
        path = self.path / name
        if read:
            info = path.lstat()
            if info.st_file_attributes & 0x400 or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                fail("output", "Windows native frame is not an ordinary private file")
        stream = open(path, "rb" if read else "xb")
        try:
            import msvcrt
            handle = msvcrt.get_osfhandle(stream.fileno())
            if self._identity(handle)[0] & 0x400:
                fail("output", "Windows recording file is a reparse point")
            self._verify_acl(handle)
            identity = os.fstat(stream.fileno())
            named = path.lstat()
            if (identity.st_ino, identity.st_dev) != (named.st_ino, named.st_dev) or identity.st_nlink != 1:
                fail("output", "Windows recording file identity mismatch")
            return os.dup(stream.fileno())
        finally:
            stream.close()

    def unlink(self, name):
        self.check()
        path = self.path / name
        if path.lstat().st_file_attributes & 0x400:
            fail("output", "Windows cleanup refused a reparse file")
        path.unlink()

    def close(self):
        for handle in reversed(self.handles):
            self.kernel.CloseHandle(handle)
        self.handles = []


ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 32 * 1024 * 1024
MAX_FRAMES = 120
JOIN_SECONDS = 35


class RecordingError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def fail(code, message):
    raise RecordingError(code, message)


class Encoder:
    """An optional already-installed executable, never a bundled dependency."""

    def __init__(self):
        self.path = shutil.which("ffmpeg")
        self.encoders = set()
        self.filters = set()
        self.error = None
        if self.path:
            try:
                self.encoders = self._names(self.run(["-encoders"]), "encoder")
                self.filters = self._names(self.run(["-filters"]), "filter")
            except (RecordingError, OSError):
                self.error = "Installed ffmpeg capability probe failed"

    @staticmethod
    def _names(raw, kind):
        pattern = (r"^\s*[A-Z.]{6}\s+([\w-]+)\s" if kind == "encoder" else
                   r"^\s*[A-Z.]{2,3}\s+([\w-]+)\s")
        return set(re.findall(pattern, raw.decode("utf-8", "replace"), re.MULTILINE))

    def summary(self):
        return {"available": bool(self.path) and not self.error,
                "gif": "gif" in self.encoders and {"palettegen", "paletteuse"} <= self.filters,
                "mp4": "libx264" in self.encoders,
                "webm": "libvpx-vp9" in self.encoders,
                "webp": "libwebp" in self.encoders,
                "png": True,
                "setup": self.error or (None if self.path else
                    "ffmpeg is not installed on PATH. PNG sequence/still works without it. "
                    "Obtain the computer owner's permission to install ffmpeg for other exports.")}

    def run(self, args, output_fd=None, limit=256 * 1024, input_data=None):
        if not self.path:
            fail("encoder", "ffmpeg is missing; use PNG or obtain installation permission")
        process = subprocess.Popen(
            [self.path, "-hide_banner", "-nostdin", "-loglevel", "error", *args],
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            shell=False)
        data = bytearray()
        errors = []

        def drain():
            total = 0
            try:
                while True:
                    chunk = process.stdout.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        fail("limit", "Encoder output exceeded the recording byte budget")
                    if output_fd is None:
                        data.extend(chunk)
                    else:
                        view = memoryview(chunk)
                        while view:
                            written = os.write(output_fd, view)
                            view = view[written:]
            except Exception:
                errors.append(True)
                process.kill()

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        writer = None
        if input_data is not None:
            def feed():
                try:
                    process.stdin.write(input_data)
                except (OSError, ValueError):
                    errors.append(True)
                finally:
                    process.stdin.close()
            writer = threading.Thread(target=feed, daemon=True)
            writer.start()
        try:
            process.wait(timeout=40 if output_fd is not None else 5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            fail("timeout", "Encoder exceeded its bounded execution deadline")
        finally:
            reader.join(5)
            process.stdout.close()
            if writer:
                writer.join(5)
        if reader.is_alive() or (writer and writer.is_alive()) or errors or process.returncode:
            fail("encoder", "Encoder failed or exceeded its output budget; PNG frames remain available")
        return bytes(data)


class Recorder:
    """One-shot recorder. output_path is a new app-layer-N-* directory."""

    def __init__(self, source, output_path, fps=2, size=(960, 540), duration=30,
                 layer_index=0, *, encoder=None):
        if (type(fps) not in (int, float) or not math.isfinite(fps) or not 0 < fps <= 15
                or type(duration) not in (int, float) or not math.isfinite(duration)
                or not 0 < duration <= 120):
            fail("invalid", "fps must be above zero through 15; duration above zero through 120 seconds")
        if (not isinstance(size, (tuple, list)) or len(size) != 2
                or any(type(v) is not int for v in size)
                or not 2 <= size[0] <= 1280 or not 2 <= size[1] <= 720
                or type(layer_index) is not int or not 0 <= layer_index < 8):
            fail("invalid", "size must be 2..1280 by 2..720; layer_index must be 0..7")
        self.source = source
        self.path = Path(output_path)
        if (not self.path.is_absolute() or ".." in self.path.parts
                or not re.fullmatch(rf"app-layer-{layer_index}-[A-Za-z0-9_-]{{1,64}}", self.path.name)
                or self.path.resolve().is_relative_to(ROOT)):
            fail("output", "Choose an absolute new app-layer-N-name folder outside the toolkit repository")
        self.fps, self.size, self.duration = fps, tuple(size), duration
        self.layer_index = layer_index
        self.encoder = encoder if encoder is not None else Encoder()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.state = "idle"
        self.error = None
        self.entries = []
        self.bytes = 0
        self.started = None
        self.ended = None
        self.directory_fd = None
        self.windows_directory = None
        self.exports = []
        self.export_lock = threading.Lock()

    def status(self):
        with self.lock:
            end = self.ended or time.monotonic()
            return {"fps": self.fps, "size": list(self.size), "duration": self.duration,
                    "length": round(end - self.started, 3) if self.started is not None else 0,
                    "frames": len(self.entries), "state": self.state, "error": self.error,
                    "layer_index": self.layer_index, "encoder": self.encoder.summary()}

    def _open_directory(self):
        if os.name == "nt":
            self.windows_directory = WindowsDirectory(self.path)
            # The descriptor sentinel keeps receipts/export lifetimes uniform;
            # actual Windows handles are owned exclusively by WindowsDirectory.
            return -1
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            fail("output", "Secure recording output requires Windows or POSIX filesystem APIs")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in self.path.parent.parts[1:]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            os.mkdir(self.path.name, 0o700, dir_fd=fd)
            result = os.open(self.path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.fchmod(result, 0o700)
            return result
        finally:
            os.close(fd)

    def _file(self, name):
        if self.windows_directory is not None:
            return self.windows_directory.file(name)
        return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                       0o600, dir_fd=self.directory_fd)

    def _unlink(self, name):
        if self.windows_directory is not None:
            self.windows_directory.unlink(name)
        else:
            os.unlink(name, dir_fd=self.directory_fd)

    def _close_directory(self):
        if self.windows_directory is not None:
            self.windows_directory.close()
            self.windows_directory = None
        elif self.directory_fd is not None:
            os.close(self.directory_fd)
        self.directory_fd = None

    def _write(self, name, raw):
        if self.bytes + len(raw) > MAX_BYTES:
            fail("limit", "Recording total byte budget exceeded")
        fd = self._file(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
        except Exception:
            self._unlink(name)
            raise
        self.bytes += len(raw)

    def _grant(self):
        if not self.source.pixels_allowed:
            fail("grant", "Recording requires the source's separate private-pixel grant")
        if not self.source.client or self.source.expires <= time.monotonic():
            fail("disconnected", "Source disconnected or its existing lease expired")
        if self.source.streaming:
            fail("busy", "Pause native preview streaming before recording; no capture queue")

    def start(self):
        with self.lock:
            if self.state != "idle":
                fail("busy", "Recorder is one-shot; stop it before creating another")
            if not self.source.lock.acquire(blocking=False):
                fail("busy", "Source command is in flight; recording is not queued")
            try:
                self._grant()
                if getattr(self.source, "_recording_owner", None) is not None:
                    fail("busy", "Another recorder owns this source")
                self.directory_fd = self._open_directory()
                self.source._recording_owner = self
                self.started = time.monotonic()
                self.state = "recording"
                self.thread = threading.Thread(target=self._record, daemon=True)
            except OSError:
                fail("output", "Output parent must exist without symlinks and folder must be new")
            finally:
                self.source.lock.release()
            try:
                self.thread.start()
            except Exception:
                self.source._recording_owner = None
                self._close_directory()
                self.thread = None
                self.state, self.error = "failed", "Recording worker could not start"
                fail("cleanup", self.error)
        return self.status()

    def _capture(self):
        if not self.source.lock.acquire(blocking=False):
            fail("busy", "Source command overlapped recording; no retry or queue")
        try:
            self._grant()
        finally:
            self.source.lock.release()
        result = self.source.operate("capture", {})
        if not self.source.lock.acquire(blocking=False):
            fail("busy", "Source changed before the captured frame could be copied")
        try:
            self._grant()
            if (self.source.stale or not isinstance(result, dict)
                    or result.get("revision") != self.source.revision):
                fail("capture", "Native frame changed before copy; no stale fallback")
            if self.layer_index >= len(self.source.frames):
                fail("capture", "Selected native layer is absent; no composite or replacement")
            frame = self.source.frames[self.layer_index]
            if (not isinstance(frame.get("raw"), (bytes, bytearray, memoryview))
                    or not 33 <= len(frame["raw"]) <= 8 * 1024 * 1024):
                fail("capture", "Native frame is not a bounded PNG")
            raw = memoryview(frame["raw"]).tobytes()
            if (len(raw) < 33 or len(raw) > 8 * 1024 * 1024
                    or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR"):
                fail("capture", "Native frame is not a bounded PNG")
            width, height = struct.unpack("!II", raw[16:24])
            if (width != frame["width"] or height != frame["height"]
                    or not 1 <= width <= 8192 or not 1 <= height <= 8192):
                fail("capture", "Native frame dimensions are invalid")
            if self.entries and (width, height) != (self.entries[0]["width"], self.entries[0]["height"]):
                fail("capture", "Selected positional layer dimensions changed; recording stopped")
            if "unconfirmed" in self.source.cleanup:
                fail("cleanup", "Native capture cleanup was not confirmed")
            return raw, width, height
        finally:
            self.source.lock.release()

    def _record(self):
        try:
            deadline = min(self.started + self.duration, self.source.expires)
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                raw, width, height = self._capture()
                with self.lock:
                    # Reserve manifest room; never partially admit an over-budget frame.
                    if self.bytes + len(raw) > MAX_BYTES - 65536:
                        fail("limit", "Recording reached its 32-MiB total budget")
                    name = f"frame-{len(self.entries):04d}.png"
                    self._write(name, raw)
                    self.entries.append({"file": name, "elapsed": round(time.monotonic() - self.started, 6),
                                         "width": width, "height": height,
                                         "sha256": hashlib.sha256(raw).hexdigest()})
                    if len(self.entries) >= MAX_FRAMES:
                        break
                # Capture latency is never compensated by a burst or parallel work.
                if self.stop_event.wait(min(1 / self.fps, max(0, deadline - time.monotonic()))):
                    break
            if not self.entries:
                fail("capture", "Recording stopped without a native frame")
        except Exception as error:
            with self.lock:
                self.state = "failed"
                self.error = (str(error) if isinstance(error, RecordingError) else
                              "Native capture failed; source or transport cleanup requires attention")
            # Disconnect only this Source; never touch shared servers or forwards.
            if getattr(error, "code", None) not in {"busy", "grant", "limit"}:
                if self.source.lock.acquire(timeout=3):
                    try:
                        self.source.disconnect()
                    except Exception:
                        with self.lock:
                            self.error = "Native capture failed; source disconnect cleanup unconfirmed"
                    finally:
                        self.source.lock.release()
                else:
                    with self.lock:
                        self.error = "Native capture failed; source disconnect lock unavailable"
        finally:
            with self.lock:
                self.ended = time.monotonic()
                if self.state == "recording":
                    self.state = "stopped"
                try:
                    manifest = {"kind": "discrete native renderer paint-layer replay",
                                "limitations": "One positional layer only; not panel, compositor, AVPlay, HDR or audio",
                                "timing": "elapsed is actual capture completion; encoded playback uses nominal fps",
                                "layer_index": self.layer_index, "fps": self.fps,
                                "size": list(self.size), "state": self.state, "frames": self.entries}
                    self._write("manifest.json", json.dumps(manifest, indent=2).encode())
                except Exception:
                    self.state, self.error = "failed", "Recording manifest write failed"
            if self.source.lock.acquire(timeout=3):
                try:
                    if getattr(self.source, "_recording_owner", None) is self:
                        self.source._recording_owner = None
                finally:
                    self.source.lock.release()
            else:
                with self.lock:
                    self.state, self.error = "failed", "Recording source ownership cleanup unconfirmed"

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(JOIN_SECONDS)
            if self.thread.is_alive():
                with self.lock:
                    self.state, self.error = "failed", "Capture thread did not stop; ownership retained, no export"
                fail("cleanup", "Capture thread cleanup unconfirmed; do not start another recorder")
        return self.receipt()

    def receipt(self):
        """Private local-browser response only, never public status or logs."""
        if self.thread and self.thread.is_alive():
            fail("busy", "Stop and join recording before requesting its private receipt")
        return {**self.status(), "output_path": str(self.path),
                "manifest": str(self.path / "manifest.json") if self.directory_fd is not None else None,
                "paths": [str(self.path / entry["file"]) for entry in self.entries] +
                         [str(self.path / name) for name in self.exports]}

    def export(self, format="png", *, still=False):
        if format not in {"png", "gif", "mp4", "webm", "webp"} or type(still) is not bool:
            fail("invalid", "Choose PNG, GIF, MP4, WebM or WebP; still must be boolean")
        if not self.export_lock.acquire(blocking=False):
            fail("busy", "An export is already in flight; no queue")
        try:
            if self.thread is None or self.thread.is_alive() or not self.entries:
                fail("busy", "Stop a recording with genuine frames before export")
            if self.directory_fd is None:
                fail("output", "Recording is closed")
            try:
                if self.windows_directory is not None:
                    self.windows_directory.check()
                else:
                    check = self._checked_directory()
                    os.close(check)
                input_data = self._input()
            except (OSError, ValueError):
                fail("output", "Output identity or native frame integrity changed; export refused")
            if format == "png":
                receipt = self.receipt()
                if still:
                    receipt["paths"] = [str(self.path / self.entries[0]["file"])]
                return receipt
            if format == "webp" and not still:
                fail("encoder", "WebP supports a still only; use PNG for a sequence")
            if not self.encoder.summary().get(format):
                fail("encoder", f"Installed ffmpeg lacks required {format} encoder/filters; PNG is available")
            name = ("still" if still else "animation") + "." + format
            try:
                fd = self._file(name)
            except OSError:
                fail("output", "Export file must be new; output is never overwritten")
            try:
                scale = (f"scale={self.size[0]}:{self.size[1]}:force_original_aspect_ratio=decrease"
                         ":force_divisible_by=2:flags=lanczos")
                args = ["-threads", "1", "-filter_threads", "1", "-filter_complex_threads", "1",
                        "-framerate", str(self.fps), "-f", "image2pipe",
                        "-i", "pipe:0", "-an",
                        "-frames:v", str(1 if still else len(self.entries))]
                if format == "gif":
                    args += ["-filter_complex", scale + ",split[a][b];[a]palettegen=stats_mode=diff[p];"
                             "[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
                             "-loop", "0", "-f", "gif"]
                elif format == "mp4":
                    args += ["-vf", scale, "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                             "-pix_fmt", "yuv420p", "-movflags", "frag_keyframe+empty_moov", "-f", "mp4"]
                elif format == "webm":
                    args += ["-vf", scale, "-c:v", "libvpx-vp9", "-deadline", "realtime",
                             "-cpu-used", "6", "-crf", "36", "-b:v", "0", "-f", "webm"]
                else:
                    args += ["-vf", scale, "-c:v", "libwebp", "-quality", "80", "-f", "image2pipe"]
                args += ["pipe:1"]
                self.encoder.run(args, output_fd=fd, limit=MAX_BYTES - self.bytes,
                                 input_data=input_data)
                length = os.fstat(fd).st_size
                if not 0 < length <= MAX_BYTES - self.bytes:
                    fail("encoder", "Encoder returned empty or oversized output")
                self.bytes += length
                self.exports.append(name)
            except Exception as error:
                os.close(fd)
                fd = None
                self._unlink(name)
                if isinstance(error, RecordingError):
                    raise
                fail("encoder", "Encoder failed; no output admitted and PNG frames remain")
            finally:
                if fd is not None:
                    os.close(fd)
            return self.receipt()
        finally:
            self.export_lock.release()

    def _checked_directory(self):
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in self.path.parts[1:]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            current, original = os.fstat(fd), os.fstat(self.directory_fd)
            if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                fail("output", "Output directory identity changed")
            return fd
        except Exception:
            os.close(fd)
            raise

    def _input(self):
        # Send verified native bytes over stdin, not paths ffmpeg could follow.
        data = bytearray()
        for entry in self.entries:
            if self.windows_directory is not None:
                fd = self.windows_directory.file(entry["file"], read=True)
            else:
                fd = os.open(entry["file"], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.directory_fd)
            with os.fdopen(fd, "rb") as stream:
                raw = stream.read(8 * 1024 * 1024 + 1)
            if (len(raw) > 8 * 1024 * 1024 or len(data) + len(raw) > MAX_BYTES
                    or hashlib.sha256(raw).hexdigest() != entry["sha256"]):
                fail("output", "Native frame integrity changed; export refused")
            data.extend(raw)
        return data

    def close(self):
        if not self.export_lock.acquire(blocking=False):
            fail("busy", "Finish export before closing recording")
        try:
            receipt = self.stop()
            self._close_directory()
            return receipt
        finally:
            self.export_lock.release()
