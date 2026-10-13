"""Bounded, attach-only ABBA measurements from an explicitly reviewed app adapter."""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import time
import uuid

from live_tv import CDP, Lease, LiveError, endpoint, fail, target_id_ok
from tizen import application_id
from workbench import lane

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE = 16 * 1024 * 1024
ORDER = ["off", "on", "on", "off"]
IDENTITY = ("(function(){var a=tizen.application.getCurrentApplication().appInfo;"
            "return {app_id:a.id,version:a.version,user_agent:navigator.userAgent};}())")
DESCRIBE = "window.__tvBenchmark.describe()"
CANCEL = "window.__tvBenchmark.cancel()"


def require(condition, message):
    if not condition:
        fail("benchmark_invalid", message)


def fields(value, names):
    require(isinstance(value, dict) and set(value) == set(names), "Unexpected benchmark fields")


def text(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and all(32 <= ord(c) < 127 for c in value)


def finite(value):
    return type(value) in (int, float) and 0 <= value <= 2 ** 53 and math.isfinite(value)


def strict_json(raw):
    def constant(_):
        fail("benchmark_invalid", "Nonfinite JSON value")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON field")
            result[key] = value
        return result

    try:
        return json.loads(raw, parse_constant=constant, object_pairs_hook=pairs)
    except (ValueError, RecursionError):
        fail("benchmark_invalid", "Malformed benchmark JSON")


def read_json(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_FILE + 1)
    require(len(raw) <= MAX_FILE, "Benchmark JSON exceeds 16 MiB")
    return strict_json(raw)


def descriptor(value):
    fields(value, {"schema", "adapter_id", "app_id", "source_id", "account_free", "metrics"})
    require(type(value["schema"]) is int and value["schema"] == 1, "Unsupported adapter schema")
    require(value["account_free"] is True, "Only reviewed account-free adapters are allowed")
    for key in ("adapter_id", "app_id", "source_id"):
        require(text(value[key]), "Adapter identity must be short plain text")
    application_id(value["app_id"])
    metrics = value["metrics"]
    require(isinstance(metrics, dict) and 1 <= len(metrics) <= 32, "Declare 1-32 metrics")
    for name, spec in metrics.items():
        require(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) is not None, "Invalid metric name")
        fields(spec, {"unit", "meaning", "supported"})
        require(spec["unit"] in ("ms", "count", "bytes"), "Unknown metric unit")
        require(spec["meaning"] in ("callback-interval", "cpu-api-wall", "elapsed", "counter"),
                "Unknown metric meaning; no GPU kernel or panel FPS inference")
        require(spec["meaning"] == "counter" or spec["unit"] == "ms", "Timing metrics require ms")
        require(spec["supported"] is None or type(spec["supported"]) is bool, "Invalid capability")
    return value


def plan(warmup, samples, timeout):
    require(type(warmup) is int and 0 <= warmup <= 20, "Warmup must be 0-20 per block")
    require(type(samples) is int and 1 <= samples <= 100, "Samples must be 1-100 per block")
    require(finite(timeout) and 1 <= timeout <= 120, "Timeout must be 1-120 seconds")
    return {"order": list(ORDER), "warmup": warmup, "samples": samples, "timeout_seconds": timeout}


def requests(run_id, config):
    for block, mode in enumerate(ORDER):
        for phase, count in (("warmup", config["warmup"]), ("sample", config["samples"])):
            for index in range(count):
                yield {"id": f"{run_id}:{block}:{phase}:{index}", "block": block,
                       "mode": mode, "phase": phase, "index": index}


def observation(value, request, spec):
    fields(value, {"id", "state", "metrics"})
    require(value["id"] == request["id"] and value["state"] == "complete", "Stale or incomplete sample")
    metrics = value["metrics"]
    require(isinstance(metrics, dict) and not set(metrics) - set(spec["metrics"]), "Undeclared metric")
    for name, values in metrics.items():
        supported = spec["metrics"][name]["supported"]
        require(values is None or (supported is True and isinstance(values, list)
                                  and 1 <= len(values) <= 4096 and all(finite(v) for v in values)),
                "Metric must be missing/null or a bounded array of finite nonnegative measurements")
    return value


