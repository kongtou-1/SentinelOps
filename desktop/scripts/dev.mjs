import { spawn, execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import electron from "electron";

const directory = fileURLToPath(new URL("../", import.meta.url));
const ui = spawn("corepack pnpm --dir ../frontend dev:desktop", {
  cwd: directory,
  shell: true,
  stdio: "inherit",
});
let client;
function stop() {
  for (const child of [client, ui]) {
    if (!child?.pid || child.exitCode !== null) continue;
    if (process.platform === "win32") {
      try {
        execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
          stdio: "ignore",
          windowsHide: true,
        });
      } catch {
        /* Already stopped. */
      }
    } else child.kill();
  }
}
process.on("exit", stop);
process.on("SIGINT", () => process.exit());
process.on("SIGTERM", () => process.exit());
let ready = false;
for (let attempt = 0; attempt < 60; attempt++) {
  if (ui.exitCode !== null) throw new Error("桌面前端开发服务启动失败");
  try {
    const response = await fetch("http://127.0.0.1:5174");
    if (response.ok) {
      ready = true;
      break;
    }
  } catch {
    /* Wait for Vite. */
  }
  await new Promise((resolve) => setTimeout(resolve, 500));
}
if (!ready) throw new Error("桌面前端开发服务启动超时");
const environment = { ...process.env };
delete environment.ELECTRON_RUN_AS_NODE;
client = spawn(electron, [directory, "--dev"], {
  env: environment,
  stdio: "inherit",
});
client.on("exit", (code) => process.exit(code || 0));
