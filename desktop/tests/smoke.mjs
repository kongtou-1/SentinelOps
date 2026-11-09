import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { chromium } from "playwright-core";
import electron from "electron";

const root = fileURLToPath(new URL("../../", import.meta.url));
const output = new URL("../../.local/desktop-acceptance/", import.meta.url);
const profile = process.env.SENTINELOPS_SMOKE_PROFILE_ROOT
  ? new URL(`file:///${process.env.SENTINELOPS_SMOKE_PROFILE_ROOT.replaceAll("\\", "/")}/run-${process.pid}/`)
  : new URL(`run-${process.pid}/`, output);
const fixture = JSON.parse(
  await readFile(new URL("backend.json", output), "utf8"),
);
assert.match(new URL(fixture.url).hostname, /^(localhost|127\.0\.0\.1)$/);
await mkdir(output, { recursive: true });
await mkdir(new URL("webview/", profile), { recursive: true });
await mkdir(new URL("config/", profile), { recursive: true });
await mkdir(new URL("temp/", profile), { recursive: true });
const childEnvironment = {
  ...process.env,
  SENTINELOPS_DESKTOP_TEST_DIR: fileURLToPath(profile),
  TEMP: fileURLToPath(new URL("temp/", profile)),
  TMP: fileURLToPath(new URL("temp/", profile)),
};
delete childEnvironment.ELECTRON_RUN_AS_NODE;
const executable = process.env.SENTINELOPS_SMOKE_APP || electron;
const args = process.env.SENTINELOPS_SMOKE_APP
  ? []
  : [fileURLToPath(new URL("../", import.meta.url))];
