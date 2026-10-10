const token = location.hash.slice(1);
history.replaceState(null, "", location.pathname);
const $ = (id) => document.getElementById(id);
let state = { sources: [] }, busy = false, active = "", stopped = false;
const urls = new Map();
const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
const fields = ["model", "year", "os_version", "firmware"];
for (const name of fields) {
  const label = document.createElement("label"); label.textContent = name.replaceAll("_", " ");
  const input = document.createElement("input"); input.type = "text"; input.maxLength = 100; input.id = `meta-${name}`;
  const provenance = document.createElement("select"); provenance.id = `from-${name}`;
  provenance.setAttribute("aria-label", `${name} provenance`);
  for (const value of ["unknown", "user-provided"]) provenance.add(new Option(value, value));
  label.append(input, provenance); $("fields").append(label);
}
function error(message) { $("error").hidden = !message; $("error").textContent = message || ""; }
async function request(path, data) {
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(path, { method: data === undefined ? "GET" : "POST", headers,
      body: data === undefined ? undefined : JSON.stringify(data), signal: controller.signal });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || "Local request failed");
    return result;
  } finally { clearTimeout(timeout); }
}
function selected() { return state.sources.find((s) => s.id === active); }
function editMetadata() {
  const source = selected(); if (!source) return;
  for (const name of fields) {
    $(`meta-${name}`).value = source.metadata[name]?.value || "";
    $(`from-${name}`).value = source.metadata[name]?.provenance === "unknown" ? "unknown" : "user-provided";
  }
}
async function command(operation, data) {
  if (operation === "stream" && data.enabled === false) {
    try { await request(`/api/stream/${active}`, data); await refresh(); }
    catch (problem) { error(`${problem.message}. Pause was not confirmed.`); }
    return;
  }
  if (busy) return; // No queue, repeat dispatch or unattended input.
  if (operation === "key" && document.activeElement?.closest("#remote")) $("remote").focus();
  if (operation === "click") $("views").focus();
  busy = true; error(""); $("status").textContent = `${operation} in flight…`; controls();
  try {
    const completed = await request(`/api/${operation}/${active}`, data);
    if (["key", "click"].includes(operation) && selected()?.pixels_allowed) {
      await refresh(); // Acknowledge measured input before requesting costly pixels.
      $("status").textContent = `Input completed in ${completed.command_ms} ms; capturing fresh paint layers…`;
      controls();
      await request(`/api/capture/${active}`, {});
    }
  }
  catch (problem) { error(`${problem.message}. No automatic retry was sent.`); }
  finally { busy = false; await refresh(); }
}
function controls() {
  const source = selected(), ready = source && ["connected", "in-flight"].includes(source.status) && source.expires_in > 0;
  $("connect").disabled = busy || ready || !$("authorized").checked;
  $("disconnect").disabled = busy || !ready;
  $("capture").disabled = busy || !ready || !source.pixels_allowed;
  $("stream").disabled = !ready || !source.pixels_allowed || (busy && !source.streaming);
  document.querySelectorAll("[data-key]").forEach((button) => {
    button.disabled = busy || !ready || !source.input_allowed || (button.dataset.key === "ok" && !source.activation_allowed);
  });
  $("duration").max = source?.run_limit_seconds || 120;
  $("stream").textContent = source?.streaming ? "Pause feed" : "Start feed";
  $("compare").disabled = state.sources.length < 2;
}
async function image(source, index, frame) {
  const key = `${source.id}/${index}/${source.revision}`;
  let url = urls.get(key);
  if (!url) {
    const response = await fetch(`/api/frame/${key}`, { headers, signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("Capture became stale while loading. Refresh it.");
    if (Number(response.headers.get("Content-Length")) > 8 * 1024 * 1024) throw new Error("Frame exceeds payload limit.");
    const blob = await response.blob();
    if (blob.size > 8 * 1024 * 1024) throw new Error("Frame exceeds payload limit.");
    url = URL.createObjectURL(blob); urls.set(key, url);
  }
  return url;
}
let rendering = false;
async function render() {
  if (rendering) return;
  rendering = true;
  try {
    const source = selected();
    if (source && !busy) $("status").textContent = `${source.status}; auto-disconnect in ${source.expires_in}s (not lease TTL). ` +
      `Input RTT ${source.command_ms ?? "—"} ms; capture ${source.capture_ms ?? "—"} ms. ${source.error || ""}`;
    $("identity").textContent = source ? `${source.kind === "tv" ? "Actual TV" : "Explicit emulator"} · ${source.id}` : "";
    controls();
    const views = $("views"); views.classList.toggle("side", $("compare").checked);
    const next = document.createDocumentFragment(), keep = new Set();
    const shown = $("compare").checked ? state.sources : state.sources.filter((s) => s.id === active);
    for (const s of shown) {
      const section = document.createElement("section"); section.className = "screen";
      const heading = document.createElement("h2"); heading.textContent = `${s.kind === "tv" ? "Actual TV" : "Emulator"} · ${s.id}`;
      const details = document.createElement("p");
      details.textContent = fields.map((f) => `${f.replaceAll("_", " ")}: ${s.metadata[f]?.value || "unknown"} (${s.metadata[f]?.provenance || "unknown"})`).join(" · ");
      const age = document.createElement("p"); age.className = "stale";
      age.textContent = s.captured_at ? `${s.stale ? "STALE — " : ""}Captured ${new Date(s.captured_at * 1000).toLocaleTimeString()} · age ${Math.max(0, Math.floor(Date.now() / 1000 - s.captured_at))}s` : "No native capture yet";
      section.append(heading, details, age);
      if (!s.frames.length) {
        const empty = document.createElement("div"); empty.className = "empty";
        empty.textContent = "Disconnected sources never show emulated or synthetic fallback pixels. Connect and authorize pixels, then Refresh frame or Start feed.";
        section.append(empty);
      }
      for (let i = 0; i < s.frames.length; i++) {
        const frame = s.frames[i], figure = document.createElement("figure"), img = document.createElement("img");
        keep.add(`${s.id}/${i}/${s.revision}`);
        img.src = await image(s, i, frame);
        img.alt = `${s.kind} native renderer paint layer ${i + 1}; may be incomplete`;
        img.width = frame.width; img.height = frame.height;
        const caption = document.createElement("figcaption");
        caption.textContent = `${s.provenance} · layer ${i + 1} · ${frame.width} × ${frame.height}. ` +
          (frame.clickable && !s.stale && s.activation_allowed ? "Fresh coordinate mapping available for audited controls." : "Coordinate input unavailable on this layer.");
        img.addEventListener("click", (event) => {
          if (s.id !== active || busy || !s.input_allowed || s.stale || !frame.clickable || !s.activation_allowed) return;
          const rect = img.getBoundingClientRect();
          const point = coordinate(rect, frame.width, frame.height, event.clientX, event.clientY);
          if (point) command("click", { frame: i, revision: s.revision, ...point });
        });
        figure.append(img, caption); section.append(figure);
      }
      const limits = document.createElement("p"); limits.className = "note"; limits.textContent = s.limitations;
      section.append(limits); next.append(section);
    }
    views.replaceChildren(next);
    for (const [key, url] of urls) if (!keep.has(key)) { URL.revokeObjectURL(url); urls.delete(key); }
  } catch (problem) {
    error(problem.message);
    document.querySelectorAll(".stale").forEach((label) => { label.textContent = "STALE — frame loading failed; last image retained."; });
  }
  finally { rendering = false; }
}
let polling = false;
async function refresh() {
  if (polling || stopped) return;
  polling = true;
  try {
    state = await request("/api/state");
    if (!active) {
      for (const source of state.sources) $("source").add(new Option(`${source.kind} · ${source.id}`, source.id));
      active = state.sources.find((s) => s.kind === "tv")?.id || state.sources[0]?.id || "";
      $("source").value = active; editMetadata();
    }
    await render();
  } catch (problem) {
    error(`Service disconnected: ${problem.message}. Controls are disabled; no fallback frame.`);
    state.sources.forEach((source) => { source.status = "disconnected"; source.stale = true; source.input_allowed = false; });
    document.querySelectorAll("[data-key]").forEach((button) => { button.disabled = true; });
    document.querySelectorAll(".stale").forEach((label) => { label.textContent = "STALE — local service disconnected. Last frame retained."; });
    for (const id of ["connect", "disconnect", "capture", "stream"]) $(id).disabled = true;
  } finally { polling = false; }
}
$("source").addEventListener("change", () => { active = $("source").value; editMetadata(); render(); });
$("authorized").addEventListener("change", controls);
$("connect").addEventListener("click", () => command("connect", { authorized: $("authorized").checked, pixels: $("pixels").checked, input: $("input").checked, run_seconds: Number($("duration").value) }));
$("disconnect").addEventListener("click", () => command("disconnect", {}));
$("capture").addEventListener("click", () => command("capture", {}));
$("stream").addEventListener("click", () => command("stream", { enabled: !selected().streaming, interval: Number($("interval").value) }));
$("compare").addEventListener("change", render);
document.querySelectorAll("[data-key]").forEach((button) => button.addEventListener("click", () => command("key", { key: button.dataset.key })));
function keyboard(event) {
  if (!handledShortcut(event)) return;
  event.preventDefault(); // Suppress native button Space/Enter, repeats and browser Back even while busy/disconnected.
  const s = selected(), ready = s?.status === "connected" && s.expires_in > 0 && s.input_allowed;
  const key = shortcut(event, ready, busy);
  if (!key) return;
  if (key === "ok" && !s.activation_allowed) { error("OK needs audited non-write control IDs."); return; }
  command("key", { key });
}
$("remote").addEventListener("keydown", keyboard);
$("views").addEventListener("keydown", keyboard);
$("metadata").addEventListener("submit", (event) => {
  event.preventDefault(); const values = {};
  for (const name of fields) values[name] = { value: $(`meta-${name}`).value, provenance: $(`from-${name}`).value };
  command("metadata", values);
});
window.addEventListener("pagehide", () => {
  stopped = true; for (const url of urls.values()) URL.revokeObjectURL(url);
  // Intentional teardown only; no device input in unload.
  for (const source of state.sources) fetch(`/api/disconnect/${source.id}`, { method: "POST", headers,
    body: "{}", keepalive: true }).catch(() => {});
});
refresh(); setInterval(refresh, 1000);
import { shortcut, handledShortcut, coordinate } from "/controls.mjs";
