// Optional portal regression gate using an existing, isolated Chromium/Edge browser.
// Usage: node tests/portal-browser.test.mjs <browser CDP URL, e.g. http://127.0.0.1:9223> <portal URL>
// Every /api/ request is answered by synthetic fixtures inside the page; no TV is contacted.
// PORTAL_BASELINE=<git revision> replays that revision's ui.js to prove a regression fails there.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const [cdpUrl, portalUrl] = process.argv.slice(2);
if (!cdpUrl || !portalUrl) throw new Error("Provide an isolated browser CDP URL and the portal URL");
for (const value of [cdpUrl, portalUrl]) {
  const { protocol, hostname } = new URL(value);
  if (protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(hostname))
    throw new Error("Browser and portal URLs must be loopback http URLs");
}
const target = await (await fetch(new URL("/json/new?about:blank", cdpUrl), { method: "PUT" })).json();
const socket = new WebSocket(target.webSocketDebuggerUrl);
const pending = new Map();
const repo = fileURLToPath(new URL("..", import.meta.url));
const baseline = process.env.PORTAL_BASELINE ? execFileSync("git", [
  "-C", repo, "show", `${process.env.PORTAL_BASELINE}:.github/extensions/tizen-live/ui.js`
]).toString("base64") : null;
let sequence = 0;
socket.addEventListener("message", ({ data }) => {
  const reply = JSON.parse(data), entry = pending.get(reply.id);
  if (reply.method === "Fetch.requestPaused") {
    rpc("Fetch.fulfillRequest", { requestId: reply.params.requestId, responseCode: 200,
      responseHeaders: [{ name: "Content-Type", value: "text/javascript" }], body: baseline });
    return;
  }
  if (!entry) return;
  pending.delete(reply.id);
  clearTimeout(entry.timer);
  if (reply.error) entry.reject(new Error(reply.error.message));
  else entry.resolve(reply.result);
});
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve);
  socket.addEventListener("error", reject);
});
function rpc(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Timeout: ${method}`)); }, 10000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await rpc("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
const failures = [];
async function check(name, expression, expected) {
  try {
    assert.deepEqual(await evaluate(expression), expected);
    console.log(`PASS ${name}`);
  } catch (error) {
    failures.push(name);
    console.error(`FAIL ${name}: ${error.message}`);
  }
}
try {
  await rpc("Page.enable");
  if (baseline) await rpc("Fetch.enable", { patterns: [{ urlPattern: "*/ui.js" }] });
  await rpc("Page.addScriptToEvaluateOnNewDocument", { source: `
    window.testCalls = []; window.testOffline = false;
    if (!sessionStorage.getItem("seeded")) {
      sessionStorage.setItem("seeded", "1");
      localStorage.setItem("tizen-preview-app", "Leaked0001.FromAnotherTv");
    }
    const tv = (alias,name,status) => ({alias,name,status,held:true,metadata:{model:"HOST-FIXTURE",os:{text:"Tizen 5.0"}},
      dev_mode:{label:"Dev mode enabled",state:"enabled"},details:{},preview:{ready:false,input_allowed:false,activation_allowed:false,frames:[]}});
    window.testState = {sdk:"ready",scan:{status:"done",found:2},sources:[],
      tvs:[tv("tv-fixture-a","Fixture A","connected"),tv("tv-fixture-b","Fixture B","developer")]};
    const originalFetch=window.fetch.bind(window);
    window.fetch=async (url,options={})=>{
      if(!String(url).startsWith("/api/")) return originalFetch(url,options);
      if(window.testOffline) throw new Error("fixture offline");
      if(url==="/api/state") return new Response(JSON.stringify(window.testState),{headers:{"Content-Type":"application/json"}});
      if(String(url).startsWith("/api/frame/")) {
        if(window.testDelayFrame) await new Promise(r=>window.releaseFrame=r);
        const data=window.testValidImage?Uint8Array.from(atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6s8AAAAASUVORK5CYII="),c=>c.charCodeAt(0)):"not an image";
        return new Response(data,{headers:{"Content-Type":"image/png"}});
      }
      window.testCalls.push({url,body:options.body});
      throw new Error("Fixture prohibits all provider operations");
    };
  ` });
  await rpc("Page.navigate", { url: portalUrl });
  await evaluate("new Promise(r=>setTimeout(r,1400))");
  await check("A typed app ID from another TV is never pre-filled",
    "[...document.querySelectorAll('input[aria-label=\"App to preview\"]')].every(i=>i.value==='')", true);
  await check("The legacy global preview app key is removed",
    "localStorage.getItem('tizen-preview-app')", null);
  await check("No Connected label without a usable preview",
    "document.querySelector('.device:not([hidden]) .pill').textContent.includes('Connected')", false);
  await check("A connected TV with no app shows one status: No app open",
    "document.querySelector('.device:not([hidden]) .pill').textContent", "No app open");
  await check("The list row shows exactly the card's status",
    "[...document.querySelectorAll('.tv-row')].every(row=>{const d=[...document.querySelectorAll('.device')].find(x=>x.querySelector('h2').textContent===row.querySelector('.row-name').textContent);return d&&row.querySelector('.row-status').textContent===d.querySelector('.pill').textContent;})", true);
  await check("No second developer-mode status is shown anywhere",
    "/Dev mode enabled|Developer Mode ready|Preview not connected/.test(document.body.innerText)", false);
  await check("Missing preview has visible guidance",
    "document.querySelector('.device:not([hidden]) .placeholder p').textContent.trim().length>0", true);
  await check("Missing preview keeps all keys disabled",
    "[...document.querySelector('.device:not([hidden]) .remote').querySelectorAll('button')].every(b=>b.disabled)", true);
  await evaluate("window.testState.tvs[0].error='Fixture TV accepted the connection but refused commands';window.testState.tvs[0].guidance={kind:'host-ip',title:'Check the TV\\'s Host PC IP',steps:[]};new Promise(r=>setTimeout(r,2600))");
  await check("A held link with an error is a connection problem, never No app open",
    "document.querySelector('.device:not([hidden]) .pill').textContent", "Connection problem");
  await check("The connection problem says what failed",
    "document.querySelector('.device:not([hidden])').innerText.includes('refused commands')", true);
  await evaluate("delete window.testState.tvs[0].error;delete window.testState.tvs[0].guidance;new Promise(r=>setTimeout(r,2600))");
  await evaluate("document.querySelectorAll('.tv-row')[1].click()");
  await check("Selected-TV isolation", "document.querySelectorAll('.device:not([hidden])').length", 1);
  await check("Selection sends no provider commands", "window.testCalls.length", 0);
  await check("Preview action is available after developer connection",
    "[...document.querySelector('.device:not([hidden])').querySelectorAll('button')].some(b=>b.getClientRects().length&&!b.disabled&&/preview/i.test(b.textContent))", true);
  await evaluate(`const pane=document.querySelector('.device:not([hidden])');
    pane.querySelector('input[aria-label="App to preview"]').value="Fixture.SafeApp";
    [...pane.querySelectorAll('button')].find(b=>/Open app for preview/.test(b.textContent)).click();`);
  await check("Preview restart requires explicit consent",
    "document.querySelector('#confirm').open&&document.querySelector('#confirm-text').textContent.includes('restarts')", true);
  await rpc("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", windowsVirtualKeyCode: 27 });
  await rpc("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", windowsVirtualKeyCode: 27 });
  await evaluate("new Promise(r=>setTimeout(r,100))");
  await check("Cancelling preview sends no provider commands", "window.testCalls.length", 0);
  await check("A failed or cancelled preview remembers no app ID",
    "Object.keys(localStorage).filter(k=>k.startsWith('tizen-preview-app')).length", 0);
  await evaluate(`window.testState.tvs[1].status="connected";
    window.testState.tvs[1].preview.ready=true;
    window.testState.tvs[1].preview.input_allowed=true;
    new Promise(r=>setTimeout(r,1400));`);
  await check("Empty captured frames cannot enable Connected",
    "document.querySelector('.device:not([hidden]) .pill').textContent.includes('Connected')", false);
  await check("Empty captured frames cannot enable remote input",
    "[...document.querySelector('.device:not([hidden]) .remote').querySelectorAll('button')].every(b=>b.disabled)", true);
  await check("Empty captured frames retain actionable guidance",
    "document.querySelector('.device:not([hidden]) .placeholder').getClientRects().length>0&&document.querySelector('.device:not([hidden]) .placeholder p').textContent.trim().length>0", true);
  await evaluate(`window.testState.tvs[1].preview.frames=[{width:64,height:64}];
    window.testState.tvs[1].preview.revision=1;
    new Promise(r=>setTimeout(r,1400));`);
  await check("Undecodable image cannot enable Connected",
    "document.querySelector('.device:not([hidden]) .pill').textContent.includes('Connected')", false);
  await check("Undecodable image cannot enable remote input",
    "[...document.querySelector('.device:not([hidden]) .remote').querySelectorAll('button')].every(b=>b.disabled)", true);
  await check("Undecodable image shows an actionable error",
    "/Could not display.*retry/.test(document.querySelector('.device:not([hidden]) .placeholder p').textContent)", true);
  await evaluate(`document.querySelector('.device:not([hidden]) .remote').dispatchEvent(
    new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true,cancelable:true}));`);
  await check("Undecodable image cannot dispatch keyboard input", "window.testCalls.length", 0);
  await evaluate(`window.testValidImage=true; window.testState.tvs[1].preview.revision=2;
    new Promise(r=>setTimeout(r,1400));`);
  await check("Decoded fixture image enables Connected",
    "document.querySelector('.device:not([hidden]) .pill').textContent==='Connected'", true);
  await check("Decoded fixture image is visible",
    "document.querySelector('.device:not([hidden]) img')?.naturalWidth>0", true);
  await check("Unaudited OK remains disabled with a decoded image",
    "document.querySelector('.device:not([hidden]) [data-key=ok]').disabled", true);
  for (const width of [1200, 390]) {
    await rpc("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: false });
    await check(`No horizontal overflow at ${width}px`, "document.documentElement.scrollWidth<=innerWidth", true);
  }
  await evaluate(`window.testDelayFrame=true; window.testState.tvs[1].preview.revision=3;
    new Promise(r=>setTimeout(r,1200));`);
  await check("Deferred frame fetch started", "typeof window.releaseFrame", "function");
  await evaluate(`window.testOffline=true; new Promise(r=>setTimeout(r,1200));`);
  await evaluate(`window.testDelayFrame=false; window.releaseFrame(); new Promise(r=>setTimeout(r,500));`);
  await check("Delayed image cannot restore Connected after disconnect",
    "document.querySelector('.device:not([hidden]) .pill').textContent.includes('Connected')", false);
  await evaluate(`document.querySelector('.device:not([hidden]) .remote').dispatchEvent(
    new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true,cancelable:true}));`);
  await check("Delayed image cannot restore keyboard input after disconnect", "window.testCalls.length", 0);
  await check("Offline state explicitly explains the lost connection",
    "document.body.innerText.includes('Connection lost')", true);
  await check("Offline cannot dispatch remote commands",
    "[...document.querySelector('.device:not([hidden]) .remote').querySelectorAll('button')].every(b=>b.disabled)", true);
  console.log("Synthetic browser fixtures only; no TV operations were performed.");
  if (failures.length) process.exitCode = 1;
} finally {
  await rpc("Page.close").catch(() => {});
  socket.close();
}
