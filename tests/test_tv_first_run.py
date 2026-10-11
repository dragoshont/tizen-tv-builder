"""First-run journey against a fake TV: Developer Mode on, laptop on the same network, nothing else.

The fake TV is real sockets on loopback (Samsung's :8001 REST API and the :26101 Developer Mode
port) plus fake `sdb`/`tizen` executables run through the toolkit's real subprocess runner.
Nothing here contacts a real TV, SDK or network beyond 127.0.0.1.
"""
import http.server
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import textwrap
import threading
import unittest
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import flows  # noqa: E402
import network_tv  # noqa: E402
import tv  # noqa: E402
import tv_fleet  # noqa: E402

FAKE_HOST = "10.255.255.30"  # Placeholder private address; the fakes never route to it.
DUID = "FIXTUREDUID0042"  # What `sdb shell 0 getduid` returns: the real certificate DUID.
SMARTTHINGS = "uuid:SMARTTHINGS-NOT-A-DUID"  # The REST API's `duid`: must never be used.
APP = "AbCdEf1234.Fixture"

FAKE_TOOL = textwrap.dedent('''
    import json, os, re, sys, zipfile
    tool, args = sys.argv[1], sys.argv[2:]
    path = os.environ["FAKE_TV_SCENARIO"]
    scenario = json.load(open(path))
    state_path = path + ".state"
    state = json.load(open(state_path)) if os.path.exists(state_path) else {"connected": [], "installed": [], "calls": []}
    state["calls"].append([tool] + args)

    def done(text, code=0):
        json.dump(state, open(state_path, "w"))
        print(text)
        sys.exit(code)

    def app_of(wgt):
        with zipfile.ZipFile(wgt) as archive:
            return re.search(r'id="([A-Za-z0-9]{10}\\.[A-Za-z0-9]+)"', archive.read("config.xml").decode()).group(1)

    if tool == "sdb":
        if args == ["devices"]:
            done("List of devices attached\\n" + "".join(s + "\\tdevice\\tUE55NU7400\\n" for s in state["connected"]))
        if args[0] == "connect":
            if scenario["connect"] == "accept":
                state["connected"].append(args[1])
                done("connecting to " + args[1] + " ...\\nconnected to " + args[1])
            done("connecting to " + args[1] + " ...\\nfailed to connect to remote target '" + args[1] + "'", 1)
        if args[0] == "disconnect":
            state["connected"] = [s for s in state["connected"] if s != args[1]]
            done("disconnected " + args[1])
        if args[0] == "-s":
            if args[1] not in state["connected"]:
                done("error: device not found", 1)
            rest = args[2:]
            if scenario.get("commands") == "closed":
                done("closed", 1)  # Accepted the link, refuses every command (Host PC IP elsewhere).
            if rest == ["capability"]:
                done("platform_version:4.0\\nprofile_name:tv")
            if rest == ["shell", "0", "getduid"]:
                done(scenario["duid"])
            if rest == ["shell", "0", "vd_applist"]:
                if scenario.get("applist") == "closed":
                    done("closed")
                apps = ["SyStEmApp1.Home"] + state["installed"]
                count = len(apps) + (5 if scenario.get("applist") == "truncated" else 0)
                done("\\n".join(["get app list...", "apps count: %d" % count]
                                + ["app_tizen_id = " + a for a in apps]))
    if tool == "tizen":
        target = args[args.index("-t") + 1] if "-t" in args else None
        if args[0] == "package":
            name, out, wgt = args[args.index("-s") + 1], args[args.index("-o") + 1], args[-1]
            if scenario.get("sign") == "expired":
                done("Error: The author certificate is expired", 1)
            if scenario.get("sign") == "password":
                done("Invaild password. See the log: ~/.tizen-studio-data/cli.log", 1)
            if name not in scenario["profiles"]:
                done("Error: the profile '" + name + "' is not found", 1)
            with zipfile.ZipFile(wgt) as archive:
                title = re.search(r"<name>([^<]+)</name>", archive.read("config.xml").decode()).group(1)
            signed = os.path.join(out, title + ".wgt")  # Named after <name>, spaces included.
            data = open(wgt, "rb").read()
            open(signed, "wb").write(data)
            with zipfile.ZipFile(signed, "a") as archive:
                archive.writestr("author-signature.xml", "<Signature/>")
                archive.writestr("signature1.xml", "<Signature/>")
            done("Package File Location: " + signed)
        if target is None:
            done("", 0)  # Without -t the real CLI silently does nothing.
        if target != "UE55NU7400":
            done("Error: device '" + target + "' is not connected", 1)
        if args[0] == "install":
            folder, name = args[-1], args[args.index("-n") + 1]
            if " " in name:
                done("Error: file not found", 1)
            outcome = scenario["install"]
            if outcome == "ok":
                if app_of(os.path.join(folder, name)) not in state["installed"]:
                    state["installed"].append(app_of(os.path.join(folder, name)))
                done("Installed the package: Id(" + app_of(os.path.join(folder, name)) + ")\\n"
                     "Tizen application is successfully installed.")
            if outcome == "ghost":  # Claims success; the TV's list never shows the app.
                done("Tizen application is successfully installed.")
            if outcome == "author":
                done("install failed[118, -11], reason: Author certificate not match :", 0)  # Zero exit: real CLI habit.
            if outcome == "chain":
                done("install failed[118, -12], reason: Check certificate error : :Invalid certificate chain", 1)
            if outcome == "expired":
                done("install failed[118, -13], reason: signature verification failed, certificate expired", 1)
        if args[0] == "run":
            app = args[args.index("-p") + 1]
            if app in state["installed"]:
                done("Launching the Tizen application...\\nTizen application is successfully launched.")
            done("Launching the Tizen application...\\nlaunch failed: the application is not installed", 0)
        if args[0] == "uninstall":
            app = args[args.index("-p") + 1]
            if app not in state["installed"]:
                done("The package is not exist", 0)
            if scenario.get("uninstall") == "noop":
                done("Platform log view", 0)  # Tizen 5.0 (2019): exits 0, app stays installed.
            state["installed"].remove(app)
            done("Uninstalled the package")
    done("fake: unexpected call " + " ".join([tool] + args), 2)
''')


