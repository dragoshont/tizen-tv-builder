#!/usr/bin/env python3
"""tv: the Samsung TV Developer Companion from a terminal.

The same words and code paths as the portal (tv_fleet.Fleet). TVs are named by their
name or tv-xxxxxx alias (any case). Output shows full data (address, Device ID, apps)
for you on this computer; nothing is logged, and the scan cache keeps names and status only.

Each run is one-shot: TVs are scanned in-process, and anything this command connects or
forwards is released before it exits. Apps you open keep running on the TV.
No setup is needed: names live in a private profile created empty on first use
(~/.tizen-tv-builder/profile.json, or $TIZEN_TV_PROFILE).
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import appkit  # noqa: E402
import live_tv  # noqa: E402
import samsung_cert  # noqa: E402
import tv_fleet  # noqa: E402
from live_tv import LiveError, read_profile  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KEYS = sorted(live_tv.KEYS)
PORTAL_PORT = 47811
CERT_POLL = 1.0  # Seconds between checks while the certificate is being made.
STATE_DIR = Path.home() / ".tizen-tv-builder"
WGT_LIMIT = 256 * 1024 * 1024  # Bytes; a TV web app is far smaller.
WGT_DEADLINE = 300.0  # Seconds for the whole download, not per read.
# The scan cache is a file on disk: names and status only, never addresses or Device IDs.
CACHE_FIELDS = ("alias", "name", "label", "headline", "status", "reachable", "year", "dev_mode", "metadata")
ALIASES = {"status": "scan", "check": "connect", "identify": "connect", "remove": "uninstall"}


def parser():
    cli = argparse.ArgumentParser(prog="tv", description=__doc__.split("\n\n")[0],
                                  formatter_class=argparse.RawDescriptionHelpFormatter, epilog=(
        "commands (TV = IP address, name or tv-xxxxxx alias):\n"
        "  scan | list            Find TVs on your network / show the last scan\n"
        "  connect TV             Connect: status, details, certificate and apps, then disconnect\n"
        "  info TV [--connect]    Every detail the portal shows; --connect also reads identity and apps\n"
        "  info TV --app APP      Is APP (exact ID or name) installed, and the version if the TV lists it\n"
        "  apps TV                Every app ID on the TV\n"
        "  open TV APP            Open an app's screen (restarts the app)\n"
        "  screenshot TV --app APP [--out DIR]   Save the app's picture as PNG\n"
        "  key TV KEY... --app APP               Send remote keys to the app: " + ", ".join(KEYS) + "\n"
        "  install TV WGT_OR_HTTPS_URL [--sign PROFILE]   Sign with the active profile and install\n"
        "  uninstall TV APP       Uninstall exactly APP; not installed = nothing to do, checked on the TV\n"
        "  cert TV | cert add TV [TV ...]  Certificate coverage / allow TVs (one sign-in for all)\n"
        "  cert use TV PROFILE    Make PROFILE the active signing profile (one that allows this TV)\n"
        "  cert reset PROFILE [PROFILE ...] [--yes]   Remove exactly these signing profiles (backed up first)\n"
        "  duid TV                Print the TV's Device ID (DUID)\n"
        "  rename TV NAME         Name a TV\n"
        "  setup [TV]             Guided setup, step by step, to your app on the TV screen\n"
        "  setup --check          Only report what this computer has; changes nothing\n"
        "  portal                 Open the Samsung TV Developer Companion in your browser\n"
        "aliases: status = scan, check/identify = connect, remove = uninstall\n"))
    cli.add_argument("--profile", type=Path, help="Private 0600 profile; optional, a default is created")
    sub = cli.add_subparsers(dest="command", required=True, metavar="command")
    for name in ("scan", "status"):
        item = sub.add_parser(name, help="Find TVs on your network")
        item.add_argument("--json", action="store_true", help="Print the same state the portal shows")
    sub.add_parser("list", help="Show the TVs from the last scan without contacting them")
    for name in ("connect", "check", "identify", "apps", "duid"):
        item = sub.add_parser(name, help="Connect to a TV" if name != "apps" else "List every app on a TV")
        item.add_argument("tv")
        item.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)  # Accepted, ignored.
        item.add_argument("--show", action="store_true", help=argparse.SUPPRESS)
    item = sub.add_parser("info", help="Every detail the portal shows for a TV")
    item.add_argument("tv")
    item.add_argument("--connect", action="store_true",
                      help="Also connect to read OS, DUID, certificate and apps, then disconnect")
    item.add_argument("--show-duid", action="store_true", help="Same as --connect (the DUID is always shown)")
    item.add_argument("--app", help="Is this app (exact ID or name) installed, and which version does the TV list")
    item = sub.add_parser("rename", help="Name a TV")
    item.add_argument("tv")
    item.add_argument("name")
    item = sub.add_parser("setup", help="Guided setup: from a new TV to your app on screen")
    item.add_argument("tv", nargs="?", help="The TV to set up (default: the one that answers)")
    item.add_argument("--check", action="store_true",
                      help="Only report what this computer has for signing and installing; changes nothing")
    item = sub.add_parser("cert", help="Certificate coverage for a TV, `cert add TV [TV ...]` (one sign-in), "
                                       "or `cert reset PROFILE [PROFILE ...] --yes`")
    item.add_argument("words", nargs="+", metavar="ARGS",
                      help="TV | add TV [TV ...] | use TV PROFILE | reset PROFILE [PROFILE ...]")
    item.add_argument("--yes", action="store_true",
                      help="cert reset only: really remove the named profiles (without it, only shows what would go)")
    item = sub.add_parser("install", help="Sign a .wgt with the active profile and install it")
    item.add_argument("tv")
    item.add_argument("package", help="A .wgt file, or an https:// link to one")
    item.add_argument("--sign", metavar="PROFILE", help="Signing profile (default: the active one)")
    item.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    for name in ("open", "uninstall", "remove", "launch"):
        item = sub.add_parser(name, help={"open": "Open an app's screen (restarts the app)",
                                          "launch": "Start an app"}.get(name, "Uninstall an app"))
        item.add_argument("tv")
        item.add_argument("app")
        item.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    item = sub.add_parser("key", help="Send remote keys to an app")
    item.add_argument("tv")
    item.add_argument("keys", nargs="+", metavar="key")
    item.add_argument("--app", help="The app to send keys to (opened first; restarts it)")
    item.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    item = sub.add_parser("screenshot", help="Save an app's picture as PNG")
    item.add_argument("tv")
    item.add_argument("--app", help="The app to capture (opened first; restarts it)")
    item.add_argument("--out", type=Path, help="Folder (default: ~/Pictures/TV Screenshots)")
    item.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    item = sub.add_parser("portal", help="Open the Samsung TV Developer Companion in your browser")
    item.add_argument("--port", type=int, default=PORTAL_PORT, help="Preferred loopback port (default 47811)")
    return cli


class Usage(Exception):
    pass


def private_dir(folder):
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.is_symlink() or folder.stat().st_uid != os.getuid():
        raise Usage("The private folder " + str(folder) + " must be a real folder owned by you")
    folder.chmod(0o700)
    return folder


def default_profile(state_dir=STATE_DIR, create=False):
    """The private profile to use without --profile; created empty (no credentials) when asked."""
    configured = os.environ.get("TIZEN_TV_PROFILE")
    if configured:
        return Path(configured).expanduser().absolute()
    path = Path(state_dir).expanduser().absolute() / "profile.json"
    if path.exists() or path.is_symlink():
        return path
    if not create:
        return None
    private_dir(path.parent)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return path
    with os.fdopen(fd, "w") as stream:
        stream.write('{"sources": []}\n')
    return path


def save_cache(state_dir, state):
    """Remember the last scan for `list`: names and status only, never addresses or identities."""
    folder = private_dir(Path(state_dir))
    data = {"scanned_at": int(time.time()),
            "tvs": [{key: tv.get(key) for key in CACHE_FIELDS} for tv in state.get("tvs", [])]}
    temporary = folder / ".last-scan.tmp"
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream)
    os.replace(temporary, folder / "last-scan.json")


def load_cache(state_dir):
    path = Path(state_dir) / "last-scan.json"
    try:
        if path.is_symlink():
            return None
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) and isinstance(data.get("tvs"), list) else None
    except (OSError, ValueError):
        return None


def default_fleet(certificate_duids=None, names=None, save_names=None, known=None, save_known=None):
    import tv_fleet
    return tv_fleet.Fleet(certificate_duids=certificate_duids, names=names or {}, save_names=save_names,
                          known=known or {}, save_known=save_known)


def title(tv):
    """The portal's title: a TV with no known name or model is called by its address."""
    address = (tv.get("details") or {}).get("address")
    return f"TV at {address}" if tv.get("unnamed") and address else tv["name"]


