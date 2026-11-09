import { onMounted, onUnmounted, watch } from "vue";
export function usePoll(
  load: () => Promise<unknown>,
  interval: () => number | null,
) {
  let timer: ReturnType<typeof setTimeout> | undefined,
    disposed = false,
    busy = false,
    pending = false;
  const stop = () => {
    if (timer) clearTimeout(timer);
    timer = undefined;
  };
  async function run() {
    stop();
    if (disposed || document.hidden) return;
    if (busy) {
      pending = true;
      return;
    }
    busy = true;
    try {
      await load();
    } finally {
      busy = false;
      const delay = interval();
      if (pending) {
        pending = false;
        void run();
      } else if (!disposed && !document.hidden && delay !== null)
        timer = setTimeout(run, delay);
    }
  }
  const visibility = () => {
    stop();
    if (!document.hidden) void run();
  };
  let stopWatching: (() => void) | undefined;
  onMounted(() => {
    stopWatching = watch(interval, (delay) => {
      stop();
      if (!busy && !disposed && !document.hidden && delay !== null)
        timer = setTimeout(run, delay);
    });
    void run();
    document.addEventListener("visibilitychange", visibility);
  });
  onUnmounted(() => {
    disposed = true;
    stop();
    stopWatching?.();
    document.removeEventListener("visibilitychange", visibility);
  });
  return { refresh: run };
}
