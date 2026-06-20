// Auto-spawn the Python engine when one isn't already running, so a single
// `groundcheck` launch is enough. Best-effort: if the engine can't be located or
// started (e.g. an npx install with no local engine/), we fall back to honest
// "unreachable" degradation rather than failing the server.
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { ENGINE_URL, engineReachable } from "./engine.js";

let child: ChildProcess | null = null;
let childError: string | null = null;

export interface EnsureResult {
  status: "reachable" | "spawned" | "disabled" | "not-found" | "failed";
  detail?: string;
}

// dist/spawn.js -> server/dist -> server -> repo/engine (and the tsx-dev variant).
function locateEngineDir(): string | null {
  const here = dirname(fileURLToPath(import.meta.url));
  const candidates = [
    process.env.GROUNDCHECK_ENGINE_DIR,
    resolve(here, "../../engine"), // from server/dist/
    resolve(here, "../engine"), // from server/src/ (tsx dev)
  ].filter((p): p is string => Boolean(p));
  for (const dir of candidates) {
    if (existsSync(resolve(dir, "groundcheck_engine/__main__.py"))) return dir;
  }
  return null;
}

function childEnv(): NodeJS.ProcessEnv {
  const url = new URL(ENGINE_URL);
  return {
    ...process.env,
    GROUNDCHECK_ENGINE_HOST: url.hostname,
    GROUNDCHECK_ENGINE_PORT: url.port || "8723",
  };
}

async function waitForHealth(tries = 25, delayMs = 300): Promise<boolean> {
  for (let i = 0; i < tries; i++) {
    if (childError) return false; // spawn failed (e.g. python missing) or engine exited
    if (await engineReachable()) return true;
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return false;
}

export async function ensureEngine(): Promise<EnsureResult> {
  if (await engineReachable()) return { status: "reachable" };
  if (process.env.GROUNDCHECK_NO_SPAWN) return { status: "disabled" };

  const engineDir = locateEngineDir();
  if (!engineDir) {
    return { status: "not-found", detail: "engine/ not found — set GROUNDCHECK_ENGINE_DIR or start it manually" };
  }

  const python = process.env.GROUNDCHECK_PYTHON ?? "python3";
  // stdio: never touch fd 1 — that's the MCP JSON-RPC channel. Engine logs -> our stderr.
  child = spawn(python, ["-m", "groundcheck_engine"], {
    cwd: engineDir,
    env: childEnv(),
    stdio: ["ignore", "ignore", "inherit"],
  });
  child.on("error", (e) => {
    childError = String(e);
  });
  child.on("exit", (code) => {
    if (code !== 0 && childError === null) childError = `engine exited early (code ${code})`;
  });

  const stop = () => {
    if (child && child.exitCode === null) {
      try {
        child.kill("SIGTERM");
      } catch {
        /* already gone */
      }
    }
  };
  process.once("exit", stop);
  process.once("SIGINT", () => {
    stop();
    process.exit(0);
  });
  process.once("SIGTERM", () => {
    stop();
    process.exit(0);
  });

  if (await waitForHealth()) {
    return { status: "spawned", detail: `${python} -m groundcheck_engine @ ${engineDir}` };
  }
  stop();
  return {
    status: "failed",
    detail: childError ?? `engine did not become healthy (python: ${python}). Try: make install`,
  };
}
