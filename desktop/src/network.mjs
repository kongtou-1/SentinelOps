export function normalizeServer(value) {
  let url;
  try {
    url = new URL(value.trim());
  } catch {
    throw new Error("请输入完整的 HTTP 或 HTTPS 服务器地址");
  }
  if (
    !["http:", "https:"].includes(url.protocol) ||
    !url.hostname ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/"
  ) {
    throw new Error("服务器地址只包含协议、域名或 IP 和端口");
  }
  return url.origin;
}

export class Network {
  constructor(fetcher = fetch, initial = "") {
    this.fetcher = fetcher;
    this.server = { url: initial ? normalizeServer(initial) : "", epoch: 0 };
    this.pending = new Map();
  }
  cancel(id) {
    const entry = this.pending.get(id);
    this.pending.delete(id);
    entry?.controller.abort();
  }
  cancelAll() {
    for (const id of this.pending.keys()) this.cancel(id);
  }
  replace(url) {
    this.cancelAll();
    this.server = { url: normalizeServer(url), epoch: this.server.epoch + 1 };
    return { ...this.server };
  }
  async testServer(value) {
    const url = normalizeServer(value);
    let response;
    try {
      response = await this.fetcher(url + "/health/ready", {
        redirect: "manual",
        signal: AbortSignal.timeout(12000),
      });
    } catch {
      throw new Error("无法连接服务器，请检查地址、网络和 HTTPS 证书");
    }
    if (!response.ok)
      throw new Error(`后台尚未就绪（HTTP ${response.status}）`);
    let health;
    try {
      health = await response.json();
    } catch {
      throw new Error("该地址没有返回后台健康状态");
    }
    if (health.status !== "ok" || health.database !== "ok")
      throw new Error("该地址没有返回 SentinelOps 就绪状态");
    return url;
  }
  open({ id, epoch, path, method, headers, body }) {
    if (epoch !== this.server.epoch) throw new Error("服务器连接已更换");
    if (!this.server.url) throw new Error("请先连接服务器");
    if (typeof path !== "string" || !path.startsWith("/"))
      throw new Error("无效的接口路径");
    const url = new URL(path, this.server.url);
    if (
      url.origin !== this.server.url ||
      url.hash ||
      !(
        url.pathname.startsWith("/api/v1/") ||
        ["/health/live", "/health/ready"].includes(url.pathname)
      )
    ) {
      throw new Error("只允许访问当前服务器的后台接口");
    }
    if (!["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"].includes(method))
      throw new Error("不支持的请求方法");
    const safe = new Headers();
    for (const [key, value] of headers) {
      if (
        !["authorization", "content-type", "accept", "last-event-id"].includes(
          key.toLowerCase(),
        )
      )
        throw new Error("不支持的请求头");
      safe.set(key, value);
    }
    if (typeof id !== "string" || !id || this.pending.has(id))
      throw new Error("无效或重复的请求标识");
    if (
      body !== null &&
      (!Array.isArray(body) ||
        !body.every((v) => Number.isInteger(v) && v >= 0 && v <= 255))
    )
      throw new Error("无效的请求内容");
    this.pending.set(id, {
      url: url.href,
      method,
      headers: safe,
      body: body === null ? undefined : new Uint8Array(body),
      controller: new AbortController(),
    });
  }
  get(id) {
    const entry = this.pending.get(id);
    if (!entry) throw new Error("请求已结束");
    return entry;
  }
  async send(id) {
    const entry = this.get(id);
    if (entry.sent) throw new Error("请求已发送");
    entry.sent = true;
    const timer = setTimeout(() => entry.controller.abort(), 30000);
    try {
      const response = await this.fetcher(entry.url, {
        method: entry.method,
        headers: entry.headers,
        body: entry.body,
        signal: entry.controller.signal,
        redirect: "manual",
      });
      entry.reader = response.body?.getReader();
      return {
        status: response.status,
        headers: [...response.headers.entries()],
      };
    } catch {
      this.cancel(id);
      throw new Error("服务器连接中断，请检查网络或证书");
    } finally {
      clearTimeout(timer);
    }
  }
  async read(id) {
    const entry = this.get(id);
    if (!entry.sent) throw new Error("响应尚未开始");
    const timer = setTimeout(() => entry.controller.abort(), 90000);
    try {
      const chunk = await entry.reader?.read();
      if (!chunk || chunk.done) {
        this.cancel(id);
        return null;
      }
      return Array.from(chunk.value);
    } catch {
      this.cancel(id);
      throw new Error("服务器响应中断");
    } finally {
      clearTimeout(timer);
    }
  }
}
