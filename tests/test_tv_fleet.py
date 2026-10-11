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

    def fleet(self, hits=(LAN_A,), certificate=True, **kwargs):
        def sweep(address, port, timeout):
            self.assertEqual(port, 26101)
            return set(hits)
        args = dict(sdb_path=str(self.tool), runner=self.sdb, interfaces=lambda: ["10.255.255.10"], sweep=sweep,
                    info=lambda host: {"name": "[TV] Samsung 7 Series (55)", "model": "UE55NU7400",
                                       "year": "2018", "developer_mode": "1", "uid": "uuid:" + host},
                    key_path=self.root / "alias.key", lease_root=self.root, canonical=self.canonical,
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
        self.assertEqual(statuses, ["busy", "connected", "not-connected"])
        lan = next(tv for tv in tvs.values() if tv["status"] == "not-connected")
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

    def test_relay_and_other_lane_leases_are_busy_and_read_only(self):
        self.sdb.devices = {"127.0.0.1:26111": "device"}
        fleet = self.fleet(hits=(LAN_A,))
        fleet.detect()
        tvs = fleet.state()["tvs"]
        relay = next(tv for tv in tvs if tv["status"] == "busy")
        lan = next(tv for tv in tvs if tv["status"] != "busy")
        self.assertIn("another", relay["busy_reason"])
        with self.assertRaises(live.LiveError) as caught:
            fleet.connect(relay["alias"], {"confirm": relay["alias"]})
        self.assertEqual(caught.exception.code, "busy")
        held = live.Lease(self.root / f"tizen-tv-{lan['alias']}.lock")
        held.acquire()
        try:
            self.assertEqual(next(t for t in fleet.state()["tvs"] if t["alias"] == lan["alias"])["status"], "busy")
            with self.assertRaises(live.LiveError):
                fleet.connect(lan["alias"], {"confirm": ""})
        finally:
            held.release()
        self.assertEqual(self.sdb.mutations(), [])

    def test_connected_elsewhere_while_canonical_lane_active_is_busy(self):
        self.sdb.devices = {f"{LAN_A}:26101": "device"}
        self.canonical.mkdir()
        fleet = self.fleet()
        fleet.detect()
        self.assertEqual(self.only(fleet)["status"], "busy")

    def test_canonical_lane_active_requires_typed_confirmation(self):
        self.canonical.mkdir()
        fleet = self.fleet()
        alias = fleet.detect()["tvs"][0]["alias"]
        self.assertTrue(self.only(fleet)["caution"])
        with self.assertRaises(live.LiveError) as caught:
            fleet.connect(alias, {"confirm": ""})
        self.assertEqual(caught.exception.code, "confirm")
        self.assertEqual(self.sdb.mutations(), [])
        fleet.connect(alias, {"confirm": alias})
        self.assertEqual(self.only(fleet)["status"], "connected")
        self.assertTrue(self.canonical.is_dir(), "The canonical lock is never touched")
        fleet.close()

    def test_connect_owned_holds_per_tv_lease_and_disconnects_only_owned(self):
        fleet = self.fleet()
        alias = fleet.detect()["tvs"][0]["alias"]
        fleet.connect(alias, {"confirm": ""})
        tv = self.only(fleet)
        self.assertEqual((tv["status"], tv["owned"]), ("connected", True))
        lease = self.root / f"tizen-tv-{alias}.lock"
        self.assertTrue(lease.is_dir())
        self.assertFalse(self.canonical.exists())
        fleet.disconnect(alias, {})
        self.assertIn(["disconnect", f"{LAN_A}:26101"], self.sdb.calls)
        self.assertFalse(lease.exists())
        self.assertEqual(self.only(fleet)["status"], "not-connected")

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

    def test_two_tvs_have_independent_leases(self):
        fleet = self.fleet(hits=(LAN_A, LAN_B))
        first, second = (tv["alias"] for tv in fleet.detect()["tvs"])
        fleet.connect(first, {"confirm": ""})
        fleet.connect(second, {"confirm": ""})
        self.assertTrue((self.root / f"tizen-tv-{first}.lock").is_dir())
        self.assertTrue((self.root / f"tizen-tv-{second}.lock").is_dir())
        fleet.disconnect(first, {})
        self.assertTrue((self.root / f"tizen-tv-{second}.lock").is_dir())
        fleet.close()
        self.assertFalse((self.root / f"tizen-tv-{second}.lock").exists())

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
        fleet = self.fleet(hits=(LAN_A,))
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

    def test_submit_connect_runs_in_background_then_identifies(self):
        fleet = self.fleet()
        fleet.submit("detect", None, {})
        fleet.wait()
        alias = fleet.state()["tvs"][0]["alias"]
        state = fleet.submit("connect", alias, {"confirm": ""})
        self.assertEqual(state["tvs"][0]["pending"], "connect")
        fleet.wait()
        tv = self.only(fleet)
        self.assertEqual((tv["status"], tv["pending"], tv["certificate"]), ("connected", None, "match"))
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


if __name__ == "__main__":
    unittest.main()
