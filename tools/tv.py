#!/usr/bin/env python3
"""tv: the TV portal's capabilities from a terminal.

Every command uses the same code paths as the portal (tv_fleet.Fleet for network TVs,
live_tv.Source for profile-configured inspector targets). Output shows names and
redacted aliases only, never TV addresses, DUIDs or certificate data.

Each run is one-shot: TVs are scanned in-process (addresses are never stored), and
anything this command connects, leases or forwards is released before it exits.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_tv  # noqa: E402
from live_tv import LiveError, read_profile  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KEYS = sorted(live_tv.KEYS)


def parser():
    cli = argparse.ArgumentParser(prog="tv", description=__doc__.split("\n\n")[0],
                                  formatter_class=argparse.RawDescriptionHelpFormatter, epilog=(
        "commands:\n"
        "  scan | status          Scan the local network for TVs (port 26101) and list them\n"
        "  rename ALIAS NAME      Name a scanned TV (saved in the private profile)\n"
        "  connect ALIAS --yes    Connect, identify (install readiness), then disconnect\n"
        "  key TV KEY... --yes    Send remote keys to a profile TV: " + ", ".join(KEYS) + "\n"
        "  screenshot TV --out DIR --yes   Save the TV's renderer layers as private PNGs\n"
        "  portal                 Start the portal in your browser (same features)\n"))
    cli.add_argument("--profile", type=Path, help="Private 0600 profile (names, inspector TVs, certificate)")
    sub = cli.add_subparsers(dest="command", required=True, metavar="command")
    for name in ("scan", "status"):
        item = sub.add_parser(name, help="Scan the network for TVs and list them")
        item.add_argument("--json", action="store_true", help="Print the same state the portal shows")
    item = sub.add_parser("rename", help="Name a scanned TV")
    item.add_argument("alias")
    item.add_argument("name")
    item = sub.add_parser("connect", help="Connect to a scanned TV, identify it, then disconnect")
    item.add_argument("alias")
    item.add_argument("--yes", action="store_true", help="Confirm: connect to this TV now")
    item = sub.add_parser("key", help="Send remote keys to a profile TV")
    item.add_argument("tv")
    item.add_argument("keys", nargs="+", metavar="key")
    item.add_argument("--yes", action="store_true", help="Confirm: remote keys will be sent")
    item = sub.add_parser("screenshot", help="Save renderer layers of a profile TV")
    item.add_argument("tv")
    item.add_argument("--out", type=Path, required=True, help="Private folder outside this repository")
    item.add_argument("--yes", action="store_true", help="Confirm: images may show account info")
    sub.add_parser("portal", help="Start the portal in your browser")
    return cli


class Usage(Exception):
    pass


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
        yield f"{tv['name']:<{width}}  {tv['alias']}  {tv['label']:<16}  {tv['headline']}"


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


def run(args, fleet_factory, cdp_factory, out):
    say = lambda line="": print(line, file=out)  # noqa: E731
    profile = config = app = None
    if args.profile is not None:
        profile, config = read_profile(args.profile.expanduser().absolute())
    if args.command == "portal":
        if profile is None:
            raise Usage("portal needs --profile; a minimal one is {\"sources\": []} with mode 0600")
        live_tv.serve(profile)
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
    elif args.command == "rename":
        raise Usage("rename needs --profile so the name can be saved privately")
    if args.command == "connect" and not args.yes:
        raise Usage("Add --yes to confirm: connect to this TV now")
    fleet = fleet_factory(certificate_duids=(config or {}).get("certificate_duids"),
                          names=(config or {}).get("tv_names", {}), save_names=save)
    try:
        state = fleet.detect()
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
        say(f"Connecting to {tv['name']}…")
        try:
            fleet.connect(args.alias, {"confirm": ""})
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


def main(argv=None, fleet_factory=default_fleet, cdp_factory=live_tv.CDP, stdout=None, stderr=None):
    out, err = stdout or sys.stdout, stderr or sys.stderr
    cli = parser()
    try:
        args = cli.parse_args(argv)
    except SystemExit as stop:
        return stop.code
    try:
        return run(args, fleet_factory, cdp_factory, out)
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