def write_tool(folder, name):
    script = folder / "fake_tool.py"
    script.write_text(FAKE_TOOL)
    if os.name == "nt":
        path = folder / (name + ".bat")
        path.write_text(f'@"{sys.executable}" "{script}" {name} %*\r\n')
    else:
        path = folder / name
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" {name} "$@"\n')
        path.chmod(0o755)
    return path


def package(folder, app=APP):
    path = folder / "Fixture.wgt"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("config.xml", f'<widget xmlns="http://www.w3.org/ns/widgets" xmlns:tizen="http://tizen.org/ns/widgets">'
                                       f'<tizen:application id="{app}" package="{app.split(".")[0]}" required_version="2.3"/>'
                                       f'<tizen:profile name="tv"/><name>Fixture App</name><content src="index.html"/></widget>')
        archive.writestr("index.html", "<!doctype html><title>Fixture</title>")
    return path


class FakeTv:
    """Loopback sockets that behave like the TV's REST API and Developer Mode port."""

    def __init__(self, developer_mode="1", dev_port=True, model_code="18_KANTM2_UHD", model="UE55NU7400"):
        device = {"duid": SMARTTHINGS, "id": "uuid:fixture-tv", "name": "[TV] Fixture TV", "modelName": model,
                  "model": model_code, "type": "Samsung SmartTV", "OS": "Tizen", "developerMode": developer_mode,
                  "firmwareVersion": "Unknown"}
        body = json.dumps({"device": device, "type": "Samsung SmartTV", "name": "[TV] Fixture TV"}).encode()

        class Api(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                code = 200 if self.path == "/api/v2/" else 404
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body if code == 200 else b"{}")

            def log_message(self, *args):
                pass

        self.api = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
        threading.Thread(target=self.api.serve_forever, daemon=True).start()
        self.dev = None
        if dev_port:
            self.dev = socket.socket()
            self.dev.bind(("127.0.0.1", 0))
            self.dev.listen(8)
            threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                connection, _ = self.dev.accept()
            except OSError:
                return
            connection.close()

    def port(self, port):
        if port == 8001:
            return self.api.server_address[1]
        return self.dev.getsockname()[1] if self.dev else None

    def sweep(self, address, port, timeout):
        """Real TCP probes of the fake TV; answers as the one private host that has the port open."""
        local = self.port(port)
        if local is None:
            return set()
        try:
            with socket.create_connection(("127.0.0.1", local), timeout=timeout):
                return {FAKE_HOST}
        except OSError:
            return set()

    def info(self, host):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{self.port(8001)}/api/v2/", timeout=2) as response:
            data = json.loads(response.read(65537))
        result = network_tv.metadata(data, host)
        result["uid"] = data["device"]["id"]
        return result

    def close(self):
        self.api.shutdown()
        self.api.server_close()
        if self.dev:
            self.dev.close()


