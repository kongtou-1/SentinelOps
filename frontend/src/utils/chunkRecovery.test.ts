import { afterEach, describe, expect, it, vi } from "vitest";
import { clearChunkRecovery, recoverChunk } from "@/utils/chunkRecovery";

afterEach(() => vi.unstubAllGlobals());

describe("deployment chunk recovery", () => {
  it("reloads the requested route once and prevents a reload loop", () => {
    const values = new Map<string, string>();
    const replace = vi.fn();
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => values.get(key) || null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    });
    vi.stubGlobal("window", { location: { replace } });
    const error = new Error(
      "Failed to fetch dynamically imported module: /assets/old.js",
    );
    expect(recoverChunk(error, "/reports/abc?view=report")).toBe(true);
    expect(replace).toHaveBeenCalledWith("/reports/abc?view=report");
    expect(recoverChunk(error, "/reports/abc?view=report")).toBe(false);
    expect(replace).toHaveBeenCalledTimes(1);
    clearChunkRecovery();
    expect(values.size).toBe(0);
    expect(recoverChunk(new Error("API unavailable"), "/reports/abc")).toBe(
      false,
    );
  });
});