def card_lines(state):
    """One line per TV with the portal's status words, then its details in full."""
    tvs = state.get("tvs", [])
    if state.get("sdk") == "missing":
        yield "Tizen Studio's sdb was not found; showing network results only."
    if not tvs:
        yield "No TVs found. Turn on Developer Mode on the TV (Apps, then 1 2 3 4 5), then scan again."
        return
    width = max(len(title(tv)) for tv in tvs)
    for tv in tvs:
        yield f"{title(tv):<{width}}  {tv['alias']}  {tv['label']}"
        details = tv.get("details") or {}
        shown = details.get("address") and not title(tv).endswith(details["address"])
        facts = describe(tv.get("metadata") or {}) + ([details["address"]] if shown else [])
        if tv.get("missing") and tv.get("last_seen"):
            facts.append("last seen " + time.strftime("%H:%M", time.localtime(tv["last_seen"])))
        if facts:
            yield "    " + " · ".join(facts)
        if tv.get("headline") and tv["headline"] != tv["label"]:
            yield "    " + tv["headline"]


def model_year_text(meta):
    year = (meta.get("model_year") or {})
    if not year.get("value"):
        return None
    source = {"reported by the TV": "reported", "inferred from the model code": "from model code"}
    return f"Model year {year['value']} ({source.get(year.get('provenance'), year.get('provenance'))})"


def describe(meta):
    """Presentation only; the metadata itself comes from tv_fleet.Fleet.metadata, shared with the portal."""
    # Unknown fields are omitted, never printed as placeholders.
    parts = [p for p in (meta.get("model"), model_year_text(meta), (meta.get("os") or {}).get("text")) if p]
    if meta.get("resolution"):
        parts.append(meta["resolution"])
    if meta.get("frame_tv"):
        parts.append("The Frame")
    if meta.get("firmware"):
        parts.append("Firmware " + meta["firmware"])
    return parts


def guidance_lines(tv):
    guide = tv.get("guidance")
    if not guide:
        return
    yield guide["title"]
    for number, step in enumerate(guide.get("steps", []), 1):
        yield f"  {number}. " + step.replace("{host}", guide.get("host_ip") or "this computer's address")
    if guide.get("host_ip"):
        yield "This computer's IP to enter on the TV: " + guide["host_ip"]


def addresses(tv):
    """The TV's network address, with and without a port."""
    address = str((tv.get("details") or {}).get("address") or "").casefold()
    if not address:
        return set()
    return {address, address.rsplit(":", 1)[0]} if address.count(":") == 1 else {address, address.strip("[]")}


