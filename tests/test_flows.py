import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import appkit
import flows
from live_tv import Lease, LiveError

TOOL = sys.executable
SERIAL = "192.0.2.10:26101"
DUID = "FAKEDUID0123456789"


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeCDP:
    instances = []

    def __init__(self, port, page, screenshot=None, marks=None):
        self.port, self.page, self.calls, self.closed = port, page, [], False
        self.screenshot = screenshot
        self.marks = marks if marks is not None else {"firstFrame": 400, "home": 900}
        FakeCDP.instances.append(self)

    def rpc(self, method, params=None, timeout=None):
        self.calls.append(method)
        if method == "Runtime.evaluate":
            if params["expression"] == flows.READY:
                return {"result": {"value": "complete"}}
            return {"result": {"value": json.dumps(self.marks)}}
        if method == "Page.captureScreenshot":
            if self.screenshot is None:
                raise LiveError("timeout", "no callback")
            return {"data": self.screenshot}
        return {}

    def close(self):
        self.closed = True


class Runner:
    """Simulates sdb/tizen/em-cli; records every argv."""

    def __init__(self, clock=None, duid=DUID, foreign_forward=False, emulator_after=None):
        self.calls, self.forwards = [], []
        self.duid, self.foreign_forward = duid, foreign_forward
        self.clock, self.emulator_after = clock, emulator_after

    def __call__(self, argv, timeout=60):
        self.calls.append(argv[1:])
        args = argv[1:]
        if args[:1] == ["list-vm"]:
            return "tv-vm\n"
        if args[:1] == ["devices"]:
            lines = ["List of devices attached", SERIAL + "\tdevice\tTV"]
            if self.emulator_after is not None and self.clock() >= self.emulator_after:
                lines.append("emulator-26101\tdevice\tvm")
            return "\n".join(lines)
        if args[:1] in (["connect"], ["install"], ["uninstall"]):
            return "ok"
        serial, rest = args[1], args[2:]
        if rest == ["capability"]:
            return "profile_name:tv\nplatform_version:3.0\n"
        if rest[:3] == ["shell", "0", "getduid"]:
            return self.duid + "\n"
        if rest[:3] == ["shell", "0", "kill"]:
            return f"Pkgid: {rest[3]} is Terminated\nspend time for pkgcmd is [109]ms\n"
        if rest[:3] == ["shell", "0", "debug"]:
            return "... successfully launched pid = 1 with debug 1 port: 42001"
        if rest == ["forward", "--list"]:
            if self.foreign_forward:
                return "\n".join(f"{serial} tcp:{local} tcp:9999" for local, _ in self.forwards)
            return "\n".join(f"{serial} tcp:{local} tcp:{remote}" for local, remote in self.forwards)
        if rest[:1] == ["forward"] and rest[1] == "--remove":
            self.forwards = [f for f in self.forwards if f"tcp:{f[0]}" != rest[2]]
            return ""
        if rest[:1] == ["forward"]:
            self.forwards.append((int(rest[1][4:]), int(rest[2][4:])))
            return ""
        if rest[:2] == ["dlog", "-d"]:
            return "I/App: started token=abcdef1234567890\n"
        raise AssertionError(argv)


