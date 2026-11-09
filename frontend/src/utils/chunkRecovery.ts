const key = "sentinelops.chunk-recovery";

export function recoverChunk(error: unknown, path: string): boolean {
  const message = error instanceof Error ? error.message : String(error);
  if (
    !/Failed to fetch dynamically imported module|Importing a module script failed|Loading chunk .* failed/i.test(
      message,
    )
  )
    return false;
  try {
    const previous = JSON.parse(sessionStorage.getItem(key) || "null");
    if (previous?.path === path && Date.now() - previous.at < 60000)
      return false;
    sessionStorage.setItem(key, JSON.stringify({ path, at: Date.now() }));
  } catch {
    // Without a persistent guard, leave recovery to a manual refresh.
    return false;
  }
  window.location.replace(
    isDesktop ? window.location.pathname + "#" + path : path,
  );
  return true;
}

export function clearChunkRecovery() {
  sessionStorage.removeItem(key);
}
import { isDesktop } from "@/platform/desktop";
