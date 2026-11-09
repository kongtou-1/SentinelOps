<script setup lang="ts">
import { computed } from "vue";
import type { summarizeService } from "@/features/applications/model/businessServices";

const props = defineProps<{
  stats: ReturnType<typeof summarizeService>;
  containers: { id: string; name: string }[];
}>();
const reasons = computed(() => {
  const names = new Map(props.containers.map((c) => [c.id, c.name]));
  const groups = new Map<string, string[]>();
  for (const issue of props.stats.issues) {
    const group = groups.get(issue.reason) || [];
    group.push(names.get(issue.container_id) || issue.container_id);
    groups.set(issue.reason, group);
  }
  return [...groups].map(([reason, names]) => ({ reason, names }));
});
const summary = computed(() =>
  !props.stats.total
    ? "尚未关联容器，无法判断服务健康"
    : reasons.value.length
      ? reasons.value
          .map((item) => `${item.names.length} 个容器${item.reason}`)
          .join("；")
      : "全部容器运行且健康检查通过",
);
</script>
<template>
  <div class="service-health-details">
    <p>{{ summary }}</p>
    <details v-if="reasons.length">
      <summary>查看具体容器</summary>
      <ul>
        <li v-for="item in reasons" :key="item.reason">
          <strong>{{ item.reason }}</strong>
          <div>{{ item.names.join("、") }}</div>
        </li>
      </ul>
    </details>
  </div>
</template>
<style scoped>
.service-health-details {
  margin: 12px 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
p {
  margin: 0;
}
summary {
  cursor: pointer;
  color: var(--el-color-primary);
  margin-top: 6px;
}
ul {
  margin: 8px 0 0;
  padding-left: 18px;
}
li + li {
  margin-top: 8px;
}
strong {
  color: var(--text-regular);
  font-weight: 500;
}
</style>
