"""Guided setup: one set of steps and words, shared by the web portal's wizard and `tv setup`.

From a never-touched TV to the user's app on screen:
1 this computer, 2 Developer Mode, 3 connect, 4 permission to install, 5 the app, 6 see it.
evaluate() turns observed facts into those steps; it never acts. The callers act.
"""
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request

import appkit
import network_tv

STEPS = (
    ("computer", "This computer"),
    ("developer-mode", "Developer Mode on the TV"),
    ("connect", "Connect"),
    ("permission", "Permission to install"),
    ("app", "Your app"),
    ("see", "See it"),
)
WAITING = "Waiting for your TV\u2026"
SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "hello-tv"
SAMPLE_APP = "KitHelloTv.Hello"

INSTALLER_BASE = "https://download.tizen.org/sdk/Installer/tizen-studio_6.1/"
INSTALLERS = {"Darwin": "web-cli_Tizen_Studio_6.1_macos-64.bin",
              "Windows": "web-cli_Tizen_Studio_6.1_windows-64.exe",
              "Linux": "web-cli_Tizen_Studio_6.1_ubuntu-64.bin"}
# Package Manager extras and packages: the Samsung certificate files and the TV tools.
EXTRAS = ("Samsung Certificate Extension", "Samsung Tizen TV SDK")
PACKAGES = "cert-add-on,TV-SAMSUNG-Extension-Tools"
MAX_INSTALLER = 2 * 1024 ** 3


class SetupError(Exception):
    """A setup step failed: the real output and the one exact command that does it by hand."""

    def __init__(self, message, output="", command=None):
        super().__init__(message)
        self.output = output
        self.command = command


def developer_steps(ip):
    return ["On the TV remote, open Apps.",
            "Type 1 2 3 4 5 (use the on-screen keypad if the remote has no number keys).",
            "Switch Developer mode On.",
            f"Host PC IP: {ip}" if ip else "Host PC IP: this computer's address on your home network.",
            "Choose OK, then restart the TV (hold Power until the logo appears)."]


def lan_ip(interfaces=network_tv.interfaces):
    """This computer's address on the home network, as the TV must be told it."""
    found = list(interfaces())
    return next((a for a in found if a.startswith("192.168.")), found[0] if found else None)


def sdk_root(home=None):
    return Path(home or Path.home()) / "tizen-studio"


def package_manager(root, system):
    return Path(root) / "package-manager" / (
        "package-manager-cli.exe" if system == "Windows" else "package-manager-cli.bin")


def install_command(system=None, home=None):
    """The one exact command that installs the tools by hand, for when the automatic install fails."""
    system = system or platform.system()
    name = INSTALLERS.get(system)
    if not name:
        return None
    url = INSTALLER_BASE + name
    if system == "Windows":
        return (f"curl.exe -fLO {url}; .\\{name} --accept-license $env:USERPROFILE\\tizen-studio; "
                f"& \"$env:USERPROFILE\\tizen-studio\\package-manager\\package-manager-cli.exe\" install --accept-license {PACKAGES}")
    return (f"curl -fLO {url} && chmod +x {name} && ./{name} --accept-license ~/tizen-studio && "
            f"~/tizen-studio/package-manager/package-manager-cli.bin install --accept-license {PACKAGES}")


def _run(command, timeout=1800):
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        return 124, (error.stdout or b"").decode("utf-8", "replace") + "\n(timed out)"
    except OSError as error:
        return 127, f"{type(error).__name__}: {error}"
    return done.returncode, done.stdout.decode("utf-8", "replace")


