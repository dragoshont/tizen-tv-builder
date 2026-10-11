import base64
import hashlib
import re
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import appkit


def signature(files, wrap=False, encode=True):
    refs = []
    for name, data in files.items():
        digest = base64.b64encode(hashlib.sha512(data).digest()).decode()
        if wrap:
            digest = digest[:76] + "\n" + digest[76:]
        uri = name.replace("/", "%2F") if encode else name
        refs.append('<Reference URI="%s"><DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha512"/>'
                    '<DigestValue>%s</DigestValue></Reference>' % (uri, digest))
    return ('<Signature xmlns="http://www.w3.org/2000/09/xmldsig#"><SignedInfo>%s'
            '<Reference URI="#prop"/></SignedInfo></Signature>' % "".join(refs)).encode()


class AppKitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        appkit.scaffold(self.root / "demo", "Demo App")
        self.source = self.root / "demo" / "app"

    def tearDown(self):
        self.temporary.cleanup()

    def codes(self, report):
        return {finding["code"] for finding in report["findings"]}

    def test_scaffold_passes_audit_with_fresh_package_id(self):
        report = appkit.audit(self.source)
        self.assertTrue(report["passed"], report["findings"])
        package = report["app"].split(".")[0]
        self.assertRegex(package, r"^[A-Za-z0-9]{10}$")
        other = appkit.scaffold(self.root / "second", "Second")
        self.assertNotEqual(other["app"], report["app"])
        with self.assertRaises(ValueError):
            appkit.scaffold(self.root / "demo", "Again")

    def test_api_without_privilege_is_an_error(self):
        script = self.source / "js" / "app.js"
        script.write_text(script.read_text() + "\nwebapis.productinfo.getFirmware();\n")
        report = appkit.audit(self.source)
        self.assertFalse(report["passed"])
        self.assertIn("privilege-missing", self.codes(report))

    def test_feature_detection_is_not_network_use(self):
        script = self.source / "js" / "app.js"
        base = script.read_text()
        script.write_text(base + '\nvar caps = { fetch: typeof window.fetch === "function", ws: "WebSocket" in window };\n')
        self.assertNotIn("privilege-missing", self.codes(appkit.audit(self.source)))
        for use in ("window.fetch('/a');", "fetch('/a');", "new XMLHttpRequest();", "new WebSocket(u);", "navigator.sendBeacon(u, d);"):
            script.write_text(base + "\n" + use + "\n")
            self.assertIn("privilege-missing", self.codes(appkit.audit(self.source)), use)

    def test_indirect_network_reference_counts_as_use(self):
        script = self.source / "js" / "app.js"
        config = self.source / "config.xml"
        base, manifest = script.read_text(), config.read_text()
        script.write_text(base + '\nvar request = options.fetch || (typeof fetch === "function" ? fetch : null);\n')
        codes = self.codes(appkit.audit(self.source))
        self.assertNotIn("privilege-missing", codes)
        self.assertIn("privilege-maybe-missing", codes)
        self.assertTrue(appkit.audit(self.source)["passed"])
        config.write_text(manifest.replace("</widget>", '<tizen:privilege name="http://tizen.org/privilege/internet"/></widget>'))
        codes = self.codes(appkit.audit(self.source))
        self.assertNotIn("privilege-unused", codes)
        self.assertNotIn("privilege-maybe-missing", codes)

    def test_private_address_in_prose_is_not_flagged(self):
        script = self.source / "js" / "app.js"
        base = script.read_text()
        script.write_text(base + '\nthrow new Error("Enter a server origin such as http://192.168.1.10:32400.");\n')
        self.assertNotIn("secret-private-address", self.codes(appkit.audit(self.source)))
        script.write_text(base + '\nvar server = "http://192.168.1.10:32400";\n')
        self.assertIn("secret-private-address", self.codes(appkit.audit(self.source)))

    def test_unset_id_injected_at_staging_warns_or_uses_override(self):
        config = self.source / "config.xml"
        manifest = config.read_text()
        package = appkit.audit(self.source)["app"].split(".")[0]
        config.write_text(re.sub(r'\s(?:id|package)="[^"]*"(?=[^>]*required_version)', "", manifest))
        report = appkit.audit(self.source)
        self.assertTrue(report["passed"], report)
        self.assertIsNone(report["app"])
        self.assertIn("app-id-unset", self.codes(report))
        self.assertEqual(appkit.audit(self.source, app_id="ABCDEFGHIJ.Tv")["app"], "ABCDEFGHIJ.Tv")
        with self.assertRaises(ValueError):
            appkit.audit(self.source, app_id="bad")
        config.write_text(manifest)
        with self.assertRaisesRegex(ValueError, "disagree"):
            appkit.audit(self.source, app_id="ZZZZZZZZZZ.Tv")
        self.assertEqual(appkit.audit(self.source)["app"].split(".")[0], package)

    def test_unused_privilege_and_wildcard_access_warn_only(self):
        config = self.source / "config.xml"
        config.write_text(config.read_text().replace(
            "<tizen:profile", '<tizen:privilege name="http://tizen.org/privilege/application.launch"/>\n'
            '  <access origin="*" subdomains="true"/>\n  <tizen:profile'))
        report = appkit.audit(self.source)
        self.assertTrue(report["passed"])
        self.assertTrue({"privilege-unused", "access-wildcard"} <= self.codes(report))

    def test_credentials_and_es2015_syntax_are_errors(self):
        (self.source / "js" / "token.js").write_text('var X_PLEX_TOKEN = "abcd1234abcd1234abcd";\n')
        (self.source / "js" / "modern.js").write_text("const value = () => 1;\n")
        (self.source / "author.p12").write_bytes(b"x")
        report = appkit.audit(self.source)
        self.assertFalse(report["passed"])
        codes = self.codes(report)
        for prefix in ("secret-assigned", "secret-file", "es5-"):
            self.assertTrue(any(code.startswith(prefix) for code in codes), (prefix, codes))
        self.assertNotIn("abcd1234abcd1234abcd", str(report))

    def test_pack_is_deterministic_and_refuses_failed_audit(self):
        first = appkit.pack(self.source, self.root / "a.wgt")
        second = appkit.pack(self.source, self.root / "b.wgt")
        self.assertEqual(first["sha256"], second["sha256"])
        checked = appkit.check_package(self.root / "a.wgt")
        self.assertTrue(checked["passed"])
        self.assertFalse(checked["signed"])
        self.assertFalse(appkit.check_package(self.root / "a.wgt", require_signed=True)["passed"])
        (self.source / "js" / "bad.js").write_text("let x = 1;\n")
        with self.assertRaises(ValueError):
            appkit.pack(self.source, self.root / "c.wgt")

    def write_package(self, name, entries):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            for entry, data in entries.items():
                archive.writestr(entry, data)
        return path

    def app_files(self):
        return {str(p.relative_to(self.source)).replace("\\", "/"): p.read_bytes()
                for p in sorted(self.source.rglob("*")) if p.is_file()}

    def signed_entries(self, **options):
        files = self.app_files()
        author = signature(files, **options)
        distributor = signature(dict(files, **{"author-signature.xml": author}), **options)
        return dict(files, **{"author-signature.xml": author, "signature1.xml": distributor})

    def test_sdk_style_signatures_with_encoded_uris_and_wrapped_digests_verify(self):
        path = self.write_package("sdk.wgt", self.signed_entries(wrap=True, encode=True))
        result = appkit.check_package(path, require_signed=True)
        self.assertTrue(result["signature_digests_consistent"], result["signatures"])
        self.assertTrue(result["passed"])

    def test_tampered_or_unsigned_file_breaks_signature(self):
        entries = self.signed_entries()
        entries["index.html"] = entries["index.html"] + b"<!-- changed -->"
        result = appkit.check_package(self.write_package("tampered.wgt", entries), require_signed=True)
        self.assertFalse(result["passed"])
        self.assertIn("index.html", result["signatures"]["author-signature.xml"]["digest_mismatches"])
        entries = self.signed_entries()
        entries["js/extra.js"] = b"var extra = 1;\n"
        result = appkit.check_package(self.write_package("extra.wgt", entries), require_signed=True)
        self.assertIn("js/extra.js", result["signatures"]["signature1.xml"]["unreferenced"])
        self.assertFalse(result["passed"])

    def test_unsafe_archives_are_rejected(self):
        for entries in ({"../evil.js": b"x", "config.xml": b"<widget/>"}, {"index.html": b"x"}):
            with self.assertRaises(ValueError):
                appkit.check_package(self.write_package("bad.wgt", entries))

    def test_cli_exit_code_follows_the_verdict(self):
        import subprocess
        cli = [sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "tizen.py")]
        ok = subprocess.run(cli + ["audit", str(self.source)], capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        (self.source / "js" / "bad.js").write_text("let x = 1;\n")
        bad = subprocess.run(cli + ["audit", str(self.source)], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        self.assertIn('"passed": false', bad.stdout)


if __name__ == "__main__":
    unittest.main()