class FirstRunBase(unittest.TestCase):
    scenario = {"connect": "accept", "install": "ok", "profiles": ["MyTV"]}
    tv_args = {}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.sdb, self.tizen = write_tool(self.bin, "sdb"), write_tool(self.bin, "tizen")
        self.scenario_path = self.root / "scenario.json"
        self.set_scenario(**self.scenario)
        previous = os.environ.get("FAKE_TV_SCENARIO")
        os.environ["FAKE_TV_SCENARIO"] = str(self.scenario_path)
        self.addCleanup(lambda: os.environ.pop("FAKE_TV_SCENARIO") if previous is None
                        else os.environ.__setitem__("FAKE_TV_SCENARIO", previous))
        self.tv = FakeTv(**self.tv_args)
        self.addCleanup(self.tv.close)
        self.data = self.root / "tizen-studio-data"
        self.state_dir = self.root / "state"

    def tearDown(self):
        self.temporary.cleanup()

    def set_scenario(self, **values):
        data = dict(self.scenario, duid=DUID)
        data.update(values)
        self.scenario_path.write_text(json.dumps(data))

    def state(self):
        path = Path(str(self.scenario_path) + ".state")
        return json.loads(path.read_text()) if path.exists() else {"connected": [], "installed": [], "calls": []}

    def calls(self, tool):
        return [c[1:] for c in self.state()["calls"] if c[0] == tool]

    def fleet(self, certificate_duids=None, names=None, save_names=None, known=None, save_known=None):
        return tv_fleet.Fleet(sdb_path=str(self.sdb), tizen_path=str(self.tizen), runner=flows.run_tool,
                              interfaces=lambda: ["10.255.255.10"], sweep=self.tv.sweep, info=self.tv.info,
                              ssdp=lambda: set(), key_path=self.root / "alias.key", lease_root=self.root,
                              canonical=self.root / "canonical.lock", certificate_duids=certificate_duids,
                              names=names or {}, save_names=save_names, known=known, save_known=save_known,
                              data_root=self.data)

    def profiles(self, names=("MyTV",), active="MyTV"):
        folder = self.data / "profile"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "profiles.xml").write_text(
            f'<?xml version="1.0"?><profiles active="{active}" version="3.1">'
            + "".join(f'<profile name="{n}"><profileitem ca="" distributor="0" key="private.p12"/></profile>'
                      for n in names) + "</profiles>")

    def detected(self, fleet):
        tvs = fleet.detect()["tvs"]
        self.assertEqual(len(tvs), 1)
        return tvs[0]

    def assert_private(self, text):
        for secret in (FAKE_HOST, DUID, SMARTTHINGS, "uuid:fixture-tv"):
            self.assertNotIn(secret, text)


