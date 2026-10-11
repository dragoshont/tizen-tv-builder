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
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_tv  # noqa: E402
from live_tv import LiveError, read_profile  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KEYS = sorted(live_tv.KEYS)
PORTAL_PORT = 47811
STATE_DIR = Path.home() / ".tizen-tv-builder"
# The scan cache is a file on disk: names and status only, never addresses or Device IDs.
CACHE_FIELDS = ("alias", "name", "label", "headline", "status", "reachable", "year", "dev_mode", "metadata")
ALIASES = {"status": "scan", "check": "connect", "identify": "connect", "remove": "uninstall"}


def parser():
    cli = argparse.ArgumentParser(prog="tv", description=__doc__.split("\n\n")[0],
                                  formatter_class=argparse.RawDescriptionHelpFormatter, epilog=(
        "commands (TV = name or tv-xxxxxx alias):\n"
        "  scan | list            Find TVs on your network / show the last scan\n"
        "  connect TV             Connect: status, details, certificate and apps, then disconnect\n"
        "  apps TV                Every app ID on the TV\n"
        "  open TV APP            Open an app's screen (restarts the app)\n"
        "  screenshot TV --app APP [--out DIR]   Save the app's picture as PNG\n"
        "  key TV KEY... --app APP               Send remote keys to the app: " + ", ".join(KEYS) + "\n"
        "  install TV WGT [--sign PROFILE]       Sign with the active profile and install\n"
        "  uninstall TV APP       Uninstall an app\n"
        "  cert TV | cert add TV  Certificate coverage / add this TV to your certificate\n"
        "  duid TV                Print the TV's Device ID (DUID)\n"
        "  rename TV NAME         Name a TV\n"
        "  setup                  What this computer has for signing and installing\n"
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
    item = sub.add_parser("rename", help="Name a TV")
    item.add_argument("tv")
    item.add_argument("name")
    sub.add_parser("setup", help="Check sdb, the tizen CLI and signing profiles; installs nothing")
    item = sub.add_parser("cert", help="Certificate coverage for a TV, or `cert add TV`")
    item.add_argument("words", nargs="+", metavar="TV | add TV")
    item = sub.add_parser("install", help="Sign a .wgt with the active profile and install it")
    item.add_argument("tv")
    item.add_argument("package", type=Path)
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


def default_fleet(certificate_duids=None, names=None, save_names=None):
    import tv_fleet
    return tv_fleet.Fleet(certificate_duids=certificate_duids, names=names or {}, save_names=save_names)


def card_lines(state):
    """One line per TV with the portal's status words, then its details in full."""
    tvs = state.get("tvs", [])
    if state.get("sdk") == "missing":
        yield "Tizen Studio's sdb was not found; showing network results only."
    if not tvs:
        yield "No TVs found. Turn on Developer Mode on the TV (Apps, then 1 2 3 4 5), then scan again."
        return
    width = max(len(tv["name"]) for tv in tvs)
    for tv in tvs:
        yield f"{tv['name']:<{width}}  {tv['alias']}  {tv['label']}"
        details = tv.get("details") or {}
        yield "    " + " · ".join(describe(tv.get("metadata") or {}) +
                                  ([details["address"]] if details.get("address") else []))
        if tv.get("headline") and tv["headline"] != tv["label"]:
            yield "    " + tv["headline"]


def model_year_text(meta):
    year = (meta.get("model_year") or {})
    if not year.get("value"):
        return "Model year unknown"
    source = {"reported by the TV": "reported", "inferred from the model code": "from model code"}
    return f"Model year {year['value']} ({source.get(year.get('provenance'), year.get('provenance'))})"


def describe(meta):
    """Presentation only; the metadata itself comes from tv_fleet.Fleet.metadata, shared with the portal."""
    parts = [meta.get("model") or "Model unknown", model_year_text(meta),
             (meta.get("os") or {}).get("text", "OS unknown")]
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
        yield f"  {number}. {step}"
    if guide.get("host_ip"):
        yield "This computer's IP to enter on the TV: " + guide["host_ip"]


def find(state, query):
    """A TV by alias or name, any case."""
    wanted = query.strip().casefold()
    matches = [t for t in state["tvs"] if wanted in (t["alias"].casefold(), str(t.get("name", "")).casefold())]
    if len(matches) != 1:
        names = ", ".join(f"{t['name']} ({t['alias']})" for t in state["tvs"]) or "none found"
        raise LiveError("unknown", ("Several TVs are called " if matches else "No TV called ") + query +
                        "; TVs: " + names)
    return matches[0]


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
                         ("Model", d.get("model")), ("OS", d.get("os")), ("Firmware", d.get("firmware"))):
        if value:
            yield f"  {label}: {value}"
    yield "  Certificate: " + certificate_text(tv)
    if tv.get("preview"):
        yield "  Showing: " + tv["preview"]["app"]


def certificate_text(tv):
    c, duid = tv.get("certificate") or {}, (tv.get("details") or {}).get("duid") or "this TV"
    covers = ", ".join(c.get("covers") or []) or "no TVs"
    return {"covered": f"{c.get('profile')} covers this TV ({duid})",
            "not-covered": f"{c.get('profile')} covers: {covers}. This TV is {duid}. Run: tv cert add {tv['alias']}",
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
    say(f"{tv['name']}: Connecting…")
    try:
        fleet.connect(alias, {})
    except LiveError:
        tv = find(fleet.state(), alias)
        say(f"{tv['name']}: {tv['label']}")
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
        say(f"{tv['name']}: {tv['label']}")
        say(tv["error"] or tv["headline"])
        for line in guidance_lines(tv):
            say(line)
        return None
    return tv


def result(say, job):
    say(job.get("message") or ("Done" if job.get("ok") else "Failed"))
    for key, label in (("backup", "Old certificate files backed up to"), ("command", "Command"), ("next", "Next")):
        if job.get(key):
            say(f"{label}: {job[key]}")
    if job.get("output"):
        say(job["output"])
    return 0 if job.get("ok") else 1


def act(args, fleet, tv, say):
    """Everything that needs the developer link: connect, act, and always disconnect."""
    command = args.command
    connected = connect(fleet, tv, say)
    if connected is None:
        return 1
    alias = connected["alias"]
    try:
        if command == "connect":
            say(f"{connected['name']}: {connected['label']}")
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
            if not args.add:
                say("Certificate: " + certificate_text(connected))
                return 0
            return result(say, fleet.cert(alias, {}))
        if command == "open":
            try:
                fleet.preview(alias, {"app": args.app})
            except LiveError as error:
                say(str(error))
                return 1
            preview = find(fleet.state(), alias)["preview"]
            say(f"Showing {args.app} on {connected['name']} ({len(preview['frames'])} picture layer(s)). "
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
            data = {"package": str(args.package.expanduser().absolute())}
            if args.sign:
                data["sign"] = args.sign
            return result(say, fleet.install(alias, data))
        verb = "remove" if command == "uninstall" else command
        return result(say, getattr(fleet, verb)(alias, {"app": args.app}))
    finally:
        release(fleet, alias, say)


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


def run(args, fleet_factory, cdp_factory, out, state_dir=STATE_DIR, setup=default_setup):
    say = lambda line="": print(line, file=out)  # noqa: E731
    args.command = ALIASES.get(args.command, args.command)
    if args.command == "setup":
        report = setup()
        for line in setup_lines(report):
            say(line)
        return 0 if report["ready"] else 1
    if args.command == "cert":
        words = args.words
        args.add = words[0] == "add" and len(words) == 2
        if len(words) != (2 if args.add else 1) or words == ["add"]:
            raise Usage("Use: tv cert TV  or  tv cert add TV")
        args.tv = words[-1]
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
            say("Nothing is scanned until you choose Scan. Press Ctrl+C to stop.")
            out.flush()
        live_tv.serve(profile, port=args.port, announce=announce)
        return 0
    if args.command in {"key", "screenshot"} and args.tv in ((config or {}).get("sources") and
                                                             {s["id"] for s in config["sources"]} or ()):
        return profile_source(args, profile, config, cdp_factory, say)
    save = None
    if profile is not None:
        save = live_tv.App(profile, config, cdp_factory).save_names
    fleet = fleet_factory(certificate_duids=(config or {}).get("certificate_duids"),
                          names=(config or {}).get("tv_names", {}), save_names=save)
    try:
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
        if args.command == "rename":
            tv = find(fleet.rename(tv["alias"], {"name": args.name}), tv["alias"])
            say(f"Renamed {tv['alias']} to {tv['name']}")
            return 0
        return act(args, fleet, tv, say)
    finally:
        fleet.close()


def main(argv=None, fleet_factory=default_fleet, cdp_factory=live_tv.CDP, stdout=None, stderr=None,
         state_dir=STATE_DIR, setup=default_setup):
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
        return run(args, fleet_factory, cdp_factory, out, state_dir, setup)
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
