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
            sweep=lambda address, port, timeout: {LAN_A},
            info=lambda host: {"name": "[TV] Samsung 7 Series (55)", "model": "UE55NU7400", "year": "2018",
                               "developer_mode": "1", "uid": "uuid:fixture"},
            key_path=self.root / "alias.key", lease_root=self.root, canonical=self.root / "canonical.lock",
            certificate_duids=certificate_duids, names=names or {}, save_names=save_names)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = tv.main(list(argv), fleet_factory=self.fleet, cdp_factory=FakeCDP, stdout=out, stderr=err)
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
        self.assertEqual(set(state), {"scan", "sdk", "tvs"})
        self.assertEqual(state["tvs"][0]["label"], "Ready to connect")
        self.assert_private(out)

    def test_connect_requires_yes_then_identifies_and_disconnects_owned(self):
        alias = self.alias()
        code, _, err = self.run_cli("--profile", str(self.profile), "connect", alias)
        self.assertEqual(code, 2)
        self.assertIn("--yes", err)
        self.assertEqual(self.sdb.mutations(), [])
        code, out, err = self.run_cli("--profile", str(self.profile), "connect", alias, "--yes")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Connected · Tizen 4.0 · ready to install", out)
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
        self.assertEqual(self.run_cli("rename", alias, "Den")[0], 2)  # Rename needs a profile.

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

    def test_help_lists_portal_capabilities(self):
        parser = tv.parser()
        text = parser.format_help()
        for command in ("scan", "status", "rename", "connect", "key", "screenshot", "portal"):
            self.assertIn(command, text)


if __name__ == "__main__":
    unittest.main()
