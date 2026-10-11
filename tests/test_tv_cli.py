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


APP = "AbCdEfGhIj.App"
STORE = "StOrEaPpId.store"


class AppsSdb(FakeSdb):
    """FakeSdb that also lists installed apps, like vd_applist on the observed TVs."""
    def __call__(self, argv, timeout=60, limit=None):
        if argv[1:][2:] == ["shell", "0", "vd_applist"]:
            self.calls.append(argv[1:])
            return f"get app list...\napps count: 2\napp_tizen_id: {APP}\napp_tizen_id: {STORE}\n"
        return super().__call__(argv, timeout, limit)


@unittest.skipUnless(os.name == "posix", "Private profiles require POSIX ownership")
class TvCliTests(unittest.TestCase):
    """The tv CLI uses the portal's Fleet paths and words; fakes only, no SDK, network or TV."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sdb = AppsSdb()
        self.tool = self.root / "sdb"
        self.tool.write_text("")
        self.certificate = self.root / "device-profile.xml"
        self.certificate.write_text(f"<profile><duid>{DUID}</duid></profile>")
        self.profile = self.root / "profile.json"
        # Emulator kind: a native TV source must use /tmp lease paths, which tests never create.
        self.write_profile({"sources": [source_config(self.root, kind="emulator")], "certificate_duids": str(self.certificate)})
        self.sessions = []
        self.launches = []

    def tearDown(self):
        self.temporary.cleanup()

    def write_profile(self, data):
        self.profile.write_text(json.dumps(data))
        self.profile.chmod(0o600)

    def fleet(self, certificate_duids=None, names=None, save_names=None, known=None, save_known=None):
        test = self
        class Session:
            def __init__(self, serial, app, legacy):
                self.app, self.forward, self.client = app, None, None
                test.sessions.append(self)
            def launch(self):
                if self.app == STORE:
                    raise tv_fleet.LiveError("sdk", "SDK command failed: closed")
                self.forward = (45555, 9222)
            def close(self):
                self.forward = None
        return tv_fleet.Fleet(
            sdb_path=str(self.tool), runner=self.run_tool, interfaces=lambda: ["10.255.255.10"],
            sweep=lambda address, port, timeout: {LAN_A}, ssdp=lambda: set(),
            info=lambda host: {"name": "[TV] Samsung 7 Series (55)", "model": "UE55NU7400", "year": "2018",
                               "developer_mode": "1", "uid": "uuid:fixture"},
            key_path=self.root / "alias.key", lease_root=self.root, canonical=self.root / "canonical.lock",
            certificate_duids=certificate_duids, names=names or {}, save_names=save_names,
            data_root=self.root / "tizen-data", tizen_path=self.tizen_path, which=lambda name: None,
            launcher=self.launch_tizen_cert, clipboard=lambda text: True,
            installer=self.install_tizen_cert, cert_locations=(self.root / "bin" / "tizen-cert",),
            pictures=self.root / "Pictures", session=Session, cdp=FakeCDP, discover=lambda port: ["page-1"])

    def install_tizen_cert(self):
        binary = self.root / "bin" / "tizen-cert"
        binary.parent.mkdir(exist_ok=True)
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        return 0, f"Installed: {binary} (v1.0.0)"

    def launch_tizen_cert(self, command, status):
        self.launches.append(command)
        Path(status).write_text(self.tizen_cert_exit)  # The person closed tizen-cert without renewing.

    tizen_cert_exit = "0\n"
    tizen_path = None

    def run_tool(self, argv, timeout=60, limit=None):
        if argv[0] != self.tizen_path:
            return self.sdb(argv, timeout, limit)
        self.write_profiles(argv[-1])  # `tizen security-profiles set-active -n NAME`
        return "Succeed to set 'active' profile."

    def write_profiles(self, active):
        """Two signing profiles; only plx-tv-2's certificate lists this TV."""
        folder = self.root / "tizen-data" / "profile"
        folder.mkdir(parents=True, exist_ok=True)
        items = ""
        for name, duids in (("plx-tv", ["OTHERDEVICE0002"]), ("plx-tv-2", ["OTHERDEVICE0002", DUID])):
            certs = self.root / name
            certs.mkdir(exist_ok=True)
            (certs / "device-profile.xml").write_text(
                "<DeviceProfile>" + "".join(f"<TestDevice>{d}</TestDevice>" for d in duids) + "</DeviceProfile>")
            items += f'<profile name="{name}"><profileitem distributor="1" key="{certs / "distributor.p12"}"/></profile>'
        (folder / "profiles.xml").write_text(f'<profiles active="{active}">{items}</profiles>')

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = tv.main(list(argv), fleet_factory=self.fleet, cdp_factory=FakeCDP, stdout=out, stderr=err,
                       state_dir=self.root / "state")
        return code, out.getvalue(), err.getvalue()

    def alias(self):
        code, out, _ = self.run_cli("scan", "--json")
        self.assertEqual(code, 0)
        return json.loads(out)["tvs"][0]["alias"]

    def assert_cache_private(self, text):
        for secret in (LAN_A, DUID, "uuid:fixture"):
            self.assertNotIn(secret, text)

    def test_scan_prints_status_words_and_full_address(self):
        code, out, err = self.run_cli("scan")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Samsung 7 Series (55) · UE55NU7400", out)
        self.assertIn("Ready to connect", out)
        self.assertIn(LAN_A, out)  # Shown to the developer on this computer.
        self.assertRegex(out, r"tv-[0-9a-f]{6}")
        self.assertEqual(self.sdb.mutations(), [])

    def test_unnamed_tv_is_titled_by_its_address_not_a_number(self):
        state = {"tvs": [{"alias": "tv-abc123", "name": "Samsung TV #1", "unnamed": True, "label": "Not reachable",
                          "headline": "Turn the TV on, then scan again", "details": {"address": LAN_A}}]}
        lines = list(tv.card_lines(state))
        self.assertTrue(lines[0].startswith(f"TV at {LAN_A}"), lines)
        self.assertIn("Not reachable", lines[0])
        self.assertNotIn("#1", "\n".join(lines))
        self.assertIs(tv.find(state, f"TV at {LAN_A}"), state["tvs"][0])

    def test_status_is_scan_and_json_matches_portal_state(self):
        code, out, _ = self.run_cli("status", "--json")
        state = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(set(state), {"scan", "sdk", "tizen", "signing_profiles", "tvs"})
        self.assertEqual(state["tvs"][0]["label"], "Ready to connect")
        self.assertEqual(state["tvs"][0]["details"]["address"], LAN_A)

    def test_connect_by_name_without_yes_shows_everything_then_disconnects(self):
        code, out, err = self.run_cli("connect", "samsung 7 series (55) · ue55nu7400")
        self.assertEqual((code, err), (0, ""))
        self.assertIn(": Connected", out)
        for value in (LAN_A, DUID, "UE55NU7400", APP, STORE, "Apps on this TV (2)"):
            self.assertIn(value, out)
        self.assertIn("No Samsung certificate yet. This TV is " + DUID, out)
        self.assertEqual([c[0] for c in self.sdb.mutations()], ["connect", "disconnect"])
        self.assertEqual(list(self.root.glob("tizen-tv-*.lock")), [])
        for banned in ("No app open", "Developer Mode ready", "Dev mode enabled", "Preview not connected",
                       "Needs to be configured", "Busy", "--yes"):
            self.assertNotIn(banned, out)

    def test_aliases_share_connect_and_scan(self):
        alias = self.alias()
        for command in ("check", "identify"):
            code, out, _ = self.run_cli(command, alias)
            self.assertEqual(code, 0)
            self.assertIn(DUID, out)
        self.assertEqual(self.run_cli("apps", alias.upper())[0], 0)
        code, out, _ = self.run_cli("duid", alias)
        self.assertEqual((code, out.strip().splitlines()[-1]), (0, DUID))

    def test_connect_failure_prints_guided_fix_and_host_ip(self):
        self.sdb.accept = False
        alias = self.alias()
        code, out, err = self.run_cli("connect", alias)
        self.assertEqual(code, 1)
        self.assertIn("Connection problem", out)
        self.assertIn("Check the TV's Host PC IP", out)
        self.assertIn("This computer's IP to enter on the TV: 10.255.255.10", out)

    def test_info_shows_portal_details_without_contacting_the_tv(self):
        alias = self.alias()
        code, out, err = self.run_cli("info", alias)
        self.assertEqual((code, err), (0, ""))
        for value in (LAN_A, "UE55NU7400", "Developer port: open", "This computer's IP to enter on the TV: 10.255.255.10"):
            self.assertIn(value, out)
        self.assertEqual(self.sdb.mutations(), [])

    def test_info_connect_shows_duid_certificate_and_apps_then_disconnects(self):
        alias = self.alias()
        for flag in ("--connect", "--show-duid"):
            code, out, err = self.run_cli("info", alias, flag)
            self.assertEqual((code, err), (0, ""))
            for value in (DUID, APP, "Certificate:", "This computer's IP to enter on the TV: 10.255.255.10"):
                self.assertIn(value, out)
            self.assertEqual([c[0] for c in self.sdb.mutations()][-2:], ["connect", "disconnect"])
        self.assertEqual(list(self.root.glob("tizen-tv-*.lock")), [])

    def test_unknown_tv_is_refused_with_the_list_of_names(self):
        code, _, err = self.run_cli("connect", "tv-ffffff")
        self.assertEqual(code, 1)
        self.assertIn("No TV called tv-ffffff", err)
        self.assertIn("Samsung 7 Series", err)
        self.assertEqual(self.sdb.mutations(), [])

    def test_open_shows_app_and_store_refusal_is_explained(self):
        alias = self.alias()
        code, out, err = self.run_cli("open", alias, APP)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Showing " + APP, out)
        code, out, _ = self.run_cli("open", alias, STORE)
        self.assertEqual(code, 1)
        self.assertIn(tv_fleet.STORE_APP, out)
        self.assertIn("closed", out)
        self.assertEqual([c[0] for c in self.sdb.mutations()][-2:], ["connect", "disconnect"])

    def test_screenshot_writes_private_png_without_yes(self):
        alias = self.alias()
        self.assertEqual(self.run_cli("screenshot", alias)[0], 2)  # Names the app to capture.
        out_dir = self.root / "shots"
        code, out, err = self.run_cli("screenshot", alias, "--app", APP, "--out", str(out_dir))
        self.assertEqual((code, err), (0, ""))
        written = sorted(out_dir.iterdir())
        self.assertEqual(len(written), 1)
        self.assertTrue(written[0].read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(oct(written[0].stat().st_mode & 0o777), "0o600")
        self.assertIn("Saved " + str(written[0].resolve()), out)
        self.assertEqual(self.run_cli("screenshot", alias, "--app", APP, "--out", str(ROOT / "shots"))[0], 2)
        self.assertFalse((ROOT / "shots").exists())

    def test_key_sends_ok_and_rejects_unknown_keys(self):
        alias = self.alias()
        code, out, err = self.run_cli("key", alias, "down", "ok", "back", "--app", APP)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Sent down, ok, back to " + APP, out)
        code, _, err = self.run_cli("key", alias, "power", "--app", APP)
        self.assertEqual(code, 2)
        self.assertIn("power", err)

    def test_profile_source_keys_and_screenshot_need_no_yes(self):
        code, out, err = self.run_cli("--profile", str(self.profile), "key", "sample", "down", "ok")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Sent down, ok", out)
        self.assertFalse(Path(source_config(self.root)["lease_path"]).exists())
        code, out, err = self.run_cli("--profile", str(self.profile), "screenshot", "sample",
                                      "--out", str(self.root / "shots"))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("video plane", out)

    def test_cert_shows_coverage_and_add_backs_up_and_opens_tizen_cert(self):
        alias = self.alias()
        code, out, _ = self.run_cli("cert", alias)
        self.assertEqual(code, 0)
        self.assertIn("This TV is " + DUID, out)
        self.assertIn("tv cert add " + alias, out)
        code, out, _ = self.run_cli("cert", "add", alias)
        self.assertEqual(code, 1, "tizen-cert ended without adding the TV: not a success")
        self.assertEqual(len(self.launches), 1)
        self.assertIn(str(self.root / "bin" / "tizen-cert"), self.launches[0])
        self.assertIn("[done] Installed tizen-cert at", out)
        self.assertIn("[next] Waiting for Samsung sign-in and for tizen-cert to finish...", out)
        self.assertIn("Certificate unchanged:", out)
        steps = [line for line in out.splitlines() if "[done]" in line or "[next]" in line]
        self.assertTrue(steps and all("→" not in line and "…" not in line for line in steps))
        self.assertEqual(self.run_cli("cert", "add")[0], 2)

    def test_cert_offers_and_switches_to_a_profile_that_allows_the_tv(self):
        self.tizen_path = str(self.root / "tizen")
        self.write_profiles("plx-tv")
        alias = self.alias()
        code, out, _ = self.run_cli("cert", alias)
        self.assertIn("Your profile plx-tv-2 allows", out)
        self.assertIn(f"Run: tv cert use {alias} plx-tv-2", out)
        code, out, _ = self.run_cli("cert", "add", alias)
        self.assertEqual((code, self.launches), (1, []), "No tizen-cert: another profile already allows the TV")
        self.assertIn(f"Run: tv cert use {alias} plx-tv-2", out)
        code, out, _ = self.run_cli("cert", "use", alias, "plx-tv-2")
        self.assertEqual(code, 0)
        self.assertIn("Installs now use plx-tv-2, which allows this TV", out)
        code, out, _ = self.run_cli("cert", alias)
        self.assertIn("plx-tv-2 covers this TV", out)
        self.assertEqual(self.run_cli("cert", "use", alias)[0], 2)

    def test_uninstall_and_remove_alias_need_tizen_cli_and_no_yes(self):
        alias = self.alias()
        for command in ("uninstall", "remove"):
            code, _, err = self.run_cli(command, alias, APP)
            self.assertEqual(code, 1)
            self.assertIn("tizen CLI was not found", err)
        self.assertEqual([c[0] for c in self.sdb.mutations()][-2:], ["connect", "disconnect"])

    def test_rename_persists_in_private_profile_by_alias(self):
        alias = self.alias()
        code, out, _ = self.run_cli("--profile", str(self.profile), "rename", alias, "Den TV")
        self.assertEqual(code, 0)
        self.assertIn("Den TV", out)
        self.assertEqual(json.loads(self.profile.read_text())["tv_names"], {alias: "Den TV"})
        self.assertEqual(oct(self.profile.stat().st_mode & 0o777), "0o600")
        code, out, _ = self.run_cli("--profile", str(self.profile), "scan")
        self.assertIn("Den TV", out)
        self.assertEqual(self.run_cli("--profile", str(self.profile), "connect", "den tv")[0], 0)
        self.assertEqual(self.run_cli("rename", alias, "Den")[0], 0)
        default = self.root / "state" / "profile.json"
        self.assertEqual(json.loads(default.read_text())["tv_names"], {alias: "Den"})
        self.assertIn("Den", self.run_cli("list")[1])

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
        self.assert_cache_private(cache.read_text())
        calls = len(self.sdb.calls)
        out, err = io.StringIO(), io.StringIO()
        def no_scan(**_):
            raise AssertionError("list must not scan")
        code = tv.main(["list"], fleet_factory=no_scan, stdout=out, stderr=err, state_dir=self.root / "state")
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertIn(alias, out.getvalue())
        self.assertIn("Ready to connect", out.getvalue())
        self.assertEqual(len(self.sdb.calls), calls)

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
        for command in ("scan", "list", "connect", "apps", "open", "screenshot", "key", "install",
                        "uninstall", "cert add", "portal"):
            self.assertIn(command, result.stdout)
        result = subprocess.run([sys.executable, str(ROOT / "tools/tizen.py"), "--help"],
                                capture_output=True, text=True, timeout=30)
        self.assertIn("tv", result.stdout)

    def test_help_lists_portal_capabilities(self):
        parser = tv.parser()
        text = parser.format_help()
        for command in ("scan", "list", "connect", "apps", "open", "screenshot", "key", "install", "uninstall",
                        "cert add", "duid", "rename", "setup", "portal", "check", "status", "remove"):
            self.assertIn(command, text)


if __name__ == "__main__":
    unittest.main()