def find(state, query):
    """A TV by IP address, alias or name, any case."""
    wanted = query.strip().casefold()
    matches = [t for t in state["tvs"]
               if wanted in (t["alias"].casefold(), str(t.get("name", "")).casefold(), title(t).casefold())
               or wanted in addresses(t)]
    if len(matches) != 1:
        names = ", ".join(f"{title(t)} ({t['alias']})" for t in state["tvs"]) or "none found"
        raise LiveError("unknown", ("Several TVs are called " if matches else "No TV called ") + query +
                        "; TVs: " + names)
    return matches[0]


def public_url(url):
    """The link without credentials, query or fragment: safe to print."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    try:
        port = parts.port
    except ValueError:
        port = None
    return urllib.parse.urlunsplit((parts.scheme, host + (f":{port}" if port else ""), parts.path, "", ""))


class HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not str(newurl).lower().startswith("https://"):
            raise LiveError("download", "The download link redirected away from HTTPS; nothing was installed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_opener(request, timeout):
    return urllib.request.build_opener(HttpsOnlyRedirect()).open(request, timeout=timeout)


class download_package:
    """`with download_package(url) as path`: a validated .wgt in a private folder, removed afterwards.

    Bounded by size and by one deadline for the whole download. Only the public part of the link
    (no credentials, query or fragment) ever appears in messages.
    """

    def __init__(self, url, opener=None, limit=WGT_LIMIT, deadline=WGT_DEADLINE, chunk=1 << 20, clock=time.monotonic):
        if not url.lower().startswith("https://"):
            raise Usage("Only https:// links can be installed; download it yourself, then install the file")
        self.url, self.public = url, public_url(url)
        self.opener, self.limit, self.deadline = opener or https_opener, limit, deadline
        self.chunk, self.clock, self.folder = chunk, clock, None
        self.size = 0
        self.version = None

    def __enter__(self):
        self.folder = Path(tempfile.mkdtemp(prefix="tv-download-"))
        try:
            return self._fetch()
        except BaseException:
            shutil.rmtree(self.folder, ignore_errors=True)
            raise

    def __exit__(self, *exc):
        shutil.rmtree(self.folder, ignore_errors=True)
        return False

    def _request(self):
        parts = urllib.parse.urlsplit(self.url)
        netloc = parts.netloc.rpartition("@")[2]
        request = urllib.request.Request(urllib.parse.urlunsplit(parts._replace(netloc=netloc)),
                                         headers={"User-Agent": "tizen-tv-builder"})
        if parts.username is not None:
            token = f"{urllib.parse.unquote(parts.username)}:{urllib.parse.unquote(parts.password or '')}"
            # Unredirected: credentials are never forwarded to another host.
            request.add_unredirected_header("Authorization", "Basic " + base64.b64encode(token.encode()).decode())
        return request

    def _fetch(self):
        name = re.sub(r"[^A-Za-z0-9._-]", "_", urllib.parse.unquote(Path(urllib.parse.urlsplit(self.url).path).name))
        target = self.folder / (name if name.lower().endswith(".wgt") and len(name) <= 128 else "download.wgt")
        started = self.clock()
        too_big = LiveError("download", f"The file at {self.public} is larger than {self.limit // (1024 * 1024)} MB; "
                                        "nothing was installed")
        try:
            with self.opener(self._request(), timeout=min(30.0, self.deadline)) as response, open(target, "xb") as out:
                if int(response.headers.get("Content-Length") or 0) > self.limit:
                    raise too_big
                while True:
                    if self.clock() - started > self.deadline:
                        raise LiveError("download", f"The download from {self.public} took longer than "
                                                    f"{self.deadline:g} s; nothing was installed")
                    data = response.read(self.chunk)
                    if not data:
                        break
                    self.size += len(data)
                    if self.size > self.limit:
                        raise too_big
                    out.write(data)
        except LiveError:
            raise
        except (OSError, ValueError) as error:
            reason = f"HTTP {error.code}" if hasattr(error, "code") and isinstance(error.code, int) \
                else type(error).__name__
            raise LiveError("download", f"Could not download {self.public} ({reason}); nothing was installed") \
                from None
        try:
            app = appkit.check_package(target)["app"]
            with zipfile.ZipFile(target) as archive:
                info = archive.getinfo("config.xml")
                config = archive.read(info).decode("utf-8", "replace") if info.file_size <= 1 << 20 else ""
        except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
            reason = str(error) if isinstance(error, ValueError) else "not a zip archive"
            raise LiveError("invalid", f"The file at {self.public} is not a Tizen TV app package ({reason}); "
                                       "nothing was installed") from None
        if not app:
            raise LiveError("invalid", f"The file at {self.public} is not a Tizen TV app package "
                                       "(config.xml has no app ID); nothing was installed")
        found = re.search(r"<widget\b[^>]*?\sversion=\"([^\"]{1,32})\"", config)
        self.app, self.version = app, found and found.group(1)
        return target


def private_output(folder):
    folder = folder.expanduser().resolve()
    if folder.is_relative_to(ROOT):
        raise Usage("Screenshots may show private screens; choose a folder outside this repository")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def default_setup():
    import tv_fleet
    return tv_fleet.setup_report()


def setup_lines(report):
    for item in report["checks"]:
        name = item["label"].split(" (")[0]
        line = f"{name}: {item['status']}"
        if item["id"] == "profiles" and report["profiles"]["names"]:
            line += " · " + ", ".join(n + (" (active)" if n == report["profiles"]["active"] else "")
                                      for n in report["profiles"]["names"])
        yield line
        if item["next"]:
            yield "    How: " + item["next"]
    yield report["note"]


COMPUTER_TOOLS = ("sdb", "tizen")


def default_installer(progress):
    import tv_setup
    return tv_setup.install_sdk(progress)


def default_ip():
    import tv_setup
    return tv_setup.lan_ip()


def computer_setup(argv, stdout=None, stderr=None, report=default_setup, installer=default_installer,
                   ip=default_ip, fleet_factory=None):
    """`tizen.py setup [--check]`: prepare this computer only, then return. Never contacts a TV.

    fleet_factory is accepted only so tests can prove it is never used."""
    import tv_setup
    out, err = stdout or sys.stdout, stderr or sys.stderr
    say = lambda line="": print(line, file=out)  # noqa: E731
    cli = argparse.ArgumentParser(prog="tizen.py setup", description=(
        "Prepare this computer for Samsung TV apps: Python, sdb and the tizen CLI. Installs Tizen "
        "Studio's command-line tools when they are missing. Does not contact a TV; for the full guided "
        "path to your app on a TV use: python3 tools/tizen.py tv setup"))
    cli.add_argument("--check", action="store_true", help="Only report what this computer has; install nothing")
    try:
        args = cli.parse_args(argv)
    except SystemExit as stop:
        if stop.code:
            print("For the guided TV setup use: python3 tools/tizen.py tv setup [TV]", file=err)
        return stop.code

    def missing(found):
        return [c for c in found["checks"] if c["id"] in COMPUTER_TOOLS and c["status"] != "found"]

    def show(found):
        say(f"Python: found ({sys.version.split()[0]})")
        for c in found["checks"]:
            if c["id"] in COMPUTER_TOOLS:
                say(f"{c['label'].split(' (')[0]}: {c['status']}")

    found = report()
    show(found)
    if missing(found):
        if args.check:
            say("Install the missing tools with: python3 tools/tizen.py setup")
            return 1
        say("")
        say("Installing Tizen Studio's command-line tools and the Samsung TV extensions (a few minutes).")
        last = None

        def progress(text, percent):
            nonlocal last
            line = "  " + text + (f" ({percent}%)" if percent is not None and text != "Done" else "")
            if line != last:
                say(line)
                last = line
        try:
            installer(progress)
        except tv_setup.SetupError as error:
            print("ERROR: " + str(error), file=err)
            if error.output:
                print(error.output.strip()[-2000:], file=err)
            if error.command:
                print("By hand: " + error.command, file=err)
            return 1
        say("")
        found = report()
        show(found)
        if missing(found):
            print("ERROR: The tools are still not found after installing.", file=err)
            command = tv_setup.install_command()
            if command:
                print("By hand: " + command, file=err)
            return 1
    say("")
    say("This computer is ready.")
    say("")
    say("Next, prepare your TV (once per TV):")
    for n, line in enumerate(tv_setup.developer_steps(ip()), 1):
        say(f"  {n}. {line}")
    say("")
    say("Then find it and install your app (TV = its IP address or name):")
    say("  python3 tools/tizen.py scan")
    say("  python3 tools/tizen.py install APP.wgt TV")
    if not found["profiles"]["names"]:
        say("No signing certificate yet. Make one for your TV with one Samsung sign-in:")
    else:
        say("If the TV does not accept your certificate, add it with one Samsung sign-in:")
    say("  python3 tools/tizen.py tv cert add TV")
    return 0


def write_private(path, text):
    path = path.expanduser().absolute()
    if path.resolve().is_relative_to(ROOT):
        raise Usage("The DUID identifies your TV; save it outside this repository")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as stream:
        os.chmod(path, 0o600)
        stream.write(text + "\n")
    return path


def details_lines(tv):
    d, c = tv.get("details") or {}, tv.get("certificate") or {}
    for label, value in (("Network address", d.get("address")), ("Device ID (DUID)", d.get("duid")),
                         ("Model", d.get("model")), ("OS", d.get("os")), ("Firmware", d.get("firmware")),
                         ("Developer port", d.get("developer_port")), ("Link", d.get("sdk_link")),
                         ("Last seen", tv.get("last_seen") and time.strftime("%H:%M", time.localtime(tv["last_seen"]))),
                         ("This computer's IP to enter on the TV", d.get("host_ip"))):
        if value:
            yield f"  {label}: {value}"
    yield "  Certificate: " + certificate_text(tv)
    if tv.get("preview"):
        yield "  Showing: " + tv["preview"]["app"]


def certificate_text(tv):
    c, duid = tv.get("certificate") or {}, (tv.get("details") or {}).get("duid") or "this TV"
    ids, names = c.get("covers") or [], c.get("covers_names") or c.get("covers") or []
    # TV names first, raw IDs alongside: the CLI shows every detail.
    covers = ", ".join(n if n == i else f"{n} ({i})" for n, i in zip(names, ids)) or "no TVs"
    who = c.get("tv_name") or "this TV"
    others = c.get("others") or []
    if others and c.get("state") != "covered":
        return (f"Your profile {others[0]} allows {who} ({duid}), but installs use {c.get('profile') or 'no profile'}, "
                f"which doesn't. Run: tv cert use {tv['alias']} {others[0]}")
    return {"covered": f"{c.get('profile')} covers this TV ({duid})",
            "not-covered": f"You can't install your apps on {who} yet. Your certificate ({c.get('profile')}) "
                           f"only allows: {covers}. This TV is {duid}. Run: tv cert add {tv['alias']}",
            "no-profile": f"No Samsung certificate yet. This TV is {duid}. Run: tv cert add {tv['alias']}",
            "default": f"The active profile {c.get('profile')} uses the default Tizen distributor. "
                       f"Run: tv cert add {tv['alias']}"}.get(c.get("state"), c.get("state") or "unknown")


def apps_lines(tv):
    apps = tv.get("apps") or {}
    if apps.get("state") != "listed":
        yield "Apps: the TV did not return its app list" if apps.get("state") == "unreadable" else "Apps: not read"
        return
    store, mine = set(apps.get("store") or ()), set(apps.get("installed_here") or ())
    yield f"Apps on this TV ({len(apps['items'])}):"
    for app in apps["items"]:
        note = " — Store app, can't be viewed" if app in store else " — installed from here" if app in mine else ""
        yield "  " + app + note


def release(fleet, alias, say):
    """Disconnect whatever this run connected; a TV already released is fine."""
    if not find(fleet.state(), alias).get("held"):
        return
    try:
        fleet.disconnect(alias, {})
    except LiveError as error:
        say(str(error))


def connect(fleet, tv, say):
    """Connect, read identity and apps like the portal's Connect. Returns the fresh TV or None."""
    alias = tv["alias"]
    if (tv.get("dev_mode") or {}).get("state") == "not-enabled":
        # Same as the portal: a TV that reports Developer Mode off gets the next step, not a Connect.
        say(f"{title(tv)}: {tv['label']}")
        say(tv["dev_mode"].get("explanation") or tv["headline"])
        for line in guidance_lines(tv):
            say(line)
        return None
    say(f"{title(tv)}: Connecting…")
    try:
        fleet.connect(alias, {})
    except LiveError:
        tv = find(fleet.state(), alias)
        say(f"{title(tv)}: {tv['label']}")
        say(tv["error"] or tv["headline"])
        for line in guidance_lines(tv):
            say(line)
        return None
    for step in (fleet.identify, fleet.apps):
        try:
            step(alias, {})
        except LiveError:
            pass  # Shown below as a Connection problem or an unread app list.
    tv = find(fleet.state(), alias)
    if tv["status"] != "connected":
        release(fleet, alias, say)
        say(f"{title(tv)}: {tv['label']}")
        say(tv["error"] or tv["headline"])
        for line in guidance_lines(tv):
            say(line)
        return None
    return tv


