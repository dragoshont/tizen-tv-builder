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


class ReReview(Base):
    def target(self):
        return flows.identify(SERIAL, TOOL, Runner(Clock()))["fingerprint"]

    def kill_with(self, returncode, stdout):
        clock = Clock()
        session = flows.Session(SERIAL, "AbCdEfGhIj.App", **session_args(flows.run_tool, clock))
        done = subprocess.CompletedProcess([], returncode, stdout=stdout)
        with patch.object(flows.subprocess, "run", return_value=done):
            session.kill()
        return session.receipt["steps"][0], flows.verdict(session.receipt)["passed"]

    def test_truncated_sdk_output_cannot_confirm_app_absence(self):
        absent = b"Pkgid: AbCdEfGhIj is already Terminated\nspend time for pkgcmd is [111]ms\n"
        for code, padding in ((1, 600), (1, 512 * 1024), (0, 512 * 1024)):
            with self.subTest(returncode=code, padding=padding):
                step, passed = self.kill_with(code, b"error: device offline\n" + b" " * padding + absent)
                self.assertEqual(step["result"], "failed")
                self.assertFalse(passed)

    def test_truncated_zero_exit_output_is_rejected_by_the_runner(self):
        valid = b"Pkgid: AbCdEfGhIj is Terminated\n"
        done = subprocess.CompletedProcess([], 0, stdout=b"error: device offline\n" + valid)
        with patch.object(flows.subprocess, "run", return_value=done):
            with self.assertRaises(LiveError) as caught:
                flows.run_tool(["sdb"], limit=len(valid))
        self.assertEqual(caught.exception.code, "sdk_output_truncated")
        self.assertLessEqual(len(str(caught.exception)), 700)
        with patch.object(flows.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=valid)):
            self.assertEqual(flows.run_tool(["sdb"], limit=len(valid)), valid.decode())
        step, passed = self.kill_with(0, b"error: device offline\n" + b" " * (512 * 1024) + valid)
        self.assertEqual((step["result"], step["reason"]), ("failed", "sdk_output_truncated"))
        self.assertFalse(passed)

    def test_zero_exit_kill_is_classified_by_observed_sdk_wording(self):
        timing = b"spend time for pkgcmd is [111]ms\n"
        cases = (
            (b"processing result : General error [-1] failed", "failed", "sdk_kill_error"),
            (b"processing result : General error [-1] failed\n" + timing, "failed", "sdk_kill_error"),
            (b"Pkgid: AbCdEfGhIj is already Terminated\n" + timing, "not-running", None),
            (b"Pkgid: AbCdEfGhIj is already Terminated\r\n", "not-running", None),
            (b"Pkgid: AbCdEfGhIj is Terminated\n" + timing, "ok", None),
            (b"Pkgid: AbCdEfGhIj is Terminated\n", "ok", None),
            (b"Pkgid: AbCdEfGhIj is already Terminated\n" + timing + b"error: device offline\n",
             "failed", "sdk_kill_unrecognized"),
            (b"error: device offline\nPkgid: AbCdEfGhIj is Terminated\n", "failed", "sdk_kill_unrecognized"),
            (b"Pkgid: AbCdEfGhIj is Terminated\nprocessing result : General error [-1] failed\n",
             "failed", "sdk_kill_error"),
            (b"Pkgid: XyXyXyXyXy is already Terminated\n", "failed", "sdk_kill_unrecognized"),
            (b"Pkgid: AbCdEfGhIjK is Terminated\n", "failed", "sdk_kill_unrecognized"),
            (b"Pkgid: AbCdEfGhIj.App is Terminated\n", "failed", "sdk_kill_unrecognized"),
            (b"Pkgid: AbCdEfGhIj is Terminated\n" + timing + timing, "failed", "sdk_kill_unrecognized"),
            (b"Pkgid: AbCdEfGhIj is Terminated\nspend time for pkgcmd is [1x]ms\n", "failed",
             "sdk_kill_unrecognized"),
            (b"  Pkgid: AbCdEfGhIj is Terminated\n", "failed", "sdk_kill_unrecognized"),
            (b"killed", "failed", "sdk_kill_unrecognized"),
            (b"", "failed", "sdk_kill_unrecognized"),
            (timing, "failed", "sdk_kill_unrecognized"),
        )
        for stdout, result, reason in cases:
            with self.subTest(stdout=stdout):
                step, passed = self.kill_with(0, stdout)
                self.assertEqual(step["result"], result)
                self.assertEqual(step.get("reason"), reason)
                self.assertEqual(passed, result != "failed")

    def test_nonzero_exit_kill_never_confirms_absence(self):
        for stdout in (b"Pkgid: AbCdEfGhIj is already Terminated\n", b"AbCdEfGhIj is not running",
                       b"AbCdEfGhIj.App does not exist", b"Pkgid: AbCdEfGhIj is Terminated\n"):
            with self.subTest(stdout=stdout):
                step, passed = self.kill_with(1, stdout)
                self.assertEqual((step["result"], step["reason"]), ("failed", "sdk"))
                self.assertFalse(passed)

    def test_failed_capture_fails_the_receipt(self):
        clock, runner = Clock(), Runner()
        receipt = flows.retail(SERIAL, self.target(), "AbCdEfGhIj.App", lease_path=self.lock,
                               **session_args(runner, clock, screenshot="YWJj"))
        self.assertIn({"step": "capture", "result": "fail", "reason": "not-png"}, receipt["steps"])
        self.assertFalse(receipt["passed"])
        self.assertIn("capture", receipt["failed_steps"])

    def test_kill_runner_errors_always_fail(self):
        class Kill(Runner):
            error = None
            def __call__(self, argv, timeout=60):
                if argv[-3:-1] == ["0", "kill"] and self.error:
                    self.calls.append(argv[1:])
                    raise self.error
                return super().__call__(argv, timeout)
        for error in (LiveError("sdk_timeout", "SDK command timed out"),
                      LiveError("sdk_output_truncated", "SDK command failed (output truncated)"),
                      LiveError("sdk", "SDK command failed: error: device offline"),
                      LiveError("sdk", "SDK command failed: Pkgid: AbCdEfGhIj is already Terminated"),
                      LiveError("sdk", "SDK command failed: AbCdEfGhIj is not running")):
            with self.subTest(error=error.code, message=str(error)):
                clock, runner = Clock(), Kill()
                session = flows.Session(SERIAL, "AbCdEfGhIj.App", **session_args(runner, clock))
                runner.error = error
                session.teardown()
                kills = [s for s in session.receipt["steps"] if s["step"] == "kill"]
                self.assertEqual([(s["result"], s["reason"]) for s in kills], [("failed", error.code)])
                self.assertFalse(flows.verdict(session.receipt)["passed"])

    def test_invalid_raw_samples_are_invalid_not_aggregated(self):
        self.assertEqual(flows._median([400, "invalid", 400]), "invalid")
        self.assertEqual(flows._median([True, False]), "invalid")
        self.assertEqual(flows._median([400, None]), "invalid")
        self.assertIsNone(flows._median([None, None]))
        self.assertEqual(flows._median([300, 500]), 400)
        clock, runner = Clock(), Runner()
        receipt = flows.retail(SERIAL, self.target(), "AbCdEfGhIj.App", runs=2, tier="legacy", lease_path=self.lock,
                               **session_args(runner, clock, marks={"firstFrame": True, "home": False}))
        self.assertEqual(receipt["perf"]["budget"]["metrics"]["firstFrameMs"]["result"], "invalid")
        self.assertFalse(receipt["passed"])

    def test_errors_and_invalid_budgets_exit_2(self):
        import tizen
        with patch.object(tizen, "run", side_effect=ValueError("SDK command failed")), \
                patch.object(sys, "argv", ["tizen.py", "doctor"]):
            self.assertEqual(tizen.main(), 2)
        metrics = self.root / "m.json"
        metrics.write_text(json.dumps({"coldStartMs": 1, "firstFrameMs": 1, "firstHomeMs": 1}))
        with patch.object(flows, "evaluate", side_effect=LiveError("invalid", "Budget limits invalid")), \
                patch.object(sys, "argv", ["tizen.py", "budgets", "--tier", "current", "--metrics", str(metrics)]):
            self.assertEqual(tizen.main(), 2)


