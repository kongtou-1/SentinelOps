type Command =
  | "set_window_theme"
  | "load_server"
  | "test_server"
  | "save_server"
  | "request_open"
  | "request_send"
  | "request_read"
  | "request_cancel"
  | "save_file"
  | "open_external";
declare global {
  interface Window {
    sentinelDesktop?: Record<
      Command,
      (args?: Record<string, unknown>) => Promise<unknown>
    >;
  }
}

export async function invoke<T = unknown>(
  command: Command,
  args?: Record<string, unknown>,
): Promise<T> {
  const bridge = window.sentinelDesktop;
  if (!bridge) throw new Error("桌面连接尚未就绪，请重新启动客户端");
  try {
    return (await bridge[command](args)) as T;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(
      message.replace(
        /^Error invoking remote method '[^']+': (?:Error: )?/,
        "",
      ),
    );
  }
}
