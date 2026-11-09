<script setup lang="ts">
import { computed, ref } from "vue";
import { request } from "@/api/client";
import { usePoll } from "@/composables/usePoll";
import {
  runConclusion,
  errorText,
  type Plan,
  type Run,
} from "@/features/inspections/ui";
const props = defineProps<{ kind: "plans" | "runs"; plans?: Plan[] }>();
const runs = ref<Run[]>([]),
  ready = ref(false),
  error = ref("");
usePoll(
  async () => {
    try {
      // Runs are returned newest first; stop after the first page before today.
      const start = new Date();
      start.setHours(0, 0, 0, 0);
      const collected: Run[] = [];
      for (let offset = 0; ; offset += 100) {
        const result = await request<Run[]>(
          `/inspection-runs?run_kind=standard&offset=${offset}&limit=100`,
        );
        collected.push(
          ...result.data.filter(
            (r) => Date.parse(r.created_at) >= start.getTime(),
          ),
        );
        if (
          result.data.length < 100 ||
          result.data.some((r) => Date.parse(r.created_at) < start.getTime()) ||
          (result.total !== null && offset + 100 >= result.total)
        )
          break;
      }
      runs.value = collected;
      ready.value = true;
      error.value = "";
    } catch (e) {
      error.value = errorText(e);
    }
  },
  () => 60000,
);
const today = computed(() => {
  const day = new Date().toLocaleDateString();
  return runs.value.filter(
    (r) => new Date(r.created_at).toLocaleDateString() === day,
  );
});
const cards = computed(() =>
  props.kind === "plans"
    ? [
        {
          label: "启用中",
          value: props.plans?.filter((p) => p.enabled).length,
          unit: "个任务",
        },
        {
          label: "已暂停",
          value: props.plans?.filter((p) => !p.enabled).length,
          unit: "个任务",
        },
        { label: "今日已执行", value: today.value.length, unit: "次" },
        {
          label: "最近执行异常",
          value: props.plans?.filter((p) => p.last_result === "fail").length,
          unit: "个任务",
          tone: "danger",
        },
      ]
    : [
        { label: "今日执行", value: today.value.length, unit: "次" },
        {
          label: "全部通过",
          value: today.value.filter(
            (r) => r.status === "completed" && runConclusion(r) === "pass",
          ).length,
          unit: "次",
          tone: "success",
        },
        {
          label: "发现异常",
          value: today.value.filter((r) => runConclusion(r) === "fail").length,
          unit: "次",
          tone: "danger",
        },
        {
          label: "执行失败",
          value: today.value.filter((r) => r.status === "failed").length,
          unit: "次",
          tone: "danger",
        },
      ],
);
</script>
<template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <div class="inspection-summary-grid" aria-label="巡检统计">
    <div
      v-for="card in cards"
      :key="card.label"
      class="inspection-summary-card"
    >
      <span>{{ card.label }}</span>
      <div>
        <strong :class="card.tone">{{
          ready && !error ? (card.value ?? "—") : "—"
        }}</strong
        ><span>{{ card.unit }}</span>
      </div>
    </div>
  </div>
</template>