def result(say, job):
    say(job.get("message") or ("Done" if job.get("ok") else "Failed"))
    if len(job.get("targets") or []) > 1:
        for target in job["targets"]:
            state = {"covered": "can install your apps", "waiting": "waiting for sign-in"}.get(
                target["state"], "not allowed yet")
            say(f"  {target['name']} ({target['alias']}): {state}")
    for step in job.get("steps") or []:
        # ASCII markers: a redirected Windows console may be cp1252.
        say(("  [done] " if step["done"] else "  [next] ") + step["text"].replace("→", "->").replace("…", "..."))
    for key, label in (("note", "Note"), ("backup", "Old certificate files backed up to"), ("command", "Command"),
                       ("next", "Next")):
        if job.get(key):
            say(f"{label}: {job[key]}")
    if job.get("output"):
        say(job["output"])
    return 0 if job.get("ok") else 1


def cert_wait(fleet, alias, job, say, sleep=time.sleep):
    """Follow the Samsung sign-in: each step once, as it happens; then the real outcome."""
    say(job["message"])
    say("Sign in to Samsung in your browser. If it didn't open, go to:")
    say("  " + job["url"])
    shown = set()
    try:
        while True:
            job = fleet.cert_result(alias) or {}
            for step in job.get("steps") or []:
                if step["text"] not in shown:
                    shown.add(step["text"])
                    say(("  [done] " if step["done"] else "  [....] ") + step["text"].replace("\u2026", "..."))
            if job.get("code") != "waiting":
                break
            sleep(CERT_POLL)
    except KeyboardInterrupt:
        try:
            fleet.cert(alias, {"cancel": True})
        except LiveError:
            pass
        say("Sign-in cancelled. Nothing was changed; run the same command to start a new sign-in.")
        return 1
    return result(say, dict(job, steps=None))


