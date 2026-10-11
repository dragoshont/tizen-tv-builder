"""Guided setup: the shared steps behind the portal wizard and `tv setup`. Fakes only; no SDK, TV or network."""
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import tv_setup  # noqa: E402
from test_tv_first_run import FirstRunBase  # noqa: E402

FOUND = {"checks": [{"id": i, "label": i, "status": "found", "next": ""}
                    for i in ("sdb", "tizen", "profiles", "openssl", "samsung-ca")]}


def report(**missing):
    return {"checks": [dict(c, status="missing", next=f"get {c['id']}") if c["id"] in missing else c
                       for c in FOUND["checks"]]}


def tv(**values):
    base = {"alias": "tv-abc123", "name": "Living room", "unnamed": False, "status": "not-connected",
            "reachable": True, "held": False, "dev_mode": {"state": "reachable"}, "pending": None, "error": None,
            "job": None, "certificate": {"state": "not-covered"}, "apps": {"items": []}, "preview": {},
            "details": {"address": "192.0.2.5", "host_ip": "192.0.2.9"}}
    base.update(values)
    return base


def status(result):
    return {s["id"]: s["status"] for s in result["steps"]}


class EvaluateTests(unittest.TestCase):
    def test_fresh_computer_asks_to_install_tools_and_everything_after_waits(self):
        result = tv_setup.evaluate(report(sdb=1, tizen=1), {"tvs": []}, ip="192.0.2.9", system="Darwin")
        self.assertEqual(result["current"], "computer")
        first = result["steps"][0]
        self.assertEqual((first["status"], first["actions"][0]["action"]), ("active", "install-sdk"))
        self.assertIn("web-cli_Tizen_Studio_6.1_macos-64.bin", first["command"])
        self.assertEqual(set(list(status(result).values())[1:]), {"todo"})

    def test_install_progress_and_real_failure_are_shown(self):
        working = tv_setup.evaluate(report(sdb=1), {"tvs": []}, sdk_job={"code": "working", "message": "Downloading", "percent": 40})
        self.assertEqual((working["steps"][0]["status"], working["steps"][0]["percent"]), ("working", 40))
        failed = tv_setup.evaluate(report(sdb=1), {"tvs": []}, system="Windows",
                                   sdk_job={"code": "failed", "message": "exit 3", "output": "disk full", "command": "X"})
        step = failed["steps"][0]
        self.assertEqual((step["status"], step["error"], step["output"], step["command"]), ("failed", "exit 3", "disk full", "X"))
        self.assertEqual(step["actions"][0]["label"], "Try again")

    def test_no_tv_shows_developer_steps_with_this_computers_ip_and_waits(self):
        result = tv_setup.evaluate(FOUND, {"tvs": [], "scan": {"status": "done"}}, ip="192.0.2.9")
        step = result["steps"][1]
        self.assertEqual((result["current"], step["status"]), ("developer-mode", "active"))
        self.assertIn("Host PC IP: 192.0.2.9", step["items"])
        self.assertEqual(step["status_line"], tv_setup.WAITING)

    def test_refused_tv_reports_failure_with_steps(self):
        refused = tv(reachable=False, dev_mode={"state": "needs-config", "explanation": "Host PC IP is another computer"})
        step = tv_setup.evaluate(FOUND, {"tvs": [refused]})["steps"][1]
        self.assertEqual((step["status"], step["error"]), ("failed", "Host PC IP is another computer"))
        self.assertIn("Host PC IP: 192.0.2.9", step["items"])  # The TV's own network address wins.

    def test_connect_permission_app_and_see_progress_in_order(self):
        result = tv_setup.evaluate(FOUND, {"tvs": [tv()]})
        self.assertEqual(result["current"], "connect")
        connected = tv(status="connected", held=True)
        result = tv_setup.evaluate(FOUND, {"tvs": [connected]})
        self.assertEqual((result["current"], result["steps"][3]["actions"][0]["action"]), ("permission", "cert"))
        waiting = tv(status="connected", held=True, job={"op": "cert", "code": "waiting", "url": "https://example.invalid/",
                                                         "steps": [{"text": "Backed up", "done": True}]})
        step = tv_setup.evaluate(FOUND, {"tvs": [waiting]})["steps"][3]
        self.assertEqual((step["status"], step["text"], step["items"]), ("working", "Waiting for Samsung sign-in\u2026", ["\u2713 Backed up"]))
        covered = tv(status="connected", held=True, certificate={"state": "covered"})
        result = tv_setup.evaluate(FOUND, {"tvs": [covered]})
        self.assertEqual([a["action"] for a in result["steps"][4]["actions"]], ["choose", "sample"])
        listed = dict(covered, apps={"items": ["Ab.App"]})
        result = tv_setup.evaluate(FOUND, {"tvs": [listed]}, setup_apps={"tv-abc123": "Ab.App"})
        self.assertEqual((result["current"], result["steps"][5]["actions"][0]["data"]), ("see", {"app": "Ab.App"}))
        shown = dict(listed, preview={"ready": True, "app": "Ab.App"})
        result = tv_setup.evaluate(FOUND, {"tvs": [shown]}, setup_apps={"tv-abc123": "Ab.App"})
        self.assertTrue(result["done"])

    def test_app_is_not_done_until_the_tv_lists_it(self):
        covered = tv(status="connected", held=True, certificate={"state": "covered"})
        result = tv_setup.evaluate(FOUND, {"tvs": [covered]}, setup_apps={"tv-abc123": "Ab.App"})
        self.assertEqual(result["current"], "app")

    def test_certificate_refusal_on_install_offers_the_certificate(self):
        failed = tv(status="connected", held=True, certificate={"state": "default"},
                    job={"op": "install", "ok": False, "code": "certificate-chain", "message": "TV refused the signature"})
        step = tv_setup.evaluate(FOUND, {"tvs": [failed]})["steps"][4]
        self.assertEqual((step["status"], step["error"], step["actions"][0]["action"]), ("failed", "TV refused the signature", "cert"))

    def test_another_profile_that_covers_the_tv_is_offered(self):
        other = tv(status="connected", held=True, certificate={"state": "not-covered", "others": ["tv-companion"]})
        step = tv_setup.evaluate(FOUND, {"tvs": [other]})["steps"][3]
        self.assertEqual(step["actions"][0], {"label": "Use tv-companion for installs", "action": "profile",
                                              "data": {"profile": "tv-companion"}})

    def test_choose_tv_prefers_named_then_held_then_reachable(self):
        a, b = tv(alias="tv-aaaaaa", reachable=False, dev_mode={"state": "closed"}), tv(alias="tv-bbbbbb")
        self.assertEqual(tv_setup.choose_tv([a, b])["alias"], "tv-bbbbbb")
        self.assertEqual(tv_setup.choose_tv([a, b], "tv-aaaaaa")["alias"], "tv-aaaaaa")
        self.assertIsNone(tv_setup.choose_tv([a]))


