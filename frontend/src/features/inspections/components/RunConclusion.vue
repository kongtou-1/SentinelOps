<script setup lang="ts">
import { computed } from "vue";
import { date } from "@/utils/display";
import { activeRun } from "@/features/inspections/progress";
import {
  coverage,
  health,
  runResult,
  runConclusion,
  tone,
  type Run,
} from "@/features/inspections/ui";
const props = defineProps<{ run: Run; latest?: boolean }>();
const scope = computed(() => coverage(props.run));
const counts = computed(() => health(props.run));
const duration = computed(() => {
  if (!props.run.started_at || !props.run.finished_at) return "";
  const seconds = Math.max(
    0,
    Math.round(
      (Date.parse(props.run.finished_at) - Date.parse(props.run.started_at)) /
        1000,
    ),
  );
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
});
</script>
<template>
  <section class="inspection-panel run-conclusion" :class="{ latest }">
    <div class="conclusion-copy">
      <div class="inspection-subline">
        {{ latest ? "最近执行 · " : ""
        }}{{ date(run.started_at || run.created_at)
        }}<span v-if="duration"> · 用时 {{ duration }}</span>
      </div>
      <h2
        :class="
          tone(run.status === 'completed' ? runConclusion(run) : run.status)
        "
      >
        {{ runResult(run) }}
      </h2>
      <div v-if="latest && scope" class="inspection-subline">
        覆盖 {{ scope.checks_completed }} /
        {{
          scope.expected_is_final
            ? scope.checks_expected
            : `至少 ${scope.checks_expected}`
        }}
        项检查 · {{ scope.collection_checks }} 个集合检查
      </div>
    </div>
    <div class="inspection-stats">
      <div v-if="!latest" class="inspection-stat">
        <span>已发现对象</span><strong>{{ run.summary.objects || 0 }}</strong>
      </div>
      <div class="inspection-stat">
        <span>正常</span><strong class="success">{{ counts.pass || 0 }}</strong>
      </div>
      <div class="inspection-stat">
        <span>异常</span><strong class="danger">{{ counts.fail || 0 }}</strong>
      </div>
      <div class="inspection-stat">
        <span>待确认</span><strong>{{ counts.unknown || 0 }}</strong>
      </div>
    </div>
    <router-link
      v-if="latest"
      class="conclusion-link"
      :to="`/inspections/runs/${run.id}`"
      >{{
        activeRun(run.status) ? "查看运行轨迹" : "查看本次检查"
      }}</router-link
    >
  </section>
</template>
