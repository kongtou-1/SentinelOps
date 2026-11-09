import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { readFile } from "node:fs/promises";
import { Network, normalizeServer } from "../src/network.mjs";

async function fixture(t, handler) {
  const server = http.createServer(handler);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  return `http://127.0.0.1:${server.address().port}`;
}
let sequence = 0;
function open(network, path = "/api/v1/me", { token, body, epoch = 0 } = {}) {
  const id = `test-${sequence++}`;
  const headers = token ? [["authorization", `Bearer ${token}`]] : [];
  if (body) headers.push(["content-type", "application/json"]);
  network.open({
    id,
    epoch,
    path,
    method: body ? "POST" : "GET",
    headers,
    body: body ? Array.from(Buffer.from(JSON.stringify(body))) : null,
  });
  return id;
}
async function request(network, path, options) {
  const id = open(network, path, options),
    head = await network.send(id);
  const chunks = [];
  let chunk;
  while ((chunk = await network.read(id)) !== null)
    chunks.push(Buffer.from(chunk));
  return { ...head, text: Buffer.concat(chunks).toString("utf8") };
}

test("root URL, API path and header restrictions", () => {
  assert.equal(
    normalizeServer(" https://OPS.example.com:443/ "),
    "https://ops.example.com",
  );
  for (const value of [
    "file:///a",
    "https://u:p@host",
    "http://host/path",
    "http://host?x=1",
    "http://host#x",
  ])
    assert.throws(() => normalizeServer(value));
  const network = new Network(fetch, "http://localhost:8000");
  for (const path of [
    "//other/api/v1/me",
    "https://other/api/v1/me",
    "/api/v1/../../etc",
    "/api/v1/%2e%2e/%2e%2e/etc",
    "/etc",
    "/api/v1/me#x",
  ])
    assert.throws(() => open(network, path));
  assert.throws(() =>
    network.open({
      id: "bad",
      epoch: 0,
      path: "/api/v1/me",
      method: "GET",
      headers: [["host", "other"]],
      body: null,
    }),
  );
});
test("health check uses the real contract and carries no credentials", async (t) => {
  const url = await fixture(t, (req, res) => {
    assert.equal(req.url, "/health/ready");
    assert.equal(req.headers.authorization, undefined);
    res.end(JSON.stringify({ status: "ok", database: "ok" }));
  });
  assert.equal(await new Network().testServer(url), url);
  const html = await fixture(t, (_req, res) => res.end("<html>login</html>"));
  await assert.rejects(new Network().testServer(html));
});
test("redirects are not followed or sent credentials", async (t) => {
  let redirected = 0;
  const destination = await fixture(t, (_req, res) => {
    redirected++;
    res.end("unexpected");
  });
  const url = await fixture(t, (_req, res) => {
    res.writeHead(302, { Location: destination + "/api/v1/me" });
    res.end();
  });
  const result = await request(new Network(fetch, url), "/api/v1/me", {
    token: "test-token",
  });
  assert.equal(result.status, 302);
  assert.equal(redirected, 0);
});
test("body streams before completion and pending reads can be cancelled", async (t) => {
  const url = await fixture(t, (_req, res) => {
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write("第一段中文");
  });
  const network = new Network(fetch, url),
    id = open(network);
  await network.send(id);
  assert.equal(
    Buffer.from(await network.read(id)).toString("utf8"),
    "第一段中文",
  );
  const reading = network.read(id);
  const rejected = assert.rejects(reading);
  network.cancel(id);
  await rejected;
  assert.equal(network.pending.size, 0);
});
test("server switches cancel requests and invalidate stale generations", () => {
  const network = new Network(fetch, "http://127.0.0.1:8000"),
    id = open(network);
  const controller = network.get(id).controller;
  network.replace("http://127.0.0.1:8001");
  assert.ok(controller.signal.aborted);
  assert.throws(() => open(network));
  assert.doesNotThrow(() => open(network, "/api/v1/me", { epoch: 1 }));
});
test("failed connections release pending requests without replay", async () => {
  let attempts = 0;
  const network = new Network(async () => {
    attempts++;
    throw new Error("offline");
  }, "http://127.0.0.1:8000");
  const id = open(network, "/api/v1/approvals/id/execute", { body: {} });
  await assert.rejects(network.send(id));
  assert.equal(attempts, 1);
  assert.equal(network.pending.size, 0);
});
test(
  "real backend: three roles, token rotation, module APIs and authenticated SSE",
  { skip: !process.env.SENTINELOPS_SMOKE_FIXTURE },
  async () => {
    const data = JSON.parse(
      await readFile(process.env.SENTINELOPS_SMOKE_FIXTURE, "utf8"),
    );
    assert.ok(data.url.startsWith("http://127.0.0.1:"));
    const network = new Network(fetch, data.url);
    await network.testServer(data.url);
    for (const [role, expected] of [
      ["admin", 200],
      ["operator", 403],
      ["viewer", 403],
    ]) {
      const login = await request(network, "/api/v1/auth/login", {
        body: { username: data.users[role].username, password: data.password },
      });
      assert.equal(login.status, 200);
      const session = JSON.parse(login.text),
        token = session.access_token;
      assert.equal(
        (await request(network, "/api/v1/users", { token })).status,
        expected,
      );
      assert.equal(
        (await request(network, "/api/v1/servers", { token })).status,
        200,
      );
      if (role !== "admin") continue;
      for (const path of [
        "alerts",
        "incidents",
        "inspection-plans",
        "approvals",
        "reports",
      ])
        assert.equal(
          (await request(network, "/api/v1/" + path, { token })).status,
          200,
          path,
        );
      const rotated = await request(network, "/api/v1/auth/refresh", {
        body: { refresh_token: session.refresh_token },
      });
      assert.equal(rotated.status, 200);
      assert.notEqual(
        JSON.parse(rotated.text).refresh_token,
        session.refresh_token,
      );
      const id = open(
        network,
        `/api/v1/analysis-conversations/${data.conversation}/stream?after_seq=0`,
        { token: JSON.parse(rotated.text).access_token },
      );
      assert.equal((await network.send(id)).status, 200);
      const chunks = [];
      while (!Buffer.concat(chunks).toString("utf8").includes("第二段恢复结果"))
        chunks.push(Buffer.from(await network.read(id)));
      network.cancel(id);
      assert.ok(
        Buffer.concat(chunks).toString("utf8").includes("第一段中文证据"),
      );
    }
  },
);
