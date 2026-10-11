// Scaffolded via extensions_manage; domain state is a private profile, not instanceId.
import { joinSession, createCanvas, CanvasError } from "@github/copilot-sdk/extension";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const folder = path.dirname(fileURLToPath(import.meta.url));
const backend = existsSync(path.join(folder, "backend/live_tv.py"))
  ? path.join(folder, "backend/live_tv.py") : path.resolve(folder, "../../../tools/live_tv.py");
const entries = new Map();
const owners = new Map();
const starting = new Map();

async function start(profilePath) {
  const child = spawn("python3", [backend, "--profile", profilePath, "--owner-stdin"], {
    detached: true, stdio: ["pipe", "pipe", "pipe"],
  });
  return await new Promise((resolve, reject) => {
    let buffer = "", settled = false;
    const timeout = setTimeout(() => {
      if (!settled) { settled = true; terminate(child); reject(new CanvasError("start_timeout", "Local inspector service did not start")); }
    }, 5000);
    child.on("error", () => {
      if (!settled) { settled = true; clearTimeout(timeout); reject(new CanvasError("service_failed", "Python 3.10+ and a valid private profile are required")); }
    });
    child.once("exit", () => {
      if (!settled) { settled = true; clearTimeout(timeout); reject(new CanvasError("profile_invalid", "Service refused the private profile; check schema, ownership and permissions")); }
      entries.delete(profilePath);
    });
    child.stderr.on("data", () => {}); // Do not copy private profiles/endpoint data into logs.
    child.stdout.on("data", (chunk) => {
      if (settled) return;
      buffer += chunk.toString();
      if (buffer.length > 4096) { settled = true; clearTimeout(timeout); terminate(child); reject(new CanvasError("protocol", "Startup reply exceeds limit")); return; }
      if (!buffer.includes("\n")) return;
      try {
        const { url } = JSON.parse(buffer.split("\n")[0]);
        if (!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(url)) throw new Error();
        settled = true; clearTimeout(timeout);
        resolve({ child, url, refs: new Set() });
      } catch {
        settled = true; clearTimeout(timeout); terminate(child);
        reject(new CanvasError("protocol", "Local service returned an invalid URL"));
      }
    });
  });
}

function terminate(child) {
  if (!child.pid || child.exitCode !== null) return;
  child.stdin.end(); // Python observes EOF if the provider dies before its signal cleanup.
  try { process.kill(-child.pid, "SIGTERM"); } catch (error) { if (error.code !== "ESRCH") throw error; }
  const timer = setTimeout(() => {
    if (child.exitCode === null) try { process.kill(-child.pid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; }
  }, 35000); // Allow bounded RPC/snapshot cleanup before stopping the owned process.
  timer.unref();
  child.once("exit", () => clearTimeout(timer));
}

async function close(instanceId) {
  const profile = owners.get(instanceId); owners.delete(instanceId);
  const entry = entries.get(profile); if (!entry) return;
  entry.refs.delete(instanceId);
  if (!entry.refs.size) { entries.delete(profile); terminate(entry.child); }
}

await joinSession({
  canvases: [createCanvas({
    id: "tizen-live", displayName: "Live TV inspector",
    description: "User-operated TV portal: scan the network for Samsung TVs, connect, identify, remote and live renderer view; no fake fallback.",
    inputSchema: { type: "object", additionalProperties: false, required: ["profilePath"],
      properties: { profilePath: { type: "string", minLength: 1 } } },
    actions: [{
      name: "status",
      description: "Read scanned TVs, connection, capture age/provenance, metadata and capabilities; no device commands or secrets.",
      inputSchema: { type: "object", additionalProperties: false },
      handler: async (ctx) => {
        const entry = entries.get(owners.get(ctx.instanceId));
        if (!entry) throw new CanvasError("disconnected", "Open the offline canvas first");
        const address = new URL(entry.url);
        try {
          const response = await fetch(`${address.origin}/api/state`, {
            signal: AbortSignal.timeout(3000),
          });
          if (!response.ok) throw new Error();
          const state = await response.json();
          return state;
        } catch {
          throw new CanvasError("service_unavailable", "Local status service is unavailable");
        }
      },
    }], // Mutation/capture are user-only HTTP controls.
    open: async (ctx) => {
      const profile = ctx.input.profilePath;
      if (!path.isAbsolute(profile)) throw new CanvasError("profile_invalid", "Use an absolute private profile path");
      if (owners.has(ctx.instanceId) && owners.get(ctx.instanceId) !== profile) await close(ctx.instanceId);
      let entry = entries.get(profile);
      if (!entry) {
        let pending = starting.get(profile);
        if (!pending) {
          pending = start(profile).then((result) => { entries.set(profile, result); return result; })
            .finally(() => starting.delete(profile));
          starting.set(profile, pending);
        }
        entry = await pending;
      }
      entry.refs.add(ctx.instanceId); owners.set(ctx.instanceId, profile);
      return { title: "Live TV inspector", url: entry.url, status: "Choose Scan network for TVs, then Connect on a TV card" };
    },
    onClose: async (ctx) => close(ctx.instanceId),
  })],
});
for (const sig of ["SIGTERM", "SIGINT"]) process.on(sig, () => {
  for (const entry of entries.values()) terminate(entry.child);
  entries.clear();
  setTimeout(() => process.exit(0), 36000).unref();
});
