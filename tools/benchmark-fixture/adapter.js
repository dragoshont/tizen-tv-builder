/* Independently authored MIT contract fixture; no network, storage, accounts or media. */
(function () {
    "use strict";
    var timer = null;
    var result = null;
    var description = {
        schema: 1,
        adapter_id: "toolkit-cpu-probe-v1",
        app_id: "TvBench001.Probe",
        source_id: "toolkit-cpu-probe-v1",
        account_free: true,
        metrics: {
            operation_ms: {unit: "ms", meaning: "cpu-api-wall", supported: true},
            gpu_counter: {unit: "count", meaning: "counter", supported: null}
        }
    };
    window.__tvBenchmark = {
        describe: function () { return description; },
        begin: function (request) {
            if (timer !== null || !request || (request.mode !== "off" && request.mode !== "on")) {
                return false;
            }
            result = {id: request.id, state: "pending"};
            timer = setTimeout(function () {
                var start = performance.now();
                var sum = 0;
                var i;
                for (i = 0; i < 10000; i += 1) {
                    sum += request.mode === "on" ? Math.imul(i, 2) : i * 2;
                }
                var elapsed = performance.now() - start;
                result = sum === 99990000 ?
                    {id: request.id, state: "complete", metrics: {operation_ms: [elapsed]}} :
                    {id: request.id, state: "error"};
                timer = null;
            }, 0);
            return true;
        },
        poll: function (id) {
            if (!result || result.id !== id) { return {id: id, state: "error"}; }
            return result;
        },
        cancel: function () {
            if (timer !== null) { clearTimeout(timer); }
            timer = null;
            result = null;
            return true;
        }
    };
}());