class FleetFirstRunTests(FirstRunBase):
    """The shared core: every result here is what the portal and the CLI both present."""

    def test_scan_sees_the_fake_tv_on_the_developer_port_and_api(self):
        fleet = self.fleet()
        try:
            tv_state = self.detected(fleet)
            self.assertTrue(tv_state["reachable"])
            self.assertEqual(tv_state["dev_mode"]["state"], "reachable")
            # The full address and DUID are shown on the local page (details) only, nowhere else in state.
            shown = fleet.state()
            for tv in shown["tvs"]:
                tv.pop("details", None)
            self.assert_private(json.dumps(shown))
        finally:
            fleet.close()

    def test_setup_detects_tools_and_profiles_without_installing_anything(self):
        self.profiles(("MyTV", "Other"), active="MyTV")
        report = tv_fleet.setup_report(sdb=str(self.sdb), tizen=str(self.tizen), data_root=self.data,
                                       which=lambda name: None, cert_locations=())
        self.assertTrue(report["ready"])
        self.assertEqual(report["profiles"], {"names": ["MyTV", "Other"], "active": "MyTV"})
        self.assertEqual({c["id"]: c["status"] for c in report["checks"]},
                         {"sdb": "found", "tizen": "found", "profiles": "found", "tizen-cert": "missing"})
        self.assertEqual(self.state()["calls"], [])  # Detection never runs an SDK tool.
        missing = tv_fleet.setup_report(sdb=None, tizen=None, data_root=self.root / "none", which=lambda name: None, cert_locations=())
        self.assertFalse(missing["ready"])
        text = json.dumps(missing)
        self.assertIn("Install Tizen Studio", text)
        self.assertIn("Nothing was installed", missing["note"])

    def test_duid_comes_from_getduid_never_from_the_rest_api(self):
        fleet = self.fleet()
        try:
            alias = self.detected(fleet)["alias"]
            fleet.connect(alias, {"confirm": alias})
            self.assertEqual(fleet.read_duid(alias), DUID)
            self.assertNotIn(SMARTTHINGS, json.dumps(fleet.state()))
            self.assertNotIn(DUID, json.dumps(fleet.state()))
        finally:
            fleet.close()
        self.assertEqual(self.state()["connected"], [])

    def test_install_resigns_with_the_named_profile_then_installs_launches_and_removes(self):
        self.profiles()
        fleet = self.fleet()
        wgt = package(self.root)
        try:
            alias = self.detected(fleet)["alias"]
            fleet.connect(alias, {"confirm": alias})
            result = fleet.install(alias, {"confirm": alias, "package": str(wgt), "sign": "MyTV"})
            self.assertEqual((result["ok"], result["code"]), (True, "installed"))
            package_call = next(c for c in self.calls("tizen") if c[0] == "package")
            self.assertEqual(package_call[package_call.index("-s") + 1], "MyTV")
            self.assertNotEqual(Path(package_call[-1]).parent, wgt.parent)  # Re-signs a private copy only.
            self.assertEqual(self.state()["installed"], [APP])
            self.assertTrue(fleet.launch(alias, {"confirm": alias, "app": APP})["ok"])
            removed = fleet.remove(alias, {"confirm": alias, "app": APP})
            self.assertEqual((removed["ok"], self.state()["installed"]), (True, []))
            self.assertEqual(fleet.state()["tvs"][0]["job"]["code"], "removed")
        finally:
            fleet.close()
        self.assertEqual(self.state()["connected"], [])

    def test_apps_lists_installed_apps_read_only_after_connect(self):
        self.profiles()
        fleet = self.fleet()
        try:
            alias = self.detected(fleet)["alias"]
            self.assertEqual(fleet.state()["tvs"][0]["apps"], {"state": "unknown", "items": [], "store": [], "installed_here": []})
            with self.assertRaises(tv_fleet.LiveError):
                fleet.apps(alias, {})  # Not connected: nothing is read.
            fleet.connect(alias, {"confirm": alias})
            before = [c for c in self.calls("tizen")]
            fleet.apps(alias, {})
            self.assertEqual(fleet.state()["tvs"][0]["apps"], {"state": "listed", "items": ["SyStEmApp1.Home"], "store": [], "installed_here": []})
            self.assertEqual(self.calls("tizen"), before)  # Listing never installs, launches or removes.
            self.set_scenario(applist="closed")
            with self.assertRaises(tv_fleet.LiveError):
                fleet.apps(alias, {})
            self.assertEqual(fleet.state()["tvs"][0]["apps"], {"state": "unreadable", "items": [], "store": [], "installed_here": []})
        finally:
            fleet.close()
        self.assertEqual(self.state()["connected"], [])

    def test_connect_then_refused_commands_releases_the_link_with_host_ip_steps(self):
        fleet = self.fleet()
        try:
            alias = self.detected(fleet)["alias"]
            self.set_scenario(commands="closed")
            fleet.submit("connect", alias, {"confirm": alias})
            fleet.wait()
            tv = fleet.state()["tvs"][0]
            self.assertFalse(tv["held"], "A link that refuses every command is not a connection")
            self.assertEqual(tv["status"], "not-connected")
            self.assertEqual(tv["guidance"]["kind"], "host-ip")
            self.assertEqual(tv["guidance"]["host_ip"], "10.255.255.10")
            self.assertIn("accepted the connection but refused commands", tv["error"])
            self.assertIn("Host PC IP", tv["error"])
            self.assertEqual(tv["dev_mode"]["state"], "needs-config")
            self.assertEqual(tv["dev_mode"]["diagnosis"], "host-ip-elsewhere")
            self.assert_private(json.dumps(tv["error"]))
        finally:
            fleet.close()
        self.assertEqual(self.state()["connected"], [], "The refused link is disconnected, not left behind")

    def test_install_is_installed_only_when_the_tv_lists_the_exact_app(self):
        self.profiles()
        fleet = self.fleet()
        try:
            alias = self.detected(fleet)["alias"]
            fleet.connect(alias, {"confirm": alias})
            self.set_scenario(install="ghost")
            result = fleet.install(alias, {"confirm": alias, "package": str(package(self.root)), "sign": "MyTV"})
            self.assertEqual((result["ok"], result["code"]), (False, "not-listed"))
            self.set_scenario(install="ok", applist="closed")
            result = fleet.install(alias, {"confirm": alias, "package": str(package(self.root)), "sign": "MyTV"})
            self.assertEqual((result["ok"], result["code"]), (False, "install-unconfirmed"))
            self.assertEqual(fleet.state()["tvs"][0]["apps"]["state"], "unreadable")
            self.set_scenario(install="ok")
            result = fleet.install(alias, {"confirm": alias, "package": str(package(self.root)), "sign": "MyTV"})
            self.assertEqual((result["ok"], result["code"]), (True, "installed"))
            self.assertIn("confirmed by the TV's app list", result["message"])
            self.assertIn(APP, fleet.state()["tvs"][0]["apps"]["items"])
            fleet.remove(alias, {"confirm": alias, "app": APP})
            self.assertNotIn(APP, fleet.state()["tvs"][0]["apps"]["items"])
        finally:
            fleet.close()

    def install_failure(self, outcome, **scenario):
        self.profiles()
        self.set_scenario(install=outcome, **scenario)
        fleet = self.fleet()
        try:
            alias = self.detected(fleet)["alias"]
            fleet.connect(alias, {"confirm": alias})
            result = fleet.install(alias, {"confirm": alias, "package": str(package(self.root)), "sign": "MyTV"})
            self.assertFalse(result["ok"])
            self.assert_private(json.dumps(result))
            return result
        finally:
            fleet.close()

    def test_author_mismatch_is_caught_even_when_the_cli_exits_zero(self):
        result = self.install_failure("author")
        self.assertEqual(result["code"], "author-mismatch")
        self.assertIn("signed by someone else", result["message"])
        self.assertIn("tv uninstall", result["next"])

    def test_invalid_chain_routes_to_the_certificate_step(self):
        result = self.install_failure("chain")
        self.assertEqual(result["code"], "certificate-chain")
        self.assertIn("does not list this TV", result["message"])
        self.assertIn("tv cert", result["next"])

    def test_expired_certificate_is_named_with_a_renewal_step(self):
        result = self.install_failure("expired")
        self.assertEqual(result["code"], "certificate-expired")
        self.assertIn("Renew", result["next"])
        before = len(self.calls("tizen"))
        result = self.install_failure("ok", sign="expired")
        self.assertEqual(result["code"], "certificate-expired")
        self.assertFalse(any(c[0] == "install" for c in self.calls("tizen")[before:]))  # Stops at signing.

    def test_unknown_signing_profile_stops_before_the_tv_is_touched(self):
        self.profiles(("MyTV",))
        fleet = self.fleet()
        try:
            alias = self.detected(fleet)["alias"]
            fleet.connect(alias, {"confirm": alias})
            result = fleet.install(alias, {"confirm": alias, "package": str(package(self.root)), "sign": "Nope"})
            self.assertEqual(result["code"], "no-profile")
            self.assertIn("MyTV", result["next"])
            self.assertEqual(self.calls("tizen"), [])
        finally:
            fleet.close()

    def test_changes_to_the_tv_need_a_connection_and_no_typed_confirmation(self):
        fleet = self.fleet()
        try:
            alias = self.detected(fleet)["alias"]
            for operation, data in (("install", {"confirm": alias, "package": str(package(self.root)), "sign": "MyTV"}),
                                    ("launch", {"confirm": alias, "app": APP}),
                                    ("remove", {"confirm": alias, "app": APP})):
                with self.assertRaises(tv_fleet.LiveError) as raised:
                    getattr(fleet, operation)(alias, data)
                self.assertEqual(raised.exception.code, "connect")
            fleet.connect(alias, {})
            fleet.launch(alias, {"app": APP})  # A trusted developer: no typed alias prompt.
        finally:
            fleet.close()

    def test_portal_path_records_the_same_result_object(self):
        fleet = self.fleet()
        try:
            alias = self.detected(fleet)["alias"]
            fleet.connect(alias, {"confirm": alias})
            direct = fleet.launch(alias, {"confirm": alias, "app": APP})
            fleet.submit("launch", alias, {"confirm": alias, "app": APP})
            fleet.wait()
            self.assertEqual(fleet.state()["tvs"][0]["job"], direct)
        finally:
            fleet.close()


