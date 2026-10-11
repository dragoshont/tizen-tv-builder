"""Production-readiness regressions: archive safety, signature structure, identity, verdicts, budgets."""
import base64
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import appkit
import flows
from live_tv import LiveError
from test_appkit import signature
from test_flows import Clock, FakeCDP, Runner, SERIAL, TOOL, session_args


class Base(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        appkit.scaffold(self.root / "demo", "Demo", "AbCdEfGhIj")
        self.source = self.root / "demo" / "app"
        self.lock = self.root / "lane.lock"

    def tearDown(self):
        self.temporary.cleanup()

    def files(self):
        return {p.relative_to(self.source).as_posix(): p.read_bytes()
                for p in sorted(self.source.rglob("*")) if p.is_file()}

    def package(self, name, entries):
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            for entry, data in entries.items():
                archive.writestr(entry, data)
        return path

    def signed(self, **options):
        files = self.files()
        author = signature(files, role="author", **options)
        distributor = signature(dict(files, **{"author-signature.xml": author}), role="distributor", **options)
        return dict(files, **{"author-signature.xml": author, "signature1.xml": distributor})


class ArchiveSafety(Base):
    def test_drive_rooted_and_noncanonical_names_are_rejected_before_extraction(self):
        for bad in ("C:/evil.js", "C:evil.js", "c:\\evil.js", "/evil.js", "//host/share.js", "a//b.js",
                    "./a.js", "a/./b.js", "a/../b.js", "a:b.js", "con/../x.js", "a/b.js "):
            entries = dict(self.files(), **{bad: b"var x = 1;"})
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "unsafe"):
                appkit.check_package(self.package("bad.wgt", entries))


class SignatureStructure(Base):
    def test_structurally_complete_signatures_pass_with_honest_scope(self):
        result = appkit.check_package(self.package("ok.wgt", self.signed()), require_signed=True)
        self.assertTrue(result["passed"], result["signatures"])
        self.assertTrue(result["signature_structure_complete"])
        self.assertIn("not cryptographically verified", result["scope"])

    def test_counterfeit_reference_only_xml_is_refused(self):
        for strip in ("SignatureValue", "X509Certificate", "SignatureMethod", "Object", "role"):
            entries = self.signed(omit=strip)
            with self.subTest(strip=strip):
                result = appkit.check_package(self.package("fake.wgt", entries), require_signed=True)
                self.assertFalse(result["passed"], result["signatures"])
                self.assertFalse(result["signature_structure_complete"])

    def test_distributor_signature_must_cover_author_signature(self):
        files = self.files()
        author = signature(files, role="author")
        distributor = signature(files, role="distributor")
        entries = dict(files, **{"author-signature.xml": author, "signature1.xml": distributor})
        result = appkit.check_package(self.package("gap.wgt", entries), require_signed=True)
        self.assertFalse(result["passed"])
        self.assertIn("author-signature.xml", result["signatures"]["signature1.xml"]["unreferenced"])


class PackageIdentity(Base):
    def test_session_refuses_a_package_for_another_app_before_any_command(self):
        package = self.root / "demo.wgt"
        appkit.pack(self.source, package)
        clock, runner = Clock(), Runner()
        session = flows.Session(SERIAL, "ZyXwVuTsRq.Other", **session_args(runner, clock))
        with self.assertRaises(LiveError) as caught:
            session.install(package)
        self.assertEqual(caught.exception.code, "package")
        self.assertEqual(runner.calls, [])

    def test_retail_refuses_mismatched_app_before_lease_or_device(self):
        package = self.package("signed.wgt", self.signed())
        clock, runner = Clock(), Runner()
        with self.assertRaises(LiveError) as caught:
            flows.retail(SERIAL, "tv-0123456789abcdef", "ZyXwVuTsRq.Other", package=package,
                         lease_path=self.lock, **session_args(runner, clock))
        self.assertEqual(caught.exception.code, "package")
        self.assertEqual(runner.calls, [])
        self.assertFalse(self.lock.exists())


