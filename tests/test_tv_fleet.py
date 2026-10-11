import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import live_tv as live  # noqa: E402
import tv_fleet  # noqa: E402

# Synthetic placeholders only; never real household addresses or identifiers.
LAN_A, LAN_B = "10.255.255.21", "10.255.255.22"
DUID = "PLACEHOLDERDUID0001"
APP = "AbCdEfGhIj.Example"


class FakeSdb:
    """Records every argv. Never runs an SDK or touches the network."""
    def __init__(self, devices=None, accept=True, duid=DUID):
        self.devices = dict(devices or {})
        self.accept = accept
        self.duid = duid
        self.calls = []

    def __call__(self, argv, timeout=60, limit=None):
        args = argv[1:]
        self.calls.append(args)
        if args == ["devices"]:
            return "List of devices attached\n" + "".join(f"{s}\t{v}\tmodel\n" for s, v in self.devices.items())
        if args[0] == "connect":
            if self.accept:
                self.devices[args[1]] = "device"
            return "connected"
        if args[0] == "disconnect":
            self.devices.pop(args[1], None)
            return "disconnected"
        if args[2:] == ["capability"]:
            return "platform_version:4.0\nprofile_name:tv\n"
        if args[2:] == ["shell", "0", "getduid"]:
            return self.duid + "\n"
        raise AssertionError("Unexpected SDB call " + " ".join(args))

    def mutations(self):
        return [c for c in self.calls if c and c[0] in {"connect", "disconnect", "kill-server", "reboot"}
                or "kill" in c or "install" in c]


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.canonical = self.root / "canonical.lock"
        self.certificate = self.root / "device-profile.xml"
        self.certificate.write_text(f"<profile><duid>{DUID}</duid></profile>")
        self.sdb = FakeSdb()
        self.tool = self.root / ("sdb.exe" if os.name == "nt" else "sdb")
        self.tool.write_text("")  # Placeholder path only; the fake runner never executes it.

    def tearDown(self):
        self.temporary.cleanup()

    def fleet(self, hits=(LAN_A,), certificate=True, api_hits=None, **kwargs):
        def sweep(address, port, timeout):
            self.assertIn(port, (26101, 8001))
            return set(hits if port == 26101 else (hits if api_hits is None else api_hits))
        args = dict(sdb_path=str(self.tool), runner=self.sdb, interfaces=lambda: ["10.255.255.10"], sweep=sweep,
                    info=lambda host: {"name": "[TV] Samsung 7 Series (55)", "model": "UE55NU7400",
                                       "year": "2018", "developer_mode": "1", "uid": "uuid:" + host},
                    ssdp=lambda: set(), key_path=self.root / "alias.key", lease_root=self.root, canonical=self.canonical,
                    certificate_duids=str(self.certificate) if certificate else None)
        args.update(kwargs)
        return tv_fleet.Fleet(**args)

    def only(self, fleet):
        tvs = fleet.state()["tvs"]
        self.assertEqual(len(tvs), 1)
        return tvs[0]

    def assertPrivate(self, value):
        text = json.dumps(value)
        for secret in (LAN_A, LAN_B, "127.0.0.1", DUID, "26101", "26111", str(self.certificate)):
            self.assertNotIn(secret, text)

    def test_detect_merges_scan_and_sdb_and_returns_aliases_only(self):
        self.sdb.devices = {f"{LAN_B}:26101": "device", "127.0.0.1:26111": "device"}
        fleet = self.fleet(hits=(LAN_A,))
        result = fleet.detect()
        self.assertPrivate(result)
        tvs = {tv["alias"]: tv for tv in result["tvs"]}
        self.assertEqual(len(tvs), 3)
        for alias in tvs:
            self.assertRegex(alias, r"^tv-[0-9a-f]{6}$")
        statuses = sorted(tv["status"] for tv in tvs.values())
        # An sdb link this portal does not hold is not "Connected": that needs a working preview.
        self.assertEqual(statuses, ["not-connected", "not-connected", "not-connected"])
        lan = next(tv for tv in tvs.values() if tv["status"] == "not-connected" and tv["reachable"])
        self.assertTrue(lan["reachable"])
        self.assertEqual(lan["model"], {"value": "UE55NU7400", "provenance": "observed"})
        self.assertEqual(lan["name"], "Samsung 7 Series (55) · UE55NU7400")
        self.assertEqual(self.sdb.mutations(), [], "Detection must be read-only")

    def test_alias_is_stable_keyed_and_not_a_plain_hash(self):
        first = self.fleet().detect()["tvs"][0]["alias"]
        self.assertEqual(self.fleet().detect()["tvs"][0]["alias"], first)
        self.assertEqual(oct((self.root / "alias.key").stat().st_mode & 0o777), "0o600") if os.name == "posix" else None
        (self.root / "alias.key").unlink()
        self.assertNotEqual(self.fleet().detect()["tvs"][0]["alias"], first)

    def foreign_locks(self, alias):
        """Lock files other sessions left behind: a live owner, a dead owner and the shared lane."""
        live_owner = live.Lease(self.root / f"tizen-tv-{alias}.lock")
        live_owner.acquire()
        dead = self.root / "tizen-tv-tv-dead00.lock"
        dead.mkdir()
        (dead / "owner.json").write_text('{"pid": 999999999, "token": "x"}')
        self.canonical.mkdir()
        return live_owner, {path: sorted(p.name for p in path.iterdir()) for path in (live_owner.path, dead, self.canonical)}

    def test_lock_files_from_other_sessions_never_block_scan_status_or_connect(self):
        self.sdb.devices = {"127.0.0.1:26111": "device"}
        fleet = self.fleet(hits=(LAN_A,))
        aliases = [tv["alias"] for tv in fleet.detect()["tvs"]]
        lan = next(tv["alias"] for tv in fleet.state()["tvs"] if tv["reachable"])
        live_owner, before = self.foreign_locks(lan)
        try:
            fleet.detect()
            for tv in fleet.state()["tvs"]:
                self.assertNotEqual(tv["status"], "busy")
                self.assertNotIn("busy_reason", tv)
                self.assertNotIn("caution", tv)
                self.assertNotIn("In use", tv["label"] + tv["headline"])
            for alias in aliases:
                fleet.connect(alias, {"confirm": ""})
            self.assertEqual({tv["status"] for tv in fleet.state()["tvs"]}, {"developer"})
            fleet.close()
            for path, names in before.items():
                self.assertEqual(sorted(p.name for p in Path(path).iterdir()), names, "Other sessions' files are never touched")
        finally:
            live_owner.release()
            fleet.close()

    def test_connect_is_stateless_and_writes_no_lock_files(self):
        fleet = self.fleet()
        alias = fleet.detect()["tvs"][0]["alias"]
        fleet.connect(alias, {"confirm": ""})
        tv = self.only(fleet)
        self.assertEqual((tv["status"], tv["owned"]), ("developer", True))
        self.assertEqual(list(self.root.glob("*.lock")), [])
        self.assertNotIn("lease", tv["details"])
        fleet.disconnect(alias, {})
        self.assertIn(["disconnect", f"{LAN_A}:26101"], self.sdb.calls)
        self.assertEqual(self.only(fleet)["status"], "not-connected")
        # A fresh process reconnects without depending on the previous one's cleanup.
        again = self.fleet()
        again.detect()
        again.connect(alias, {"confirm": ""})
        self.assertEqual(self.only(again)["status"], "developer")
        again.close()

    def test_same_portal_still_serializes_its_own_connect(self):
        fleet = self.fleet()
        alias = fleet.detect()["tvs"][0]["alias"]
        fleet.connect(alias, {"confirm": ""})
        with self.assertRaises(live.LiveError) as caught:
            fleet.connect(alias, {"confirm": ""})
        self.assertEqual(caught.exception.code, "busy")
        fleet.close()

    def test_already_connected_tv_is_never_disconnected_by_portal(self):
        self.sdb.devices = {f"{LAN_A}:26101": "device"}
        fleet = self.fleet()
        alias = fleet.detect()["tvs"][0]["alias"]
        fleet.connect(alias, {"confirm": ""})
        self.assertFalse(self.only(fleet)["owned"])
        fleet.close()
        self.assertNotIn("connect", [c[0] for c in self.sdb.calls])
        self.assertNotIn("disconnect", [c[0] for c in self.sdb.calls])
        self.assertFalse((self.root / f"tizen-tv-{alias}.lock").exists())

    def test_refused_connection_is_rolled_back_with_human_instructions(self):
        self.sdb.accept = False
        fleet = self.fleet()
        alias = fleet.detect()["tvs"][0]["alias"]
        with self.assertRaises(live.LiveError) as caught:
            fleet.connect(alias, {"confirm": ""})
        self.assertIn("Developer", str(caught.exception))
        self.assertPrivate(str(caught.exception))
        self.assertFalse((self.root / f"tizen-tv-{alias}.lock").exists())

    def test_sdk_errors_are_scrubbed_of_addresses(self):
        def broken(argv, timeout=60, limit=None):
            if argv[1:] == ["devices"]:
                return "List of devices attached\n"
            live.fail("sdk", f"SDK command failed: error: failed to connect to {LAN_A}:26101")
        fleet = self.fleet(runner=broken)
        alias = fleet.detect()["tvs"][0]["alias"]
        with self.assertRaises(live.LiveError) as caught:
            fleet.connect(alias, {"confirm": ""})
        self.assertPrivate(str(caught.exception))

    def test_identify_reports_duid_presence_and_certificate_match_only(self):
        fleet = self.fleet()
        alias = fleet.detect()["tvs"][0]["alias"]
        with self.assertRaises(live.LiveError):
            fleet.identify(alias, {})  # Not connected by this portal.
        fleet.connect(alias, {"confirm": ""})
        fleet.identify(alias, {})
        tv = self.only(fleet)
        self.assertPrivate(fleet.state())
        self.assertEqual(tv["identity"], {"duid_present": True, "tizen": "4.0", "profile": "tv", "tier": "mid"})
        self.assertEqual(tv["certificate"], "match")
        self.assertNotIn("fingerprint", json.dumps(fleet.state()))
        fleet.close()

    def test_observed_os_version_survives_disconnect_and_rescan(self):
        fleet = self.fleet()
        alias = fleet.detect()["tvs"][0]["alias"]
        fleet.connect(alias, {"confirm": ""})
        fleet.identify(alias, {})
        self.assertEqual(self.only(fleet)["metadata"]["os"]["version"], "4.0")
        fleet.disconnect(alias, {})
        tv = self.only(fleet)
        self.assertIsNone(tv["identity"])
        self.assertEqual(tv["metadata"]["os"]["version"], "4.0")
        self.assertIn("observed", tv["metadata"]["os"]["provenance"])
        fleet.detect()
        self.assertEqual(self.only(fleet)["metadata"]["os"]["version"], "4.0")
        self.assertPrivate(fleet.state())
        fleet.close()

    def test_certificate_missing_or_unconfigured_blocks_run(self):
        for certificate, expected in ((False, "unconfigured"), (True, "missing")):
            with self.subTest(certificate=certificate):
                if certificate:
                    self.certificate.write_text("<profile><duid>OTHERDEVICE0002</duid></profile>")
                fleet = self.fleet(certificate=certificate)
                alias = fleet.detect()["tvs"][0]["alias"]
                fleet.connect(alias, {"confirm": ""})
                fleet.identify(alias, {})
                self.assertEqual(self.only(fleet)["certificate"], expected)
                for operation, data in (("run", {"confirm": alias, "app": APP, "package": ""}),):
                    with self.assertRaises(live.LiveError) as caught:
                        getattr(fleet, operation)(alias, data)
                    self.assertEqual(caught.exception.code, "certificate")
                    self.assertIn("certificate lacks this DUID", str(caught.exception))
                fleet.close()

    def test_run_requires_identify_and_typed_confirmation(self):
        calls = []
        fleet = self.fleet(retail=lambda *a, **k: calls.append((a, k)) or {"passed": True, "steps": []})
        alias = fleet.detect()["tvs"][0]["alias"]
        fleet.connect(alias, {"confirm": ""})
        with self.assertRaises(live.LiveError) as caught:
            fleet.run(alias, {"confirm": alias, "app": APP, "package": ""})
        self.assertEqual(caught.exception.code, "certificate")  # Identify first.
        fleet.identify(alias, {})
        with self.assertRaises(live.LiveError) as caught:
            fleet.run(alias, {"confirm": "wrong", "app": APP, "package": ""})
        self.assertEqual(caught.exception.code, "confirm")
        fleet.run(alias, {"confirm": alias, "app": APP, "package": ""})
        fleet.wait()
        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(args[2], APP)
        self.assertIs(kwargs["lease"], fleet.records[alias]["lease"])
        job = self.only(fleet)["job"]
        self.assertEqual(job["status"], "passed")
        self.assertPrivate(fleet.state())
        fleet.close()

    def test_two_tvs_connect_independently(self):
        fleet = self.fleet(hits=(LAN_A, LAN_B))
        first, second = (tv["alias"] for tv in fleet.detect()["tvs"])
        fleet.connect(first, {"confirm": ""})
        fleet.connect(second, {"confirm": ""})
        fleet.disconnect(first, {})
        states = {tv["alias"]: tv["status"] for tv in fleet.state()["tvs"]}
        self.assertEqual((states[first], states[second]), ("not-connected", "developer"))
        fleet.close()
        self.assertEqual(list(self.root.glob("*.lock")), [])

    def test_scan_is_bounded(self):
        many = [f"10.254.0.{n}" for n in range(1, 101)]
        seen = []
        fleet = self.fleet(hits=many, interfaces=lambda: [f"10.0.{n}.5" for n in range(10)],
                           info=lambda host: seen.append(host) or {"model": "unknown"})
        result = fleet.detect()
        self.assertLessEqual(len(result["tvs"]), tv_fleet.MAX_TVS)
        self.assertLessEqual(len(seen), tv_fleet.MAX_TVS)
        self.assertEqual(result["scan"]["networks"], tv_fleet.MAX_NETWORKS)

    def test_sdb_missing_still_reports_network_hits(self):
        fleet = self.fleet(sdb_path=None)
        result = fleet.detect()
        self.assertEqual(result["scan"]["sdb"], "unavailable")
        self.assertEqual(result["tvs"][0]["status"], "not-connected")
        with self.assertRaises(live.LiveError) as caught:
            fleet.connect(result["tvs"][0]["alias"], {"confirm": ""})
        self.assertIn("Tizen Studio", str(caught.exception))

    def test_names_fall_back_and_rename_persists_by_alias(self):
        saved = []
        fleet = self.fleet(hits=(LAN_A, LAN_B), info=lambda host: None, save_names=saved.append)
        names = sorted(tv["name"] for tv in fleet.detect()["tvs"])
        self.assertEqual(names, ["Samsung TV #1", "Samsung TV #2"])
        info = lambda host: {"name": "unknown", "model": "unknown", "year": "2018"}
        fleet = self.fleet(info=info, save_names=saved.append)
        alias = fleet.detect()["tvs"][0]["alias"]
        self.assertEqual(self.only(fleet)["name"], "Samsung TV (2018) #1")
        fleet.rename(alias, {"name": "Bedroom TV"})
        self.assertEqual(saved[-1], {alias: "Bedroom TV"})
        self.assertEqual(self.only(fleet)["name"], "Bedroom TV")
        restarted = self.fleet(names=saved[-1], info=info)
        restarted.detect()
        self.assertEqual(self.only(restarted)["name"], "Bedroom TV")
        for bad in ("x" * 61, "line\nbreak", 5):
            with self.assertRaises(live.LiveError):
                fleet.rename(alias, {"name": bad})
        fleet.rename(alias, {"name": ""})
        self.assertEqual(saved[-1], {})
        self.assertPrivate(saved)

    def test_alias_follows_tv_identity_not_address(self):
        info = lambda host: {"name": "TV", "model": "M", "year": "2018", "uid": "uuid:same-tv"}
        first = self.fleet(hits=(LAN_A,), info=info).detect()["tvs"][0]["alias"]
        second = self.fleet(hits=(LAN_B,), info=info).detect()["tvs"][0]["alias"]
        self.assertEqual(first, second)

    def test_guidance_for_closed_port_wrong_host_ip_and_certificate(self):
        self.sdb.accept = False
        self.sdb.devices = {f"{LAN_B}:26101": "offline"}
        fleet = self.fleet(hits=(LAN_A,), info=lambda host: None if host == LAN_B else
                           {"name": "[TV] Samsung", "model": "UE55NU7400", "uid": "uuid:" + host})
        fleet.detect()
        tvs = {tv["reachable"]: tv["alias"] for tv in fleet.state()["tvs"]}
        for reachable, kind in ((True, "host-ip"), (False, "developer-mode")):
            with self.assertRaises(live.LiveError):
                fleet.connect(tvs[reachable], {"confirm": ""})
            tv = next(t for t in fleet.state()["tvs"] if t["alias"] == tvs[reachable])
            self.assertEqual(tv["guidance"]["kind"], kind)
            self.assertTrue(any("12345" in s.replace(" ", "") for s in tv["guidance"]["steps"]))
        host = next(t for t in fleet.state()["tvs"] if t["alias"] == tvs[True])
        self.assertEqual(host["guidance"]["host_ip"], "10.255.255.10")
        self.sdb.accept = True
        self.certificate.write_text("<profile><duid>OTHERDEVICE0002</duid></profile>")
        fleet.connect(tvs[True], {"confirm": ""})
        fleet.identify(tvs[True], {})
        tv = next(t for t in fleet.state()["tvs"] if t["alias"] == tvs[True])
        self.assertEqual(tv["guidance"]["title"], "Connected, install blocked: needs certificate update")
        fleet.close()

    def test_plain_status_headline_for_every_card_state(self):
        self.sdb.accept = False
        self.sdb.devices = {f"{LAN_B}:26101": "offline"}
        fleet = self.fleet(hits=(LAN_A,), info=lambda host: None if host == LAN_B else
                           {"name": "[TV] Samsung", "model": "UE55NU7400", "uid": "uuid:" + host})
        fleet.detect()
        tvs = {tv["reachable"]: tv["alias"] for tv in fleet.state()["tvs"]}
        def card(alias):
            return next(t for t in fleet.state()["tvs"] if t["alias"] == alias)
        self.assertEqual((card(tvs[True])["label"], card(tvs[True])["tone"]), ("Ready to connect", "idle"))
        self.assertEqual(card(tvs[False])["label"], "Not reachable")
        with self.assertRaises(live.LiveError):
            fleet.connect(tvs[True], {"confirm": ""})
        tv = card(tvs[True])
        self.assertEqual((tv["status"], tv["label"], tv["tone"]), ("not-connected", "Needs setup", "warn"))
        self.assertIn("Host PC IP", tv["headline"])
        self.sdb.accept = True
        fleet.connect(tvs[True], {"confirm": ""})
        # A developer link alone is never "Connected"; that label needs a working app preview.
        dev = card(tvs[True])
        self.assertEqual((dev["label"], dev["tone"], dev["status"]), ("Developer Mode ready", "idle", "developer"))
        self.assertEqual(dev["headline"], "Preview not connected · Open an app for preview")
        fleet.identify(tvs[True], {})
        self.certificate.write_text("<profile><duid>OTHERDEVICE0002</duid></profile>")
        fleet.identify(tvs[True], {})
        # The certificate problem is the install line, not the status.
        self.assertEqual(card(tvs[True])["label"], "Developer Mode ready")
        self.assertEqual(card(tvs[True])["install"], "Install: blocked until the certificate includes this TV")
        self.assertEqual(card(tvs[True])["dev_mode"]["label"], "Dev mode enabled")
        fleet.close()
        self.canonical.write_text("{}")
        other = self.fleet(hits=())
        self.sdb.devices = {"127.0.0.1:26111": "device"}
        other.detect()
        tv = self.only(other)
        self.assertNotEqual(tv["tone"], "busy")
        for text in (tv["headline"], tv["label"]):
            self.assertNotRegex(text, r"lane|lease|sdb|CDP|In use|\d+\.\d+\.\d+\.\d+")
        other.close()

    def test_submit_connect_runs_in_background_then_identifies(self):
        fleet = self.fleet()
        fleet.submit("detect", None, {})
        fleet.wait()
        alias = fleet.state()["tvs"][0]["alias"]
        state = fleet.submit("connect", alias, {"confirm": ""})
        self.assertEqual(state["tvs"][0]["pending"], "connect")
        fleet.wait()
        tv = self.only(fleet)
        self.assertEqual((tv["status"], tv["pending"], tv["certificate"]), ("developer", None, "match"))
        with self.assertRaises(live.LiveError):
            fleet.submit("connect", "tv-000000", {"confirm": ""})
        fleet.close()

    def test_submit_failure_lands_on_card_without_addresses(self):
        self.sdb.accept = False
        fleet = self.fleet()
        alias = fleet.detect()["tvs"][0]["alias"]
        fleet.submit("connect", alias, {"confirm": ""})
        fleet.wait()
        tv = self.only(fleet)
        self.assertIn("Developer", tv["error"])
        self.assertIsNone(tv["pending"])
        self.assertPrivate(fleet.state())

    def test_emulator_is_not_listed_as_a_tv(self):
        self.sdb.devices = {"emulator-26101": "device"}
        self.assertEqual([tv["status"] for tv in self.fleet().detect()["tvs"]], ["not-connected"])