class RemoveVerificationTests(FirstRunBase):
    """`tizen uninstall` can exit 0 and change nothing (Tizen 5.0); only a re-list proves removal."""

    def installed_fleet(self):
        self.profiles()
        self.set_scenario(install="ok")
        fleet = self.fleet()
        alias = self.detected(fleet)["alias"]
        fleet.connect(alias, {"confirm": alias})
        self.assertTrue(fleet.install(alias, {"confirm": alias, "package": str(package(self.root)), "sign": "MyTV"})["ok"])
        return fleet, alias

    def test_silent_noop_uninstall_is_reported_as_failure_with_tv_steps(self):
        fleet, alias = self.installed_fleet()
        try:
            self.set_scenario(install="ok", uninstall="noop")
            result = fleet.remove(alias, {"confirm": alias, "app": APP})
            self.assertEqual((result["ok"], result["code"]), (False, "still-installed"))
            self.assertIn("Delete", result["next"])
            self.assertEqual(self.state()["installed"], [APP])
        finally:
            fleet.close()

    def test_unlistable_tv_leaves_removal_unconfirmed_not_successful(self):
        fleet, alias = self.installed_fleet()
        try:
            self.set_scenario(install="ok", applist="closed")
            result = fleet.remove(alias, {"confirm": alias, "app": APP})
            self.assertEqual((result["ok"], result["code"]), (False, "remove-unconfirmed"))
            self.assertIn("Delete", result["next"])
        finally:
            fleet.close()

    def test_truncated_list_is_unconfirmed_not_a_removal(self):
        fleet, alias = self.installed_fleet()
        try:
            self.set_scenario(install="ok", applist="truncated")
            result = fleet.remove(alias, {"confirm": alias, "app": APP})
            self.assertEqual((result["ok"], result["code"]), (False, "remove-unconfirmed"))
        finally:
            fleet.close()


