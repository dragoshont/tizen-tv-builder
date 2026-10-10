#!/usr/bin/env python3
"""Bounded wrappers around already-installed Samsung developer tools."""
import argparse
import http.client
import ipaddress
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
import time
import xml.etree.ElementTree as ET

WIDGET = "{http://www.w3.org/ns/widgets}"
TIZEN = "{http://tizen.org/ns/widgets}"
ASSETS = {".html", ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".webp",
          ".svg", ".ico", ".woff", ".woff2", ".ttf", ".otf"}


def application_id(value):
    if not re.fullmatch(r"[A-Za-z0-9]{10}\.[A-Za-z0-9_]+", value):
        raise ValueError("App ID must be a ten-character package plus .Application")
    return value


def device_serial(value):
    host, separator, port = value.rpartition(":")
    if not separator or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("Serial must be an explicit IPv4 address:port")
    try:
        address = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError as error:
        raise ValueError("Serial must be an explicit IPv4 address:port") from error
    if address.is_unspecified or address.is_multicast or address == ipaddress.IPv4Address("255.255.255.255"):
        raise ValueError("Serial must identify one device")
    return value


def safe_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Manifest entry must be a local relative file")
    candidate = root / path
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError("Manifest entry must exist without symlinks")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("Manifest entry escapes source")
    return candidate


def validate(source):
    source = Path(source)
    if not source.is_dir() or source.is_symlink():
        raise ValueError("Source must be a real directory")
    manifest = safe_path(source, "config.xml")
    data = manifest.read_bytes()
    if len(data) > 256 * 1024 or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Manifest is oversized or contains entity declarations")
    root = ET.fromstring(data)
    if root.tag != WIDGET + "widget":
        raise ValueError("Expected W3C widget manifest")
    apps = root.findall(TIZEN + "application")
    if len(apps) != 1:
        raise ValueError("Expected exactly one tizen:application")
    app = apps[0]
    app_id = application_id(app.get("id", ""))
    if app.get("package") != app_id.split(".")[0]:
        raise ValueError("Application package and ID disagree")
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", app.get("required_version", "")):
        raise ValueError("Explicit required_version is required")
    profile = root.find(TIZEN + "profile")
    if profile is None or profile.get("name") != "tv":
        raise ValueError("TV profile is required")
    content = root.find(WIDGET + "content")
    if content is None:
        raise ValueError("Manifest content entry is required")
    safe_path(source, content.get("src", ""))
    return {"app": app_id, "required_version": app.get("required_version")}


def stage(source, target):
    validate(source)
    source, target = Path(source), Path(target)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part.startswith(".") or part in {"private", "receipts", "node_modules"} for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError("Symlinks are not permitted in staged sources")
        if not path.is_file():
            continue
        if relative.as_posix() != "config.xml" and path.suffix.lower() not in ASSETS:
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


def tool_path(name, override=None):
    sdk = Path(os.environ.get("TIZEN_SDK_ROOT", Path.home() / "tizen-studio"))
    candidate = Path(override) if override else sdk / ("tools/ide/bin/tizen" if name == "tizen" else "tools/sdb")
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    if override:
        raise ValueError("Requested SDK executable is missing or not executable")
    discovered = shutil.which(name)
    if discovered:
        return discovered
    raise ValueError("SDK executable is missing; install/configure it with owner permission")


def redact(output):
    output = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<address>", output)
    return re.sub(r"(?i)((?:token|password|duid)[=:]\s*)[^\s&]+", r"\1<redacted>", output)


def execute(arguments, timeout=120):
    if os.name != "posix":
        raise ValueError("SDK execution requires POSIX process-group ownership; use Samsung's CLI directly on Windows")
    with subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except BaseException as error:
            # Only the session created by this invocation, never a shared SDB server.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                process.wait()
            if isinstance(error, subprocess.TimeoutExpired):
                raise ValueError("SDK command deadline exceeded; owned process group stopped") from None
            raise
        if process.returncode:
            raise ValueError("SDK command failed: " + redact((stderr or stdout)[-2000:]))
        return redact(stdout[-8000:])


class _DeadlineSocket(socket.socket):
    def __init__(self, connected, deadline):
        super().__init__(fileno=connected.detach())
        self.deadline = deadline

    def _set_remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Inspector request deadline exceeded")
        self.settimeout(remaining)

    def recv_into(self, *args):
        # Buffered HTTP status/header reads must also consume the same deadline.
        self._set_remaining()
        return super().recv_into(*args)

    def sendall(self, *args):
        self._set_remaining()
        return super().sendall(*args)


