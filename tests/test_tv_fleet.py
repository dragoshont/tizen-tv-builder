import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import live_tv as live  # noqa: E402
import tv_fleet  # noqa: E402

# Synthetic placeholders only; never real household addresses or identifiers.
LAN_A, LAN_B = "10.255.255.21", "10.255.255.22"
DUID = "PLACEHOLDERDUID0001"
APP = "AbCdEfGhIj.Example"


def device_profile(*duids):
    return "<DeviceProfile>" + "".join(f"<TestDevice>{d}</TestDevice>" for d in duids) + "</DeviceProfile>"


def write_profiles(data_root, certs):
    """A Tizen Studio profiles.xml; with certs, the active profile uses a Samsung distributor there."""
    folder = Path(data_root) / "profile"
    folder.mkdir(parents=True, exist_ok=True)
    if certs is None:
        (folder / "profiles.xml").write_text("<profiles/>")
        return
    (folder / "profiles.xml").write_text(
        '<profiles active="test-profile"><profile name="test-profile">'
        f'<profileitem distributor="1" key="{certs / "distributor.p12"}"/></profile></profiles>')


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
        if args[2:] == ["shell", "0", "vd_applist"]:
            return "get app list...\napps count: 0\n"
        raise AssertionError("Unexpected SDB call " + " ".join(args))

    def mutations(self):
        return [c for c in self.calls if c and c[0] in {"connect", "disconnect", "kill-server", "reboot"}
                or "kill" in c or "install" in c]


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.canonical = self.root / "canonical.lock"
        self.data_root = self.root / "tizen-data"
        self.certs = self.root / "certs"
        self.certs.mkdir()
        self.certificate = self.certs / "device-profile.xml"
        self.certificate.write_text(device_profile(DUID))
        self.launches = []
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
                    data_root=self.data_root, tizen_path=None, which=lambda name: None,
                    clipboard=lambda text: True, pictures=self.root / "shots", cert_system="Linux",
                    browser=lambda url: self.fail("No browser in this test"),
                    signin=lambda: self.fail("No sign-in listener in this test"),
                    opener=lambda *args: self.fail("Nothing reaches Samsung in this test"))
        write_profiles(self.data_root, self.certs if certificate else None)
        args.update(kwargs)
        return tv_fleet.Fleet(**args)

    def only(self, fleet):
        tvs = fleet.state()["tvs"]
        self.assertEqual(len(tvs), 1)
        return tvs[0]

    def assertPrivate(self, value):
        """Addresses and the DUID appear only in the local page's details and certificate fields."""
        value = json.loads(json.dumps(value))
        for tv in (value.get("tvs") or []) if isinstance(value, dict) else []:
            tv.pop("details", None)
            tv.pop("certificate", None)  # Coverage lists DUIDs on purpose: the user's own certificate.
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
            self.assertEqual({tv["status"] for tv in fleet.state()["tvs"]}, {"connected"})
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
        self.assertEqual((tv["status"], tv["owned"]), ("connected", True))
        self.assertEqual(list(self.root.glob("*.lock")), [])
        self.assertNotIn("lease", tv["details"])
        fleet.disconnect(alias, {})
        self.assertIn(["disconnect", f"{LAN_A}:26101"], self.sdb.calls)
        self.assertEqual(self.only(fleet)["status"], "not-connected")
        # A fresh process reconnects without depending on the previous one's cleanup.
        again = self.fleet()
        again.detect()
        again.connect(alias, {"confirm": ""})
        self.assertEqual(self.only(again)["status"], "connected")
        again.close()

    def test_connect_again_is_a_no_op_not_busy(self):
        fleet = self.fleet()
        alias = fleet.detect()["tvs"][0]["alias"]
        fleet.connect(alias, {"confirm": ""})
        calls = len(self.sdb.calls)
        state = fleet.connect(alias, {"confirm": ""})
        self.assertEqual(len(self.sdb.calls), calls, "No second sdb connect")
        self.assertEqual(state["tvs"][0]["status"], "connected")
        fleet.close()

    def test_configured_source_takes_no_lock_file_and_ignores_foreign_locks(self):
        lane = self.root / "tizen-tv-tv-abc123.lock"
        other = live.Lease(lane)
        other.acquire()
        try:
            source = live.Source({"id": "tv-abc123", "lease_path": str(lane), "lease_seconds": 60})
            source.lease.acquire()
            source.lease.release()
            self.assertTrue((lane / "owner.json").is_file(), "Another session's lock is never removed")
        finally:
            other.release()

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
        self.assertEqual((tv["certificate"]["state"], tv["certificate"]["covers"]), ("covered", [DUID]))
        self.assertEqual((tv["details"]["address"], tv["details"]["duid"]), (LAN_A, DUID), "Full data on the local page")
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

    def test_certificate_coverage_comes_from_the_active_profile(self):
        cases = ((False, None, "no-profile", []),
                 (True, device_profile("OTHERDEVICE0002"), "not-covered", ["OTHERDEVICE0002"]),
                 (True, device_profile("OTHERDEVICE0002", DUID), "covered", ["OTHERDEVICE0002", DUID]),
                 (True, '<!DOCTYPE x [<!ENTITY a "b">]>' + device_profile(DUID), "unreadable", []))
        for certificate, xml, expected, covers in cases:
            with self.subTest(expected=expected):
                if xml:
                    self.certificate.write_text(xml)
                fleet = self.fleet(certificate=certificate)
                alias = fleet.detect()["tvs"][0]["alias"]
                fleet.connect(alias, {})
                fleet.identify(alias, {})
                coverage = self.only(fleet)["certificate"]
                self.assertEqual((coverage["state"], coverage["covers"], coverage["duid"]), (expected, covers, DUID))
                self.assertIsNone(self.only(fleet)["guidance"], "Coverage is information, never a block")
                fleet.close()

    def test_certificate_names_the_tvs_it_covers(self):
        self.certificate.write_text(device_profile("OTHERDEVICE0002", "OTHERDEVICE0003"))
        known = {"tv-living": {"name": "[TV]Livingroom", "model": "UE55MU6172", "duid": "OTHERDEVICE0002"}}
        fleet = self.fleet(known=known)
        alias = next(t["alias"] for t in fleet.detect()["tvs"] if t["alias"] != "tv-living")
        fleet.connect(alias, {})
        fleet.identify(alias, {})
        tv = next(t for t in fleet.state()["tvs"] if t["alias"] == alias)
        coverage = tv["certificate"]
        self.assertEqual(coverage["state"], "not-covered")
        # Known TVs by name; an ID nobody has seen stays an ID.
        self.assertEqual(coverage["covers_names"], ["[TV]Livingroom", "OTHERDEVICE0003"])
        self.assertTrue(coverage["tv_name"])
        self.assertNotEqual(coverage["tv_name"], DUID)
        fleet.close()

    def cert_fleet(self, add_tvs, **kwargs):
        """A fleet whose certificate work is `add_tvs`; nothing reaches Samsung, a browser or the Keychain."""
        self.opened = []
        fleet = self.fleet(browser=self.opened.append, openssl="/bin/openssl", cert_system="Linux",
                           cert_home=self.root / "SamsungCertificate", **kwargs)
        self.calls = []

        def fake(duids, **options):
            self.calls.append((duids, options))
            return add_tvs(duids, **options)
        patcher = mock.patch.object(tv_fleet.samsung_cert, "add_tvs", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        alias = fleet.detect()["tvs"][0]["alias"]
        return fleet, alias

    def wait_for_cert(self, fleet, alias):
        for _ in range(200):
            job = fleet.cert_result(alias)
            if job and job["code"] != "waiting":
                return job
            time.sleep(0.01)
        self.fail("The certificate job never finished")

    def test_cert_add_keeps_covered_tvs_adds_this_one_and_reports_success_from_a_fresh_read(self):
        self.certificate.write_text(device_profile("OTHERDEVICE0002"))
        gate = threading.Event()

        def add_tvs(duids, progress, browser, **options):
            progress("signin", "Waiting for Samsung sign-in\u2026", False)
            browser("https://signin.example.invalid")
            gate.wait(5)
            progress("signin", "Signed in to Samsung", True)
            self.certificate.write_text(device_profile(*duids))
            return {"profile": "tv-companion", "backup": "/backup", "reinstall": False}
        fleet, alias = self.cert_fleet(add_tvs)
        with self.assertRaises(live.LiveError):
            fleet.cert(alias, {})  # The DUID is read over the developer link first.
        fleet.connect(alias, {})
        fleet.identify(alias, {})
        job = fleet.cert(alias, {})
        self.assertEqual((job["ok"], job["code"], job["duid"]), (True, "waiting", DUID))
        self.assertEqual(job["next"], "Sign in to Samsung in the browser window that opened")
        for _ in range(100):
            if self.opened:
                break
            time.sleep(0.01)
        self.assertEqual(self.only(fleet)["job"]["code"], "waiting", "Waiting is not success")
        self.assertEqual(self.calls[0][0], ["OTHERDEVICE0002", DUID], "TVs the certificate already allows stay")
        self.assertEqual(self.calls[0][1]["system"], "Linux")
        # A second click while waiting brings the sign-in page back; it starts nothing new.
        self.assertEqual(fleet.cert(alias, {})["code"], "waiting")
        self.assertEqual((len(self.calls), self.opened[-1]), (1, tv_fleet.samsung_cert.SIGNIN_URL))
        gate.set()
        job = self.wait_for_cert(fleet, alias)
        self.assertEqual((job["ok"], job["code"], job["note"]), (True, "covered", None))
        self.assertIn("You can install your apps on", job["message"])
        self.assertEqual(job["steps"][-1], {"text": "Signed in to Samsung", "done": True})
        self.assertEqual(self.only(fleet)["certificate"]["state"], "covered")
        self.assertEqual(fleet.cert(alias, {})["code"], "covered")
        self.assertEqual(len(self.calls), 1)
        fleet.close()

    def test_cert_add_shows_the_real_error_and_never_claims_success(self):
        self.certificate.write_text(device_profile("OTHERDEVICE0002"))
        outcomes = [tv_fleet.samsung_cert.CertError("rate-limited", "Samsung is limiting certificate requests "
                                                    "right now (HTTP 403)", "Wait a few minutes, then try again"),
                    RuntimeError("disk full"), None]

        def add_tvs(duids, **options):
            outcome = outcomes.pop(0)
            if outcome:
                raise outcome
            return {"profile": "tv-companion", "backup": None, "reinstall": True}  # But nothing changed.
        fleet, alias = self.cert_fleet(add_tvs)
        fleet.connect(alias, {})
        fleet.identify(alias, {})
        fleet.cert(alias, {})
        job = self.wait_for_cert(fleet, alias)
        self.assertEqual((job["ok"], job["code"], job["next"], job["retry"]),
                         (False, "rate-limited", "Wait a few minutes, then try again", True))
        self.assertIn("HTTP 403", job["message"])
        fleet.cert(alias, {})
        job = self.wait_for_cert(fleet, alias)
        self.assertEqual((job["ok"], job["code"], job["message"]), (False, "cert-failed", "RuntimeError: disk full"))
        fleet.cert(alias, {})
        job = self.wait_for_cert(fleet, alias)
        self.assertEqual((job["ok"], job["code"]), (False, "unchanged"))
        self.assertIn("installs still use one that doesn't list", job["message"])
        fleet.close()

    def test_cert_add_for_a_tv_without_any_certificate_mentions_reinstall_once(self):
        def add_tvs(duids, **options):
            self.assertEqual(duids, [DUID], "No certificate yet: only this TV")
            write_profiles(self.data_root, self.certs)
            self.certificate.write_text(device_profile(*duids))
            return {"profile": "tv-companion", "backup": None, "reinstall": True}
        fleet, alias = self.cert_fleet(add_tvs, certificate=False)
        fleet.connect(alias, {})
        fleet.identify(alias, {})
        fleet.cert(alias, {})
        job = self.wait_for_cert(fleet, alias)
        self.assertEqual((job["ok"], job["code"]), (True, "covered"))
        self.assertEqual(job["note"], "Apps installed with your old certificate need one reinstall.")
        fleet.close()

    def two_profiles(self, switch=True):
        """test-profile (active, without this TV) and plx-tv-2 (a newer profile listing it)."""
        second = self.root / "certs2"
        second.mkdir()
        (second / "device-profile.xml").write_text(device_profile("OTHERDEVICE0002", DUID))
        self.certificate.write_text(device_profile("OTHERDEVICE0002"))
        profiles = self.data_root / "profile" / "profiles.xml"
        def body(active):
            return (f'<profiles active="{active}"><profile name="test-profile">'
                    f'<profileitem distributor="1" key="{self.certs / "distributor.p12"}" password="secret"/></profile>'
                    f'<profile name="plx-tv-2"><profileitem distributor="1" key="{second / "distributor.p12"}"/>'
                    '</profile></profiles>')
        tizen = str(self.root / "tizen")
        self.tizen_calls = []
        def runner(argv, timeout=60, limit=None):
            if argv[0] != tizen:
                return self.sdb(argv, timeout, limit)
            self.tizen_calls.append(argv[1:])
            if switch:
                profiles.write_text(body(argv[-1]))
                return "Succeed to set 'active' profile."
            return "[ERROR] something went wrong"
        fleet = self.fleet(runner=runner, tizen_path=tizen)
        profiles.write_text(body("test-profile"))
        return fleet

    def test_another_profile_that_allows_the_tv_is_offered_instead_of_a_sign_in(self):
        fleet = self.two_profiles()
        alias = fleet.detect()["tvs"][0]["alias"]
        fleet.connect(alias, {})
        fleet.identify(alias, {})
        certificate = self.only(fleet)["certificate"]
        self.assertEqual((certificate["state"], certificate["profile"], certificate["others"]),
                         ("not-covered", "test-profile", ["plx-tv-2"]))
        self.assertNotIn("by_profile", certificate, "Other profiles' device lists stay private")
        job = fleet.cert(alias, {})
        self.assertEqual((job["ok"], job["code"], job["use_profile"]), (False, "other-profile", "plx-tv-2"))
        self.assertIn("plx-tv-2 allows", job["message"])
        job = fleet.profile(alias, {"profile": "plx-tv-2"})
        self.assertEqual((job["ok"], job["code"]), (True, "profile-active"))
        self.assertEqual(self.tizen_calls, [["security-profiles", "set-active", "-n", "plx-tv-2"]])
        self.assertEqual(self.only(fleet)["certificate"]["state"], "covered")
        self.assertIn("allows this TV", job["message"])
        with self.assertRaises(live.LiveError):
            fleet.profile(alias, {"profile": "no-such-profile"})
        fleet.close()

    def test_a_new_profile_that_allows_the_tv_is_offered_after_the_job(self):
        fleet = self.two_profiles()
        (self.root / "certs2" / "device-profile.xml").write_text(device_profile("OTHERDEVICE0002"))
        alias = fleet.detect()["tvs"][0]["alias"]
        fleet.connect(alias, {})
        fleet.identify(alias, {})
        (self.root / "certs2" / "device-profile.xml").write_text(device_profile("OTHERDEVICE0002", DUID))
        job = fleet.cert(alias, {})
        self.assertEqual((job["code"], job["use_profile"]), ("other-profile", "plx-tv-2"))
        fleet.close()

    def test_a_profile_switch_the_cli_did_not_make_is_not_claimed(self):
        fleet = self.two_profiles(switch=False)
        alias = fleet.detect()["tvs"][0]["alias"]
        fleet.connect(alias, {})
        fleet.identify(alias, {})
        job = fleet.profile(alias, {"profile": "plx-tv-2"})
        self.assertEqual((job["ok"], job["code"]), (False, "profile-unchanged"))
        self.assertIn("still test-profile", job["message"])
        self.assertIn("[ERROR] something went wrong", job["output"])
        fleet.close()

    def test_changes_need_only_a_connection_and_the_tizen_cli(self):
        fleet = self.fleet()
        alias = fleet.detect()["tvs"][0]["alias"]
        for operation, data in (("install", {"package": str(self.root / "a.wgt")}),
                                ("remove", {"app": APP}), ("launch", {"app": APP})):
            with self.assertRaises(live.LiveError) as caught:
                getattr(fleet, operation)(alias, data)
            self.assertEqual(caught.exception.code, "connect")
        fleet.connect(alias, {})
        for operation, data in (("remove", {"app": APP}), ("launch", {"app": APP})):
            with self.assertRaises(live.LiveError) as caught:
                getattr(fleet, operation)(alias, data)
            self.assertEqual(caught.exception.code, "sdk", "No typed confirmation; only the missing CLI stops it")
        fleet.close()

    def test_offline_tv_keeps_last_known_name_model_and_last_seen_without_an_address(self):
        saved = []
        fleet = self.fleet(save_known=saved.append)
        fleet.detect()
        alias = self.only(fleet)["alias"]
        self.assertTrue(saved, "A seen TV's facts are persisted")
        fact = saved[-1][alias]
        self.assertEqual((fact["name"], fact["model"]), ("[TV] Samsung 7 Series (55)", "UE55NU7400"))
        self.assertNotIn(LAN_A, json.dumps(saved[-1]), "The network address is never persisted")
        live.read_known({"known_tvs": saved[-1]})  # The persisted shape passes the profile validator.
        # Next run, before any scan: remembered but unchecked, so "Checking…", never "Not reachable".
        later = self.fleet(hits=[], known=saved[-1])
        before = self.only(later)
        self.assertEqual((before["label"], before["tone"]), ("Checking…", "progress"))
        self.assertIn("Checking…", tv_fleet.STATUSES)
        # After the scan the TV is off: it still has its card, name and model, plainly "Not reachable".
        later.detect()
        tv = self.only(later)
        self.assertEqual(tv["alias"], alias)
        self.assertEqual((tv["label"], tv["missing"]), ("Not reachable", True))
        self.assertEqual(tv["headline"], tv_fleet.OFFLINE)
        self.assertIsNone(tv["dev_mode"].get("next"))
        self.assertEqual(tv["metadata"]["model"], "UE55NU7400")
        self.assertIn("Samsung 7 Series", tv["name"])
        self.assertEqual(tv["last_seen"], fact["last_seen"])

    def test_asleep_tv_still_listed_by_sdb_keeps_its_name_and_real_last_seen(self):
        saved, now = [], [1_000_000]
        clock = lambda: now[0]
        fleet = self.fleet(save_known=saved.append, clock=clock)
        fleet.detect()
        alias = self.only(fleet)["alias"]
        first = saved[-1][alias]["last_seen"]
        # Hours later the TV sleeps: its ports are closed, its API is silent, but sdb still lists it offline.
        now[0] += 3 * 3600

        def silent(host):
            raise OSError("timed out")
        self.sdb.devices = {f"{LAN_A}:26101": "offline"}
        later = self.fleet(hits=[], info=silent, known=saved[-1], save_known=saved.append, clock=clock)
        later.detect()
        tv = self.only(later)
        self.assertEqual(tv["alias"], alias, "Same card, not a second serial-keyed one")
        self.assertIn("Samsung 7 Series", tv["name"])
        self.assertNotIn("#", tv["name"])
        self.assertEqual(tv["label"], "Not reachable")
        self.assertEqual(tv["last_seen"], first, "Last seen is when it was reachable, not this scan")

    def test_serial_only_memory_without_facts_is_not_a_nameless_card(self):
        fleet = self.fleet(hits=[], known={"tv-abcdef": {"last_seen": 1_000_000}})
        fleet.detect()
        self.assertEqual(fleet.state()["tvs"], [], "No 'Samsung TV #1' for a TV we know nothing about")

    def test_tv_seen_by_its_api_only_is_remembered_and_listed_when_off(self):
        saved = []
        fleet = self.fleet(hits=[], api_hits=[LAN_B], save_known=saved.append,
                           info=lambda host: {"name": "[TV] Neo QLED", "model": "QE55QN90AATXXU",
                                              "year": "2021", "developer_mode": "0", "uid": "uuid:neo"})
        fleet.detect()
        alias = self.only(fleet)["alias"]
        self.assertEqual(saved[-1][alias]["model"], "QE55QN90AATXXU")
        later = self.fleet(hits=[], api_hits=[], known=saved[-1])
        later.detect()
        tv = self.only(later)
        self.assertEqual((tv["alias"], tv["label"]), (alias, "Not reachable"))
        self.assertIn("Neo QLED", tv["name"])

    def test_os_without_a_version_is_omitted_not_a_bare_tizen(self):
        fleet = self.fleet(info=lambda host: {"name": "[TV] Den", "model": "UE55NU7400", "os": "Tizen",
                                              "developer_mode": "1", "uid": "uuid:" + host})
        fleet.detect()
        tv = self.only(fleet)
        self.assertIsNone(tv["metadata"]["os"]["text"])
        self.assertIsNone(tv["details"]["os"])

    def test_known_tvs_reject_addresses_and_unknown_fields(self):
        for bad in ({"tv-abcdef": {"host": "192.0.2.4"}}, {"tv-abcdef": {"name": "192.0.2.4:26101"}},
                    {"192.0.2.4": {"name": "TV"}}, {"tv-abcdef": {"last_seen": "yesterday"}}):
            with self.subTest(bad=bad), self.assertRaises(live.LiveError):
                live.read_known({"known_tvs": bad})

    def test_known_tv_missed_by_one_scan_stays_as_not_found(self):
        hits, answering = [LAN_A, LAN_B], {LAN_A, LAN_B}

        def info(host):
            if host not in answering:
                raise OSError("timed out")
            return {"name": "[TV] Samsung", "model": "UE55NU7400", "year": "2019", "developer_mode": "1", "uid": "uuid:" + host}
        fleet = self.fleet(hits=hits, info=info)
        fleet.detect()
        before = {tv["alias"]: tv["name"] for tv in fleet.state()["tvs"]}
        held, bedroom = sorted(before)
        held = next(tv["alias"] for tv in fleet.state()["tvs"] if tv["alias"] != bedroom)
        fleet.connect(held, {"confirm": ""})
        identity = fleet.records[held]["serial"]
        lost = fleet.records[bedroom]["serial"]
        hits[:] = [LAN_A] if lost.startswith(LAN_B) else [LAN_B]
        fleet.detect()
        tvs = {tv["alias"]: tv for tv in fleet.state()["tvs"]}
        self.assertEqual(set(tvs), set(before), "A TV missing from one scan keeps its card")
        self.assertEqual((tvs[bedroom]["label"], tvs[bedroom]["missing"], tvs[bedroom]["status"]),
                         ("Not reachable", True, "not-connected"))
        self.assertEqual(tvs[bedroom]["name"], before[bedroom])
        self.assertEqual(tvs[bedroom]["dev_mode"]["state"], "unknown", "Cached API info is not a diagnosis")
        self.assertEqual(fleet.records[held]["serial"], identity, "The held TV is never retargeted")
        self.assertEqual(tvs[held]["status"], "connected")
        # It returns while its REST info times out: same card, not a second serial-keyed one.
        hits[:] = [LAN_A, LAN_B]
        answering.clear()
        fleet.detect()
        tvs = {tv["alias"]: tv for tv in fleet.state()["tvs"]}
        self.assertEqual(set(tvs), set(before))
        self.assertFalse(tvs[bedroom]["missing"])
        self.assertEqual(tvs[bedroom]["name"], before[bedroom])
        self.assertPrivate(fleet.state())
        fleet.close()

    def test_two_tvs_connect_independently(self):
        fleet = self.fleet(hits=(LAN_A, LAN_B))
        first, second = (tv["alias"] for tv in fleet.detect()["tvs"])
        fleet.connect(first, {"confirm": ""})
        fleet.connect(second, {"confirm": ""})
        fleet.disconnect(first, {})
        states = {tv["alias"]: tv["status"] for tv in fleet.state()["tvs"]}
        self.assertEqual((states[first], states[second]), ("not-connected", "connected"))
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
        tvs = fleet.state()["tvs"]
        self.assertTrue(all(tv["unnamed"] for tv in tvs), "Portal and CLI title these 'TV at <address>'")
        self.assertEqual(sorted(tv["details"]["address"] for tv in tvs), [LAN_A, LAN_B])
        info = lambda host: {"name": "unknown", "model": "unknown", "year": "2018"}
        fleet = self.fleet(info=info, save_names=saved.append)
        alias = fleet.detect()["tvs"][0]["alias"]
        self.assertEqual(self.only(fleet)["name"], "Samsung TV (2018) #1")
        fleet.rename(alias, {"name": "Bedroom TV"})
        self.assertEqual(saved[-1], {alias: "Bedroom TV"})
        self.assertEqual(self.only(fleet)["name"], "Bedroom TV")
        self.assertFalse(self.only(fleet)["unnamed"])
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
        self.certificate.write_text(device_profile("OTHERDEVICE0002"))
        fleet.connect(tvs[True], {})
        fleet.identify(tvs[True], {})
        tv = next(t for t in fleet.state()["tvs"] if t["alias"] == tvs[True])
        self.assertIsNone(tv["guidance"], "An uncovered certificate never blocks or nags before an install fails")
        self.assertEqual(tv["label"], "Connected")
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
        self.assertEqual((tv["status"], tv["label"], tv["tone"]), ("not-connected", "Connection problem", "lost"))
        self.assertIn("Host PC IP", tv["headline"])
        self.sdb.accept = True
        fleet.connect(tvs[True], {})
        fleet.identify(tvs[True], {})
        dev = card(tvs[True])
        self.assertEqual((dev["label"], dev["tone"], dev["status"]), ("Connected", "ok", "connected"))
        self.assertTrue(dev["headline"].startswith("Connected"), dev["headline"])
        self.certificate.write_text(device_profile("OTHERDEVICE0002"))
        fleet.identify(tvs[True], {})
        self.assertEqual(card(tvs[True])["label"], "Connected", "The certificate is never the status")
        text = repr(fleet.state())
        for banned in tv_fleet.BANNED if hasattr(tv_fleet, "BANNED") else (
                "No app open", "Developer Mode ready", "Dev mode enabled", "Preview not connected",
                "Needs to be configured", "Busy"):
            self.assertNotIn(banned, text)
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
        self.assertEqual((tv["status"], tv["pending"], tv["certificate"]["state"]), ("connected", None, "covered"))
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

    def test_open_screen_needs_no_consent_and_keeps_connected(self):
        fleet, alias, Session = self.preview_fleet()
        try:
            self.assertEqual(self.only(fleet)["status"], "connected")
            fleet.preview(alias, {"app": "AbCdEfGhIj.App"})
            tv = self.only(fleet)
            self.assertEqual((tv["status"], tv["label"]), ("connected", "Connected"))
            self.assertIn("Showing AbCdEfGhIj.App", tv["headline"])
            self.assertTrue(tv["preview"]["ready"] and tv["preview"]["frames"])
            self.assertTrue(tv["preview"]["activation_allowed"], "OK goes to the opened app")
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

    def test_store_app_refusal_is_shown_and_remembered(self):
        refused = []
        class Refusing:
            def __init__(self, serial, app, legacy):
                self.app, self.forward, self.client = app, None, None
            def launch(self):
                refused.append(self.app)
                raise live.LiveError("sdk", "SDK command failed: closed")
            def close(self):
                pass
        fleet = self.fleet(session=Refusing)
        alias = fleet.detect()["tvs"][0]["alias"]
        fleet.connect(alias, {})
        try:
            with self.assertRaises(live.LiveError) as caught:
                fleet.preview(alias, {"app": "AbCdEfGhIj.Store"})
            self.assertIn(tv_fleet.STORE_APP, str(caught.exception))
            self.assertEqual(self.only(fleet)["apps"]["store"], ["AbCdEfGhIj.Store"])
            self.assertEqual(self.only(fleet)["label"], "Connected", "A refused app is not a broken link")
            fleet.records[alias]["installed_here"] = {"AbCdEfGhIj.Mine"}
            with self.assertRaises(live.LiveError) as caught:
                fleet.preview(alias, {"app": "AbCdEfGhIj.Mine"})
            self.assertNotIn(tv_fleet.STORE_APP, str(caught.exception), "Own apps show the TV's real error")
            self.assertEqual(self.only(fleet)["apps"]["store"], ["AbCdEfGhIj.Store"])
            self.assertEqual(refused, ["AbCdEfGhIj.Store", "AbCdEfGhIj.Mine"], "Open stays clickable on any app")
        finally:
            fleet.close()

    def test_failed_capture_stays_developer_and_cleans_up(self):
        fleet, alias, Session = self.preview_fleet(capture_frames=False)
        try:
            with self.assertRaises(live.LiveError) as caught:
                fleet.preview(alias, {"app": "AbCdEfGhIj.App", "restart": True})
            self.assertEqual(caught.exception.code, "preview")
            tv = self.only(fleet)
            self.assertEqual((tv["status"], tv["preview"]), ("connected", None))
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
            self.assertEqual(tvs[name]["dev_mode"]["label"], "Developer port open")
            self.assertIn("Connect", tvs[name]["dev_mode"]["next"])
        for name in ("31", "32"):
            self.assertEqual(tvs[name]["dev_mode"]["label"], "Developer Mode off")
            self.assertIn("reports Developer Mode off", tvs[name]["dev_mode"]["explanation"])
            self.assertIn("1 2 3 4 5", tvs[name]["dev_mode"]["next"])
            self.assertEqual(tvs[name]["label"], "Developer Mode off")
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
        self.assertEqual(refused["label"], "Host PC IP points elsewhere")
        self.assertIn("Host PC IP", refused["next"])
        self.sdb.accept = True
        fleet.connect(alias, {"confirm": ""})
        enabled = self.by_host(fleet)["21"]
        self.assertEqual(enabled["dev_mode"]["label"], "Developer Mode on")
        self.assertEqual(enabled["label"], "Connected")
        self.assertNotIn("install", enabled)
        fleet.close()

    def test_closed_port_without_reported_mode_is_not_called_off(self):
        fleet = self.fleet(hits=(), api_hits=(LAN_A,),
                           info=lambda host: {"name": "[TV] Den", "model": "UE55NU7400", "developer_mode": "unknown"})
        dev = fleet.detect()["tvs"][0]["dev_mode"]
        self.assertEqual((dev["state"], dev["label"]), ("closed", "Developer port closed"))

    def test_api_reporting_developer_mode_on_with_closed_port_needs_configuration(self):
        fleet = self.fleet(hits=(), api_hits=(LAN_A,),
                           info=lambda host: {"name": "[TV] Den", "model": "UE55NU7400", "developer_mode": "on"})
        fleet.detect()
        dev = self.only(fleet)["dev_mode"]
        self.assertEqual(dev["label"], "Developer Mode on, port closed")
        self.assertIn("Restart", dev["next"])
        fleet.close()

    def test_tv_missing_from_the_scan_is_never_diagnosed_from_cached_api_info(self):
        hits = [LAN_A]
        answering = {LAN_A}

        def info(host):
            if host not in answering:
                raise OSError("timed out")
            return {"name": "[TV] Den", "model": "UE55NU7400", "developer_mode": "on", "uid": "uuid:" + host}
        fleet = self.fleet(hits=hits, info=info)
        fleet.detect()
        hits.clear()
        answering.clear()
        fleet.detect()  # Off or gone: the cached "developer_mode on" plus a closed port proves nothing now.
        tv = self.only(fleet)
        self.assertEqual((tv["label"], tv["missing"]), ("Not reachable", True))
        self.assertEqual(tv["dev_mode"]["state"], "unknown")
        self.assertNotEqual(tv["dev_mode"]["label"], "Needs to be configured")
        self.assertIsNone(tv["dev_mode"]["diagnosis"])
        fleet.close()

    def test_an_earlier_developer_connection_is_not_reported_as_working_now(self):
        fleet = self.lab()
        fleet.detect()
        alias = self.by_host(fleet)["21"]["alias"]
        fleet.connect(alias, {"confirm": ""})
        self.assertEqual(self.by_host(fleet)["21"]["dev_mode"]["state"], "enabled")
        fleet.disconnect(alias, {})
        dev = self.by_host(fleet)["21"]["dev_mode"]
        self.assertNotEqual(dev["state"], "enabled")
        self.assertNotIn("has a working developer connection", dev["explanation"])
        self.assertIn("earlier", dev["explanation"])
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
        self.assertIsNone(meta["os"]["text"], "A bare \"Tizen\" without a version is omitted")
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