class PreviewTests(FleetTests):
    """Connected means developer link plus a captured app preview; a restart needs consent."""

    def preview_fleet(self, capture_frames=True):
        import importlib.util
        spec = importlib.util.spec_from_file_location("live_fixture", Path(__file__).with_name("test_live_tv.py"))
        fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
        test = self
        class Session:
            made = []
            def __init__(self, serial, app, legacy):
                self.app, self.forward, self.client, self.launched, self.closed = app, None, None, 0, 0
                Session.made.append(self)
            def launch(self):
                self.launched += 1
                self.forward = (45555, 9222)
            def close(self):
                self.closed += 1
                self.forward = None
        class CDP(fixture.FakeCDP):
            def layer_list(self):
                return super().layer_list() if capture_frames else []
        fleet = self.fleet(session=lambda serial, app, legacy: Session(serial, app, legacy), cdp=CDP,
                           discover=lambda port: (test.assertEqual(port, 45555), ["page-1"])[1])
        alias = fleet.detect()["tvs"][0]["alias"]
        fleet.connect(alias, {"confirm": ""})
        return fleet, alias, Session

    def test_restart_needs_consent_and_connected_needs_capture(self):
        fleet, alias, Session = self.preview_fleet()
        try:
            self.assertEqual(self.only(fleet)["status"], "developer")
            with self.assertRaises(live.LiveError) as caught:
                fleet.preview(alias, {"app": "AbCdEfGhIj.App", "restart": False})
            self.assertEqual(caught.exception.code, "confirm")
            self.assertEqual(Session.made, [], "No launch without consent")
            fleet.preview(alias, {"app": "AbCdEfGhIj.App", "restart": True})
            tv = self.only(fleet)
            self.assertEqual((tv["status"], tv["label"]), ("connected", "Connected"))
            self.assertIn("not the whole TV", tv["headline"])
            self.assertTrue(tv["preview"]["ready"] and tv["preview"]["frames"])
            self.assertEqual(tv["preview"]["activation_allowed"], False)
            self.assertNotIn("45555", json.dumps(tv), "Forward details stay private")
            # Re-opening the same app re-attaches without another restart.
            fleet.preview(alias, {"app": "AbCdEfGhIj.App", "restart": False})
            self.assertEqual((len(Session.made), Session.made[0].launched), (1, 1))
            fleet.disconnect(alias, {})
            self.assertEqual(Session.made[0].closed, 1)
            self.assertIsNone(self.only(fleet)["preview"])
            self.assertEqual(self.only(fleet)["status"], "not-connected")
        finally:
            fleet.close()

    def test_failed_capture_stays_developer_and_cleans_up(self):
        fleet, alias, Session = self.preview_fleet(capture_frames=False)
        try:
            with self.assertRaises(live.LiveError) as caught:
                fleet.preview(alias, {"app": "AbCdEfGhIj.App", "restart": True})
            self.assertEqual(caught.exception.code, "preview")
            tv = self.only(fleet)
            self.assertEqual((tv["status"], tv["preview"]), ("developer", None))
            self.assertEqual(Session.made[0].closed, 1)
        finally:
            fleet.close()

    def test_preview_requires_connection(self):
        fleet = self.fleet()
        alias = fleet.detect()["tvs"][0]["alias"]
        with self.assertRaises(live.LiveError) as caught:
            fleet.preview(alias, {"app": "AbCdEfGhIj.App", "restart": True})
        self.assertEqual(caught.exception.code, "connect")