def cert_places():
    """(profiles.xml, certificate home) the Tizen CLI signs with."""
    return tv_fleet.default_data_root() / "profile" / "profiles.xml", Path.home() / "SamsungCertificate"


def cert_reset(names, yes, state_dir, say):
    """`cert reset PROFILE...`: remove exactly the named signing profiles. Never contacts a TV."""
    if not names:
        raise Usage("Use: tv cert reset PROFILE [PROFILE ...] --yes   (e.g. cert reset plx-tv tv-companion --yes)")
    profiles_path, cert_home = cert_places()
    try:
        plan = samsung_cert.reset_plan(names, profiles_path=profiles_path, cert_home=cert_home)
        if not yes:
            say("Would remove (nothing was changed; add --yes to remove):")
            for item in plan:
                say("  " + reset_line(item, pending=True))
            return 1
        receipt = samsung_cert.reset_profiles(names, profiles_path=profiles_path, cert_home=cert_home,
                                              backup_root=Path(state_dir) / "cert-backups")
    except samsung_cert.CertError as error:
        say("Refused: " + error.message + ". Nothing was changed.")
        say("Next: " + error.next)
        return 1
    except OSError as error:
        say("Certificate reset failed: " + type(error).__name__ + ". Check the backup folder before retrying.")
        return 1
    say("Certificate reset receipt (" + receipt["time"] + "):")
    for item in receipt["profiles"]:
        say("  " + reset_line(item))
    if receipt["backup"]:
        say("Backup: " + receipt["backup"])
    for error in receipt["keychain_errors"]:
        say("Keychain error: " + error)
    if receipt["keychain_errors"]:
        say("Next: open Keychain Access and delete the listed certificate-manager entries")
        return 1
    return 0


def reset_line(item, pending=False):
    if not item["entry"] and not item["folder"]:
        return item["profile"] + ": not present (nothing to remove)"
    parts = (["profiles.xml entry" + (" (active)" if item["active"] else "")] if item["entry"] else []) + \
        ([item["folder"]] if item["folder"] else []) + \
        ([str(len(item["keychain"])) + " Keychain password(s)"] if item["keychain"] else [])
    line = item["profile"] + (": would remove " if pending else ": removed ") + ", ".join(parts)
    if item["kept_files"]:
        line += "; kept (used from outside its folder): " + ", ".join(item["kept_files"])
    return line