class HugeIntegers(Base):
    def test_huge_integers_are_invalid_not_overflow(self):
        huge = 10 ** 400
        self.assertTrue(flows._milliseconds(flows.MAX_MS))
        self.assertFalse(flows._milliseconds(flows.MAX_MS + 1))
        self.assertFalse(flows._milliseconds(-huge))
        self.assertFalse(flows._milliseconds(huge))
        self.assertEqual(flows._median([huge, 1]), "invalid")
        result = flows.evaluate({"coldStartMs": huge, "firstFrameMs": 1, "firstHomeMs": 1}, "current")
        self.assertEqual(result["metrics"]["coldStartMs"]["result"], "invalid")
        self.assertFalse(result["passed"])
        budgets = json.loads(flows.BUDGETS.read_text())
        budgets["tiers"]["current"]["budgets_ms"]["coldStartMs"] = huge
        with self.assertRaises(LiveError):
            flows.evaluate({"coldStartMs": 1}, "current", budgets)
        import live_tv
        self.assertFalse(live_tv.number(huge, 0, 10))
        import recording
        for kwargs in ({"fps": huge}, {"duration": huge}):
            with self.subTest(**{k: "huge" for k in kwargs}), self.assertRaises(recording.RecordingError):
                recording.Recorder(None, self.root / "out", **kwargs)

    def test_budgets_cli_with_huge_metric_exits_2(self):
        import tizen
        metrics = self.root / "m.json"
        metrics.write_text('{"coldStartMs": 1' + "0" * 400 + ', "firstFrameMs": 1, "firstHomeMs": 1}')
        with patch.object(sys, "argv", ["tizen.py", "budgets", "--tier", "current", "--metrics", str(metrics)]), \
                patch("sys.stdout"):
            self.assertEqual(tizen.main(), 2)


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
