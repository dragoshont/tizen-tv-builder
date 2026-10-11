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


def validate(source, app_id=None, allow_unset_id=False):
    """app_id is supplied when the source omits it and staging injects it; it must match if both exist."""
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
    declared = app.get("id") is not None or app.get("package") is not None
    if declared:
        declared_id = application_id(app.get("id", ""))
        if app.get("package") != declared_id.split(".")[0]:
            raise ValueError("Application package and ID disagree")
        if app_id is not None and application_id(app_id) != declared_id:
            raise ValueError("--id and the manifest application ID disagree")
        app_id = declared_id
    elif app_id is not None:
        app_id = application_id(app_id)
    elif not allow_unset_id:
        application_id("")
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


TV_COMMANDS = {"scan", "apps", "info", "open", "screenshot", "portal"}
TV_HELP = """Start here:
  setup [--check]                       Prepare this computer (sdb, tizen CLI); installs them if missing

TV commands (TV = IP address, name or tv-xxxxxx alias):
  scan                                  Find Samsung TVs on your network
  install PACKAGE_OR_HTTPS_URL TV       Sign a .wgt (file or https:// link) with your profile and install it
  info TV [--app APP]                   TV details; --app: is APP (ID or name) installed, and which version
  apps TV                               Every app ID on the TV
  open TV APP                           Open an app's screen (restarts the app)
  screenshot TV --app APP [--out DIR]   Save the app's picture as PNG
  portal                                Open the Samsung TV Developer Companion in your browser
  tv COMMAND ...                        Every TV command, e.g. tv setup (guided, to your app on a TV), tv cert add TV, tv key, tv uninstall

Legacy device commands keep their form: install --serial SERIAL PACKAGE, run, debug, forward, unforward."""
INSTALL_USAGE = ("Use: install PACKAGE_OR_HTTPS_URL TV [--sign PROFILE]  "
                 "(legacy serial install: install --serial SERIAL PACKAGE)")


INSTALL_HELP = """usage: install PACKAGE_OR_HTTPS_URL TV [--sign PROFILE]
       install --serial SERIAL [--authorized] PACKAGE   (legacy, by sdb serial)

PACKAGE_OR_HTTPS_URL  a .wgt file, or an https:// link to one (downloaded to a private
                      folder, checked, installed, then deleted)
TV                    the TV's IP address, name or tv-xxxxxx alias
--sign PROFILE        signing profile (default: the active one)"""


class InstallShape(ValueError):
    pass


def tv_argv(argv):
    """The `tv` arguments for a TV command, or None for every other (build/legacy) command."""
    if argv[:1] == ["tv"]:
        return argv[1:]
    if argv[:1] and argv[0] in TV_COMMANDS:
        return argv
    if argv[:1] != ["install"] or any(a == "--serial" or a.startswith("--serial=") or a == "--authorized"
                                      for a in argv[1:]):
        return None
    if {"-h", "--help"} & set(argv[1:]):
        raise InstallShape(INSTALL_HELP)
    words, options, before, rest = [], [], [], iter(argv[1:])
    for word in rest:
        if word == "--profile":
            before += [word, next(rest, "")]
        elif word == "--sign":
            options += [word, next(rest, "")]
        elif word.startswith("-"):
            options.append(word)
        else:
            words.append(word)
    if len(words) != 2:
        raise InstallShape(INSTALL_USAGE)
    return before + ["install", words[1], words[0]] + options