def cert_batch(fleet, state, names, say):
    """`tv cert add TV1 TV2 ...`: read every TV's Device ID first, then one Samsung sign-in for all."""
    tvs = list({tv["alias"]: tv for tv in (find(state, name) for name in names)}.values())
    aliases = []
    try:
        for tv in tvs:
            connected = connect(fleet, tv, say)
            if connected is None:
                say("Nothing was changed: every TV must be connected before the one sign-in.")
                return 1
            if not (connected.get("details") or {}).get("duid"):
                say(f"{title(connected)}: the TV did not report its Device ID. Nothing was changed.")
                return 1
            aliases.append(connected["alias"])
        try:
            job = fleet.cert(aliases[0], {"with": aliases[1:]})
        except LiveError as error:
            say(str(error))
            return 1
    finally:
        for alias in [tv["alias"] for tv in tvs]:
            release(fleet, alias, say)  # The sign-in needs no TV link.
    if job.get("code") != "waiting":
        return result(say, job)
    return cert_wait(fleet, aliases[0], job, say)


SETUP_POLL = 3.0  # Seconds between checks while waiting for the TV or the installer.


def plain(text):
    """ASCII for a Windows console that may be cp1252."""
    return str(text).replace("\u2026", "...").replace("\u2713", "[done]").replace("\u2192", "->")


def guided(args, fleet, say, ask, sleep=time.sleep):
    """`tv setup`: the portal wizard's six steps and words, one prompt at a time."""
    alias, shown, connected, finished = None, None, False, set()
    say("Set up a TV: six steps from a new TV to your app on its screen. Ctrl+C stops at any time.")
    if args.tv:
        alias = find(fleet.detect(), args.tv)["alias"]

    def attempt(work):
        try:
            return work()
        except LiveError as error:
            say(plain(error))
            return None

    def go(label):
        answer = ask(f"Press Enter to {label[0].lower() + label[1:]} (q to quit): ")
        return answer is not None and answer.strip().lower() not in {"q", "quit"}

    try:
        while True:
            view = fleet.wizard({"tv": alias} if alias else {})
            if view["done"]:
                app = view["steps"][-1]["app"]
                say("")
                say(f"Done: {plain(view['steps'][-1]['text'])}")
                say(f"  Remote:      python3 tools/tizen.py tv key {view['alias']} Down Enter --app {app}")
                say(f"  Screenshot:  python3 tools/tizen.py tv screenshot {view['alias']} --app {app}")
                say("  Or open the portal: python3 tools/tizen.py tv portal")
                return 0
            for item in view["steps"]:
                if item["status"] == "done" and item["id"] not in finished:
                    finished.add(item["id"])
                    if shown and shown[0] == item["id"]:
                        say("[done] " + plain(item["text"]))
            step = next(s for s in view["steps"] if s["id"] == view["current"])
            if (step["id"], step["status"], step["text"]) != shown:
                shown = (step["id"], step["status"], step["text"])
                say("")
                say(f"Step {step['n']} of {len(view['steps'])}: {step['title']}")
                say(plain(step["text"]))
                for n, item in enumerate(step["items"], 1):
                    say(f"  {n}. {plain(item)}" if step["id"] == "developer-mode" else f"  {plain(item)}")
                for key, label in (("error", "Error"), ("output", "Output")):
                    if step.get(key):
                        say(f"{label}: {plain(step[key])}")
                if step["status"] == "failed" and step.get("command"):
                    say("Or do it by hand: " + step["command"])
                if step.get("status_line"):
                    say(plain(step["status_line"]))
            action = (step["actions"] or [{}])[0]
            status, ident = step["status"], step["id"]
            if status == "working":
                sleep(1 if ident == "computer" else SETUP_POLL)
                continue
            if ident == "computer" or action.get("action") == "install-sdk":
                if step.get("command") and status != "failed":
                    say("By hand instead: " + step["command"])
                if not go(action.get("label") or "Install Tizen Studio tools"):
                    return 1
                job, last = fleet.install_sdk({}), None
                while job.get("code") == "working":
                    line = job.get("message") + (f" ({job['percent']}%)" if job.get("percent") is not None else "")
                    if line != last:
                        say("  " + plain(line))
                        last = line
                    sleep(1)
                    job = dict(fleet.sdk_job)
                continue
            if ident == "developer-mode":
                if status == "failed" and not go("check again"):
                    return 1
                sleep(SETUP_POLL)
                state = fleet.detect()
                ready = [t for t in state["tvs"] if t.get("reachable") or t.get("held")]
                if not alias and len(ready) > 1:
                    say("Several TVs answered:")
                    for n, tv in enumerate(ready, 1):
                        say(f"  {n}. {title(tv)} ({tv['alias']})")
                    while alias is None:
                        answer = ask(f"Which one (1-{len(ready)}, q to quit)? ")
                        if answer is None or answer.strip().lower() in {"q", "quit"}:
                            return 1
                        if answer.strip().isdigit() and 1 <= int(answer) <= len(ready):
                            alias = ready[int(answer) - 1]["alias"]
                elif not alias and ready:
                    alias = ready[0]["alias"]
                continue
            alias = alias or view["alias"]
            if ident == "connect":
                if status == "failed" and not go("try again"):
                    return 1
                connected = connect(fleet, find(fleet.state(), alias), say) is not None or connected
                if not connected and not go("try again"):
                    return 1
                continue
            if ident == "permission" or (ident == "app" and action.get("action") == "cert"):
                if not action:
                    if step.get("command"):
                        say("Command: " + step["command"])
                    if not go("check again"):
                        return 1
                    fleet._setup_report = None
                    continue
                if not go(action["label"]):
                    return 1
                if action["action"] == "profile":
                    attempt(lambda: result(say, fleet.profile(alias, action["data"])))
                    continue
                job = attempt(lambda: fleet.cert(alias, {})) or {}
                if job.get("code") == "waiting":
                    cert_wait(fleet, alias, job, say, sleep)
                elif job:
                    result(say, job)
                continue
            if ident == "app":
                answer = ask("Path to your .wgt, or press Enter to install a sample app (q to quit): ")
                if answer is None or answer.strip().lower() in {"q", "quit"}:
                    return 1
                path = answer.strip().strip('"').strip("'")
                if path:
                    say("Installing (signing it with your certificate, then checking the TV's app list)...")
                    attempt(lambda: result(say, fleet.install(alias, {"package": str(Path(path).expanduser().absolute())})))
                else:
                    say("Installing the sample app (signing it with your certificate)...")
                    attempt(lambda: result(say, fleet.sample(alias, {})))
                continue
            if ident == "see":
                if status == "failed" and not go("try again"):
                    return 1
                if attempt(lambda: fleet.preview(alias, action["data"]) or True) is None and not go("try again"):
                    return 1
                continue
            return 1
    finally:
        if alias:
            release(fleet, alias, say)