class DeveloperModeOffTests(FirstRunBase):
    tv_args = {"developer_mode": "0", "dev_port": False}

    def test_check_says_developer_mode_is_off(self):
        fleet = self.fleet()
        try:
            dev = self.detected(fleet)["dev_mode"]
            self.assertEqual(dev["diagnosis"], "developer-mode-off")
            self.assertIn("Developer Mode is off", dev["explanation"])
        finally:
            fleet.close()


class HostPcIpElsewhereTests(FirstRunBase):
    scenario = dict(FirstRunBase.scenario, connect="refuse")

    def test_check_says_host_pc_ip_points_to_another_computer(self):
        fleet = self.fleet()
        try:
            alias = self.detected(fleet)["alias"]
            with self.assertRaises(tv_fleet.LiveError):
                fleet.connect(alias, {"confirm": alias})
            dev = fleet.state()["tvs"][0]["dev_mode"]
            self.assertEqual(dev["diagnosis"], "host-ip-elsewhere")
            self.assertIn("Developer Mode is on", dev["explanation"])
            self.assertIn("Host PC IP", dev["explanation"])
        finally:
            fleet.close()
        self.assertEqual(self.state()["connected"], [])


@unittest.skipUnless(os.name == "posix", "Private CLI state folders require POSIX ownership")
class CliFirstRunTests(FirstRunBase):
    """The same journey through `tv`: one-shot commands that release everything they open."""

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = tv.main(list(argv), fleet_factory=self.fleet, stdout=out, stderr=err, state_dir=self.state_dir,
                       setup=lambda: tv_fleet.setup_report(sdb=str(self.sdb), tizen=str(self.tizen),
                                                           data_root=self.data, which=lambda name: None, cert_locations=()))
        self.assertEqual(self.state()["connected"], [], "every command disconnects what it connected")
        return code, out.getvalue(), err.getvalue()

    def alias(self):
        code, out, _ = self.run_cli("scan", "--json")
        self.assertEqual(code, 0)
        return json.loads(out)["tvs"][0]["alias"]

    def test_setup_lists_what_is_missing_and_how_to_get_it(self):
        code, out, _ = self.run_cli("setup")
        self.assertEqual(code, 1)  # No signing profile yet.
        self.assertIn("sdb: found", out)
        self.assertIn("Signing profiles: missing", out)
        self.assertIn("Nothing was installed", out)
        self.profiles()
        code, out, _ = self.run_cli("setup")
        self.assertEqual(code, 0)
        self.assertIn("MyTV (active)", out)

    def test_duid_is_shown_and_never_the_rest_value(self):
        alias = self.alias()
        code, out, _ = self.run_cli("duid", alias)
        self.assertEqual(code, 0)
        self.assertIn(DUID, out)
        self.assertNotIn(SMARTTHINGS, out)

    def test_cert_names_this_tv_and_the_one_command_that_adds_it(self):
        alias = self.alias()
        code, out, _ = self.run_cli("cert", alias)
        self.assertEqual(code, 0)
        self.assertIn(f"This TV is {DUID}", out)
        self.assertIn(f"tv cert add {alias}", out)

    def test_install_runs_without_yes_and_maps_errors_in_plain_language(self):
        self.profiles()
        alias = self.alias()
        wgt = str(package(self.root))
        self.set_scenario(install="chain")
        code, out, _ = self.run_cli("install", alias, wgt, "--sign", "MyTV")
        self.assertEqual(code, 1)
        self.assertIn("does not list this TV", out)
        self.assertIn("Next: ", out)
        self.assert_private(out)
        self.set_scenario(install="ok")
        code, out, _ = self.run_cli("install", alias, wgt, "--sign", "MyTV")
        self.assertEqual(code, 0, out)
        code, out, _ = self.run_cli("launch", alias, APP)
        self.assertEqual(code, 0, out)
        code, out, _ = self.run_cli("uninstall", alias, APP)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.state()["installed"], [])

    def test_check_tells_host_pc_ip_elsewhere_apart_from_developer_mode_off(self):
        self.set_scenario(connect="refuse")
        code, out, _ = self.run_cli("check", self.alias())
        self.assertEqual(code, 1)
        self.assertIn("answered but refused this computer", out)
        self.assertIn("Connection problem", out)
        self.assertNotIn("Developer Mode is off", out)


@unittest.skipUnless(os.name == "posix", "Private CLI state folders require POSIX ownership")
class CliDeveloperModeOffTests(CliFirstRunTests.__base__):
    tv_args = {"developer_mode": "0", "dev_port": False}

    def test_check_says_developer_mode_is_off(self):
        out, err = io.StringIO(), io.StringIO()
        code = tv.main(["scan", "--json"], fleet_factory=self.fleet, stdout=out, stderr=err, state_dir=self.state_dir)
        alias = json.loads(out.getvalue())["tvs"][0]["alias"]
        out = io.StringIO()
        code = tv.main(["check", alias], fleet_factory=self.fleet, stdout=out, stderr=err, state_dir=self.state_dir)
        self.assertEqual(code, 1)
        self.assertIn("Developer Mode is off", out.getvalue())
        self.assertFalse([c for c in self.calls("sdb") if c[0] == "connect"])  # Proven off: no connect attempted.


if __name__ == "__main__":
    unittest.main()