def metadata(value):
    allowed = {"model", "model_group", "region", "firmware", "runtime", "source_sha", "package_sha256",
               "sdk_version"}
    require(isinstance(value, dict) and not set(value) - allowed, "Unknown metadata field")
    for key, item in value.items():
        fields(item, {"value", "provenance"})
        require(text(item["value"]) and item["provenance"] in ("user-provided", "observed"),
                "Metadata needs exact value and provenance")
        if key in ("source_sha", "package_sha256"):
            require(re.fullmatch(r"[0-9a-f]{40}" if key == "source_sha" else r"[0-9a-f]{64}",
                                 item["value"]) is not None, "Invalid source/package digest")
    return value


def summarize(report):
    fields(report, {"schema", "run_id", "plan", "adapter", "metadata", "host", "identity", "target",
                    "records", "status", "error", "cleanup"})
    require(type(report["schema"]) is int and report["schema"] == 1, "Unsupported report schema")
    require(isinstance(report["run_id"], str) and
            re.fullmatch(r"[0-9a-f]{32}", report["run_id"]) is not None, "Invalid run identity")
    config = report["plan"]
    fields(config, {"order", "warmup", "samples", "timeout_seconds"})
    require(config == plan(config["warmup"], config["samples"], config["timeout_seconds"]), "Not an ABBA plan")
    spec = descriptor(report["adapter"])
    metadata(report["metadata"])
    fields(report["target"], {"device_alias", "inspector_page", "provenance"})
    require(isinstance(report["target"]["device_alias"], str) and
            re.fullmatch(r"tv-[0-9a-f]{6}", report["target"]["device_alias"]) is not None
            and target_id_ok(report["target"]["inspector_page"])
            and report["target"]["provenance"] == "user-provided", "Invalid target provenance")
    require(isinstance(report["host"], dict), "Missing host provenance")
    fields(report["host"], {"python", "platform", "runner_sha256"})
    require(all(text(v) for v in report["host"].values()), "Invalid host provenance")
    identity = report["identity"]
    if identity is not None:
        fields(identity, {"app_id", "version", "user_agent"})
        require(identity["app_id"] == spec["app_id"] and all(text(v) for v in identity.values()),
                "App identity mismatch")
    require(report["status"] in ("complete", "failed", "cancelled"), "Invalid report status")
    require(report["error"] is None or text(report["error"]), "Invalid failure code")
    fields(report["cleanup"], {"adapter", "connection", "lease", "target_lease"})
    require(all(v in ("not-acquired", "confirmed", "failed") for v in report["cleanup"].values()),
            "Invalid cleanup receipt")
    records = report["records"]
    expected = list(requests(report["run_id"], config))
    require(isinstance(records, list) and len(records) <= len(expected), "Too many samples")
    for index, record in enumerate(records):
        fields(record, {"request", "result"})
        require(record["request"] == expected[index], "Sample order/provenance mismatch")
        require(type(record["request"]["block"]) is int and type(record["request"]["index"]) is int,
                "Sample provenance indices must be integers")
        observation(record["result"], expected[index], spec)
    complete = report["status"] == "complete"
    require(not complete or (len(records) == len(expected) and identity is not None
                            and report["error"] is None
                            and all(v == "confirmed" for v in report["cleanup"].values())),
            "A complete run requires every sample, identity and confirmed cleanup")
    result = {}
    for mode in ("off", "on"):
        selected = [r["result"]["metrics"] for r in records
                    if r["request"]["mode"] == mode and r["request"]["phase"] == "sample"]
        result[mode] = {}
        for name, definition in spec["metrics"].items():
            values = sorted(v for r in selected for v in (r.get(name) or []))
            missing = sum(r.get(name) is None for r in selected)
            result[mode][name] = {
                **definition, "n": len(values), "measured_requests": len(selected) - missing,
                "missing_requests": missing, "median": statistics.median(values) if values else None,
                "p95_nearest_rank": values[math.ceil(.95 * len(values)) - 1] if values else None,
                "small_n": len(values) < 20, "incomplete": not complete or missing > 0,
            }
    return {"schema": 1, "run_id": report["run_id"], "status": report["status"],
            "warmup_excluded": True, "metrics": result}