def act(args, fleet, tv, say):
    """Everything that needs the developer link: connect, act, and always disconnect."""
    command = args.command
    connected = connect(fleet, tv, say)
    if connected is None:
        return 1
    alias = connected["alias"]
    try:
        if command == "info" and args.app:
            return app_info(fleet, alias, connected, args.app, say)
        if command in {"connect", "info"}:
            say(f"{title(connected)}: {connected['label']}")
            say(connected["headline"])
            for line in details_lines(connected):
                say(line)
            for line in apps_lines(connected):
                say(line)
            return 0
        if command == "apps":
            for line in apps_lines(connected):
                say(line)
            return 0 if (connected.get("apps") or {}).get("state") == "listed" else 1
        if command == "duid":
            duid = (connected.get("details") or {}).get("duid")
            say(duid or "The TV did not report its Device ID")
            return 0 if duid else 1
        if command == "cert":
            if getattr(args, "use", None):
                return result(say, fleet.profile(alias, {"profile": args.use[1]}))
            if not args.add:
                say("Certificate: " + certificate_text(connected))
                return 0
            job = fleet.cert(alias, {})
            if job.get("code") != "waiting":
                code = result(say, job)
                if job.get("use_profile"):
                    say(f"Run: tv cert use {alias} {job['use_profile']}")
                return code
            return cert_wait(fleet, alias, job, say)
        if command == "open":
            try:
                fleet.preview(alias, {"app": args.app})
            except LiveError as error:
                say(str(error))
                return 1
            preview = find(fleet.state(), alias)["preview"]
            say(f"Showing {args.app} on {title(connected)} ({len(preview['frames'])} picture layer(s)). "
                "The app keeps running on the TV.")
            return 0
        if command in {"screenshot", "key"}:
            if not args.app:
                raise Usage(f"Name the app: tv {command} {args.tv} ... --app APP_ID (see `tv apps {args.tv}`)")
            if command == "key":
                bad = [k for k in args.keys if k not in live_tv.KEYS]
                if bad:
                    raise Usage("Unsupported key " + ", ".join(bad) + "; keys: " + ", ".join(KEYS))
            try:
                fleet.preview(alias, {"app": args.app})
            except LiveError as error:
                say(str(error))
                return 1
            if command == "screenshot":
                folder = private_output(args.out) if args.out else None
                return result(say, fleet.screenshot(alias, {}, folder=folder))
            source = fleet.preview_source(alias)
            for key in args.keys:
                source.operate("key", {"key": key})
            say(f"Sent {', '.join(args.keys)} to {args.app} ({source.command_ms} ms for the last key)")
            return 0
        if command == "install":
            data = {"package": str(Path(args.package).expanduser().absolute())}
            if args.sign:
                data["sign"] = args.sign
            return result(say, fleet.install(alias, data))
        if command == "uninstall":
            job = fleet.remove(alias, {"app": args.app})
            code = result(say, job)
            say("Receipt: " + time.strftime("%Y-%m-%d %H:%M:%S") + f" {title(connected)} ({alias}) app {args.app}: "
                + job.get("code", "failed") + (" (verified by the TV's app list)" if job.get("ok") else ""))
            return code
        return result(say, getattr(fleet, command)(alias, {"app": args.app}))
    finally:
        release(fleet, alias, say)


APP_UNKNOWN = {
    "unreadable": "the TV's app list could not be read; try again",
    "names-not-listed": "this TV lists app IDs only, so a name can't be checked; use the app ID (see `tv apps TV`)",
    "ambiguous": "several apps have that name; use the app ID (see `tv apps TV`)",
}


def app_info(fleet, alias, tv, query, say):
    """Installed yes/no/unknown, and the version only when the TV itself lists one."""
    found = fleet.app_info(alias, {"app": query})
    say(f"{title(tv)}: {tv['label']}")
    if found["installed"] == "yes":
        say(f"{found['app']}: installed" + (f" ({found['title']})" if found.get("title") else ""))
        say(f"  Version: {found['version']} (as the TV lists it)" if found.get("version")
            else "  Version: unknown (this TV's app list does not show versions)")
        return 0
    if found["installed"] == "no":
        say(f"{query}: not installed")
        return 1
    say(f"{query}: unknown — " + APP_UNKNOWN.get(found.get("reason"), "the TV did not say"))
    return 1


