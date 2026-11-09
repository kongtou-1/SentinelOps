import { describe, expect, it } from "vitest";
import { predictionTimeline, type PredictionRun } from "./timeline";

const task = {
  id: "task",
  metric: "container_memory_bytes",
  container_id: "container",
  server_id: null,
  llm_endpoint_id: null,
  selector: {},
};
const iso = (seconds: number) => new Date(seconds * 1000).toISOString();
function run(
  id: string,
  issued: number,
  points: [number, number][],
  overrides: Partial<PredictionRun> = {},
): PredictionRun {
  return {
    id,
    task_id: task.id,
    status: "completed",
    trigger: "scheduled",
    started_at: iso(issued - 10),
    finished_at: iso(issued),
    data_end_at: iso(issued - 20),
    error_code: null,
    fresh: true,
    created_at: iso(issued - 10),
    updated_at: iso(issued),
    config: {
      ...task,
      server_id: "server",
      docker_id: "docker",
      source_epoch: "epoch",
    },
    horizons: {
      "24": {
        points: points.map(([timestamp, value]) => ({ timestamp, value })),
      },
    },
    quality: {},
    anomalies: [],
    ...overrides,
  };
}

describe("prediction task timeline", () => {
  it("combines batches into one historical curve and uses only the latest batch for the future", () => {
    const old = run("old", 100, [
      [300, 1],
      [600, 2],
      [900, 3],
    ]);
    const latest = run("latest", 400, [
      [300, 99],
      [600, 4],
      [900, 5],
      [1200, 6],
    ]);
    const result = predictionTimeline([latest, old], task, "24", 0, 800);
    expect(result.latest?.id).toBe("latest");
    expect(result.history).toEqual([
      { timestamp: 300, value: 1, issuedAt: 100 },
      { timestamp: 600, value: 4, issuedAt: 400 },
    ]);
    expect(result.future).toEqual([
      { timestamp: 900, value: 5 },
      { timestamp: 1200, value: 6 },
    ]);
  });
  it("does not backfill past values with a forecast completed after the target time", () => {
    const result = predictionTimeline(
      [
        run("late", 650, [
          [600, 99],
          [900, 4],
        ]),
        run("early", 100, [
          [300, 0],
          [600, 2],
        ]),
      ],
      task,
      "24",
      400,
      800,
    );
    expect(result.history).toEqual([
      { timestamp: 600, value: 2, issuedAt: 100 },
    ]);
  });
  it("keeps zero values, the requested horizon and the history boundary", () => {
    const batch = run("batch", 100, [[300, 88]]);
    batch.horizons["1"] = {
      points: [
        { timestamp: 600, value: 0 },
        { timestamp: 900, value: 2 },
      ],
    };
    const result = predictionTimeline([batch], task, "1", 600, 600);
    expect(result.history).toEqual([
      { timestamp: 600, value: 0, issuedAt: 100 },
    ]);
    expect(result.future).toEqual([{ timestamp: 900, value: 2 }]);
  });
  it("excludes other tasks, resources, metrics and selectors", () => {
    const valid = run("valid", 100, [[300, 1]]);
    const invalid = [
      run("other-task", 200, [[300, 99]], { task_id: "other" }),
      run("other-container", 200, [[300, 99]], {
        config: { ...valid.config, container_id: "other" },
      }),
      run("other-metric", 200, [[300, 99]], {
        config: { ...valid.config, metric: "container_cpu_percent" },
      }),
      run("other-selector", 200, [[300, 99]], {
        config: { ...valid.config, selector: { interface: "eth0" } },
      }),
      run("failed", 200, [[300, 99]], { status: "failed" }),
    ];
    expect(
      predictionTimeline([...invalid, valid], task, "24", 0, 600).history,
    ).toEqual([{ timestamp: 300, value: 1, issuedAt: 100 }]);
  });
  it("does not merge replaced containers or earlier collection configurations", () => {
    const latest = run("latest", 400, [[600, 3]]);
    const oldEpoch = run("epoch", 100, [[300, 99]], {
      config: { ...latest.config, source_epoch: "old" },
    });
    const oldDocker = run("docker", 100, [[300, 88]], {
      config: { ...latest.config, docker_id: "old" },
    });
    expect(
      predictionTimeline([latest, oldEpoch, oldDocker], task, "24", 0, 800)
        .history,
    ).toEqual([{ timestamp: 600, value: 3, issuedAt: 400 }]);
  });
  it("does not turn expired or missing forecasts into future values", () => {
    expect(
      predictionTimeline([run("old", 100, [[300, 1]])], task, "24", 0, 800)
        .future,
    ).toEqual([]);
    expect(predictionTimeline([], task, "24", 0, 800)).toEqual({
      latest: undefined,
      history: [],
      future: [],
    });
    expect(
      predictionTimeline(
        [run("unknown", 100, [[900, 1]], { finished_at: null })],
        task,
        "24",
        0,
        800,
      ).latest,
    ).toBeUndefined();
  });
});
