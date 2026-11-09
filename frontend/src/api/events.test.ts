import { describe, expect, it, vi } from "vitest";
import { consumeEvents, streamEvents } from "@/api/events";

describe("SSE events", () => {
  it("confirms the authenticated connection before reading events", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('event: event\ndata: {"seq":1}\n\n')),
    );
    const order: string[] = [];
    try {
      await streamEvents(
        "conversation",
        0,
        () => order.push("event"),
        new AbortController().signal,
        () => order.push("open"),
      );
      expect(order).toEqual(["open", "event"]);
    } finally {
      vi.unstubAllGlobals();
    }
  });
  it("reads split UTF-8 frames, heartbeats and multiple events", async () => {
    const bytes = new TextEncoder().encode(
      ': heartbeat\n\nevent: event\nid: 1\ndata: {"seq":1,"kind":"assistant_delta","data":{"text":"诊断中"}}\n\nevent: event\nid: 2\ndata: {"seq":2,"kind":"run_completed","data":{}}\n\n',
    );
    const stream = new ReadableStream({
      start(controller) {
        for (let i = 0; i < bytes.length; i += 3)
          controller.enqueue(bytes.slice(i, i + 3));
        controller.close();
      },
    });
    const received: unknown[] = [];
    await consumeEvents(new Response(stream), (event) => received.push(event));
    expect(received).toEqual([
      { seq: 1, kind: "assistant_delta", data: { text: "诊断中" } },
      { seq: 2, kind: "run_completed", data: {} },
    ]);
  });
  it("reports auth expiry so reconnect can use the shared refresh", async () => {
    await expect(
      consumeEvents(
        new Response("event: auth_required\ndata: {}\n\n"),
        () => {},
      ),
    ).rejects.toMatchObject({ status: 401 });
  });
  it("handles CRLF split between chunks", async () => {
    const stream = new ReadableStream({
      start(controller) {
        for (const part of [
          "event: event\r",
          '\ndata: {"seq":3}\r',
          "\n\r",
          "\n",
        ])
          controller.enqueue(new TextEncoder().encode(part));
        controller.close();
      },
    });
    const received: unknown[] = [];
    await consumeEvents(new Response(stream), (event) => received.push(event));
    expect(received).toEqual([{ seq: 3 }]);
  });
});