class Verdicts(Base):
    def target(self):
        return flows.identify(SERIAL, TOOL, Runner(Clock()))["fingerprint"]

    def test_failed_nested_budget_fails_the_receipt(self):
        clock, runner = Clock(), Runner()
        receipt = flows.retail(SERIAL, self.target(), "AbCdEfGhIj.App", tier="legacy", lease_path=self.lock,
                               **session_args(runner, clock, marks={"firstFrame": 999999, "home": 999999}))
        self.assertFalse(receipt["perf"]["budget"]["passed"])
        self.assertFalse(receipt["passed"])
        clock, runner = Clock(), Runner()
        receipt = flows.retail(SERIAL, self.target(), "AbCdEfGhIj.App", tier="legacy", lease_path=self.lock,
                               **session_args(runner, clock))
        self.assertTrue(receipt["passed"], receipt)

    def test_failed_teardown_fails_the_receipt(self):
        class Failing(Runner):
            def __call__(self, argv, timeout=60):
                if argv[1:2] == ["uninstall"]:
                    self.calls.append(argv[1:])
                    raise LiveError("sdk", "uninstall failed")
                return super().__call__(argv, timeout)
        clock, runner = Clock(), Failing()
        receipt = flows.retail(SERIAL, self.target(), "AbCdEfGhIj.App", uninstall=True, lease_path=self.lock,
                               **session_args(runner, clock))
        self.assertFalse(receipt["passed"])
        self.assertFalse(self.lock.exists())

    def test_cli_budget_failure_exits_nonzero(self):
        metrics = self.root / "m.json"
        metrics.write_text(json.dumps({"coldStartMs": 999999, "firstFrameMs": 1, "firstHomeMs": 1}))
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "tizen.py"), "budgets", "--tier", "current",
                                 "--metrics", str(metrics)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


class LargeScripts(Base):
    def test_oversized_script_fails_closed_and_blocks_packaging_input(self):
        (self.source / "js" / "big.js").write_text("var a = 1;\n" * 200)
        with patch.object(appkit, "MAX_TEXT", 1000):
            report = appkit.audit(self.source)
        self.assertFalse(report["passed"])
        self.assertIn("file-too-large", [f["code"] for f in report["findings"]])


class BudgetValues(unittest.TestCase):
    def test_measurements_must_be_finite_non_negative_numbers(self):
        for bad in (-1, True, False, float("nan"), float("inf"), "12"):
            with self.subTest(bad=bad):
                result = flows.evaluate({"coldStartMs": bad, "firstFrameMs": 1, "firstHomeMs": 1}, "current")
                self.assertFalse(result["passed"])
                self.assertEqual(result["metrics"]["coldStartMs"]["result"], "invalid")

    def test_budget_limits_must_be_finite_non_negative_numbers(self):
        for bad in (-1, True, float("nan"), None):
            budgets = {"tiers": {"x": {"budgets_ms": {"coldStartMs": bad}}}}
            with self.subTest(bad=bad), self.assertRaises(LiveError):
                flows.evaluate({"coldStartMs": 1}, "x", budgets)


class Release(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(), "needs a git checkout")
    def test_every_tracked_file_is_classified(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("release", ROOT / "tools" / "release.py")
        release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(release)
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True,
                                 check=True).stdout.splitlines()
        selected = release.payload_paths(tracked)
        self.assertIn("docs/budgets.json", selected)


class CiAndDocs(unittest.TestCase):
    def test_full_suite_runs_on_ubuntu_and_windows_and_pull_requests(self):
        workflow = (ROOT / ".github" / "workflows" / "workbench-windows.yml").read_text()
        self.assertRegex(workflow, r"(?m)^  pull_request:")
        self.assertRegex(workflow, r"os: \[ubuntu-latest, windows-latest\][\s\S]*unittest discover -s tests")

    def test_lease_skill_does_not_claim_a_ttl(self):
        skill = (ROOT / "skills" / "samsung-tv-lease" / "SKILL.md").read_text()
        self.assertNotRegex(skill, r"\bTTL\b")
        self.assertIn("owner.json", skill)


if __name__ == "__main__":
    unittest.main()