def evaluate(client, expression, timeout):
    result = client.rpc("Runtime.evaluate", {"expression": expression, "returnByValue": True},
                        timeout=timeout)
    require(not result.get("wasThrown") and not result.get("exceptionDetails"), "Adapter evaluation threw")
    remote = result.get("result")
    require(isinstance(remote, dict) and "value" in remote and "unserializableValue" not in remote,
            "Adapter returned no JSON value")
    return remote["value"]


def collect(port, target, spec, config, facts, report, *, device_alias, factory=CDP, lease=None, target_lease=None,
            clock=time.monotonic, sleep=time.sleep):
    """`report` retains the valid raw prefix on failure; teardown has its own 2s RPC budget."""
    descriptor(spec)
    require(config == plan(config["warmup"], config["samples"], config["timeout_seconds"]), "Invalid plan")
    metadata(facts)
    require(type(port) is int and 1024 <= port <= 65535 and target_id_ok(target), "Explicit inspector required")
    require(isinstance(device_alias, str) and re.fullmatch(r"tv-[0-9a-f]{6}", device_alias) is not None,
            "Explicit toolkit device alias required")
    owned = lease if lease is not None else Lease(lane())
    target_owned = target_lease if target_lease is not None else Lease(lane().parent / f"tizen-tv-{device_alias}.lock")
    report.update(schema=1, run_id=uuid.uuid4().hex, plan=config, adapter=spec, metadata=facts,
                  host={"python": platform.python_version(), "platform": platform.system(),
                        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
                  identity=None, records=[], status="failed", error=None,
                  target={"device_alias": device_alias, "inspector_page": target, "provenance": "user-provided"},
                  cleanup={"adapter": "not-acquired", "connection": "not-acquired", "lease": "not-acquired",
                           "target_lease": "not-acquired"})
    client, acquired, target_acquired, verified = None, False, False, False
    deadline = clock() + config["timeout_seconds"]

    def call(expression):
        remaining = deadline - clock()
        if remaining <= 0:
            fail("benchmark_timeout", "Benchmark deadline exceeded")
        value = evaluate(client, expression, min(5, remaining))
        if clock() >= deadline:
            fail("benchmark_timeout", "Benchmark deadline exceeded")
        return value

    try:
        owned.acquire()
        acquired = True
        target_owned.acquire()
        target_acquired = True
        client = factory(port, target, deadline=deadline)
        identity = call(IDENTITY)
        fields(identity, {"app_id", "version", "user_agent"})
        require(identity["app_id"] == spec["app_id"] and all(text(v) for v in identity.values()),
                "Selected inspector is not the explicitly reviewed app")
        report["identity"] = identity
        require(descriptor(call(DESCRIBE)) == spec, "Adapter descriptor differs from reviewed contract")
        verified = True
        for request in requests(report["run_id"], config):
            require(call("window.__tvBenchmark.begin(" + json.dumps(request) + ")") is True,
                    "Adapter refused sample")
            while True:
                value = call("window.__tvBenchmark.poll(" + json.dumps(request["id"]) + ")")
                require(isinstance(value, dict) and value.get("id") == request["id"], "Stale sample response")
                if value.get("state") == "pending":
                    fields(value, {"id", "state"})
                    sleep(min(.05, max(0, deadline - clock())))
                    continue
                observation(value, request, spec)
                record = {"request": request, "result": value}
                require(len(json.dumps(report, allow_nan=False)) + len(json.dumps(record)) < MAX_FILE - 65536,
                        "Collected data exceeds report size limit")
                report["records"].append(record)
                break
        if clock() >= deadline:
            fail("benchmark_timeout", "Benchmark deadline exceeded")
        report["status"] = "complete"
    except BaseException as error:
        report["status"] = "cancelled" if isinstance(error, KeyboardInterrupt) else "failed"
        report["error"] = (error.code if isinstance(error, LiveError) else
                           "benchmark_cancelled" if isinstance(error, KeyboardInterrupt) else
                           "benchmark_transport" if isinstance(error, OSError) else "benchmark_internal")
        raise
    finally:
        cleanup_failed = False
        if verified:
            try:
                require(evaluate(client, CANCEL, 2) is True, "Adapter cancellation not confirmed")
                report["cleanup"]["adapter"] = "confirmed"
            except (LiveError, OSError, ValueError, KeyboardInterrupt):
                report["cleanup"]["adapter"] = "failed"
                cleanup_failed = True
        if client is not None:
            try:
                client.close()
                report["cleanup"]["connection"] = "confirmed"
            except (OSError, KeyboardInterrupt):
                report["cleanup"]["connection"] = "failed"
                cleanup_failed = True
        if target_acquired:
            try:
                target_owned.release()
                report["cleanup"]["target_lease"] = "confirmed"
            except (LiveError, OSError, KeyboardInterrupt):
                report["cleanup"]["target_lease"] = "failed"
                cleanup_failed = True
        if acquired:
            try:
                owned.release()
                report["cleanup"]["lease"] = "confirmed"
            except (LiveError, OSError, KeyboardInterrupt):
                report["cleanup"]["lease"] = "failed"
                cleanup_failed = True
        if cleanup_failed:
            report["status"], report["error"] = "failed", "benchmark_cleanup"
            fail("benchmark_cleanup", "Cleanup unconfirmed; inspect owned adapter/connection/lane before reuse")


def add_parser(sub):
    cli = sub.add_parser("benchmark", help="Attach-only ABBA benchmark; offline validate/summarize")
    commands = cli.add_subparsers(dest="benchmark_command", required=True)
    for name in ("validate", "summarize"):
        commands.add_parser(name).add_argument("report", type=Path)
    run = commands.add_parser("run")
    run.add_argument("--endpoint", required=True, help="Existing http://127.0.0.1:PORT forward")
    run.add_argument("--target", required=True, help="Exact inspector page ID, never first available page")
    run.add_argument("--device-alias", required=True, help="Exact toolkit tv-xxxxxx alias for the target lane")
    run.add_argument("--app", required=True)
    run.add_argument("--contract", required=True, type=Path)
    run.add_argument("--metadata", type=Path)
    run.add_argument("--warmup", required=True, type=int)
    run.add_argument("--samples", required=True, type=int)
    run.add_argument("--timeout", required=True, type=float)
    run.add_argument("--authorized", action="store_true")
    run.add_argument("--reviewed-account-free", action="store_true")
    run.add_argument("--output", required=True, type=Path)


def run(args):
    if args.benchmark_command != "run":
        summary = summarize(read_json(args.report))
        return summary if args.benchmark_command == "summarize" else {
            "valid": True, "status": summary["status"], "acceptance": "schema only, not TV acceptance"}
    require(args.authorized and args.reviewed_account_free,
            "Run requires --authorized and --reviewed-account-free for this exact app/adapter/target")
    port = endpoint(args.endpoint)
    require(target_id_ok(args.target), "Explicit inspector page ID required")
    require(re.fullmatch(r"tv-[0-9a-f]{6}", args.device_alias) is not None, "Explicit toolkit device alias required")
    spec = descriptor(read_json(args.contract))
    require(spec["app_id"] == args.app, "Contract app differs from --app")
    config = plan(args.warmup, args.samples, args.timeout)
    facts = metadata(read_json(args.metadata)) if args.metadata else {}
    output = args.output
    require(output.is_absolute() and not output.resolve().is_relative_to(ROOT),
            "Raw output must be an absolute private path outside the toolkit")
    report = {}
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        try:
            collect(port, args.target, spec, config, facts, report, device_alias=args.device_alias)
        finally:
            json.dump(report, stream, allow_nan=False)
            stream.write("\n")
    summarize(report)
    return {"status": "complete", "records": len(report["records"]),
            "acceptance": "adapter measurements only; inspect private raw report"}
