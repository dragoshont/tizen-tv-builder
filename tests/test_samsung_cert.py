import json
import os
import re
from pathlib import Path
import subprocess
import sys
import socket
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import samsung_cert as sc  # noqa: E402

# Synthetic placeholders only.
DUID, OTHER = "PLACEHOLDERDUID0001", "OTHERDEVICE0002"
CODE = json.dumps({"access_token": "token-1.ab", "userId": "user1", "inputEmailID": "dev@example.invalid"})
OPENSSL = sc.find_openssl()


def profile_xml(*duids):
    return "<DeviceProfile>" + "".join(f"<TestDevice>{d}</TestDevice>" for d in duids) + "</DeviceProfile>"


class SignInFake:
    """The browser sign-in, already finished."""
    def __init__(self):
        self.closed = False

    def wait(self, timeout, cancel=None):
        return sc.parse_code(CODE)

    def close(self):
        self.closed = True


class PartsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_sign_in_answer_needs_a_token_and_user_and_keeps_only_a_valid_email(self):
        self.assertEqual(sc.parse_code(CODE), {"access_token": "token-1.ab", "user_id": "user1",
                                               "email": "dev@example.invalid"})
        self.assertIsNone(sc.parse_code(json.dumps({"access_token": "t", "userId": "u", "inputEmailID": "x\ny"}))["email"])
        for bad in ("", "[]", "{}", json.dumps({"access_token": "a b", "userId": "u"}),
                    json.dumps({"access_token": "t\r\nX: y", "userId": "u"})):
            with self.assertRaises(sc.CertError):
                sc.parse_code(bad)

    def test_sign_in_listener_takes_the_callback_by_post_or_get_on_loopback_only(self):
        listener = sc.SignIn(port=0)
        try:
            base = f"http://127.0.0.1:{listener.port}"
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(base + "/other")
            self.assertEqual(caught.exception.code, 404)
            caught.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(base + sc.CALLBACK + "?code=nonsense")
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
            self.assertFalse(listener.done.is_set())
            body = urllib.parse.urlencode({"code": CODE}).encode()
            with urllib.request.urlopen(urllib.request.Request(base + sc.CALLBACK, data=body)) as answer:
                self.assertIn(b"You can close this tab", answer.read())
            self.assertEqual(listener.wait(1)["user_id"], "user1")
            self.assertEqual(listener.server.server_address[0], "127.0.0.1")
        finally:
            listener.close()
        listener = sc.SignIn(port=0)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{listener.port}{sc.CALLBACK}?" + urllib.parse.urlencode({"code": CODE}))
            self.assertEqual(listener.wait(1)["access_token"], "token-1.ab")
        finally:
            listener.close()
        listener = sc.SignIn(port=0)
        try:
            with self.assertRaises(sc.CertError) as caught:
                listener.wait(0)
            self.assertEqual(caught.exception.code, "signin-timeout")
        finally:
            listener.close()

    def test_cancel_stops_waiting_and_a_closed_listener_frees_its_port(self):
        listener = sc.SignIn(port=0)
        port = listener.port
        cancel = threading.Event()
        threading.Timer(0.1, cancel.set).start()
        try:
            with self.assertRaises(sc.CertError) as caught:
                listener.wait(30, cancel=cancel)
            self.assertEqual((caught.exception.code, caught.exception.next), ("cancelled", "Start a new sign-in"))
        finally:
            listener.close()
        # A retry can listen on the same port again: nothing leaked.
        again = sc.SignIn(port=port)
        again.close()

    def test_samsung_errors_are_shown_as_they_are_with_one_next_step(self):
        self.assertEqual(sc.samsung_error(403, b"").code, "rate-limited")
        error = sc.samsung_error(401, json.dumps({"error": {"code": 303, "description": "x"}}).encode())
        self.assertEqual(error.code, "no-email")
        self.assertIn(sc.EMAIL_HELP, error.next)
        error = sc.samsung_error(500, json.dumps({"error": {"code": 12, "description": "Bad CSR"}}).encode())
        self.assertEqual(error.message, "Samsung's certificate service answered HTTP 500 (code 12): Bad CSR")
        self.assertIn("gateway down", sc.samsung_error(502, b"gateway down").message)

    def test_post_sends_multipart_and_turns_network_failures_into_real_errors(self):
        sent = []

        def opener(url, body, headers, timeout):
            sent.append((url, body, headers, timeout))
            return 200, b"ok"
        self.assertEqual(sc.post("/v3/authors", [("platform", "VD")], "author.csr", "CSR", opener, "https://x.invalid"), "ok")
        url, body, headers, timeout = sent[0]
        self.assertEqual((url, timeout), ("https://x.invalid/v3/authors", sc.HTTP_TIMEOUT))
        self.assertIn(b"name=platform\r\n\r\nVD\r\n", body)
        self.assertIn(b"filename=author.csr", body)
        self.assertTrue(body.endswith(b"--*****--\r\n"))

        def down(*args):
            raise OSError("Network is unreachable")
        with self.assertRaises(sc.CertError) as caught:
            sc.post("/v3/authors", [], "a.csr", "", down)
        self.assertEqual(caught.exception.code, "network")
        self.assertIn("OSError: Network is unreachable", caught.exception.message)

    def test_device_list_must_name_every_requested_tv_and_has_no_dtd(self):
        self.assertTrue(sc.device_profile(profile_xml(DUID, OTHER), [DUID]))
        with self.assertRaises(sc.CertError) as caught:
            sc.device_profile(profile_xml(OTHER), [DUID])
        self.assertIn(DUID, caught.exception.message)
        with self.assertRaises(sc.CertError):
            sc.device_profile('<!DOCTYPE x [<!ENTITY a "b">]><DeviceProfile/>', [DUID])

    def test_register_adds_an_active_profile_and_keeps_the_others(self):
        path = self.root / "profile" / "profiles.xml"
        path.parent.mkdir()
        path.write_text('<profiles active="demo-tv" version="3.1"><profile name="demo-tv">'
                        '<profileitem distributor="0" key="/a.p12" password="/a.pwd"/></profile></profiles>')
        sc.register(path, sc.PROFILE, ("/k/author.p12", "/k/author.pwd"), ("/k/distributor.p12", "ENC"))
        sc.register(path, sc.PROFILE, ("/k/author.p12", "/k/author.pwd"), ("/k/distributor.p12", "ENC2"))
        root = ET.parse(path).getroot()
        self.assertEqual(root.get("active"), sc.PROFILE)
        self.assertEqual([p.get("name") for p in root.iter("profile")], ["demo-tv", sc.PROFILE])
        items = [(i.get("distributor"), i.get("key"), i.get("password"))
                 for i in root.find(f"profile[@name='{sc.PROFILE}']").iter("profileitem")]
        self.assertEqual(items, [("0", "/k/author.p12", "/k/author.pwd"), ("1", "/k/distributor.p12", "ENC2"), ("2", "", "")])
        self.assertFalse(path.with_name("profiles.xml.tmp").exists())

    def test_the_active_samsung_author_is_reused_verbatim(self):
        certs = self.root / "certs"
        certs.mkdir()
        (certs / "author.p12").write_bytes(b"key")
        path = self.root / "profiles.xml"
        body = ('<profiles active="demo-tv"><profile name="demo-tv">'
                f'<profileitem distributor="0" key="{certs / "author.p12"}" password="{certs / "author.pwd"}"/>'
                f'<profileitem distributor="1" key="{certs / "distributor.p12"}" password="x"/></profile></profiles>')
        path.write_text(body)
        self.assertIsNone(sc.reusable_author(path), "Without a device list it isn't a Samsung profile")
        (certs / "device-profile.xml").write_text(profile_xml(OTHER))
        self.assertEqual(sc.reusable_author(path), (str(certs / "author.p12"), str(certs / "author.pwd")))
        self.assertTrue(sc.has_profile(path))
        self.assertIsNone(sc.reusable_author(self.root / "missing.xml"))
        self.assertFalse(sc.has_profile(self.root / "missing.xml"))

    def test_backup_copies_files_and_profiles_privately(self):
        folder = self.root / "tv-companion"
        self.assertIsNone(sc.backup(folder, self.root / "none.xml", "s1"))
        folder.mkdir()
        (folder / "distributor.p12").write_bytes(b"k")
        (self.root / "profiles.xml").write_text("<profiles/>")
        saved = Path(sc.backup(folder, self.root / "profiles.xml", "s2"))
        self.assertEqual(sorted(p.name for p in saved.iterdir()), ["distributor.p12", "profiles.xml"])
        if os.name == "posix":
            self.assertEqual(saved.stat().st_mode & 0o777, 0o700)

    def test_keychain_password_goes_through_stdin_never_argv(self):
        calls = []

        def run(argv, input=None, **kwargs):
            calls.append((argv, input))
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        self.assertTrue(sc.keychain_store("/home/x/author.pwd", "Secret123", system="Darwin", run=run))
        (first, stdin), (second, _) = calls
        self.assertEqual(first, ["security", "-i"])
        self.assertIn(b"-s certificate-manager", stdin)
        self.assertIn(b"-w Secret123", stdin)
        self.assertNotIn("Secret123", " ".join(second))
        self.assertFalse(sc.keychain_store("/x.pwd", "Secret123", system="Linux", run=run))
        self.assertFalse(sc.keychain_store('/x".pwd', "Secret123", system="Darwin", run=run))
        self.assertFalse(sc.keychain_store("/x.pwd", "bad pass", system="Darwin", run=run))
        self.assertEqual(len(calls), 2)

    def test_passwords_are_long_and_random(self):
        first, second = sc.new_password(), sc.new_password()
        self.assertTrue(len(first) == 24 and first.isalnum() and first != second)

    def test_sdk_roots_look_next_to_the_tools_found(self):
        roots = sc.sdk_roots("/opt/tizen-studio/tools/sdb", "/opt/tizen-studio/tools/ide/bin/tizen")
        self.assertEqual(roots[0], Path("/opt/tizen-studio"))
        self.assertIn(Path("/opt/tizen-studio"), roots)
        self.assertIsNone(sc.find_ca([self.root]))