def _download(url, target, progress, opener=urllib.request.urlopen):
    if not url.startswith("https://"):
        raise SetupError("Refusing a non-HTTPS download")
    try:
        with opener(url, timeout=60) as response, open(target, "xb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            if total > MAX_INSTALLER:
                raise SetupError("The installer is larger than expected")
            got = 0
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                got += len(chunk)
                if got > MAX_INSTALLER:
                    raise SetupError("The installer is larger than expected")
                out.write(chunk)
                progress(int(got * 100 / total) if total else None)
            if total and got != total:
                raise SetupError(f"The download stopped early ({got} of {total} bytes)")
    except OSError as error:
        raise SetupError(f"Could not download the installer: {type(error).__name__}: {error}") from error


def parse_extras(text):
    """`package-manager-cli extra --list` blocks: Index / Name / Activate."""
    extras, current = [], {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "index":
            current = {"index": value}
            extras.append(current)
        elif key in {"name", "activate"} and current:
            current[key] = value
    return extras


def install_sdk(progress, system=None, home=None, run=_run, download=_download, temp=None):
    """Install Tizen Studio's command-line tools and the Samsung TV extras. Raises SetupError with real output.

    progress(text, percent) is called for each step; percent is None when unknown."""
    system = system or platform.system()
    name = INSTALLERS.get(system)
    command = install_command(system, home)
    if not name:
        raise SetupError(f"There is no Tizen Studio installer for {system}")
    root = sdk_root(home)
    suffix = ".exe" if system == "Windows" else ""
    sdb = root / "tools" / ("sdb" + suffix)
    if not sdb.is_file():
        folder = Path(tempfile.mkdtemp(prefix="tizen-setup-", dir=temp))
        try:
            installer = folder / name
            progress("Downloading Tizen Studio's command-line tools", 0)
            download(INSTALLER_BASE + name, installer, lambda pct: progress(
                "Downloading Tizen Studio's command-line tools", pct))
            if system != "Windows":
                installer.chmod(0o700)
            progress("Installing Tizen Studio's command-line tools (a few minutes)", None)
            code, output = run([str(installer), "--accept-license", str(root)])
            if code or not sdb.is_file():
                raise SetupError("The Tizen Studio installer did not finish", output[-2000:], command)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    manager = package_manager(root, system)
    if not manager.is_file():
        raise SetupError("Tizen Studio's Package Manager was not found after installing", "", command)
    progress("Adding the Samsung TV extensions", None)
    code, output = run([str(manager), "extra", "--list"], 300)
    if code:
        raise SetupError("Package Manager could not list its extensions", output[-2000:], command)
    for extra in parse_extras(output):
        if extra.get("name") in EXTRAS and extra.get("activate", "").lower() != "true":
            code, out = run([str(manager), "extra", "-act", extra["index"]], 300)
            if code:
                raise SetupError(f"Package Manager could not turn on {extra['name']}", out[-2000:], command)
    progress("Installing the Samsung certificate and TV tools (a few minutes)", None)
    code, output = run([str(manager), "install", "--accept-license", PACKAGES])
    if code:
        raise SetupError("Package Manager could not install the Samsung TV tools", output[-2000:], command)
    progress("Done", 100)


def build_sample(folder):
    """The sample app as an unsigned .wgt in a private folder; installing signs it with the user's profile."""
    output = Path(folder) / "hello-tv.wgt"
    return Path(folder) / appkit.pack(SAMPLE, output)["package"]


# ---- the steps ------------------------------------------------------------------------

def _python():
    return "Python " + ".".join(map(str, sys.version_info[:3]))


def choose_tv(tvs, alias=None):
    """The TV the guide is about: the one named, else a connected one, else one with Developer Mode open."""
    if alias:
        return next((t for t in tvs if t["alias"] == alias), None)
    for test in (lambda t: t.get("held"), lambda t: t.get("reachable"),
                 lambda t: (t.get("dev_mode") or {}).get("state") == "needs-config"):
        found = next((t for t in tvs if test(t)), None)
        if found:
            return found
    return None


def _name(tv):
    details = tv.get("details") or {}
    return f"the TV at {details['address']}" if tv.get("unnamed") and details.get("address") else tv["name"]


def _job(tv, *ops):
    job = tv.get("job") or {}
    return job if job.get("op") in ops else None


def evaluate(report, state, alias=None, ip=None, sdk_job=None, setup_apps=None, system=None):
    """Steps from facts only. Each step: id, n, title, status, text, items, error, output, actions, command.

    status is done, active (the user's turn), working (something is running), failed or todo."""
    system = system or platform.system()
    checks = {c["id"]: c for c in report.get("checks", [])}
    missing = lambda *ids: [i for i in ids if (checks.get(i) or {}).get("status") != "found"]
    tv = choose_tv(state.get("tvs") or [], alias)
    name = _name(tv) if tv else "your TV"
    # The address on the TV's own network beats a guess at this computer's main one.
    ip = ((tv or {}).get("details") or {}).get("host_ip") or ip
    install = {"label": "Install Tizen Studio tools", "action": "install-sdk"}
    steps = {}

    def put(ident, status, text, items=None, error=None, actions=None, command=None, output=None, **extra):
        steps[ident] = dict(extra, status=status, text=text, items=items or [], error=error, output=output,
                            actions=actions or [], command=command)

    # 1 This computer.
    job = sdk_job or {}
    if job.get("code") == "working":
        put("computer", "working", job.get("message") or "Installing\u2026", percent=job.get("percent"))
    elif job.get("code") == "failed" and missing("sdb", "tizen", "samsung-ca"):
        put("computer", "failed", "Tizen Studio's tools could not be installed automatically.",
            error=job.get("message"), output=job.get("output"), actions=[dict(install, label="Try again")],
            command=job.get("command") or install_command(system))
    elif missing("sdb", "tizen"):
        put("computer", "active",
            "This computer needs Tizen Studio's command-line tools: sdb talks to the TV and the tizen CLI "
            "signs and installs apps.",
            items=[f"{_python()}: ready"] + [f"{checks[i]['label']}: missing" for i in missing("sdb", "tizen")],
            actions=[install], command=install_command(system))
    else:
        put("computer", "done", f"{_python()}, sdb and the tizen CLI are ready.")

    # 2 Developer Mode on the TV.
    dev = (tv or {}).get("dev_mode") or {}
    scanning = (state.get("scan") or {}).get("status") == "scanning"
    if tv and (tv.get("held") or tv.get("reachable")):
        put("developer-mode", "done", f"Found {name}: Developer Mode is on.")
    elif tv and dev.get("state") == "needs-config":
        put("developer-mode", "failed", dev.get("explanation") or "The TV refused this computer.",
            items=developer_steps(ip), error=dev.get("explanation"),
            actions=[{"label": "Check again", "action": "scan"}])
    else:
        put("developer-mode", "active", "Do this on your TV:", items=developer_steps(ip),
            status_line=WAITING + (" (checking now)" if scanning else ""),
            actions=[{"label": "Check now", "action": "scan"}], ip=ip)

    # 3 Connect.
    if tv and tv.get("status") == "connected":
        put("connect", "done", f"Connected to {name}.")
    elif tv and tv.get("pending") == "connect":
        put("connect", "working", f"Connecting to {name}\u2026")
    elif tv and tv.get("error"):
        put("connect", "failed", f"Could not connect to {name}.", error=tv["error"],
            actions=[{"label": "Try again", "action": "connect"}])
    else:
        put("connect", "active", f"Connecting to {name} happens automatically.",
            actions=[{"label": "Connect", "action": "connect"}])

    # 4 Permission to install.
    cert = (tv or {}).get("certificate") or {}
    job = _job(tv or {}, "cert", "profile")
    allow = {"label": "Allow installing my apps on this TV", "action": "cert"}
    if cert.get("state") == "covered":
        put("permission", "done", f"You can install your apps on {name}.")
    elif cert.get("state") == "default":
        put("permission", "done", "Tizen's built-in developer certificate will be used. If the TV refuses "
            "the app, this step offers a Samsung certificate.")
    elif job and job.get("code") == "waiting":
        put("permission", "working", "Waiting for Samsung sign-in\u2026",
            items=[("\u2713 " if s["done"] else "\u2026 ") + s["text"] for s in job.get("steps") or []],
            url=job.get("url"))
    elif job and not job.get("ok"):
        put("permission", "failed", f"{name} is not allowed yet.", error=job.get("message"),
            output=job.get("output"), actions=[dict(allow, label="Try again")])
    elif cert.get("others"):
        other = cert["others"][0]
        put("permission", "active", f"Your certificate {other} already allows {name}.",
            actions=[{"label": f"Use {other} for installs", "action": "profile", "data": {"profile": other}}])
    elif missing("samsung-ca", "openssl"):
        lacking = missing("samsung-ca", "openssl")
        put("permission", "active", "Making a Samsung certificate needs two more things on this computer.",
            items=[f"{checks[i]['label']}: missing. {checks[i]['next']}" for i in lacking],
            actions=[dict(install, label="Install Samsung certificate tools")] if "samsung-ca" in lacking else [],
            command=install_command(system))
    else:
        put("permission", "active",
            f"Your apps need a certificate that lists {name}. One Samsung sign-in in your browser; "
            "everything else is automatic.", actions=[allow])

    # 5 Your app.
    apps = ((tv or {}).get("apps") or {}).get("items") or []
    job = _job(tv or {}, "install", "sample")
    app = (setup_apps or {}).get(tv["alias"]) if tv else None
    choose = [{"label": "Choose your .wgt", "action": "choose"},
              {"label": "Install a sample app", "action": "sample"}]
    if tv and tv.get("pending") in {"install", "sample"}:
        put("app", "working", "Installing\u2026 (signing it with your certificate, then checking the TV's app list)")
    elif job and not job.get("ok"):
        fix = [allow] if job.get("code") in {"certificate-chain", "certificate-expired"} else choose
        put("app", "failed", "The app was not installed.", error=job.get("message"), output=job.get("output"),
            actions=fix)
    elif app and app in apps:
        put("app", "done", f"{app} is installed (the TV's app list shows it).", app=app)
    else:
        put("app", "active", "Choose your .wgt, or install a sample app if you don't have one yet.", actions=choose)

    # 6 See it.
    preview = (tv or {}).get("preview") or {}
    job = _job(tv or {}, "preview")
    if app and preview.get("ready") and preview.get("app") == app:
        put("see", "done", f"{name} shows {app}. Use the remote, or Save screenshot.", app=app)
    elif tv and tv.get("pending") == "preview":
        put("see", "working", f"Opening {app or 'your app'} (this restarts it)\u2026")
    elif job and not job.get("ok"):
        put("see", "failed", f"Could not open {app or 'your app'}.", error=job.get("message"),
            actions=[{"label": "Try again", "action": "preview", "data": {"app": app}}])
    else:
        put("see", "active", f"Opening {app or 'your app'} restarts it and shows its screen here.",
            actions=[{"label": "Open screen", "action": "preview", "data": {"app": app}}] if app else [])

    ordered, blocked = [], False
    for n, (ident, title) in enumerate(STEPS, 1):
        step = dict(steps[ident], id=ident, n=n, title=title)
        if blocked:
            step["status"] = "todo"
        elif step["status"] != "done":
            blocked = True
        ordered.append(step)
    current = next((s["id"] for s in ordered if s["status"] != "done"), None)
    return {"steps": ordered, "current": current, "done": current is None,
            "alias": tv["alias"] if tv else None, "tv_name": name if tv else None, "ip": ip}
