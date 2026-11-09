import { ApiError, authorizedFetch, type Models } from "@/api/client";

export type RunEvent = Models["EventResponse"];

export async function consumeEvents(
  response: Response,
  receive: (event: RunEvent) => void,
) {
  if (!response.body) throw new Error("事件订阅没有返回数据流");
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let pending = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      pending = (pending + decoder.decode(value, { stream: true })).replace(
        /\r\n/g,
        "\n",
      );
      let boundary: number;
      while ((boundary = pending.indexOf("\n\n")) >= 0) {
        const frame = pending.slice(0, boundary);
        pending = pending.slice(boundary + 2);
        const lines = frame.split("\n"),
          kind = lines
            .find((l) => l.startsWith("event:"))
            ?.slice(6)
            .trim();
        if (kind === "auth_required") throw new ApiError(401, "登录需要刷新");
        if (kind === "permission_lost")
          throw new ApiError(403, "会话查看权限已失效");
        if (kind === "connection_error")
          throw new Error("事件连接中断，正在重连");
        const data = lines
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trimStart())
          .join("\n");
        if (kind === "event" && data) receive(JSON.parse(data));
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function streamEvents(
  id: string,
  after: number,
  receive: (event: RunEvent) => void,
  signal: AbortSignal,
  opened?: () => void,
) {
  const response = await authorizedFetch(
    `/analysis-conversations/${id}/stream?after_seq=${after}`,
    { signal },
  );
  opened?.();
  await consumeEvents(response, receive);
}
