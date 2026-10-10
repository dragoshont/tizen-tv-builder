// Optional integration gate using an existing, isolated Chromium browser. No TV calls.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";

const [browserProfile, canvasUrl, screenshotPath] = process.argv.slice(2);
if (!browserProfile || !canvasUrl) throw new Error("Provide an isolated browser profile and offline canvas URL");
const [port] = (await readFile(`${browserProfile}/DevToolsActivePort`, "utf8")).split("\n");
const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
const socket = new WebSocket(target.webSocketDebuggerUrl), pending = new Map();
let next = 0;
socket.addEventListener("message", (event) => {
  const reply = JSON.parse(event.data), entry = pending.get(reply.id);
  if (!entry) return;
  pending.delete(reply.id); clearTimeout(entry.timer);
  if (reply.error) entry.reject(new Error(reply.error.message)); else entry.resolve(reply.result);
});
await new Promise((resolve, reject) => { socket.addEventListener("open", resolve); socket.addEventListener("error", reject); });
function rpc(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++next, timer = setTimeout(() => { pending.delete(id); reject(new Error(`Timed out: ${method}`)); }, 5000);
    pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await rpc("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error("Browser assertion/evaluation failed");
  return result.result.value;
}
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function key(key, code) {
  await rpc("Input.dispatchKeyEvent", { type: "keyDown", key, windowsVirtualKeyCode: code });
  await rpc("Input.dispatchKeyEvent", { type: "keyUp", key, windowsVirtualKeyCode: code });
  await wait(100);
}
try {
  await rpc("Page.enable");
  await rpc("Page.navigate", { url: canvasUrl });
  await wait(1200);
  assert.equal(await evaluate("document.getElementById('status').textContent.includes('disconnected')"), true);
  assert.equal(await evaluate("document.querySelectorAll('img').length"), 0);
  assert.equal(await evaluate("document.querySelector('[data-key=up]').disabled"), true);
  for (const width of [1200, 390]) {
    await rpc("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: false });
    assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"), true, `Overflow at ${width}`);
  }
  if (screenshotPath) {
    await rpc("Emulation.setDeviceMetricsOverride", { width: 1200, height: 1100, deviceScaleFactor: 1, mobile: false });
    const capture = await rpc("Page.captureScreenshot", { format: "png" });
    await writeFile(screenshotPath, Buffer.from(capture.data, "base64"));
  }
  // UI-only connected fixture. It intercepts API requests, never connects a provider.
  await rpc("Page.addScriptToEvaluateOnNewDocument", { source: `
    window.uiCalls = []; window.uiDelay = 0; window.uiFailed = false;
    window.uiState = {sources:[{id:"fixture",kind:"emulator",metadata:{},status:"connected",
      expires_in:120,input_allowed:true,pixels_allowed:false,run_limit_seconds:120,
      activation_allowed:true,frames:[],stale:true,interval:10,streaming:false,
      provenance:"HOST UI FIXTURE ONLY",limitations:"No TV or emulator provider connected"}]};
    const originalFetch = window.fetch.bind(window);
    window.fetch = async function(url, options) {
      if (!String(url).startsWith("/api/")) return originalFetch(url, options);
      if (window.uiFailed) throw new Error("fixture disconnect");
      if (url === "/api/state") return new Response(JSON.stringify(window.uiState),{headers:{"Content-Type":"application/json"}});
      window.uiCalls.push({url,body:JSON.parse(options.body)});
      if (window.uiDelay) await new Promise(r => setTimeout(r,window.uiDelay));
      return new Response(JSON.stringify({...window.uiState.sources[0],command_ms:3}),{headers:{"Content-Type":"application/json"}});
    };` });
  await rpc("Page.navigate", { url: "about:blank" }); await wait(100);
  await rpc("Page.navigate", { url: canvasUrl }); await wait(1200);
  await evaluate("document.querySelector('[data-key=ok]').focus()");
  await key("Enter", 13);
  assert.equal(await evaluate("uiCalls.filter(c=>c.url.includes('/key/')).length"), 1, "Enter must not also click the native button");
  await key(" ", 32);
  assert.equal(await evaluate("uiCalls.filter(c=>c.url.includes('/key/')).length"), 2, "Space must not double dispatch");
  await evaluate("document.getElementById('remote').focus()");
  for (const [name, code] of [["ArrowUp", 38], ["ArrowDown", 40], ["ArrowLeft", 37], ["ArrowRight", 39], ["Backspace", 8]]) await key(name, code);
  assert.deepEqual(await evaluate("uiCalls.map(c=>c.body.key)"), ["ok", "ok", "up", "down", "left", "right", "back"]);
  await evaluate(`document.getElementById('remote').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',repeat:true,bubbles:true,cancelable:true}))`);
  assert.equal(await evaluate("uiCalls.length"), 7);
  await evaluate("document.querySelector('details').open=true; document.getElementById('meta-model').focus()");
  await key("Enter", 13); // May save metadata through the form, but must never send remote input.
  assert.equal(await evaluate("uiCalls.filter(c=>c.url.includes('/key/')).length"), 7);
  await evaluate("document.getElementById('views').focus()");
  await key("ArrowUp", 38);
  assert.equal(await evaluate("uiCalls.filter(c=>c.url.includes('/key/')).length"), 8, "Focused capture surface shortcut");
  await evaluate("window.uiDelay=500; document.getElementById('remote').focus()");
  await key("ArrowRight", 39); await key("Enter", 13);
  await wait(550);
  assert.equal(await evaluate("uiCalls.filter(c=>c.url.includes('/key/')).length"), 9, "Busy command must not grow a queue");
  await evaluate("window.uiFailed=true");
  await wait(1200); await key("Backspace", 8);
  assert.equal(await evaluate("uiCalls.filter(c=>c.url.includes('/key/')).length"), 9, "Disconnected shortcut must not send");
  assert.equal(await evaluate("document.getElementById('error').textContent.includes('disconnected')"), true);
  assert.equal(await evaluate("document.querySelectorAll('img').length"), 0);
  console.error("Isolated browser: offline rendering, narrow layout, focus/editing, Enter/Space single dispatch, backpressure and disconnect passed. UI fixture only.");
} finally {
  await rpc("Page.close").catch(() => {});
  socket.close();
}