@unittest.skipUnless(OPENSSL, "openssl is not installed")
class OpensslTests(unittest.TestCase):
    """Real openssl, a fake Samsung service that signs with a throwaway test CA."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.ca_dir = self.root / "ca"
        self.ca_dir.mkdir()
        subprocess.run([OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.crt",
                        "-days", "2", "-subj", "/CN=Test CA"], cwd=self.ca_dir, check=True, capture_output=True)
        self.ca_pem = (self.ca_dir / "ca.crt").read_text()
        self.requests = []
        self.answers = {}

    def tearDown(self):
        self.temporary.cleanup()

    def sign(self, csr):
        (self.ca_dir / "in.csr").write_text(csr)
        subprocess.run([OPENSSL, "x509", "-req", "-in", "in.csr", "-CA", "ca.crt", "-CAkey", "ca.key",
                        "-CAcreateserial", "-out", "out.crt", "-days", "1"], cwd=self.ca_dir, check=True, capture_output=True)
        return (self.ca_dir / "out.crt").read_text()

    def samsung(self, url, body, headers, timeout):
        text = body.decode()
        fields = dict(re_fields(text))
        csr = text.split("\r\n\r\n")[-1].split("\r\n--*****--")[0]
        path = url[len(sc.API):]
        self.requests.append((path, fields, csr))
        if path in self.answers:
            return self.answers[path]
        if path == "/v1/distributors":
            return 200, profile_xml(*re.findall(r"deviceid=([A-Za-z0-9]+)", self.csr_text(csr))).encode()
        return 200, ("Certificate:\n" + self.sign(csr)).encode()

    def csr_text(self, csr):
        (self.ca_dir / "look.csr").write_text(csr)
        return subprocess.run([OPENSSL, "req", "-in", "look.csr", "-noout", "-text"], cwd=self.ca_dir,
                              capture_output=True, text=True, check=True).stdout

    def add(self, duids, profiles, **kwargs):
        steps = []
        options = dict(profiles_path=profiles, cert_home=self.root / "SamsungCertificate", openssl=OPENSSL,
                       ca={"author": self.ca_pem, "distributor": self.ca_pem},
                       progress=lambda step, text, done: steps.append((step, done)), browser=self.opened.append,
                       signin=SignInFake, opener=self.samsung, clock=lambda: 0, system="Linux")
        options.update(kwargs)
        return sc.add_tvs(duids, **options), steps

    def test_tizen_inline_password_format_is_3des_with_the_fixed_key(self):
        text = sc.encrypt_password(OPENSSL, "Abc123")
        self.assertEqual(self.decrypt(text), b"Abc123")
        self.assertEqual(text, sc.encrypt_password(OPENSSL, "Abc123"), "No salt: Tizen decrypts it with its fixed key")

    def decrypt(self, text):
        return subprocess.run([OPENSSL, "enc", "-d", "-des-ede3", "-K", sc.PASSWORD_KEY.hex(), "-nosalt", "-a", "-A"],
                              input=text.encode(), capture_output=True, check=True).stdout

    def test_from_scratch_one_sign_in_makes_an_author_and_a_distributor_for_the_tvs(self):
        self.opened = []
        profiles = self.root / "profile" / "profiles.xml"
        done, steps = self.add([OTHER, DUID, DUID], profiles)
        self.assertEqual(self.opened, [sc.SIGNIN_URL])
        self.assertEqual([path for path, _, _ in self.requests], ["/v3/authors", "/v1/distributors", "/v3/distributors"])
        self.assertEqual(self.requests[0][1], {"access_token": "token-1.ab", "user_id": "user1", "platform": "VD"})
        self.assertEqual(self.requests[1][1]["privilege_level"], "Public")
        distributor = self.csr_text(self.requests[1][2])
        self.assertIn(f"URI:URN:tizen:deviceid={OTHER}, URI:URN:tizen:deviceid={DUID}", distributor)
        self.assertIn("dev@example.invalid", distributor)
        self.assertEqual((done["profile"], done["new_author"], done["reinstall"], done["backup"], done["password_storage"]),
                         (sc.PROFILE, True, False, None, "profiles.xml"))
        self.assertEqual(steps, [("signin", False), ("signin", True), ("request", False), ("request", True),
                                 ("register", True)])
        folder = Path(done["folder"])
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ["author.crt", "author.p12", "device-profile.xml", "distributor.crt", "distributor.p12"])
        root = ET.parse(profiles).getroot()
        self.assertEqual(root.get("active"), sc.PROFILE)
        author, distributor_item, _ = root.find("profile").iter("profileitem")
        self.assertEqual(author.get("key"), str(folder / "author.p12"))
        # The inline password opens the p12: decrypt it as Tizen's CLI would, then read the p12.
        password = self.decrypt(distributor_item.get("password"))
        self.assertEqual(len(password), 24)
        for name in ("author.p12", "distributor.p12"):
            subprocess.run([OPENSSL, "pkcs12", "-in", str(folder / name), "-noout", "-passin", "stdin"],
                           input=password + b"\n", check=True, capture_output=True)
            if os.name == "posix":
                self.assertEqual((folder / name).stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(folder.glob("*.pri")), "Private keys live only inside the password-protected p12")

    def test_renewal_reuses_the_samsung_author_and_backs_up_first(self):
        self.opened = []
        profiles = self.root / "profile" / "profiles.xml"
        first, _ = self.add([OTHER], profiles)
        author_before = (Path(first["folder"]) / "author.p12").read_bytes()
        self.requests.clear()
        done, steps = self.add([OTHER, DUID], profiles)
        self.assertEqual([path for path, _, _ in self.requests], ["/v1/distributors", "/v3/distributors"])
        self.assertEqual((done["new_author"], done["reinstall"]), (False, False))
        self.assertEqual((Path(done["folder"]) / "author.p12").read_bytes(), author_before)
        self.assertIn(("backup", True), steps)
        self.assertEqual(sorted(p.name for p in Path(done["backup"]).iterdir())[-1], "profiles.xml")
        self.assertIn(DUID, (Path(done["folder"]) / "device-profile.xml").read_text())

    def snapshot(self, profiles):
        folder = self.root / "SamsungCertificate" / sc.PROFILE
        files = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} if folder.exists() else None  # A backup is additive.
        return files, profiles.read_bytes() if profiles.exists() else None

    def test_batch_renewal_keeps_the_author_and_every_tv_already_allowed(self):
        self.opened = []
        profiles = self.root / "profile" / "profiles.xml"
        self.add([OTHER], profiles)
        author = [dict(item.attrib) for item in ET.parse(profiles).getroot().iter("profileitem")][0]
        self.requests.clear()
        done, _ = self.add([OTHER, DUID, "PLACEHOLDERDUID0003"], profiles)
        self.assertNotIn("/v3/authors", [path for path, _, _ in self.requests], "No new author")
        self.assertEqual(self.opened.count(sc.SIGNIN_URL), 2, "One sign-in per request, whatever the TV count")
        after = [dict(item.attrib) for item in ET.parse(profiles).getroot().iter("profileitem")][0]
        self.assertEqual(after, author, "The author key and its password are unchanged")
        listed = (Path(done["folder"]) / "device-profile.xml").read_text()
        for duid in (OTHER, DUID, "PLACEHOLDERDUID0003"):
            self.assertIn(duid, listed)

    def test_cancel_after_samsung_issued_the_certificate_changes_nothing(self):
        self.opened = []
        profiles = self.root / "profile" / "profiles.xml"
        self.add([OTHER], profiles)
        before = self.snapshot(profiles)
        cancel = threading.Event()

        def progress(step, text, done):
            if step == "request" and done:
                cancel.set()  # The user cancels while Samsung's answer is being saved.
        with self.assertRaises(sc.CertError) as caught:
            self.add([OTHER, DUID], profiles, progress=progress, cancel=cancel)
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertEqual(self.snapshot(profiles), before)
        self.assertFalse(list((self.root / "SamsungCertificate").glob(".tv-companion-*")))

    def test_a_failure_while_saving_puts_the_old_certificate_and_profiles_back(self):
        self.opened = []
        profiles = self.root / "profile" / "profiles.xml"
        self.add([OTHER], profiles)
        before = self.snapshot(profiles)
        broken = self.root / "never-written.xml"
        for failure in (mock.patch.object(sc, "prepare_register", side_effect=OSError("disk full")),
                        mock.patch.object(sc, "prepare_register", return_value=str(broken))):
            with failure, self.assertRaises(OSError):
                self.add([OTHER, DUID], profiles)
            self.assertEqual(self.snapshot(profiles), before, "Old files and profiles.xml are intact")
            self.assertFalse(list((self.root / "SamsungCertificate").glob(".tv-companion-*")), "No stage left over")

    def test_samsung_refusal_changes_nothing(self):
        self.opened = []
        profiles = self.root / "profile" / "profiles.xml"
        self.answers["/v1/distributors"] = (403, b"")
        with self.assertRaises(sc.CertError) as caught:
            self.add([DUID], profiles)
        self.assertEqual(caught.exception.code, "rate-limited")
        self.assertFalse(profiles.exists())
        self.assertFalse((self.root / "SamsungCertificate").exists())

    def test_missing_tools_are_named_before_any_sign_in(self):
        self.opened = []
        for kwargs, code in (({"openssl": None}, "openssl"), ({"ca": None}, "samsung-ca")):
            with self.assertRaises(sc.CertError) as caught:
                self.add([DUID], self.root / "p.xml", **kwargs)
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(sc.CertError):
            self.add(["bad id!"], self.root / "p.xml")
        self.assertEqual(self.opened, [])


def re_fields(text):
    return re.findall(r"name=(\w+)\r\n\r\n([^\r]*)\r\n", text)


if __name__ == "__main__":
    unittest.main()


class ResetTests(unittest.TestCase):
    """`cert reset`: exactly the named profiles, backed up outside the certificate folders."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "SamsungCertificate"
        self.profiles = self.root / "data" / "profile" / "profiles.xml"
        self.backups = self.root / "state" / "cert-backups"
        self.calls = []
        for name in ("demo-tv", "tv-companion", "keep-me"):
            folder = self.home / name
            folder.mkdir(parents=True)
            for stem in ("author", "distributor"):
                (folder / (stem + ".p12")).write_bytes(b"x" + name.encode())
            (folder / "device-profile.xml").write_text("<p/>")
        self.profiles.parent.mkdir(parents=True)
        items = lambda n: "".join(  # noqa: E731
            f'<profileitem ca="" distributor="{i}" key="{self.home / n / (s + ".p12")}" '
            f'password="{self.home / n / (s + ".pwd")}" rootca=""/>' for i, s in enumerate(("author", "distributor")))
        self.profiles.write_text('<profiles active="tv-companion" version="3.1">' + "".join(
            f'<profile name="{n}">{items(n)}</profile>' for n in ("demo-tv", "tv-companion", "keep-me")) + "</profiles>")

    def tearDown(self):
        self.tmp.cleanup()

    def run_security(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.code, b"", b"SecKeychain failed: denied")

    def reset(self, *names, code=0, system="Darwin"):
        self.code = code
        return sc.reset_profiles(names, profiles_path=self.profiles, cert_home=self.home,
                                           backup_root=self.backups, clock=lambda: 0, system=system,
                                           run=self.run_security)

    def names(self):
        root = sc._load(self.profiles)
        return [p.get("name") for p in root.findall("profile")], root.get("active")

    def test_removes_exactly_the_named_profiles_with_backup_and_keychain(self):
        receipt = self.reset("demo-tv", "tv-companion")
        self.assertEqual(self.names(), (["keep-me"], ""))
        self.assertFalse((self.home / "demo-tv").exists())
        self.assertFalse((self.home / "tv-companion").exists())
        self.assertEqual((self.home / "keep-me" / "author.p12").read_bytes(), b"xkeep-me")
        self.assertEqual([p["state"] for p in receipt["profiles"]], ["removed", "removed"])
        backup = Path(receipt["backup"])
        self.assertFalse(sc._inside(backup, self.home))
        self.assertEqual((backup / "demo-tv" / "author.p12").read_bytes(), b"xdemo-tv")
        self.assertIn("keep-me", (backup / "profiles.xml").read_text())
        self.assertTrue((backup / "receipt.json").is_file())
        accounts = sorted(c[-1] for c in self.calls)
        self.assertEqual(accounts, sorted(str(self.home / n / (s + ".pwd")) for n in ("demo-tv", "tv-companion")
                                          for s in ("author", "distributor")))
        self.assertTrue(all(c[:4] == ["security", "delete-generic-password", "-s", "certificate-manager"]
                            for c in self.calls))
        self.assertFalse(any("keep-me" in c[-1] for c in self.calls))
        self.assertEqual(receipt["keychain_errors"], [])

    def test_missing_profile_is_a_no_op(self):
        before = self.profiles.read_bytes()
        receipt = self.reset("never-made")
        self.assertEqual(receipt["profiles"][0]["state"], "not-present")
        self.assertIsNone(receipt["backup"])
        self.assertEqual(self.profiles.read_bytes(), before)
        self.assertEqual(self.calls, [])
        again = self.reset("demo-tv")
        self.assertEqual(self.reset("demo-tv")["profiles"][0]["state"], "not-present")
        self.assertEqual(again["profiles"][0]["state"], "removed")

    def test_keychain_errors_are_reported_not_hidden(self):
        receipt = self.reset("demo-tv", code=51)
        self.assertEqual(len(receipt["keychain_errors"]), 2)
        self.assertIn("denied", receipt["keychain_errors"][0])
        self.assertEqual(self.reset("tv-companion", code=44)["keychain_errors"], [], "44 = already absent")

    def test_no_keychain_off_macos(self):
        receipt = self.reset("demo-tv", system="Windows")
        self.assertEqual(self.calls, [])
        self.assertEqual(set(receipt["profiles"][0]["keychain_result"].values()), {"not-applicable"})

    def refused(self, *names):
        before = self.profiles.read_bytes()
        with self.assertRaises(sc.CertError) as caught:
            self.reset(*names)
        self.assertEqual(self.profiles.read_bytes(), before)
        self.assertFalse(self.backups.exists())
        self.assertEqual(self.calls, [])
        return caught.exception

    def test_symlinks_are_refused(self):
        (self.home / "demo-tv" / "link").symlink_to(self.root)
        self.assertEqual(self.refused("demo-tv").code, "symlink")
        (self.home / "demo-tv" / "link").unlink()
        (self.home / "evil").symlink_to(self.root / "data", target_is_directory=True)
        self.assertEqual(self.refused("evil").code, "symlink")
        self.assertTrue((self.root / "data" / "profile" / "profiles.xml").is_file())

    def test_unsafe_names_and_shared_folders_are_refused(self):
        for bad in ("..", "../data", "*", "", "a/b"):
            self.assertEqual(self.refused(bad).code, "invalid")
        text = self.profiles.read_text().replace(str(self.home / "keep-me" / "author.p12"),
                                                 str(self.home / "demo-tv" / "author.p12"))
        self.profiles.write_text(text)
        self.assertEqual(self.refused("demo-tv").code, "in-use")
        self.assertTrue((self.home / "demo-tv" / "author.p12").is_file())

    def test_a_reused_author_outside_the_folder_is_kept(self):
        text = self.profiles.read_text().replace(str(self.home / "tv-companion" / "author.p12"),
                                                 str(self.home / "demo-tv" / "author.p12"), 1)
        self.profiles.write_text(text)
        receipt = self.reset("tv-companion")
        self.assertEqual(receipt["profiles"][0]["kept_files"], [str(self.home / "demo-tv" / "author.p12")])
        self.assertTrue((self.home / "demo-tv" / "author.p12").is_file())