def profile_source(args, profile, config, cdp_factory, say):
    """A target configured in the profile (emulator or a known inspector): keys/screenshot directly."""
    if args.command == "key":
        bad = [k for k in args.keys if k not in live_tv.KEYS]
        if bad:
            raise Usage("Unsupported key " + ", ".join(bad) + "; keys: " + ", ".join(KEYS))
    app = live_tv.App(profile, config, cdp_factory)
    source = app.sources[args.tv]
    source.config["user_activation"] = True  # The person typing the key is the activation.
    try:
        source.operate("connect", {"authorized": True, "pixels": args.command == "screenshot",
                                   "input": args.command == "key"})
        if args.command == "key":
            for key in args.keys:
                source.operate("key", {"key": key})
            say("Sent " + ", ".join(args.keys) + f" ({source.command_ms} ms for the last key)")
            return 0
        folder = private_output(args.out or Path.home() / "Pictures" / "TV Screenshots")
        source.operate("capture", {})
        stamp = time.strftime("%Y-%m-%d-%H%M%S")
        for index, frame in enumerate(source.frames):
            suffix = "" if len(source.frames) == 1 else f"-layer{index + 1}"
            target = folder / f"{args.tv}-{stamp}{suffix}.png"
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(frame["raw"])
            say("Saved " + str(target))
        say("The app's drawing only: the TV's video plane and menus are not in it")
        return 0
    finally:
        app.close()


def terminal_ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        return None


def run(args, fleet_factory, cdp_factory, out, state_dir=STATE_DIR, setup=default_setup, ask=None):
    say = lambda line="": print(line, file=out)  # noqa: E731
    args.command = ALIASES.get(args.command, args.command)
    interactive = args.command == "setup" and not args.check and (
        ask is not None or (sys.stdin.isatty() and getattr(out, "isatty", lambda: False)()))
    if args.command == "setup" and not interactive:
        report = setup()
        for line in setup_lines(report):
            say(line)
        return 0 if report["ready"] else 1
    if args.command == "cert" and args.words[0] == "reset":
        return cert_reset(args.words[1:], args.yes, state_dir, say)
    if args.command == "cert":
        words = args.words
        args.add = words[0] == "add" and len(words) >= 2
        args.use = words[1:] if words[0] == "use" and len(words) == 3 else None
        if not args.use and ((not args.add and len(words) != 1) or words in (["add"], ["use"])):
            raise Usage("Use: tv cert TV  or  tv cert add TV [TV ...]  or  tv cert use TV PROFILE")
        args.tvs = words[1:] if args.add else []
        args.tv = args.use[0] if args.use else (words[1] if args.add else words[-1])
    profile = config = None
    chosen = args.profile.expanduser().absolute() if args.profile is not None else \
        default_profile(state_dir, create=args.command in {"portal", "rename"})
    if chosen is not None:
        profile, config = read_profile(chosen)
    if args.command == "list":
        cache = load_cache(state_dir)
        if not cache:
            say("No scan yet. Run `tv scan` to look for TVs on your network.")
            return 0
        names = (config or {}).get("tv_names", {})
        tvs = [dict(t, name=names.get(t.get("alias"), t.get("name"))) for t in cache["tvs"]]
        minutes = max(0, int(time.time()) - int(cache.get("scanned_at", 0))) // 60
        say(f"From the last scan ({minutes} min ago; run `tv scan` to refresh):")
        for line in card_lines({"tvs": tvs}):
            say(line)
        return 0
    if args.command == "portal":
        def announce(url):
            say("Samsung TV Developer Companion running. Open " + url + " in your browser.")
            say("Opening the page scans your network for TVs (read-only). Press Ctrl+C to stop.")
            out.flush()
        live_tv.serve(profile, port=args.port, announce=announce)
        return 0
    if args.command in {"key", "screenshot"} and args.tv in ((config or {}).get("sources") and
                                                             {s["id"] for s in config["sources"]} or ()):
        return profile_source(args, profile, config, cdp_factory, say)
    save = save_known = None
    if profile is not None:
        app = live_tv.App(profile, config, cdp_factory)
        save, save_known = app.save_names, app.save_known
    fleet = fleet_factory(certificate_duids=(config or {}).get("certificate_duids"),
                          names=(config or {}).get("tv_names", {}), save_names=save,
                          known=(config or {}).get("known_tvs", {}), save_known=save_known)
    try:
        if interactive:
            return guided(args, fleet, say, ask or terminal_ask)
        state = fleet.detect()
        save_cache(state_dir, state)
        if args.command == "scan":
            if args.json:
                say(json.dumps(state, indent=2))
            else:
                for line in card_lines(state):
                    say(line)
            return 0
        tv = find(state, args.tv)
        if args.command == "cert" and args.add and len({find(state, t)["alias"] for t in args.tvs}) > 1:
            return cert_batch(fleet, state, args.tvs, say)
        if args.command == "rename":
            tv = find(fleet.rename(tv["alias"], {"name": args.name}), tv["alias"])
            say(f"Renamed {tv['alias']} to {title(tv)}")
            return 0
        if args.command == "info" and not (args.connect or args.show_duid or args.app):
            say(f"{title(tv)}: {tv['label']}")
            say(tv["headline"])
            for line in details_lines(tv):
                say(line)
            return 0
        if args.command == "install" and "://" in args.package:
            download = download_package(args.package)
            with download as path:
                say(f"Downloaded {download.public} ({max(1, download.size // 1024)} KB): app {download.app}"
                    + (f", package version {download.version} (from the file, not read from the TV)"
                       if download.version else ""))
                args.package = str(path)
                return act(args, fleet, tv, say)
        return act(args, fleet, tv, say)
    finally:
        fleet.close()


def main(argv=None, fleet_factory=default_fleet, cdp_factory=live_tv.CDP, stdout=None, stderr=None,
         state_dir=STATE_DIR, setup=default_setup, ask=None):
    out, err = stdout or sys.stdout, stderr or sys.stderr
    if sys.version_info < (3, 10):
        print("tv: Python 3.10 or newer is required (this is " + sys.version.split()[0] + ")", file=err)
        return 1
    cli = parser()
    try:
        args = cli.parse_args(argv)
    except SystemExit as stop:
        return stop.code
    try:
        return run(args, fleet_factory, cdp_factory, out, state_dir, setup, ask)
    except Usage as error:
        print("tv: " + str(error), file=err)
        return 2
    except LiveError as error:
        print("tv: " + str(error), file=err)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
