#!/usr/bin/env python3
"""tv: the TV portal's capabilities from a terminal.

Every command uses the same code paths as the portal (tv_fleet.Fleet for network TVs,
live_tv.Source for profile-configured inspector targets). Output shows names and
redacted aliases only, never TV addresses, DUIDs or certificate data.

Each run is one-shot: TVs are scanned in-process (addresses are never stored), and
anything this command connects or forwards is released before it exits.
No setup is needed: names and settings live in a private profile that is created
empty on first use (~/.tizen-tv-builder/profile.json, or $TIZEN_TV_PROFILE).
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
CACHE_FIELDS = ("alias", "name", "label", "headline", "status", "reachable", "year", "dev_mode", "install",
                "metadata")


def parser():
    cli = argparse.ArgumentParser(prog="tv", description=__doc__.split("\n\n")[0],
                                  formatter_class=argparse.RawDescriptionHelpFormatter, epilog=(
        "commands:\n"
        "  scan | status          Scan the local network for Samsung TVs (ports 26101, 8001, SSDP)\n"
        "  list                   Show the TVs from the last scan (no network contact)\n"
        "  check ALIAS            Test Developer Mode: port open, connection accepted, identity\n"
        "  connect ALIAS          Connect, identify (install readiness), then disconnect\n"
        "  info ALIAS [--connect] Every detail the portal shows for this TV (--show-duid to print the DUID)\n"
        "  setup                  What this computer has for signing and installing (detects only)\n"
        "  duid ALIAS             Read the TV's certificate DUID (hidden unless --show or --out)\n"
        "  cert ALIAS             Which certificate this TV needs, with the DUID saved privately\n"
        "  install ALIAS WGT --sign PROFILE --yes   Re-sign a private copy and install it\n"
        "  launch | remove ALIAS APP_ID --yes       Start or uninstall an app on the TV\n"
        "  rename ALIAS NAME      Name a scanned TV (saved in the private profile)\n"
        "  key TV KEY... --yes    Send remote keys to a profile TV: " + ", ".join(KEYS) + "\n"
        "  screenshot TV --out DIR --yes   Save the TV's renderer layers as private PNGs\n"
        "  portal                 Start the portal in your browser (same features)\n"))
    cli.add_argument("--profile", type=Path, help="Private 0600 profile; optional, a default is created")
    sub = cli.add_subparsers(dest="command", required=True, metavar="command")
    for name in ("scan", "status"):
        item = sub.add_parser(name, help="Scan the network for TVs and list them")
        item.add_argument("--json", action="store_true", help="Print the same state the portal shows")
    sub.add_parser("list", help="Show the TVs from the last scan without contacting them")
    item = sub.add_parser("check", help="Test whether a scanned TV accepts a Developer Mode connection")
    item.add_argument("alias")
    item = sub.add_parser("rename", help="Name a scanned TV")
    item.add_argument("alias")
    item.add_argument("name")
    item = sub.add_parser("connect", help="Connect to a scanned TV, identify it, then disconnect")
    item.add_argument("alias")
    item.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)  # Accepted for older scripts.
    item = sub.add_parser("info", help="Show every detail the portal shows for a scanned TV")
    item.add_argument("alias")
    item.add_argument("--connect", action="store_true",
                      help="Also connect to read the Tizen version, DUID and certificate match, then disconnect")
    item.add_argument("--show-duid", action="store_true", help="Print the DUID (implies --connect)")
    sub.add_parser("setup", help="Check sdb, the tizen CLI and signing profiles; installs nothing")
    item = sub.add_parser("duid", help="Read the TV's certificate DUID over the developer link")
    item.add_argument("alias")
    item.add_argument("--show", action="store_true", help="Print the DUID (it identifies this TV)")
    item.add_argument("--out", type=Path, help="Save the DUID to this private file (mode 0600) instead")
    item = sub.add_parser("cert", help="Explain which certificate this TV needs; saves its DUID privately")
    item.add_argument("alias")
    item = sub.add_parser("install", help="Re-sign a .wgt with a signing profile and install it")
    item.add_argument("alias")
    item.add_argument("package", type=Path)
    item.add_argument("--sign", required=True, metavar="PROFILE", help="Tizen Studio signing profile name")
    item.add_argument("--yes", action="store_true", help="Confirm: the app is installed on the TV")
    for name, verb in (("launch", "started"), ("remove", "uninstalled")):
        item = sub.add_parser(name, help=f"An app is {verb} on the TV")
        item.add_argument("alias")
        item.add_argument("app")
        item.add_argument("--yes", action="store_true", help=f"Confirm: the app is {verb} on the TV")
    item = sub.add_parser("key", help="Send remote keys to a profile TV")
    item.add_argument("tv")
    item.add_argument("keys", nargs="+", metavar="key")
    item.add_argument("--yes", action="store_true", help="Confirm: remote keys will be sent")
    item = sub.add_parser("screenshot", help="Save renderer layers of a profile TV")
    item.add_argument("tv")
    item.add_argument("--out", type=Path, required=True, help="Private folder outside this repository")
    item.add_argument("--yes", action="store_true", help="Confirm: images may show account info")
    item = sub.add_parser("portal", help="Start the portal in your browser")
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
    tvs = state.get("tvs", [])
    if state.get("sdk") == "missing":
        yield "Tizen Studio's sdb was not found; showing network results only."
    if not tvs:
        yield "No TVs found. Turn on Developer Mode on the TV (Apps, then 1 2 3 4 5), then scan again."
        return
    width = max(len(tv["name"]) for tv in tvs)
    for tv in tvs:
        dev = tv.get("dev_mode") or {}
        yield f"{tv['name']:<{width}}  {tv['alias']}  {tv['label']:<16}  {dev.get('label', 'Dev mode unknown')}"
        yield "    " + " · ".join(describe(tv.get("metadata") or {}))
        if dev.get("next") and dev["next"] != "None":
            yield "    Next: " + dev["next"]


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


def find(state, alias):
    tv = next((t for t in state["tvs"] if t["alias"] == alias), None)
    if tv is None:
        raise LiveError("unknown", "No TV with that name was found; run `tv scan` and use its tv-xxxxxx alias")
    return tv


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


CERT_TESTED_YEARS = {2017, 2019}  # Default certificate install observed in Developer Mode on these years.


def first_run(args, fleet, tv, say, state_dir):
    """duid/cert/install/launch/remove: connect, act, and always disconnect what was connected."""
    alias = tv["alias"]
    if args.command in {"install", "launch", "remove"} and not args.yes:
        raise Usage("Add --yes to confirm: this changes the apps on " + tv["name"])
    try:
        fleet.connect(alias, {"confirm": alias})
    except LiveError:
        tv = find(fleet.state(), alias)
        dev = tv.get("dev_mode") or {}
        say(f"{tv['label']}: {dev.get('explanation') or tv['error'] or tv['headline']}")
        if dev.get("next"):
            say("Next: " + dev["next"])
        return 1
    try:
        if args.command == "duid":
            duid = fleet.read_duid(alias)
            if args.out:
                say("Saved the DUID to " + str(write_private(args.out, duid)) + " (mode 0600)")
            elif args.show:
                say(duid)
            else:
                say("DUID read; hidden because it identifies this TV. Add --show to print it or --out FILE to save it.")
            say("This is the certificate DUID from the developer link, not the `duid` the TV's network API reports.")
            return 0
        if args.command == "cert":
            duid = fleet.read_duid(alias)
            handoff = write_private(private_dir(Path(state_dir)) / f"duid-{alias}.txt", duid)
            year = ((tv.get("metadata") or {}).get("model_year") or {}).get("value")
            tested = year in CERT_TESTED_YEARS
            say(f"{tv['name']} ({year or 'model year unknown'})")
            say("1. Try the default certificate first: a Tizen Studio author certificate with the default "
                "Tizen distributor certificate. No Samsung account is needed in Developer Mode.")
            say("   " + ("Installs were observed on a TV from this year." if tested else
                         "Not yet tested, so unverified for this model year: installs were observed on 2017 and 2019 TVs only."))
            say(f"2. If install says the certificate chain is invalid (-12), make a Samsung certificate with tizen-cert:")
            say("   Create a new profile for TV, sign in with your Samsung account, and paste the DUID from")
            say("   " + str(handoff) + " (private, mode 0600; delete it when done).")
            say(f"3. Then: tv install {alias} APP.wgt --sign PROFILE --yes")
            return 0
        if args.command == "install":
            result = fleet.install(alias, {"confirm": alias, "package": str(args.package.expanduser().absolute()),
                                           "sign": args.sign})
        else:
            result = getattr(fleet, args.command)(alias, {"confirm": alias, "app": args.app})
        say(result["message"])
        if result.get("next"):
            say("Next: " + result["next"])
        return 0 if result["ok"] else 1
    finally:
        fleet.disconnect(alias, {})


def run(args, fleet_factory, cdp_factory, out, state_dir=STATE_DIR, setup=default_setup):
    say = lambda line="": print(line, file=out)  # noqa: E731
    if args.command == "setup":
        report = setup()
        for line in setup_lines(report):
            say(line)
        return 0 if report["ready"] else 1
    profile = config = app = None
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
            say("TV portal running. Open " + url + " in your browser.")
            say("Nothing is scanned until you choose Scan. Press Ctrl+C to stop.")
            out.flush()
        live_tv.serve(profile, port=args.port, announce=announce)
        return 0
    if args.command in {"key", "screenshot"}:
        if profile is None:
            raise Usage(args.command + " needs --profile with the TV's inspector settings")
        if not args.yes:
            raise Usage("Add --yes to confirm: " + ("remote keys will be sent" if args.command == "key"
                                                     else "screen images may show account info"))
        if args.command == "key":
            bad = [k for k in args.keys if k not in live_tv.KEYS]
            if bad:
                raise Usage("Unsupported key " + ", ".join(bad) + "; allowed: " + ", ".join(KEYS))
        app = live_tv.App(profile, config, cdp_factory)
        if args.tv not in app.sources:
            raise Usage("No TV named " + args.tv + " in the profile")
        source = app.sources[args.tv]
        try:
            source.operate("connect", {"authorized": True, "pixels": args.command == "screenshot",
                                       "input": args.command == "key"})
            if args.command == "key":
                for key in args.keys:
                    source.operate("key", {"key": key})
                say("Sent " + ", ".join(args.keys) + f" ({source.command_ms} ms for the last key)")
            else:
                folder = private_output(args.out)
                source.operate("capture", {})
                for index, frame in enumerate(source.frames):
                    target = folder / f"{args.tv}-{source.revision[:8]}-{index}.png"
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(frame["raw"])
                say(f"Saved {len(source.frames)} image(s) to {folder}")
                say("Note: renderer layers, not the full panel or video plane")
        finally:
            app.close()
        return 0

    save = None
    if profile is not None:
        save = live_tv.App(profile, config, cdp_factory).save_names
    fleet = fleet_factory(certificate_duids=(config or {}).get("certificate_duids"),
                          names=(config or {}).get("tv_names", {}), save_names=save)
    try:
        state = fleet.detect()
        save_cache(state_dir, state)
        if args.command in {"scan", "status"}:
            if args.json:
                say(json.dumps(state, indent=2))
            else:
                for line in card_lines(state):
                    say(line)
            return 0
        tv = find(state, args.alias)
        if args.command == "rename":
            tv = find(fleet.rename(args.alias, {"name": args.name}), args.alias)
            say(f"Renamed {args.alias} to {tv['name']}")
            return 0
        if args.command == "check":
            return check(fleet, tv, say)
        if args.command == "info":
            return info(args, fleet, tv, say)
        if args.command in {"duid", "cert", "install", "launch", "remove"}:
            return first_run(args, fleet, tv, say, state_dir)
        say(f"Connecting to {tv['name']}…")
        try:
            fleet.connect(args.alias, {"confirm": args.alias})
        except LiveError:
            tv = find(fleet.state(), args.alias)
            say(f"{tv['label']}: {tv['error'] or tv['headline']}")
            for line in guidance_lines(tv):
                say(line)
            return 1
        try:
            fleet.identify(args.alias, {})
        except LiveError:
            pass  # Shown on the card below.
        tv = find(fleet.state(), args.alias)
        say(tv["headline"])
        for line in guidance_lines(tv):
            say(line)
        fleet.disconnect(args.alias, {})
        say("Disconnected")
        return 0
    finally:
        fleet.close()


CERTIFICATE_TEXT = {"match": "includes this TV", "mismatch": "does not include this TV",
                    "unconfigured": "not configured (install needs a certificate with this TV)"}


def detail_rows(tv):
    """The portal's TV detail panel (ui.js), row for row, from the same Fleet state."""
    meta, d = tv.get("metadata") or {}, tv.get("details") or {}
    year = meta.get("model_year") or {}
    os_info = meta.get("os")
    dev = tv.get("dev_mode") or {}
    return [("Name", tv.get("name")), ("Alias", tv.get("alias")), ("Status", tv.get("label")),
            ("Name source", "Your name" if tv.get("renamed") else
             "From the TV" if tv.get("default_name") == tv.get("name") else "Default"),
            ("Network address", d.get("network")), ("Developer port", d.get("developer_port")),
            ("Link", d.get("sdk_link")), ("Device ID (DUID)", d.get("duid")),
            ("Certificate", CERTIFICATE_TEXT.get(tv.get("certificate"), tv.get("certificate"))),
            ("Manufacturer", meta.get("manufacturer")), ("Model", meta.get("model")), ("Family", meta.get("family")),
            ("Model year", f"{year['value']} ({year.get('provenance')})" if year.get("value") else "Unknown"),
            ("OS", f"{os_info['text']} ({os_info.get('provenance')})" if os_info else None),
            ("Platform baseline", meta.get("baseline")), ("Resolution", meta.get("resolution")),
            ("The Frame", "Yes" if meta.get("frame_tv") else None), ("Firmware", meta.get("firmware")),
            ("Developer Mode", dev.get("explanation") or dev.get("label")),
            ("This computer's IP to enter on the TV", d.get("host_ip")),
            ("Next", dev.get("next") if dev.get("next") not in (None, "", "None") else None),
            ("Install", tv.get("install"))]


