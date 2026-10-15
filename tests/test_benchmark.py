import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import benchmark as bench
from live_tv import Lease, LiveError
from test_live_tv import Chrome47Inspector


def contract():
    return {"schema": 1, "adapter_id": "host-fixture-v1", "app_id": "TvBench001.Probe",
            "source_id": "independent-host-fixture-v1", "account_free": True,
            "metrics": {"cost": {"unit": "ms", "meaning": "cpu-api-wall", "supported": True},
                        "gpu": {"unit": "count", "meaning": "counter", "supported": None}}}


class Clock:
    def __init__(self):
        self.now = 100.

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Inspector:
    def __init__(self):
        self.calls, self.begins = [], []
        self.closed, self.cancelled, self.pending = False, False, False
        self.transform = lambda value: value
        self.spec = contract()
        self.app = self.spec["app_id"]
        self.reject = None

    def rpc(self, method, params, timeout):
        assert method == "Runtime.evaluate" and 0 < timeout <= 5
        expression = params["expression"]
        self.calls.append(expression)
        if expression == self.reject:
            raise LiveError("unsupported", "untrusted details must stay out of receipts")
        if expression == bench.IDENTITY:
            value = {"app_id": self.app, "version": "0.0.0", "user_agent": "host fake, not TV"}
        elif expression == bench.DESCRIBE:
            value = copy.deepcopy(self.spec)
        elif expression == bench.CANCEL:
            self.cancelled = True
            value = True
        elif expression.startswith("window.__tvBenchmark.begin("):
            request = json.loads(expression[len("window.__tvBenchmark.begin("):-1])
            self.begins.append(request)
            value = True
        elif expression.startswith("window.__tvBenchmark.poll("):
            request = self.begins[-1]
            value = {"id": request["id"], "state": "pending"} if self.pending else {
                "id": request["id"], "state": "complete",
                "metrics": {"cost": [999] if request["phase"] == "warmup" else [1, 2, 3, 4]}}
            value = self.transform(value)
        else:
            raise AssertionError(expression)
        return {"result": {"value": value}}

    def close(self):
        self.closed = True


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lease = Lease(self.root / "canonical.lock")
        self.target_lease = Lease(self.root / "target.lock")
        self.inspector = Inspector()
        self.clock = Clock()
        self.report = {}

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, warmup=1, samples=2, timeout=10, **kwargs):
        def factory(port, target, deadline):
            self.assertEqual((port, target), (12345, "explicit-page"))
            self.assertEqual(deadline, self.clock() + timeout)
            return self.inspector
        bench.collect(12345, "explicit-page", contract(), bench.plan(warmup, samples, timeout),
                      {"model": {"value": "host-only", "provenance": "user-provided"}},
                      self.report, factory=factory, lease=self.lease,
                      target_lease=self.target_lease, device_alias="tv-000000",
                      clock=self.clock, sleep=self.clock.sleep, **kwargs)

    def cleaned(self):
        self.assertFalse(Path(self.lease.path).exists())
        self.assertFalse(Path(self.target_lease.path).exists())
        self.assertTrue(self.inspector.closed)
        self.assertTrue(self.inspector.cancelled)

    def test_abba_warmup_original_samples_and_summary(self):
        self.collect()
        self.cleaned()
        self.assertEqual([r["mode"] for r in self.inspector.begins], ["off"] * 3 + ["on"] * 6 + ["off"] * 3)
        self.assertEqual(len(self.report["records"]), 12)
        before = copy.deepcopy(self.report)
        summary = bench.summarize(self.report)
        self.assertEqual(self.report, before)
        for mode in ("off", "on"):
            metric = summary["metrics"][mode]["cost"]
            self.assertEqual((metric["n"], metric["median"], metric["p95_nearest_rank"]), (16, 2.5, 4))
            self.assertTrue(metric["small_n"])
            self.assertIsNone(summary["metrics"][mode]["gpu"]["median"])
            self.assertEqual(summary["metrics"][mode]["gpu"]["missing_requests"], 4)
        self.assertEqual(self.report["records"][0]["result"]["metrics"]["cost"], [999])

    def test_nearest_rank_not_interpolation(self):
        self.inspector.transform = lambda value: dict(value, metrics={"cost": list(range(1, 21))})
        self.collect(warmup=0, samples=1)
        summary = bench.summarize(self.report)["metrics"]["off"]["cost"]
        self.assertEqual(summary["p95_nearest_rank"], 19)
        self.assertEqual(summary["median"], 10.5)
        self.assertFalse(summary["small_n"])

    def test_invalid_nonfinite_negative_boolean_unknown_and_empty_samples(self):
        for metrics in ({"cost": [float("nan")]}, {"cost": [float("inf")]}, {"cost": [-1]},
                        {"cost": [True]}, {"cost": [2 ** 54]}, {"cost": []},
                        {"cost": ["1"]}, {"cost": 1}, {"other": [1]}, {"gpu": [0]}):
            with self.subTest(metrics=metrics):
                self.inspector.transform = lambda value: dict(value, metrics=metrics)
                with self.assertRaises(LiveError):
                    self.collect()
                self.cleaned()
                self.assertEqual(self.report["records"], [])
                self.assertEqual(bench.summarize(self.report)["status"], "failed")

    def test_partial_prefix_preserved_on_error(self):
        def transform(value):
            return value if len(self.inspector.begins) == 1 else dict(value, state="error")
        self.inspector.transform = transform
        with self.assertRaises(LiveError):
            self.collect()
        self.assertEqual(len(self.report["records"]), 1)
        self.cleaned()
        bench.summarize(self.report)

    def test_deadline_and_keyboard_cancellation_cleanup(self):
        self.inspector.pending = True
        with self.assertRaisesRegex(LiveError, "deadline"):
            self.collect(timeout=1)
        self.cleaned()
        self.assertLessEqual(self.clock(), 101.001)
        self.inspector.pending = False
        def interrupt(value):
            raise KeyboardInterrupt()
        self.inspector.transform = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        self.cleaned()
        self.assertEqual(self.report["status"], "cancelled")

    def test_stale_id_and_evaluation_exception(self):
        self.inspector.transform = lambda value: dict(value, id="stale")
        with self.assertRaises(LiveError):
            self.collect()
        self.cleaned()
        with patch.object(self.inspector, "rpc", return_value={"wasThrown": True, "result": {"value": True}}):
            with self.assertRaises(LiveError):
                bench.evaluate(self.inspector, bench.DESCRIBE, 1)

    def test_wrong_app_or_contract_never_begins_or_cancels_foreign_adapter(self):
        for wrong_app in (True, False):
            self.inspector = Inspector()
            if wrong_app:
                self.inspector.app = "OtherApp01.App"
            else:
                self.inspector.spec["source_id"] = "not-reviewed"
            with self.assertRaises(LiveError):
                self.collect()
            self.assertEqual(self.inspector.begins, [])
            self.assertFalse(self.inspector.cancelled)
            self.assertTrue(self.inspector.closed)
            self.assertFalse(Path(self.lease.path).exists())

    def test_busy_lane_never_connects_and_remains_owned(self):
        self.lease.acquire()
        try:
            with self.assertRaises(LiveError):
                self.collect()
            self.assertTrue(Path(self.lease.path).exists())
            self.assertEqual(self.inspector.calls, [])
        finally:
            self.lease.release()

    def test_failed_cancel_cannot_report_complete_and_still_releases_lane(self):
        self.inspector.reject = bench.CANCEL
        with self.assertRaisesRegex(LiveError, "Cleanup"):
            self.collect()
        self.assertEqual(self.report["status"], "failed")
        self.assertEqual(self.report["cleanup"]["adapter"], "failed")
        self.assertTrue(self.inspector.closed)
        self.assertFalse(Path(self.lease.path).exists())
        self.assertNotIn("untrusted", json.dumps(self.report))

    def test_busy_target_lane_releases_only_new_canonical_lease(self):
        self.target_lease.acquire()
        try:
            with self.assertRaises(LiveError):
                self.collect()
            self.assertFalse(Path(self.lease.path).exists())
            self.assertTrue(Path(self.target_lease.path).exists())
            self.assertEqual(self.inspector.calls, [])
        finally:
            self.target_lease.release()

    def test_offline_rejects_tampered_provenance_missing_samples_and_nonfinite_json(self):
        self.collect()
        for mutate in (lambda r: r["records"].pop(),
                       lambda r: r["records"][0]["request"].update(mode="on"),
                       lambda r: r["records"][0]["result"].update(id="wrong")):
            report = copy.deepcopy(self.report)
            mutate(report)
            with self.assertRaises(LiveError):
                bench.summarize(report)
        for raw in ('{"a":NaN}', '{"a":Infinity}', '{"a":1,"a":2}'):
            with self.assertRaises(LiveError):
                bench.strict_json(raw)

    def test_invalid_counts_and_capabilities(self):
        for args in ((-1, 1, 10), (21, 1, 10), (0, 0, 10), (0, 101, 10), (0, 1, float("inf"))):
            with self.assertRaises(LiveError):
                bench.plan(*args)
        for supported in (0, "yes", [], {}):
            spec = contract()
            spec["metrics"]["cost"]["supported"] = supported
            with self.assertRaises(LiveError):
                bench.descriptor(spec)

    def test_cli_offline_and_authorization_without_sdk(self):
        self.collect()
        path = self.root / "raw.json"
        path.write_text(json.dumps(self.report))
        for command in ("validate", "summarize"):
            run = subprocess.run([sys.executable, str(ROOT / "tools/tizen.py"), "benchmark", command, str(path)],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            json.loads(run.stdout)
        args = argparse.Namespace(benchmark_command="run", authorized=False, reviewed_account_free=True)
        with self.assertRaises(LiveError):
            bench.run(args)

    def test_private_exclusive_report_written_on_failure(self):
        source = self.root / "contract.json"
        source.write_text(json.dumps(contract()))
        output = self.root / "report.json"
        args = argparse.Namespace(benchmark_command="run", authorized=True, reviewed_account_free=True,
            endpoint="http://127.0.0.1:12345", target="explicit-page", app="TvBench001.Probe",
            contract=source, metadata=None, warmup=0, samples=1, timeout=1, output=output, device_alias="tv-000000")
        def failed(*args, **kwargs):
            args[5].update(self.report)
            raise LiveError("timeout", "private inspector failure")
        self.collect()
        with patch.object(bench, "collect", side_effect=failed):
            with self.assertRaises(LiveError):
                bench.run(args)
        self.assertEqual(json.loads(output.read_text()), self.report)
        if os.name == "posix":
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            bench.run(args)

    def test_end_to_end_real_websocket_transport_with_host_inspector(self):
        def evaluate(params):
            return self.inspector.rpc("Runtime.evaluate", params, 1)["result"]["value"]
        server = Chrome47Inspector(evaluator=evaluate)
        try:
            bench.collect(server.port, "chrome47-page", contract(), bench.plan(1, 2, 10), {}, self.report,
                          device_alias="tv-000000", lease=self.lease, target_lease=self.target_lease)
            self.assertEqual(bench.summarize(self.report)["status"], "complete")
            self.assertEqual(len(self.inspector.begins), 12)
            self.assertTrue(self.inspector.cancelled)
            self.assertFalse(Path(self.lease.path).exists())
            self.assertFalse(Path(self.target_lease.path).exists())
        finally:
            server.close()

    def test_expired_connection_budget_cannot_open_socket(self):
        with patch("live_tv.socket.create_connection") as connect:
            with self.assertRaises(TimeoutError):
                bench.CDP(12345, "explicit-page", deadline=time.monotonic() - 1)
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