args.push("--remote-debugging-port=19222");
const child = spawn(executable, args, {
  cwd: process.env.SENTINELOPS_SMOKE_APP ? path.dirname(executable) : root,
  env: childEnvironment,
  stdio: ["ignore", "pipe", "pipe"],
});
let processLog = "";
child.stdout.on("data", (b) => {
  processLog += b;
});
child.stderr.on("data", (b) => {
  processLog += b;
});
child.on("error", (e) => {
  processLog += String(e);
});
let browser;
const checks = [];
try {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null)
      throw new Error(
        `Desktop exited with code ${child.exitCode}: ${processLog}`,
      );
    try {
      browser = await chromium.connectOverCDP("http://127.0.0.1:19222", {
        timeout: 1000,
      });
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  assert.ok(browser, "Desktop did not expose its test WebView");
  const page = browser.contexts()[0].pages()[0];
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  await page.waitForSelector("#app h1");
  assert.equal(new URL(page.url()).hostname, "app");
  await page.screenshot({
    path: fileURLToPath(new URL("connection.png", output)),
  });

  await page.evaluate(() => {
    window.location.hash = "#/connection";
  });
  await page.getByPlaceholder("https://ops.example.com").fill(fixture.url);
  await page.getByRole("button", { name: "测试连接", exact: true }).click();
  await page.getByText("连接成功", { exact: true }).waitFor();
  await page.getByRole("button", { name: "保存并连接", exact: true }).click();
  await page.getByRole("heading", { name: "登录运维平台" }).waitFor();
  checks.push("runtime server configuration and real backend readiness");

  async function login(role) {
    await page
      .getByPlaceholder("例如 admin")
      .fill(fixture.users[role].username);
    await page.locator('input[type="password"]').fill(fixture.password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await page.waitForFunction(
      () =>
        !window.location.hash.startsWith("#/login") &&
        !!document.querySelector(".account-button"),
    );
  }
  async function logout() {
    await page.locator(".account-button").click();
    await page.getByRole("menuitem", { name: "退出登录" }).click();
    await page.getByRole("heading", { name: "登录运维平台" }).waitFor();
  }
  async function request(path, body = null, method = "GET") {
    return page.evaluate(
      async ({ path, body, method }) => {
        // Exercise the same IPC commands used by the compiled transport; no product test API.
        const invoke = (command, args) => window.sentinelDesktop[command](args);
        const { epoch } = await invoke("load_server");
        const token = JSON.parse(sessionStorage.getItem("sentinelops.session"));
        const id = crypto.randomUUID();
        const headers = token
          ? [["authorization", `Bearer ${token.access_token}`]]
          : [];
        if (body) headers.push(["content-type", "application/json"]);
        await invoke("request_open", {
          id,
          epoch,
          path,
          method,
          headers,
          body: body ? Array.from(new TextEncoder().encode(body)) : null,
        });
        const head = await invoke("request_send", { id });
        let text = "";
        const decoder = new TextDecoder();
        while (true) {
          const bytes = await invoke("request_read", { id });
          if (bytes === null) break;
          text += decoder.decode(new Uint8Array(bytes), { stream: true });
        }
        return { status: head.status, text };
      },
      { path, body, method },
    );
  }

  await login("admin");
  assert.equal((await request("/api/v1/users")).status, 200);
  for (const path of [
    "/servers",
    "/alerts",
    "/incidents",
    "/predictions",
    "/inspections",
    "/approvals",
    "/reports",
  ]) {
    await page.evaluate((path) => {
      window.location.hash = "#" + path;
    }, path);
    await page.waitForFunction(
      (path) =>
        window.location.hash === "#" + path &&
        !!document.querySelector("main h1"),
      path,
    );
    assert.equal(await page.locator(".el-message--error").count(), 0, path);
  }
  await page.screenshot({
    path: fileURLToPath(new URL("platform.png", output)),
  });
  checks.push("admin login and core module navigation");

  await page.evaluate(() => {
    const token = JSON.parse(sessionStorage.getItem("sentinelops.session"));
    token.access_token = "expired-test-access-token";
    sessionStorage.setItem("sentinelops.session", JSON.stringify(token));
    window.location.hash = "#/servers";
  });
  await page.waitForFunction(
    () =>
      JSON.parse(sessionStorage.getItem("sentinelops.session"))
        ?.access_token !== "expired-test-access-token",
  );
  checks.push("real 401 refresh through native transport");

  const frame = await page.evaluate(async (conversation) => {
    const invoke = (command, args) => window.sentinelDesktop[command](args);
    const { epoch } = await invoke("load_server");
    const token = JSON.parse(sessionStorage.getItem("sentinelops.session"));
    const id = crypto.randomUUID();
    await invoke("request_open", {
      id,
      epoch,
      path: `/api/v1/analysis-conversations/${conversation}/stream?after_seq=0`,
      method: "GET",
      headers: [["authorization", `Bearer ${token.access_token}`]],
      body: null,
    });
    const head = await invoke("request_send", { id });
    let text = "";
    const decoder = new TextDecoder();
    while (!text.includes("第二段恢复结果")) {
      const bytes = await invoke("request_read", { id });
      if (bytes === null) break;
      text += decoder.decode(new Uint8Array(bytes), { stream: true });
    }
    await invoke("request_cancel", { id });
    return { status: head.status, text };
  }, fixture.conversation);
  assert.equal(frame.status, 200);
  assert.ok(
    frame.text.includes("第一段中文证据") &&
      frame.text.includes("event: event"),
  );
  checks.push("real authenticated SSE frames and cancellation");

  await page.getByRole("link", { name: "项目介绍" }).click();
  await page.waitForURL("**#/project-intro");
  assert.equal(browser.contexts()[0].pages().length, 1);
  await page.evaluate(() => {
    window.location.hash = "#/discover/skills";
  });
  await page.getByRole("link", { name: "返回运维平台" }).waitFor();
  assert.equal(new URL(page.url()).hostname, "app");
  await page.evaluate(() => {
    window.location.hash = "#/servers";
  });
  await page.locator(".account-button").waitFor();
  checks.push(
    "internal introduction and resource centre stay in the desktop window",
  );

  await logout();
  for (const role of ["operator", "viewer"]) {
    await login(role);
    assert.equal((await request("/api/v1/users")).status, 403);
    assert.equal((await request("/api/v1/servers")).status, 200);
    await logout();
  }
  checks.push("operator and viewer server-side permission boundaries");
  await login("admin");
  await page.getByRole("link", { name: "服务器连接", exact: true }).click();
  await page
    .getByPlaceholder("https://ops.example.com")
    .fill("http://localhost:18092");
  await page.getByRole("button", { name: "保存并连接" }).click();
  await page.getByRole("button", { name: "更换服务器", exact: true }).click();
  await page.getByRole("heading", { name: "登录运维平台" }).waitFor();
  assert.equal(
    await page.evaluate(() => sessionStorage.getItem("sentinelops.session")),
    null,
  );
  const saved = JSON.parse(
    await readFile(new URL("config/connection.json", profile), "utf8"),
  );
  assert.equal(saved.url, "http://localhost:18092");
  checks.push("server switch clears session and persists runtime URL");
  assert.deepEqual(pageErrors, []);
  const report = {
    passed: checks.length,
    checks,
    nativePage: page.url(),
    testDatabase: "sentinelops_desktop_test",
  };
  await writeFile(
    new URL("smoke.json", output),
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify(report, null, 2));
} finally {
  await writeFile(new URL("desktop.log", output), processLog);
  if (browser) await browser.close();
  child.kill();
}