def info(args, fleet, tv, say):
    """Read-only by default; --connect adds the identity rows and always disconnects what it connected."""
    alias, duid, code = tv["alias"], None, 0
    if args.connect or args.show_duid:
        try:
            fleet.connect(alias, {"confirm": alias})
        except LiveError:
            code = 1
        else:
            try:
                fleet.identify(alias, {})
                if args.show_duid:
                    duid = fleet.read_duid(alias)
            except LiveError:
                code = 1
            finally:
                tv = find(fleet.state(), alias)
                fleet.disconnect(alias, {})
        after = find(fleet.state(), alias)
        tv = dict(after, identity=tv["identity"], certificate=tv["certificate"], metadata=tv["metadata"],
                  details=dict(after["details"], duid=tv["details"]["duid"]))
    rows = detail_rows(tv)
    if duid:
        rows = [(k, duid if k == "Device ID (DUID)" else v) for k, v in rows]
    width = max(len(k) for k, v in rows if v not in (None, ""))
    for key, value in rows:
        if value not in (None, ""):
            say(f"{key:<{width}}  {value}")
    if code:
        say("Could not read the identity: " + (tv.get("error") or tv.get("headline") or "unknown error"))
    return code


def check(fleet, tv, say):
    """Each stage is observed separately; an open port alone never counts as Developer Mode ready."""
    alias = tv["alias"]
    say(f"Checking {tv['name']} ({alias})")
    meta = tv.get("metadata") or {}
    say("About: " + " · ".join(describe(meta)))
    if meta.get("baseline"):
        say("  " + meta["baseline"])
    say("Developer Mode port: " + ("open" if tv["reachable"] else "closed or not answering"))
    if not tv["reachable"] and tv["status"] != "connected":
        dev = tv["dev_mode"]
        say(f"{dev['label']}: {dev['explanation']}")
        say("Next: " + dev["next"])
        return 1
    try:
        fleet.connect(alias, {"confirm": alias})
    except LiveError:
        tv = find(fleet.state(), alias)
        say("Developer connection: refused")
        say(tv["dev_mode"]["label"] + ": " + tv["dev_mode"]["explanation"])
        say("Next: " + tv["dev_mode"]["next"])
        say("Not ready: " + (tv["error"] or tv["headline"]))
        for line in guidance_lines(tv):
            say(line)
        return 1
    say("Developer connection: accepted")
    ready = True
    try:
        fleet.identify(alias, {})
        tv = find(fleet.state(), alias)
        identity = tv["identity"] or {}
        say(f"Identity: Tizen {identity.get('tizen', 'unknown')}, DUID present (hidden)")
        say(tv["dev_mode"]["label"])
        say("Certificate: " + {"match": "includes this TV", "mismatch": "does not include this TV",
                               "unconfigured": "not configured (install needs a certificate with this TV)"}
            .get(tv["certificate"], tv["certificate"]))
    except LiveError:
        ready = False
        tv = find(fleet.state(), alias)
        say("Identity: could not be read (" + (tv["error"] or "unknown error") + ")")
    fleet.disconnect(alias, {})
    say("Developer Mode ready" if ready else "Not ready: connected, but the TV identity could not be read")
    return 0 if ready else 1


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
