"""The top-level TV command family: `tizen.py scan | install | info | apps | open | screenshot | portal`.

Host-only: fake sdb/tizen tools and a loopback fake TV. Nothing is installed on a real TV.
"""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock
import zipfile

from test_tv_first_run import APP, FAKE_HOST, FirstRunBase, package

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import tizen  # noqa: E402
import tv  # noqa: E402

URL = "https://user:hunter2@downloads.example.invalid/builds/Fixture.wgt?token=s3cret#frag"
SECRETS = ("hunter2", "s3cret", "token=", "user:", "#frag")


class Dispatch(unittest.TestCase):
    def tv_argv(self, *argv):
        return tizen.tv_argv(list(argv))

    def test_tv_commands_go_to_the_tv_cli_with_the_same_arguments(self):
        for argv in (["scan"], ["scan", "--json"], ["apps", "Sebi"], ["info", "Sebi", "--app", APP],
                     ["open", "Sebi", APP], ["screenshot", "Sebi", "--app", APP, "--out", "/tmp/x"],
                     ["portal", "--port", "47900"]):
            self.assertEqual(self.tv_argv(*argv), argv)

    def test_legacy_tv_namespace_still_works(self):
        self.assertEqual(self.tv_argv("tv", "install", "Sebi", "a.wgt"), ["install", "Sebi", "a.wgt"])
        self.assertEqual(self.tv_argv("tv", "scan"), ["scan"])

    def test_install_package_then_tv_is_reordered_for_the_tv_cli(self):
        self.assertEqual(self.tv_argv("install", "app.wgt", "192.168.1.9"), ["install", "192.168.1.9", "app.wgt"])
        self.assertEqual(self.tv_argv("install", URL, "Sebi TV", "--sign", "tv-companion"),
                         ["install", "Sebi TV", URL, "--sign", "tv-companion"])
        self.assertEqual(self.tv_argv("install", "--sign", "p", "app.wgt", "Sebi"),
                         ["install", "Sebi", "app.wgt", "--sign", "p"])
        self.assertEqual(self.tv_argv("install", "app.wgt", "Sebi", "--profile", "/p.json"),
                         ["--profile", "/p.json", "install", "Sebi", "app.wgt"])

    def test_install_help_shows_both_forms_in_this_order(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.assertEqual(tizen.main(["install", "--help"]), 0)
        self.assertIn("install PACKAGE_OR_HTTPS_URL TV", out.getvalue())
        self.assertIn("install --serial SERIAL", out.getvalue())

    def test_legacy_serial_install_and_other_build_commands_are_untouched(self):
        for argv in (["install", "--serial", "S", "a.wgt"], ["install", "--serial=S", "--authorized", "a.wgt"],
                     ["install", "--authorized", "--serial", "S", "a.wgt"], ["doctor"], ["build", "app"],
                     ["validate", "app"], ["identify", "--serial", "S"]):
            self.assertIsNone(self.tv_argv(*argv), argv)

    def test_main_hands_tv_commands_to_tv_main(self):
        with mock.patch.object(tv, "main", return_value=7) as called:
            self.assertEqual(tizen.main(["install", "app.wgt", "Sebi"]), 7)
            called.assert_called_once_with(["install", "Sebi", "app.wgt"])

    def test_wrong_install_shape_explains_both_forms(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertEqual(tizen.main(["install", "app.wgt"]), 2)
        self.assertIn("install PACKAGE_OR_HTTPS_URL TV", err.getvalue())
        self.assertIn("--serial", err.getvalue())

    def test_help_lists_the_tv_commands_with_full_forms(self):
        text = tizen.parser().format_help()
        for words in ("scan", "install PACKAGE_OR_HTTPS_URL TV", "info TV [--app APP]", "apps TV",
                      "open TV APP", "screenshot TV --app APP", "portal", "install --serial SERIAL PACKAGE"):
            self.assertIn(words, text)


class FakeResponse:
    def __init__(self, body, length=True, delay=0.0, url="https://downloads.example.invalid/Fixture.wgt"):
        self.body, self.delay, self.url = body, delay, url
        self.headers = {"Content-Length": str(len(body))} if length else {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def geturl(self):
        return self.url

    def read(self, size):
        time.sleep(self.delay)
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


class Download(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.wgt = package(self.root).read_bytes()

    def fetch(self, body=None, url=URL, **options):
        response = options.pop("response", None) or FakeResponse(self.wgt if body is None else body)
        seen = []

        def opener(request, timeout):
            seen.append((request, timeout))
            return response
        return tv.download_package(url, opener=opener, **options), seen

    def assert_clean(self, text):
        for secret in SECRETS:
            self.assertNotIn(secret, text)

    def test_public_url_drops_credentials_query_and_fragment(self):
        self.assertEqual(tv.public_url(URL), "https://downloads.example.invalid/builds/Fixture.wgt")

    def test_downloads_into_a_private_folder_and_removes_it(self):
        with self.fetch()[0] as path:
            self.assertTrue(path.is_file())
            self.assertEqual(path.suffix, ".wgt")
            folder = path.parent
            if os.name != "nt":
                self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertFalse(folder.exists())

    def test_refuses_plain_http(self):
        with self.assertRaises(tv.Usage) as caught:
            self.fetch(url="http://user:hunter2@downloads.example.invalid/a.wgt?token=s3cret")[0].__enter__()
        self.assertIn("https", str(caught.exception))
        self.assert_clean(str(caught.exception))

    def test_size_limit_from_header_and_from_the_body(self):
        for length in (True, False):
            with self.assertRaises(tv.LiveError) as caught:
                self.fetch(response=FakeResponse(b"x" * 5000, length=length), limit=1000)[0].__enter__()
            self.assertIn("larger than", str(caught.exception))
            self.assert_clean(str(caught.exception))

    def test_overall_deadline(self):
        slow = FakeResponse(self.wgt, delay=0.05)
        with self.assertRaises(tv.LiveError) as caught:
            self.fetch(response=slow, deadline=0.01, chunk=64)[0].__enter__()
        self.assertIn("took longer than", str(caught.exception))

    def test_rejects_a_file_that_is_not_a_tizen_package(self):
        broken = self.root / "noconfig.zip"
        with zipfile.ZipFile(broken, "w") as archive:
            archive.writestr("index.html", "x")
        for body in (b"<html>sign in</html>", broken.read_bytes()):
            with self.assertRaises(tv.LiveError) as caught:
                self.fetch(body=body)[0].__enter__()
            self.assertIn("not a Tizen TV app package", str(caught.exception))
            self.assert_clean(str(caught.exception))

    def test_leftover_folder_is_removed_when_validation_fails(self):
        before = set(Path(tempfile.gettempdir()).glob("tv-download-*"))
        with self.assertRaises(tv.LiveError):
            self.fetch(body=b"nope")[0].__enter__()
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("tv-download-*")), before)

    def test_redirect_to_http_is_refused(self):
        handler = tv.HttpsOnlyRedirect()
        with self.assertRaises(tv.LiveError) as caught:
            handler.redirect_request(None, None, 302, "Found", {}, "http://evil.invalid/a.wgt?token=s3cret")
        self.assert_clean(str(caught.exception))


class FleetAppInfo(FirstRunBase):
    """The shared, platform-independent answer the CLI prints."""

    def setUp(self):
        super().setUp()
        Path(str(self.scenario_path) + ".state").write_text(
            json.dumps({"connected": [], "installed": [APP], "calls": []}))

    def ask(self, query):
        fleet = self.fleet()
        try:
            alias = self.detected(fleet)["alias"]
            with self.assertRaises(tv.LiveError):
                fleet.app_info(alias, {"app": APP})  # Needs a connection first.
            fleet.connect(alias, {"confirm": alias})
            return fleet.app_info(alias, {"app": query})
        finally:
            fleet.close()

    def test_id_name_missing_and_unreadable(self):
        self.assertEqual(self.ask(APP)["installed"], "yes")
        self.assertIsNone(self.ask(APP)["version"])
        self.assertEqual(self.ask("Nope000000.nope")["installed"], "no")
        self.assertEqual(self.ask("Fixture App")["reason"], "names-not-listed")
        self.set_scenario(applist_versions="1.2.3")
        found = self.ask("title " + APP)
        self.assertEqual((found["installed"], found["app"], found["version"]), ("yes", APP, "1.2.3"))
        self.set_scenario(applist="closed")
        self.assertEqual((self.ask(APP)["installed"], self.ask(APP)["reason"]), ("unknown", "unreadable"))


@unittest.skipUnless(os.name == "posix", "Private CLI state folders require POSIX ownership")
class AppInfo(FirstRunBase):
    def setUp(self):
        super().setUp()
        Path(str(self.scenario_path) + ".state").write_text(
            json.dumps({"connected": [], "installed": [APP], "calls": []}))

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = tv.main(list(argv), fleet_factory=self.fleet, stdout=out, stderr=err, state_dir=self.state_dir)
        self.assertEqual(self.state()["connected"], [], "every command disconnects what it connected")
        return code, out.getvalue(), err.getvalue()

    def test_tv_is_found_by_its_ip(self):
        code, out, err = self.run_cli("info", FAKE_HOST)
        self.assertEqual(code, 0, err)
        self.assertIn("Network address: " + FAKE_HOST, out)

    def test_installed_app_without_a_listed_version_says_unknown(self):
        code, out, err = self.run_cli("info", FAKE_HOST, "--app", APP)
        self.assertEqual(code, 0, err)
        self.assertIn(f"{APP}: installed", out)
        self.assertIn("Version: unknown (this TV's app list does not show versions)", out)

    def test_version_is_shown_only_when_the_tv_lists_it(self):
        self.set_scenario(applist_versions="1.2.3")
        code, out, _ = self.run_cli("info", FAKE_HOST, "--app", APP)
        self.assertEqual(code, 0)
        self.assertIn("Version: 1.2.3 (as the TV lists it)", out)
        code, out, _ = self.run_cli("info", FAKE_HOST, "--app", "Title " + APP)
        self.assertEqual(code, 0)
        self.assertIn(f"{APP}: installed", out)

    def test_missing_app_is_not_installed(self):
        code, out, _ = self.run_cli("info", FAKE_HOST, "--app", "ZzZzZzZzZz.Other")
        self.assertEqual(code, 1)
        self.assertIn("ZzZzZzZzZz.Other: not installed", out)

    def test_a_name_cannot_be_checked_when_the_tv_lists_ids_only(self):
        code, out, _ = self.run_cli("info", FAKE_HOST, "--app", "Fixture App")
        self.assertEqual(code, 1)
        self.assertIn("Fixture App: unknown", out)
        self.assertIn("use the app ID", out)

    def test_unreadable_app_list_is_unknown(self):
        self.set_scenario(applist="closed")
        code, out, _ = self.run_cli("info", FAKE_HOST, "--app", APP)
        self.assertEqual(code, 1)
        self.assertIn(f"{APP}: unknown", out)

    def test_install_from_https_url_uses_the_normal_install_and_cleans_up(self):
        self.profiles()
        body = package(self.root).read_bytes()
        folders = []

        def opener(request, timeout):
            return FakeResponse(body)
        real = tv.download_package

        def tracked(url, **options):
            context = real(url, opener=opener, **options)
            folders.append(context)
            return context
        with mock.patch.object(tv, "download_package", tracked):
            code, out, err = self.run_cli("install", FAKE_HOST, URL)
        self.assertEqual(code, 0, out + err)
        self.assertIn("Downloaded https://downloads.example.invalid/builds/Fixture.wgt", out)
        self.assertIn("confirmed by the TV's app list", out)
        for secret in SECRETS:
            self.assertNotIn(secret, out + err)
        self.assertFalse(folders[0].folder.exists())


if __name__ == "__main__":
    unittest.main()


def report(sdb=True, tizen=True, profiles=()):
    checks = [{"id": "sdb", "label": "sdb (connects to the TV)", "status": "found" if sdb else "missing",
               "next": None if sdb else "Install Tizen Studio"},
              {"id": "tizen", "label": "tizen CLI (signs and installs)", "status": "found" if tizen else "missing",
               "next": None if tizen else "Install Tizen Studio"},
              {"id": "profiles", "label": "Signing profiles", "status": "found" if profiles else "missing",
               "next": None if profiles else "Make one"}]
    return {"ready": sdb and tizen and bool(profiles), "checks": checks,
            "profiles": {"names": list(profiles), "active": profiles[0] if profiles else None}, "note": "n"}


class ComputerSetup(unittest.TestCase):
    """`tizen.py setup`: this computer only. It never waits for a TV, a certificate or an app."""

    def run_setup(self, *argv, reports=(), install=None):
        reports = list(reports)
        out, err = io.StringIO(), io.StringIO()
        calls = []

        def installer(progress):
            calls.append("install")
            progress("Downloading Tizen Studio's command-line tools", 50)
            progress("Done", 100)
            if install:
                raise install

        code = tv.computer_setup(list(argv), stdout=out, stderr=err, report=lambda: reports.pop(0),
                                 installer=installer, ip=lambda: "192.168.1.142",
                                 fleet_factory=lambda: self.fail("setup must not touch a TV"))
        return code, out.getvalue(), err.getvalue(), calls

    def test_top_level_setup_is_computer_only_and_tv_setup_stays_the_wizard(self):
        with mock.patch.object(tv, "computer_setup", return_value=0) as computer, \
                mock.patch.object(tv, "main", return_value=5) as wizard:
            self.assertEqual(tizen.main(["setup"]), 0)
            computer.assert_called_once_with([])
            self.assertEqual(tizen.main(["setup", "--check"]), 0)
            self.assertEqual(tizen.main(["tv", "setup"]), 5)
            wizard.assert_called_once_with(["setup"])
        self.assertIn("setup [--check]", tizen.parser().format_help())

    def test_ready_computer_installs_nothing_and_points_to_the_tv_steps(self):
        code, out, _, calls = self.run_setup(reports=[report(profiles=("tv-companion",))])
        self.assertEqual((code, calls), (0, []))
        self.assertIn("Python: found", out)
        self.assertIn("This computer is ready.", out)
        for words in ("Apps", "1 2 3 4 5", "Host PC IP: 192.168.1.142",
                      "python3 tools/tizen.py scan", "python3 tools/tizen.py install APP.wgt TV"):
            self.assertIn(words, out)

    def test_missing_tools_are_installed_with_progress_then_checked_again(self):
        code, out, _, calls = self.run_setup(reports=[report(sdb=False, tizen=False), report()])
        self.assertEqual((code, calls), (0, ["install"]))
        self.assertIn("Downloading Tizen Studio's command-line tools (50%)", out)
        self.assertIn("sdb: found", out.split("Done")[-1])
        self.assertIn("This computer is ready.", out)
        # No certificate yet is not a computer problem: it is made later, for a named TV.
        self.assertIn("python3 tools/tizen.py tv cert add TV", out)

    def test_check_only_reports_and_never_installs(self):
        code, out, _, calls = self.run_setup("--check", reports=[report(sdb=False)])
        self.assertEqual((code, calls), (1, []))
        self.assertIn("sdb: missing", out)
        self.assertIn("python3 tools/tizen.py setup", out)

    def test_install_failure_shows_the_real_error_and_the_command_by_hand(self):
        import tv_setup
        failure = tv_setup.SetupError("The Tizen Studio installer did not finish", "disk full", "curl -fLO x && ./x")
        code, out, err, _ = self.run_setup(reports=[report(sdb=False)], install=failure)
        self.assertEqual(code, 1)
        self.assertIn("The Tizen Studio installer did not finish", err)
        self.assertIn("disk full", err)
        self.assertIn("By hand: curl -fLO x && ./x", err)
        self.assertNotIn("This computer is ready.", out)

    def test_unknown_options_are_usage_errors(self):
        code, _, err, calls = self.run_setup("Sebi", reports=[])
        self.assertEqual((code, calls), (2, []))
        self.assertIn("tv setup", err)
