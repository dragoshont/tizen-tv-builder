// Optional existing-Chromium gate. All /api calls are intercepted by host-only fixtures.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const [profile, url] = process.argv.slice(2);
if (!profile || !url) throw new Error("Provide an isolated Chromium profile and local workbench URL");
const [port] = (await readFile(`${profile}/DevToolsActivePort`, "utf8")).split("\n");
const page = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" })).json();
const socket = new WebSocket(page.webSocketDebuggerUrl), pending = new Map();
let id = 0;
socket.addEventListener("message", (event) => {
  const reply = JSON.parse(event.data), entry = pending.get(reply.id);
  if (!entry) return;
  pending.delete(reply.id); clearTimeout(entry.timer);
  if (reply.error) entry.reject(new Error(reply.error.message)); else entry.resolve(reply.result);
});
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve); socket.addEventListener("error", reject);
});
function rpc(method, params = {}) {
  return new Promise((resolve, reject) => {
    const next = ++id, timer = setTimeout(() => { pending.delete(next); reject(new Error(method)); }, 5000);
    pending.set(next, { resolve, reject, timer }); socket.send(JSON.stringify({ id: next, method, params }));
  });
}
async function evaluate(expression) {
  const result = await rpc("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error("Browser fixture assertion failed");
  return result.result.value;
}
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function key(name, code) {
  await rpc("Input.dispatchKeyEvent", { type: "keyDown", key: name, windowsVirtualKeyCode: code });
  await rpc("Input.dispatchKeyEvent", { type: "keyUp", key: name, windowsVirtualKeyCode: code });
  await wait(250);
}
try {
  await rpc("Page.enable");
  await rpc("Page.addScriptToEvaluateOnNewDocument", { source: `
    window.uiCalls = []; window.uiFailed = false; window.uiRecording = null;
    window.uiSource = {id:"fixture",kind:"tv",metadata:{},status:"connected",expires_in:120,
      input_allowed:true,pixels_allowed:false,activation_allowed:true,extended_input:true,
      remote_keys:["play","digit-1"],frames:[],stale:true,streaming:false,provenance:"HOST UI FIXTURE ONLY"};
    window.fetch = async function(path, options = {}) {
      if (!String(path).startsWith("/api/")) throw new Error("Only fixture APIs allowed");
      if (uiFailed) throw new Error("fixture connection lost");
      let data;
      if (path === "/api/setup") {
        if(options.method === "POST") uiCalls.push({path,body:JSON.parse(options.body)});
        data = {sdb:true,tizen:true,platform:"HOST FIXTURE",page_count:1,
          configured:true,devices:[],apps:[],forward_owned:false};
      }
      else if (path === "/api/record") {
        if (options.method === "POST") {
          const body=JSON.parse(options.body); uiCalls.push({path,body});
          uiRecording={state:body.operation==="start"?"recording":"stopped",frames:2,length:1};
          data=body.operation==="start"?uiRecording:{...uiRecording,output_path:"HOST-FIXTURE-ONLY"};
        } else data = {recording:uiRecording,encoder:{png:true,available:false},default_parent:"/HOST-FIXTURE-ONLY"};
      }
      else if (path === "/api/state") data = {sources:[uiSource]};
      else {uiCalls.push({path,body:JSON.parse(options.body)}); data=uiSource;}
      return new Response(JSON.stringify(data), {headers:{"Content-Type":"application/json"}});
    };` });
  await rpc("Page.navigate", { url }); await wait(1200);
  assert.equal(await evaluate("document.querySelectorAll('[data-key]').length"), 39);
  await evaluate("document.querySelector('[data-key=ok]').focus()");
  await key("Enter", 13);
  await key(" ", 32);
  assert.equal(await evaluate("uiCalls.filter(c=>c.body.key==='ok').length"), 2, "Single dispatch and focus retained");
  await evaluate("document.querySelector('[data-key=play]').click()");
  await wait(250);
  assert.equal(await evaluate("uiCalls.filter(c=>c.body.key==='play').length"), 1);
  assert.equal(await evaluate("document.querySelector('[data-key=power]').disabled"), true);
  await evaluate("document.querySelector('#remote').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',repeat:true,bubbles:true,cancelable:true}))");
  assert.equal(await evaluate("uiCalls.filter(c=>c.body.key==='ok').length"), 2);
  await evaluate("uiSource.pixels_allowed=true;document.querySelector('#save-grant').checked=true");
  await wait(1200);
  await evaluate("document.querySelector('#record').click()"); await wait(300);
  assert.equal(await evaluate("uiCalls.filter(c=>c.path==='/api/record'&&c.body.operation==='start').length"), 1);
  assert.equal(await evaluate("document.querySelector('#capture').disabled"), true);
  await evaluate("document.querySelector('#record').click()"); await wait(300);
  assert.equal(await evaluate("uiCalls.filter(c=>c.path==='/api/record'&&c.body.operation==='stop').length"), 1);
  await evaluate("uiFailed=true"); await wait(1500);
  assert.equal(await evaluate("document.querySelector('[data-key=up]').disabled"), true);
  for (const width of [1440, 390]) {
    await rpc("Emulation.setDeviceMetricsOverride", { width, height: 1000, deviceScaleFactor: 1, mobile: false });
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
  }
  await evaluate("uiFailed=false;uiSource.status='disconnected';uiSource.expires_in=0");
  await wait(1200);
  assert.equal(await evaluate("document.querySelector('#sdk-grant') === null"), true);
  assert.equal(await evaluate("document.querySelector('#find-network').disabled"), false);
  await evaluate("document.querySelector('#find-network').click()");
  await wait(300);
  assert.equal(await evaluate("uiCalls.filter(c=>c.path==='/api/setup'&&c.body.operation==='find-network'&&c.body.authorized===true).length"), 1);
  console.log("Workbench fixture: keyboard, media route, repeat, disconnect and responsive contracts passed. No TV called.");
} finally {
  socket.close();
  await fetch(`http://127.0.0.1:${port}/json/close/${page.id}`);
}