class _DeadlineConnection(http.client.HTTPConnection):
    def __init__(self, host, port, deadline):
        super().__init__(host, port, timeout=5)
        self.deadline = deadline

    def connect(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Inspector request deadline exceeded")
        self.timeout = remaining
        super().connect()
        self.sock = _DeadlineSocket(self.sock, self.deadline)


def inspect(port):
    data = {}
    for endpoint in ("version", "list"):
        deadline = time.monotonic() + 5
        connection = _DeadlineConnection("127.0.0.1", port, deadline)
        response = None
        try:
            connection.request("GET", f"/json/{endpoint}")
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError("Inspector request refused; redirects are not followed")
            raw = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("Inspector full-body deadline exceeded")
                chunk = response.read1(65536)
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > 1024 * 1024:
                    raise ValueError("Inspector response exceeds one MiB")
            data[endpoint] = json.loads(raw)
        except TimeoutError:
            raise ValueError("Inspector request deadline exceeded") from None
        finally:
            if response is not None:
                response.close()
            connection.close()
    if not isinstance(data["version"], dict) or not isinstance(data["list"], list):
        raise ValueError("Inspector response shape is invalid")
    browser = data["version"].get("Browser", "")
    protocol = data["version"].get("Protocol-Version", "")
    if not isinstance(browser, str) or not re.fullmatch(r"[A-Za-z]+/[0-9.]+", browser):
        raise ValueError("Inspector browser metadata is invalid")
    if not isinstance(protocol, str) or not re.fullmatch(r"[0-9.]+", protocol):
        raise ValueError("Inspector protocol metadata is invalid")
    types = [entry.get("type") for entry in data["list"] if isinstance(entry, dict)]
    if any(value not in {"page", "background_page", "service_worker", "worker", "other"} for value in types):
        raise ValueError("Inspector target type is invalid")
    return {"browser": browser,
            "protocol": protocol,
            "target_count": len(data["list"]),
            "types": types,
            "capture": "not tested by HTTP discovery"}


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--tizen")
    cli.add_argument("--sdb")
    sub = cli.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    for name in ("validate", "build", "package"):
        command = sub.add_parser(name)
        command.add_argument("source", type=Path)
        if name == "package":
            command.add_argument("--profile", required=True)
            command.add_argument("--output", type=Path, required=True)
    for name in ("install", "run", "debug", "forward", "unforward"):
        command = sub.add_parser(name)
        command.add_argument("--serial", required=True)
        command.add_argument("--authorized", action="store_true")
        if name == "install":
            command.add_argument("package", type=Path)
        elif name in ("run", "debug"):
            command.add_argument("--app", required=True)
            if name == "debug":
                command.add_argument("--legacy-timeout", type=int, choices=range(0, 61))
        else:
            command.add_argument("--port", type=int, required=True)
    command = sub.add_parser("inspect")
    command.add_argument("--port", type=int, required=True)
    return cli


def run(args):
    if hasattr(args, "serial"):
        device_serial(args.serial)
        if not args.authorized:
            raise ValueError("Device operation requires --authorized and caller-held exclusive ownership")
    if hasattr(args, "app"):
        application_id(args.app)
    if hasattr(args, "port") and not 1024 <= args.port <= 65535:
        raise ValueError("Debug port must be between 1024 and 65535")
    if args.command == "doctor":
        tools = {}
        for name in ("tizen", "sdb"):
            try:
                tools[name] = {"available": True, "version": execute([tool_path(name, getattr(args, name)), "version"], 10).strip()}
            except ValueError as error:
                tools[name] = {"available": False, "error": str(error)}
        return {"host": sys.platform, "tools": tools, "device": "not contacted"}
    if args.command == "validate":
        return validate(args.source)
    if args.command in ("build", "package"):
        validate(args.source)
        if args.command == "package":
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.profile) or args.profile.startswith("-"):
                raise ValueError("Invalid certificate profile name")
            if args.output.exists() or args.output.is_symlink() or args.output.suffix.lower() != ".wgt":
                raise ValueError("Output must be a new .wgt file")
            if not args.output.parent.is_dir():
                raise ValueError("Output directory must already exist")
        tizen = tool_path("tizen", args.tizen)
        with tempfile.TemporaryDirectory(prefix="tizen-tv-builder-") as temporary:
            source = Path(temporary) / "app"
            source.mkdir()
            stage(args.source, source)
            if args.command == "build":
                output = execute([tizen, "build-web", "--", str(source)])
                return {"build": "unsigned", "sdk": output, "device": "not contacted"}
            package_dir = Path(temporary) / "package"
            package_dir.mkdir()
            execute([tizen, "package", "-t", "wgt", "-s", args.profile, "-o", str(package_dir), "--", str(source)])
            packages = list(package_dir.glob("*.wgt"))
            if len(packages) != 1:
                raise ValueError("SDK did not produce exactly one package")
            with args.output.open("xb") as destination, packages[0].open("rb") as package:
                shutil.copyfileobj(package, destination)
            return {"package": "signed by requested SDK profile", "device": "not contacted"}
    if args.command == "inspect":
        return inspect(args.port)
    if args.command == "install":
        if not args.package.is_file() or args.package.is_symlink() or args.package.suffix.lower() != ".wgt":
            raise ValueError("Install requires an existing real .wgt file")
        output = execute([tool_path("tizen", args.tizen), "install", "-n", args.package.name,
                          "-s", args.serial, "--", str(args.package.resolve().parent)])
    elif args.command == "run":
        output = execute([tool_path("tizen", args.tizen), "run", "-p", args.app, "-s", args.serial])
    else:
        command = [tool_path("sdb", args.sdb), "-s", args.serial]
        if args.command == "debug":
            command += ["shell", "0", "debug", args.app]
            if args.legacy_timeout is not None:
                command += [str(args.legacy_timeout)]
        elif args.command == "forward":
            command += ["forward", f"tcp:{args.port}", f"tcp:{args.port}"]
        else:
            command += ["forward", "--remove", f"tcp:{args.port}"]
        output = execute(command, 30)
    return {"sdk_response": output, "acceptance": "command result only; verify actual device behavior"}


class _Cancelled(KeyboardInterrupt):
    def __init__(self, signum):
        super().__init__()
        self.signum = signum


def _cancel(signum, frame):
    raise _Cancelled(signum)


def main():
    previous_sigterm = signal.signal(signal.SIGTERM, _cancel)
    try:
        print(json.dumps(run(parser().parse_args()), indent=2))
    except (ValueError, OSError, ET.ParseError, http.client.HTTPException, subprocess.TimeoutExpired) as error:
        print("ERROR: " + redact(str(error)), file=sys.stderr)
        return 1
    except KeyboardInterrupt as error:
        print("ERROR: cancelled", file=sys.stderr)
        return 128 + getattr(error, "signum", signal.SIGINT)
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