class InstallTests(unittest.TestCase):
    EXTRAS = ("Index : 1\nName : Samsung Certificate Extension\nActivate : false\n\n"
              "Index : 2\nName : Samsung Tizen TV SDK\nActivate : true\n\nIndex : 3\nName : Other\nActivate : false\n")

    def test_parse_extras(self):
        self.assertEqual([e["index"] for e in tv_setup.parse_extras(self.EXTRAS)], ["1", "2", "3"])
        self.assertEqual(tv_setup.parse_extras(self.EXTRAS)[0]["activate"], "false")

    def fake(self, home, codes=None):
        calls = []

        def run(command, timeout=1800):
            calls.append(command)
            if command[0].endswith(".bin") and "--accept-license" in command and len(command) == 3:
                tools = Path(command[2]) / "tools"
                tools.mkdir(parents=True)
                (tools / "sdb").write_text("")
                manager = Path(command[2]) / "package-manager"
                manager.mkdir()
                (manager / "package-manager-cli.bin").write_text("")
            if command[1:3] == ["extra", "--list"]:
                return 0, self.EXTRAS
            return (codes or {}).get(command[1], 0), "boom output"
        return calls, run

    def test_downloads_installs_activates_only_inactive_samsung_extras_then_packages(self):
        with tempfile.TemporaryDirectory() as home:
            calls, run = self.fake(home)
            seen = []
            tv_setup.install_sdk(lambda text, pct: seen.append(text), system="Darwin", home=home, run=run,
                                 download=lambda url, target, progress: Path(target).write_text("x"), temp=home)
            self.assertEqual(calls[0][1:], ["--accept-license", str(Path(home) / "tizen-studio")])
            self.assertIn(["extra", "-act", "1"], [c[1:] for c in calls])
            self.assertNotIn(["extra", "-act", "3"], [c[1:] for c in calls])
            self.assertEqual(calls[-1][1:], ["install", "--accept-license", tv_setup.PACKAGES])
            self.assertEqual(seen[-1], "Done")

    def test_existing_sdk_skips_the_download(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home) / "tizen-studio"
            (root / "tools").mkdir(parents=True)
            (root / "tools" / "sdb").write_text("")
            (root / "package-manager").mkdir()
            (root / "package-manager" / "package-manager-cli.bin").write_text("")
            calls, run = self.fake(home)
            tv_setup.install_sdk(lambda *a: None, system="Darwin", home=home, run=run,
                                 download=lambda *a: self.fail("downloaded"), temp=home)
            self.assertFalse(any("--accept-license" in c and len(c) == 3 for c in calls))

    def test_failure_raises_the_real_output_and_the_manual_command(self):
        with tempfile.TemporaryDirectory() as home:
            calls, run = self.fake(home, codes={"install": 2})
            with self.assertRaises(tv_setup.SetupError) as caught:
                tv_setup.install_sdk(lambda *a: None, system="Darwin", home=home, run=run,
                                     download=lambda url, target, progress: Path(target).write_text("x"), temp=home)
            self.assertEqual(caught.exception.output, "boom output")
            self.assertIn("macos-64.bin", caught.exception.command)

    def test_download_refuses_plain_http(self):
        with tempfile.TemporaryDirectory() as home, self.assertRaises(tv_setup.SetupError):
            tv_setup._download("http://example.invalid/x", Path(home) / "x", lambda pct: None)

    def test_sample_app_builds_a_wgt_with_its_app_id(self):
        with tempfile.TemporaryDirectory() as folder:
            package = tv_setup.build_sample(folder)
            with zipfile.ZipFile(package) as archive:
                self.assertIn(tv_setup.SAMPLE_APP, archive.read("config.xml").decode())


