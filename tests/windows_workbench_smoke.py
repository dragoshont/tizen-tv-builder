"""Native Windows host gate: only loopback HTTP and original fixture pixels."""
import base64
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zlib
import struct

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from recording import Recorder, Encoder, WindowsDirectory


def fixture_png():
    def chunk(kind, data):
        return (struct.pack("!I", len(data)) + kind + data +
                struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack("!IIBBBBB", 2, 2, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"\0" + b"\x40\x80\xc0\xff" * 2 +
                                        b"\0" + b"\x40\x80\xc0\xff" * 2)) +
            chunk(b"IEND", b""))


class FixtureSource:
    def __init__(self):
        self.lock = threading.Lock()
        self.pixels_allowed = True
        self.streaming = False
        self.client = object()
        self.expires = time.monotonic() + 30
        self.stale = False
        self.cleanup = "snapshots released"
        self.frames = []
        self.revision = 0

    def operate(self, operation, data):
        assert operation == "capture" and data == {}
        with self.lock:
            self.revision += 1
            self.frames = [{"raw": fixture_png(), "width": 2, "height": 2}]
            return {"revision": self.revision}

    def disconnect(self):
        self.client = None


def main():
    if os.name != "nt":
        raise RuntimeError("This gate requires a real Windows host")
    server = subprocess.Popen([sys.executable, str(ROOT / "tools/workbench.py"), "--no-open"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(server.stdout.readline()), daemon=True).start()
    parent = Path(tempfile.mkdtemp(prefix="workbench-host-fixture-"))
    recorder = None
    try:
        line = lines.get(timeout=20)
        match = re.search(r"(http://127\.0\.0\.1:\d+)/#([A-Za-z0-9_-]+)", line)
        if not match:
            raise RuntimeError("Workbench did not emit its private loopback bootstrap")
        url, token = match.groups()
        for route in ("/api/setup", "/api/state", "/api/record"):
            request = urllib.request.Request(url + route,
                                             headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.load(response)
                assert response.status == 200 and isinstance(data, dict)
            if route == "/api/state":
                assert data["sources"] == []
        print("test result: authenticated Windows loopback setup/state/record PASS")
        probe = WindowsDirectory(parent / "acl-probe")
        try:
            fd = probe.file("fixture.png")
            os.write(fd, fixture_png())
            os.close(fd)
        finally:
            probe.close()
        recorder = Recorder(FixtureSource(), parent / "app-layer-0-windows-fixture",
                            duration=.1, encoder=Encoder())
        recorder.start()
        recorder.thread.join(10)
        receipt = recorder.stop()
        assert receipt["state"] == "stopped" and receipt["frames"] >= 1, receipt["error"]
        exported = recorder.export("png")
        assert Path(exported["manifest"]).is_file()
        assert base64.b64encode(Path(exported["paths"][0]).read_bytes()).startswith(b"iVBOR")
        system = recorder.windows_directory._system_directory()
        for path in [recorder.path, *[Path(p) for p in exported["paths"]]]:
            result = subprocess.run([str(system / "icacls.exe"), str(path)],
                                    capture_output=True, timeout=10, check=True)
            assert result.stdout
        print("test result: native Windows private ACL/fixture PNG/export lifecycle PASS")
    finally:
        if recorder is not None:
            recorder.close()
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        server.stdout.close()
        server.stderr.close()
        shutil.rmtree(parent)
    print("test result: owned process/files cleanup PASS; hosted Windows image, not TV or desktop acceptance")


if __name__ == "__main__":
    main()