def session_args(runner, clock, **extra):
    FakeCDP.instances = []
    return dict(runner=runner, sdb=TOOL, tizen=TOOL, discover=lambda port: [{"id": "page"}],
                factory=lambda port, page: FakeCDP(port, page, **extra), clock=clock, sleep=clock.sleep)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.lock = self.root / "lane.lock"

    def tearDown(self):
        self.temporary.cleanup()

    def target(self):
        clock = Clock()
        return flows.identify(SERIAL, TOOL, Runner(clock))["fingerprint"]

    def test_debug_port_parsing_needs_exactly_one_port(self):
        self.assertEqual(flows.debug_port("launched pid = 7 with debug 1 port: 42001"), 42001)
        for text in ("no port here", "port: 42001 port: 42002", "port: 80"):
            with self.assertRaises(LiveError):
                flows.debug_port(text)

    def test_identify_never_returns_the_duid(self):
        result = flows.identify(SERIAL, TOOL, Runner(Clock()))
        self.assertRegex(result["fingerprint"], r"^tv-[0-9a-f]{16}$")
        self.assertNotIn(DUID, json.dumps(result))
        self.assertEqual(result["tier"], "legacy")
        with self.assertRaises(LiveError):
            flows.identify(SERIAL, TOOL, Runner(Clock(), duid="??"))

    def test_held_lane_refuses_before_any_device_command(self):
        self.lock.mkdir()
        clock, runner = Clock(), Runner()
        with self.assertRaises(LiveError) as caught:
            flows.retail(SERIAL, "tv-0123456789abcdef", "AbCdEfGhIj.App", lease_path=self.lock,
                         **session_args(runner, clock))
        self.assertEqual(caught.exception.code, "device_lock_held")
        self.assertEqual(runner.calls, [])
        self.assertTrue(self.lock.is_dir())

    def test_different_tv_is_refused_without_install_and_lane_is_released(self):
        clock, runner = Clock(), Runner()
        with self.assertRaises(LiveError) as caught:
            flows.retail(SERIAL, "tv-0000000000000000", "AbCdEfGhIj.App", lease_path=self.lock,
                         **session_args(runner, clock))
        self.assertEqual(caught.exception.code, "target")
        self.assertFalse(any(call[:1] == ["install"] or "debug" in call for call in runner.calls))
        self.assertFalse(self.lock.exists())

    def test_retail_requires_signed_package(self):
        appkit.scaffold(self.root / "app", "Demo")
        unsigned = self.root / "demo.wgt"
        appkit.pack(self.root / "app" / "app", unsigned)
        clock, runner = Clock(), Runner()
        with self.assertRaises(LiveError) as caught:
            flows.retail(SERIAL, self.target(), "AbCdEfGhIj.App", package=unsigned, lease_path=self.lock,
                         **session_args(runner, clock))
        self.assertEqual(caught.exception.code, "package")
        self.assertEqual(runner.calls, [])

    def test_retail_flow_measures_records_unsupported_capture_and_cleans_owned_forward(self):
        clock, runner = Clock(), Runner()
        receipt = flows.retail(SERIAL, self.target(), "AbCdEfGhIj.App", keys=["down", "back"], tier="legacy",
                               lease_path=self.lock, **session_args(runner, clock))
        self.assertEqual(receipt["lane"], "released")
        self.assertFalse(self.lock.exists())
        self.assertEqual(runner.forwards, [])
        self.assertTrue(FakeCDP.instances[0].closed)
        steps = {s["step"]: s for s in receipt["steps"]}
        self.assertEqual(steps["capture"]["result"], "unsupported")
        self.assertEqual(steps["keys"]["sent"], ["down", "back"])
        self.assertEqual(receipt["perf"]["median"]["firstHomeMs"], 900)
        self.assertTrue(receipt["perf"]["budget"]["passed"])
        self.assertNotIn(DUID, json.dumps(receipt))
        kills = [c for c in runner.calls if c[2:5] == ["shell", "0", "kill"]]
        debug = [i for i, c in enumerate(runner.calls) if c[2:5] == ["shell", "0", "debug"]]
        self.assertLess(runner.calls.index(kills[0]), debug[0])

    def test_retail_with_caller_lease_requires_it_held_and_keeps_it(self):
        clock, runner = Clock(), Runner()
        lease = Lease(self.root / "tizen-tv-tv-abc123.lock")
        with self.assertRaises(LiveError) as caught:
            flows.retail(SERIAL, self.target(), "AbCdEfGhIj.App", lease=lease, **session_args(runner, clock))
        self.assertEqual(caught.exception.code, "device_lock_held")
        self.assertEqual(runner.calls, [])
        lease.acquire()
        try:
            receipt = flows.retail(SERIAL, self.target(), "AbCdEfGhIj.App", lease=lease,
                                   **session_args(runner, clock))
            self.assertEqual(receipt["lane"], "held by caller")
            self.assertTrue((self.root / "tizen-tv-tv-abc123.lock").is_dir())
            self.assertFalse(self.lock.exists(), "A caller-held per-TV lease never takes the canonical lane")
        finally:
            lease.release()

    def test_changed_forward_mapping_is_not_deleted(self):
        clock, runner = Clock(), Runner(foreign_forward=True)
        session = flows.Session(SERIAL, "AbCdEfGhIj.App", **session_args(runner, clock))
        with self.assertRaises(LiveError):
            session.launch()
        session.teardown()
        self.assertFalse(any("--remove" in call for call in runner.calls))
        self.assertEqual(len(runner.forwards), 1)

    def test_capture_writes_only_real_png(self):
        import base64
        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 20).decode()
        clock, runner = Clock(), Runner()
        session = flows.Session(SERIAL, "AbCdEfGhIj.App", output=self.root,
                                **session_args(runner, clock, screenshot=png))
        session.launch()
        self.assertIsNotNone(session.capture())
        self.assertEqual(len(list(self.root.glob("app-layer-*.png"))), 1)
        tail = session.logs()
        self.assertNotIn("abcdef1234567890", "\n".join(tail))
        session.teardown()
        session.client = FakeCDP(0, None, screenshot=base64.b64encode(b"GIF89a").decode())
        self.assertIsNone(session.capture())

    def test_budgets_pass_fail_and_unmeasured(self):
        self.assertTrue(flows.evaluate({"coldStartMs": 1, "firstFrameMs": 1, "firstHomeMs": 1}, "current")["passed"])
        result = flows.evaluate({"coldStartMs": 99999, "firstFrameMs": 1}, "current")
        self.assertFalse(result["passed"])
        self.assertEqual(result["metrics"]["coldStartMs"]["result"], "fail")
        self.assertEqual(result["metrics"]["firstHomeMs"]["result"], "unmeasured")
        budgets = json.loads(flows.BUDGETS.read_text())["tiers"]
        for tier in ("legacy", "mid", "current"):
            self.assertEqual(set(budgets[tier]["budgets_ms"]), {"coldStartMs", "firstFrameMs", "firstHomeMs"})
        self.assertEqual([flows.tier_for(v) for v in ("3.0", "4.0", "5.0", "5.5", "9.0", "x")],
                         ["legacy", "mid", "mid", "current", "current", None])

    def test_emulator_flow_uses_new_serial_and_stops_only_its_launcher(self):
        clock = Clock()
        runner = Runner(clock, emulator_after=104)
        launched = []

        class Process:
            def __init__(self, argv, **kwargs):
                launched.append(argv[1:])
                self.state = None

            def poll(self):
                return self.state

            def terminate(self):
                self.state = 0

            def wait(self, timeout=None):
                return 0

        receipt = flows.emulator("tv-vm", "AbCdEfGhIj.App", em_cli=TOOL, spawn=Process,
                                 **session_args(runner, clock))
        self.assertEqual(launched, [["launch", "-n", "tv-vm"]])
        self.assertEqual(receipt["serial"], "emulator-26101")
        self.assertEqual(receipt["emulator"], "stopped (owned launcher)")
        self.assertTrue(any(c[:1] == ["uninstall"] for c in runner.calls))
        self.assertFalse(any(SERIAL in c for c in runner.calls if len(c) > 1 and c[0] == "-s"))
        with self.assertRaises(LiveError):
            flows.emulator("missing", "AbCdEfGhIj.App", em_cli=TOOL, spawn=Process, **session_args(runner, clock))

    def test_emulator_launcher_failure_is_reported_immediately(self):
        clock, runner = Clock(), Runner()

        class Failed:
            def __init__(self, argv, stdout=None, **kwargs):
                stdout.write(b"Error: Failed to start this VM.\nYour system cannot support HW virtualization.\n")
                stdout.flush()

            def poll(self):
                return 2

        with self.assertRaises(LiveError) as caught:
            flows.emulator("tv-vm", "AbCdEfGhIj.App", em_cli=TOOL, spawn=Failed, **session_args(runner, clock))
        self.assertEqual(caught.exception.code, "emulator")
        self.assertIn("HW virtualization", str(caught.exception))
        self.assertLess(clock.now, 110)


if __name__ == "__main__":
    unittest.main()
