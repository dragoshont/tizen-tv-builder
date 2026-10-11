import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
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

    def wait(self, timeout):
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
        path.write_text('<profiles active="plx-tv" version="3.1"><profile name="plx-tv">'
                        '<profileitem distributor="0" key="/a.p12" password="/a.pwd"/></profile></profiles>')
        sc.register(path, sc.PROFILE, ("/k/author.p12", "/k/author.pwd"), ("/k/distributor.p12", "ENC"))
        sc.register(path, sc.PROFILE, ("/k/author.p12", "/k/author.pwd"), ("/k/distributor.p12", "ENC2"))
        root = ET.parse(path).getroot()
        self.assertEqual(root.get("active"), sc.PROFILE)
        self.assertEqual([p.get("name") for p in root.iter("profile")], ["plx-tv", sc.PROFILE])
        items = [(i.get("distributor"), i.get("key"), i.get("password"))
                 for i in root.find(f"profile[@name='{sc.PROFILE}']").iter("profileitem")]
        self.assertEqual(items, [("0", "/k/author.p12", "/k/author.pwd"), ("1", "/k/distributor.p12", "ENC2"), ("2", "", "")])
        self.assertFalse(path.with_name("profiles.xml.tmp").exists())

    def test_the_active_samsung_author_is_reused_verbatim(self):
        certs = self.root / "certs"
        certs.mkdir()
        (certs / "author.p12").write_bytes(b"key")
        path = self.root / "profiles.xml"
        body = ('<profiles active="plx-tv"><profile name="plx-tv">'
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