class DiscoveryAndReadinessTests(FleetTests):
    """Five physical TVs: three with Developer Mode, two seen only through Samsung's read-only API."""

    DEV = ("10.255.255.21", "10.255.255.22", "10.255.255.23")
    API = ("10.255.255.31", "10.255.255.32")
    OTHER = "10.255.255.40"  # Something else with port 8001 open: not a Samsung TV.

    def lab(self, **kwargs):
        def info(host):
            if host == self.OTHER:
                raise ValueError("not Samsung")
            return {"name": "[TV] TV " + host[-2:], "model": "UE43RU7092UXXH", "developer_mode": "0",
                    "os": "Tizen", "platform_year": "unknown", "uid": "uuid:" + host}
        return self.fleet(hits=self.DEV, api_hits=self.DEV + self.API + (self.OTHER,), info=info, **kwargs)

    def by_host(self, fleet):
        return {t["name"].split(" · ")[0][-2:]: t for t in fleet.state()["tvs"]}

    def test_api_only_tvs_are_listed_and_non_samsung_hosts_are_not(self):
        fleet = self.lab()
        result = fleet.detect()
        self.assertPrivate(result)
        self.assertEqual(len(result["tvs"]), 5)
        self.assertEqual(result["scan"]["found"], 5)
        tvs = self.by_host(fleet)
        self.assertEqual(sorted(tvs), ["21", "22", "23", "31", "32"])
        for name in ("21", "22", "23"):
            self.assertEqual(tvs[name]["dev_mode"]["label"], "Dev mode reachable · not checked")
            self.assertIn("Connect", tvs[name]["dev_mode"]["next"])
        for name in ("31", "32"):
            self.assertEqual(tvs[name]["dev_mode"]["label"], "Dev mode not enabled")
            self.assertIn("reports Developer Mode off", tvs[name]["dev_mode"]["explanation"])
            self.assertIn("1 2 3 4 5", tvs[name]["dev_mode"]["next"])
            self.assertEqual(tvs[name]["label"], "Not connected")
        fleet.close()

    def test_duplicate_observations_of_one_tv_merge(self):
        fleet = self.fleet(hits=(LAN_A,), api_hits=(LAN_A, LAN_B),
                           info=lambda host: {"name": "[TV] Lounge", "model": "UE55NU7400", "uid": "uuid:same"})
        fleet.detect()
        tv = self.only(fleet)
        self.assertTrue(tv["reachable"])
        self.assertEqual(tv["dev_mode"]["state"], "reachable")
        fleet.close()

    def test_dev_mode_labels_follow_observed_evidence(self):
        self.sdb.accept = False
        fleet = self.lab()
        fleet.detect()
        alias = self.by_host(fleet)["21"]["alias"]
        with self.assertRaises(live.LiveError):
            fleet.connect(alias, {"confirm": ""})
        refused = self.by_host(fleet)["21"]["dev_mode"]
        self.assertEqual(refused["label"], "Needs to be configured")
        self.assertIn("Host PC IP", refused["next"])
        self.sdb.accept = True
        fleet.connect(alias, {"confirm": ""})
        enabled = self.by_host(fleet)["21"]
        self.assertEqual(enabled["dev_mode"]["label"], "Dev mode enabled")
        self.assertIsNone(enabled["install"])  # Certificate readiness is separate and unknown until Identify.
        fleet.close()

    def test_closed_port_without_reported_mode_is_not_called_off(self):
        fleet = self.fleet(hits=(), api_hits=(LAN_A,),
                           info=lambda host: {"name": "[TV] Den", "model": "UE55NU7400", "developer_mode": "unknown"})
        dev = fleet.detect()["tvs"][0]["dev_mode"]
        self.assertEqual((dev["state"], dev["label"]), ("closed", "Dev mode port closed"))

    def test_api_reporting_developer_mode_on_with_closed_port_needs_configuration(self):
        fleet = self.fleet(hits=(), api_hits=(LAN_A,),
                           info=lambda host: {"name": "[TV] Den", "model": "UE55NU7400", "developer_mode": "on"})
        fleet.detect()
        dev = self.only(fleet)["dev_mode"]
        self.assertEqual(dev["label"], "Needs to be configured")
        self.assertIn("Restart", dev["next"])
        fleet.close()

    def test_offline_tv_stays_not_reachable_not_dev_mode_off(self):
        self.sdb.devices = {f"{LAN_B}:26101": "offline"}
        fleet = self.fleet(hits=(), api_hits=(), info=lambda host: None)
        fleet.detect()
        dev = self.only(fleet)["dev_mode"]
        self.assertEqual((dev["state"], dev["label"]), ("unknown", "Not reachable"))
        fleet.close()

    def test_scan_metadata_schema_separates_reported_inferred_and_observed(self):
        fleet = self.lab()
        fleet.detect()
        meta = self.by_host(fleet)["31"]["metadata"]
        self.assertEqual(meta["model"], "UE43RU7092UXXH")
        self.assertEqual(meta["model_year"], {"value": 2019, "provenance": "inferred from the model code"})
        self.assertIsNone(meta["manufacture_date"])
        self.assertEqual(meta["os"]["text"], "Tizen · version unavailable")
        self.assertIsNone(meta["os"]["version"])
        self.assertIn("not this TV's observed version", meta["baseline"])
        self.assertIsNone(meta["firmware"])
        fleet.close()
        reported = self.fleet(info=lambda host: {"name": "[TV] A", "model": "XYZ123", "platform_year": "2018",
                                                 "os": "Tizen", "resolution": "3840x2160"})
        reported.detect()
        meta = self.only(reported)["metadata"]
        self.assertEqual(meta["model_year"], {"value": 2018, "provenance": "reported by the TV"})
        self.assertEqual(meta["resolution"], "3840x2160")
        self.sdb.accept = True
        alias = self.only(reported)["alias"]
        reported.connect(alias, {"confirm": ""})
        reported.identify(alias, {})
        os_info = self.only(reported)["metadata"]["os"]
        self.assertEqual((os_info["text"], os_info["provenance"]), ("Tizen 4.0", "observed over the developer connection"))
        reported.close()


for _name in [n for n in vars(FleetTests) if n.startswith("test_")]:
    setattr(DiscoveryAndReadinessTests, _name, None)  # Inherit fixtures only, not the base tests.


if __name__ == "__main__":
    unittest.main()
