import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import fs from "node:fs";

test("independent account-free fixture runs ABBA and cancels pending work", () => {
    let pending = null;
    const context = vm.createContext({
        window: {},
        performance,
        setTimeout(callback) { pending = callback; return 1; },
        clearTimeout() { pending = null; }
    });
    vm.runInContext(fs.readFileSync(new URL("../tools/benchmark-fixture/adapter.js", import.meta.url), "utf8"), context);
    const adapter = context.window.__tvBenchmark;
    assert.equal(adapter.describe().account_free, true);
    assert.equal(adapter.describe().metrics.gpu_counter.supported, null);
    for (const [block, mode] of ["off", "on", "on", "off"].entries()) {
        for (const phase of ["warmup", "sample"]) {
            const request = {id: `${block}:${phase}`, block, mode, phase, index: 0};
            assert.equal(adapter.begin(request), true);
            assert.equal(adapter.poll(request.id).state, "pending");
            pending();
            const result = adapter.poll(request.id);
            assert.equal(result.state, "complete");
            assert.equal(result.id, request.id);
            assert.equal(result.metrics.operation_ms.length, 1);
            assert.ok(Number.isFinite(result.metrics.operation_ms[0]));
            assert.ok(result.metrics.operation_ms[0] >= 0);
            assert.equal(result.metrics.gpu_counter, undefined);
        }
    }
    assert.equal(adapter.begin({id: "cancel", mode: "off"}), true);
    assert.equal(adapter.cancel(), true);
    assert.equal(pending, null);
    assert.equal(adapter.poll("cancel").state, "error");
});
