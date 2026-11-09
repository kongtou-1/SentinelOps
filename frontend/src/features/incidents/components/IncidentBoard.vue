<script setup lang="ts">
import { computed } from "vue";
import type { Row } from "@/types/records";
import { date, short } from "@/utils/display";
import StatusTag from "@/components/common/StatusTag.vue";
const props = defineProps<{ rows: Row[]; assignees: Row[] }>();
const lanes = computed(() =>
  [
    { title: "待处理", states: ["open"], color: "#f09a45" },
    { title: "处理中", states: ["in_progress"], color: "#4c82ee" },
    { title: "已恢复", states: ["recovered"], color: "#3dbf8f" },
    { title: "已闭环", states: ["closed", "merged"], color: "#8a9099" },
  ].map((l) => ({
    ...l,
    items: props.rows.filter((r) => l.states.includes(r.status)),
  })),
);
function owner(row: Row) {
  return (
    props.assignees.find((u) => u.id === row.assignee_id)?.username ||
    (row.assignee_id ? short(row.assignee_id) : "未分配")
  );
}
</script>
<template>
  <div class="cards event-stats">
    <div v-for="lane in lanes" :key="lane.title" class="stat-card">
      <div class="stat-title">{{ lane.title }}</div>
      <div class="stat-value">{{ lane.items.length }} <small>条</small></div>
      <div class="stat-foot">当前筛选页 · 事故状态</div>
    </div>
  </div>
  <div class="kanban-board">
    <section v-for="lane in lanes" :key="lane.title" class="kanban-lane">
      <div class="lane-heading">
        <h2>
          <i
            class="status-dot"
            :style="{ background: lane.color, marginRight: '7px' }"
          />{{ lane.title }}<small>{{ lane.items.length }}</small>
        </h2>
        <span class="muted">···</span>
      </div>
      <router-link
        v-for="row in lane.items"
        :key="row.id"
        :to="'/incidents/' + row.id"
        class="event-card"
        ><div class="event-meta">
          <StatusTag :value="row.severity" /><span>{{
            date(row.started_at)
          }}</span>
        </div>
        <h3>{{ row.title }}</h3>
        <small>{{ short(row.id) }}</small>
        <div class="event-footer">
          <el-avatar :size="22">{{ owner(row).slice(0, 1) }}</el-avatar
          ><small>{{ owner(row) }}</small
          ><StatusTag :value="row.status" /></div
      ></router-link>
      <div v-if="!lane.items.length" class="empty-hint">
        暂无{{ lane.title }}事故
      </div>
      <div class="lane-footer">
        <span class="placeholder-label">待接入</span> SLA 与批量派单
      </div>
    </section>
  </div>
</template>