class FleetSetupTests(FirstRunBase):
    def test_wizard_follows_the_fake_tv_to_an_installed_sample(self):
        self.profiles()
        fleet = self.fleet()
        try:
            alias = self.detected(fleet)["alias"]
            self.assertEqual(fleet.wizard({})["current"], "connect")
            fleet.connect(alias, {})
            result = fleet.sample(alias, {})
            self.assertEqual((result["ok"], result["app"]), (True, tv_setup.SAMPLE_APP))
            self.assertIn(tv_setup.SAMPLE_APP, self.state()["installed"])
            self.assertEqual(fleet.setup_apps[alias], tv_setup.SAMPLE_APP)
        finally:
            fleet.close()

    def test_sdk_install_job_reports_progress_then_the_real_error(self):
        fleet = self.fleet()

        def broken(progress):
            progress("Downloading", 10)
            raise tv_setup.SetupError("installer exited 3", "disk full", "manual command")
        fleet.sdk_installer = broken
        try:
            self.assertEqual(fleet.install_sdk({})["code"], "working")
            fleet.wait(5)
            self.assertEqual({k: fleet.sdk_job[k] for k in ("code", "message", "output", "command")},
                             {"code": "failed", "message": "installer exited 3", "output": "disk full", "command": "manual command"})
        finally:
            fleet.close()

    def test_wizard_rejects_unknown_fields(self):
        fleet = self.fleet()
        try:
            with self.assertRaises(Exception):
                fleet.wizard({"tv": "../etc"})
            with self.assertRaises(Exception):
                fleet.wizard({"other": 1})
        finally:
            fleet.close()


if __name__ == "__main__":
    unittest.main()
