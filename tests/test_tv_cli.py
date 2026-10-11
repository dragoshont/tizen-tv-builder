import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import tv  # noqa: E402
import tv_fleet  # noqa: E402
from test_tv_fleet import FakeSdb, LAN_A, DUID  # noqa: E402
from test_live_tv import FakeCDP, source_config, live  # noqa: E402


@unittest.skipUnless(os.name == "posix", "Private profiles require POSIX ownership")
class TvCliTests(unittest.TestCase):
    """The tv CLI shares the portal's Fleet/Source paths; fakes only, no SDK, network or TV."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sdb = FakeSdb()
        self.tool = self.root / "sdb"
        self.tool.write_text("")
        self.certificate = self.root / "device-profile.xml"
        self.certificate.write_text(f"<profile><duid>{DUID}</duid></profile>")
        self.profile = self.root / "profile.json"
        # Emulator kind: a native TV source must use /tmp lease paths, which tests never create.
        self.write_profile({"sources": [source_config(self.root, kind="emulator")], "certificate_duids": str(self.certificate)})

    def tearDown(self):
        self.temporary.cleanup()

    def write_profile(self, data):
        self.profile.write_text(json.dumps(data))
        self.profile.chmod(0o600)

    def fleet(self, certificate_duids=None, names=None, save_names=None):
        return tv_fleet.Fleet(
            sdb_path=str(self.tool), runner=self.sdb, interfaces=lambda: ["10.255.255.10"],
            sweep=lambda address, port, timeout: {LAN_A}, ssdp=lambda: set(),
            info=lambda host: {"name": "[TV] Samsung 7 Series (55)", "model": "UE55NU7400", "year": "2018",
                               "developer_mode": "1", "uid": "uuid:fixture"},
            key_path=self.root / "alias.key", lease_root=self.root, canonical=self.root / "canonical.lock",
            certificate_duids=certificate_duids, names=names or {}, save_names=save_names)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = tv.main(list(argv), fleet_factory=self.fleet, cdp_factory=FakeCDP, stdout=out, stderr=err,
                       state_dir=self.root / "state")
        return code, out.getvalue(), err.getvalue()

    def alias(self):
        code, out, _ = self.run_cli("scan", "--json")
        self.assertEqual(code, 0)
        return json.loads(out)["tvs"][0]["alias"]

    def assert_private(self, text):
        for secret in (LAN_A, DUID, "12345", "uuid:fixture", "test-page"):
            self.assertNotIn(secret, text)

    def test_scan_prints_named_cards_with_plain_status_and_no_identifiers(self):
        code, out, err = self.run_cli("scan")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Samsung 7 Series (55) · UE55NU7400", out)
        self.assertIn("Ready to connect", out)
        self.assertRegex(out, r"tv-[0-9a-f]{6}")
        self.assert_private(out)
        self.assertEqual(self.sdb.mutations(), [])

    def test_status_is_scan_and_json_matches_portal_state_shape(self):
        code, out, _ = self.run_cli("status", "--json")
        state = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(set(state), {"scan", "sdk", "tizen", "signing_profiles", "tvs"})
        self.assertEqual(state["tvs"][0]["label"], "Ready to connect")
        self.assert_private(out)

    def test_connect_identifies_then_disconnects_owned(self):
        alias = self.alias()
        code, out, err = self.run_cli("--profile", str(self.profile), "connect", alias)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Choose Show apps to pick an app on this TV, or install a package", out)
        self.assertNotIn("Connected ·", out)
        self.assertIn("Disconnected", out)
        self.assertEqual([c[0] for c in self.sdb.mutations()], ["connect", "disconnect"])
        self.assertEqual(list(self.root.glob("tizen-tv-*.lock")), [])
        self.assert_private(out + err)

    def test_connect_failure_prints_guided_fix_and_host_ip_without_tv_address(self):
        self.sdb.accept = False
        alias = self.alias()
        code, out, err = self.run_cli("connect", alias, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("Check the TV's Host PC IP", out)
        self.assertIn("This computer's IP to enter on the TV: 10.255.255.10", out)
        self.assertIn("12345", out.replace(" ", ""))  # The Developer Mode step, not the TV address.
        self.assertNotIn(LAN_A, out + err)

    def test_info_prints_portal_detail_rows_without_contacting_the_tv(self):
        alias = self.alias()
        code, out, err = self.run_cli("info", alias)
        self.assertEqual((code, err), (0, ""))
        for row in (r"Model +UE55NU7400", r"Model year +2018", r"Developer port +open", r"Device ID \(DUID\) +not read",
                    r"This computer's IP to enter on the TV +10\.255\.255\.10"):
            self.assertRegex(out, row)
        self.assert_private(out)
        self.assertEqual(self.sdb.mutations(), [])

    def test_info_connect_reads_identity_and_certificate_then_disconnects(self):
        alias = self.alias()
        code, out, err = self.run_cli("--profile", str(self.profile), "info", alias, "--connect")
        self.assertEqual((code, err), (0, ""))
        self.assertRegex(out, r"Device ID \(DUID\) +present \(hidden\)")
        self.assertRegex(out, r"Certificate +includes this TV")
        self.assertRegex(out, r"OS +Tizen 4\.0 \(observed")
        self.assertRegex(out, r"Link +none")
        self.assert_private(out)
        self.assertEqual([c[0] for c in self.sdb.mutations()], ["connect", "disconnect"])
        self.assertEqual(list(self.root.glob("tizen-tv-*.lock")), [])

    def test_info_show_duid_prints_it_only_when_asked(self):
        alias = self.alias()
        code, out, _ = self.run_cli("--profile", str(self.profile), "info", alias, "--show-duid")
        self.assertEqual(code, 0)
        self.assertIn(DUID, out)
        self.assertNotIn(LAN_A, out)
        self.assertEqual([c[0] for c in self.sdb.mutations()], ["connect", "disconnect"])

    def test_unknown_alias_and_raw_address_are_refused(self):
        for target in ("tv-ffffff", LAN_A):
            code, _, err = self.run_cli("connect", target, "--yes")
            self.assertNotEqual(code, 0)
            self.assertNotIn(LAN_A, err.replace(target, "") if target != LAN_A else "")
        self.assertEqual(self.sdb.mutations(), [])

    def test_rename_persists_in_private_profile_by_alias(self):
        alias = self.alias()
        code, out, _ = self.run_cli("--profile", str(self.profile), "rename", alias, "Den TV")
        self.assertEqual(code, 0)
        self.assertIn("Den TV", out)
        self.assertEqual(json.loads(self.profile.read_text())["tv_names"], {alias: "Den TV"})
        self.assertEqual(oct(self.profile.stat().st_mode & 0o777), "0o600")
        code, out, _ = self.run_cli("--profile", str(self.profile), "scan")
        self.assertIn("Den TV", out)
        # Without --profile, rename saves to the private default profile it creates.
        self.assertEqual(self.run_cli("rename", alias, "Den")[0], 0)
        default = self.root / "state" / "profile.json"
        self.assertEqual(json.loads(default.read_text())["tv_names"], {alias: "Den"})
        self.assertIn("Den", self.run_cli("list")[1])

    def test_key_sends_allowlisted_keys_then_disconnects(self):
        self.assertEqual(self.run_cli("--profile", str(self.profile), "key", "sample", "down")[0], 2)
        code, out, err = self.run_cli("--profile", str(self.profile), "key", "sample", "down", "back", "--yes")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Sent down, back", out)
        self.assertFalse(Path(source_config(self.root)["lease_path"]).exists())
        # OK keeps the portal's audited-safe-target gate, then still releases the lease.
        code, _, err = self.run_cli("--profile", str(self.profile), "key", "sample", "ok", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("OK", err)
        self.assertFalse(Path(source_config(self.root)["lease_path"]).exists())
        code, _, err = self.run_cli("--profile", str(self.profile), "key", "sample", "power", "--yes")
        self.assertEqual(code, 2)
        self.assertIn("power", err)

    def test_screenshot_writes_private_png_outside_repo_only(self):
        out_dir = self.root / "shots"
        code, out, err = self.run_cli("--profile", str(self.profile), "screenshot", "sample",
                                      "--out", str(out_dir), "--yes")
        self.assertEqual((code, err), (0, ""))
        written = sorted(out_dir.iterdir())
        self.assertEqual(len(written), 1)
        self.assertTrue(written[0].read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(oct(written[0].stat().st_mode & 0o777), "0o600")
        self.assertIn("renderer layers, not the full panel or video", out)
        code, _, err = self.run_cli("--profile", str(self.profile), "screenshot", "sample",
                                    "--out", str(ROOT / "shots"), "--yes")
        self.assertEqual(code, 2)
        self.assertFalse((ROOT / "shots").exists())

    def test_default_profile_is_created_private_and_empty_without_credentials(self):
        state = self.root / "state"
        path = tv.default_profile(state, create=True)
        self.assertEqual(path, state / "profile.json")
        self.assertEqual(json.loads(path.read_text()), {"sources": []})
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(state.stat().st_mode & 0o777), "0o700")
        self.assertEqual(tv.default_profile(state, create=True), path)  # Reused, not rewritten.
        self.assertIsNone(tv.default_profile(self.root / "absent", create=False))
        os.environ["TIZEN_TV_PROFILE"] = str(self.profile)
        try:
            self.assertEqual(tv.default_profile(state, create=True), self.profile)
        finally:
            del os.environ["TIZEN_TV_PROFILE"]

    def test_scan_saves_private_list_without_addresses_and_list_never_scans(self):
        code, before, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("tv scan", before)
        alias = self.alias()
        cache = self.root / "state" / "last-scan.json"
        self.assertEqual(oct(cache.stat().st_mode & 0o777), "0o600")
        self.assert_private(cache.read_text())
        calls = len(self.sdb.calls)
        out, err = io.StringIO(), io.StringIO()
        def no_scan(**_):
            raise AssertionError("list must not scan")
        code = tv.main(["list"], fleet_factory=no_scan, stdout=out, stderr=err, state_dir=self.root / "state")
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertIn(alias, out.getvalue())
        self.assertIn("Ready to connect", out.getvalue())
        self.assertEqual(len(self.sdb.calls), calls)
        self.assert_private(out.getvalue())

    def test_check_separates_open_port_from_working_developer_connection(self):
        alias = self.alias()
        code, out, err = self.run_cli("check", alias)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Developer Mode port: open", out)
        self.assertIn("Developer connection: accepted", out)
        self.assertIn("Identity: Tizen 4.0, DUID present (hidden)", out)
        self.assertIn("Developer Mode ready", out)
        self.assertIn("Dev mode enabled", out)
        self.assertIn("About: UE55NU7400 · Model year 2018 (reported) · Tizen · version unavailable", out)
        self.assertEqual([c[0] for c in self.sdb.mutations()], ["connect", "disconnect"])
        self.assert_private(out)

    def test_check_reports_port_open_but_connection_refused_as_not_ready(self):
        self.sdb.accept = False
        alias = self.alias()
        code, out, _ = self.run_cli("check", alias)
        self.assertEqual(code, 1)
        self.assertIn("Developer Mode port: open", out)
        self.assertIn("Developer connection: refused", out)
        self.assertIn("Needs to be configured: Developer Mode is on, but the TV refused this computer: its Host PC IP "
                      "most likely points to another computer", out)
        self.assertIn("Next: On the TV set Host PC IP to this computer", out)
        self.assertIn("Not ready", out)
        self.assertNotIn("Developer Mode ready", out)
        self.assertNotIn(LAN_A, out)

    def test_scan_and_list_share_portal_metadata_and_dev_labels(self):
        code, out, _ = self.run_cli("scan")
        self.assertEqual(code, 0)
        self.assertIn("Dev mode reachable · not checked", out)
        self.assertNotIn("Needs to be configured", out)
        self.assertIn("UE55NU7400 · Model year 2018 (reported) · Tizen · version unavailable", out)
        self.assertIn("Next: Connect to check", out)
        code, listed, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("Model year 2018 (reported)", listed)
        self.assertIn("Dev mode reachable · not checked", listed)
        self.assert_private(out + listed)

    def test_portal_uses_default_profile_and_prints_plain_url(self):
        seen = {}
        def fake_serve(profile, owner_stdin=False, port=0, announce=None):
            seen["profile"] = profile
            seen["port"] = port
            announce("http://127.0.0.1:47811/")
        original = tv.live_tv.serve
        tv.live_tv.serve = fake_serve
        try:
            code, out, err = self.run_cli("portal")
        finally:
            tv.live_tv.serve = original
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(seen["profile"], self.root / "state" / "profile.json")
        self.assertEqual(seen["port"], tv.PORTAL_PORT)
        self.assertIn("Open http://127.0.0.1:47811/", out)
        self.assertNotIn("Scanning", out)  # Starting the page never contacts TVs.

    def test_kit_entrypoint_delegates_tv_commands(self):
        import subprocess
        result = subprocess.run([sys.executable, str(ROOT / "tools/tizen.py"), "tv", "--help"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("scan", "list", "check", "connect", "portal"):
            self.assertIn(command, result.stdout)
        result = subprocess.run([sys.executable, str(ROOT / "tools/tizen.py"), "--help"],
                                capture_output=True, text=True, timeout=30)
        self.assertIn("tv", result.stdout)

    def test_help_lists_portal_capabilities(self):
        parser = tv.parser()
        text = parser.format_help()
        for command in ("scan", "list", "check", "status", "rename", "connect", "key", "screenshot", "portal"):
            self.assertIn(command, text)


if __name__ == "__main__":
    unittest.main()