def parser():
    cli = argparse.ArgumentParser(description=__doc__, epilog=TV_HELP,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    version = json.loads((Path(__file__).resolve().parents[1] / "plugin.json").read_text())["version"]
    cli.add_argument("--version", action="version", version="tizen-tv-builder " + version)
    cli.add_argument("--tizen")
    cli.add_argument("--sdb")
    sub = cli.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    sub.add_parser("tv", help="Samsung TV Developer Companion: tv scan | connect | apps | open | screenshot | key | install | uninstall | cert add | portal", add_help=False)
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
    command = sub.add_parser("new", help="Create a starter app with a fresh package ID")
    command.add_argument("target", type=Path)
    command.add_argument("--name", required=True)
    command.add_argument("--package")
    command = sub.add_parser("audit", help="Manifest IDs, privileges vs API use, ES5 and credential scan")
    command.add_argument("source", type=Path)
    command.add_argument("--id", help="PACKAGE.Application when config.xml omits it (injected at staging)")
    command = sub.add_parser("pack", help="Deterministic unsigned .wgt (CI receipt, not installable)")
    command.add_argument("source", type=Path)
    command.add_argument("--output", type=Path, required=True)
    command = sub.add_parser("check-package", help="Structure and signature digests of a .wgt")
    command.add_argument("package", type=Path)
    command.add_argument("--require-signed", action="store_true")
    command = sub.add_parser("budgets", help="Evaluate measured metrics against a tier budget")
    command.add_argument("--tier", required=True, choices=("legacy", "mid", "current"))
    command.add_argument("--metrics", type=Path, required=True, help="JSON with coldStartMs/firstFrameMs/firstHomeMs")
    for name in ("identify", "retail"):
        command = sub.add_parser(name)
        command.add_argument("--serial", required=True)
        command.add_argument("--authorized", action="store_true")
        if name == "retail":
            command.add_argument("--target", required=True, help="Fingerprint from `identify`")
            command.add_argument("--app", required=True)
    command = sub.add_parser("emulator", help="Launch a VM, run the flow, stop the VM it launched")
    command.add_argument("--vm", required=True)
    command.add_argument("--app", required=True)
    command.add_argument("--em-cli")
    for command in (sub.choices["retail"], sub.choices["emulator"]):
        command.add_argument("--package", type=Path)
        command.add_argument("--keys", default="", help="Comma-separated keys: up,down,left,right,ok,back")
        command.add_argument("--runs", type=int, default=1)
        command.add_argument("--tier", choices=("legacy", "mid", "current"))
        command.add_argument("--output", type=Path, help="Existing folder for capture and log tail")
        command.add_argument("--uninstall", action="store_true")
        command.add_argument("--legacy-timeout", action="store_true", help="Append 0 to debug launch (Tizen 3)")
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
    if args.command in ("new", "audit", "pack", "check-package", "budgets", "identify", "retail", "emulator"):
        return _app_command(args)
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
        import appkit
        errors = [f for f in appkit.audit(args.source)["findings"] if f["level"] == "error"]
        if errors:
            raise ValueError("App audit failed; run `tizen.py audit` for details: "
                             + "; ".join(f"{f['code']} {f.get('where', '')}".strip() for f in errors[:5]))
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


def _app_command(args):
    import appkit
    if args.command == "new":
        return appkit.scaffold(args.target, args.name, args.package)
    if args.command == "audit":
        return appkit.audit(args.source, app_id=args.id)
    if args.command == "pack":
        return appkit.pack(args.source, args.output)
    if args.command == "check-package":
        return appkit.check_package(args.package, args.require_signed)
    import flows
    from live_tv import LiveError
    try:
        if args.command == "budgets":
            return flows.evaluate(json.loads(args.metrics.read_text()), args.tier)
        if args.command == "identify":
            return flows.identify(args.serial, args.sdb)
        options = {"package": args.package, "keys": [k.strip().lower() for k in args.keys.split(",") if k.strip()], "runs": args.runs,
                   "tier": args.tier, "output": args.output, "uninstall": args.uninstall,
                   "sdb": args.sdb, "tizen": args.tizen, "legacy": args.legacy_timeout}
        options = {k: v for k, v in options.items() if v is not None}
        if args.output and not args.output.is_dir():
            raise ValueError("--output must be an existing folder")
        if args.command == "retail":
            return flows.retail(args.serial, args.target, args.app, **options)
        return flows.emulator(args.vm, args.app, em_cli=args.em_cli, **options)
    except LiveError as error:
        raise ValueError(str(error)) from None


class _Cancelled(KeyboardInterrupt):
    def __init__(self, signum):
        super().__init__()
        self.signum = signum


def _cancel(signum, frame):
    raise _Cancelled(signum)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        forwarded = tv_argv(argv)
    except InstallShape as error:
        if str(error) == INSTALL_HELP:
            print(INSTALL_HELP)
            return 0
        print("ERROR: " + str(error), file=sys.stderr)
        return 2
    if forwarded is not None or argv[:1] == ["setup"]:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import tv
        if forwarded is None:
            return tv.computer_setup(argv[1:])
        return tv.main(forwarded)
    previous_sigterm = signal.signal(signal.SIGTERM, _cancel)
    try:
        result = run(parser().parse_args(argv))
        print(json.dumps(result, indent=2))
        if isinstance(result, dict) and result.get("passed") is False:
            return 2
    except (ValueError, OSError, ET.ParseError, http.client.HTTPException, subprocess.TimeoutExpired) as error:
        print("ERROR: " + redact(str(error)), file=sys.stderr)
        return 2
    except KeyboardInterrupt as error:
        print("ERROR: cancelled", file=sys.stderr)
        return 128 + getattr(error, "signum", signal.SIGINT)
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
