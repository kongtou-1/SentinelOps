<script setup lang="ts">
import { computed } from "vue";
import type { Row } from "@/types/records";
import { date, label } from "@/utils/display";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import StatusTag from "@/components/common/StatusTag.vue";
const props = defineProps<{
  content: Row;
  snapshot?: Row;
  revision?: number;
  status?: string;
}>();
const records = computed<Row[]>(() => props.snapshot?.records || []),
  timeline = computed(() =>
    records.value
      .filter(
        (r) =>
          r.kind === "timeline" &&
          (r.data?.actor_type === "user" ||
            !["alert_created", "alert_linked"].includes(r.data?.event_type)),
      )
      .sort(
        (a, b) =>
          new Date(a.occurred_at).getTime() - new Date(b.occurred_at).getTime(),
      ),
  ),
  alerts = computed(() => records.value.filter((r) => r.kind === "alert")),
  closure = computed(
    () =>
      records.value.find(
        (r) =>
          r.kind === "incident" && r.record_id === props.snapshot?.incident_id,
      )?.data?.closure || {},
  ),
  verified = computed(
    () =>
      alerts.value.filter((r) => r.data?.recovery_status === "verified").length,
  );
const events: Record<string, string> = {
  incident_created: "事故创建",
  alert_created: "告警触发",
  alert_resolved: "告警通知结束",
  alert_recovery_checked: "资源恢复核验",
  analysis_queued: "诊断任务排队",
  analysis_auto_skipped: "自动诊断未启动",
  execution_queued: "执行任务排队",
  execution_prechecking: "执行前置检查",
  execution_executing: "执行固定动作",
  execution_verifying: "验证容器状态",
  execution_denied: "执行请求被拒绝",
  created: "事故创建",
  alert_linked: "关联告警",
  assigned: "分配负责人",
  status_changed: "状态更新",
  analysis_requested: "发起 AI 诊断",
  analysis_completed: "AI 诊断完成",
  diagnosis_adopted: "采用诊断结论",
  approval_requested: "申请执行审批",
  approval_approved: "审批通过",
  approval_rejected: "审批拒绝",
  execution_requested: "人工发起执行",
  execution_succeeded: "执行成功",
  recovered: "资源恢复已核验",
  closed: "事故关闭",
  incident_force_closed: "管理员强制关闭事故",
  merged: "事故合并",
};
</script>
<template>
  <section class="panel report-summary">
    <h2>{{ snapshot?.title || "事故复盘" }}</h2>
    <h3>事故摘要</h3>
    <p class="muted" style="margin: 0">{{ content.summary }}</p>
    <div class="report-section">
      <h3>影响范围</h3>
      <p>{{ content.impact }}</p>
    </div>
    <el-alert
      v-if="closure.forced"
      type="warning"
      :closable="false"
      show-icon
      :title="
        closure.recovery_verified
          ? '管理员强制关闭 · 关闭时恢复已核验'
          : '管理员强制关闭 · 关闭时恢复未核验'
      "
      :description="`${closure.actor_username} 于 ${date(closure.closed_at)} 关闭。原因：${closure.reason}`"
    />
    <div class="report-facts">
      <div>
        <small>关联告警</small><strong>{{ alerts.length }} 条</strong>
      </div>
      <div>
        <small>冻结证据</small><strong>{{ records.length }} 条</strong>
      </div>
      <div>
        <small>资源恢复核验</small
        ><strong
          :style="{
            color:
              verified === alerts.length && alerts.length
                ? '#3dbf8f'
                : '#f09a45',
          }"
          >{{ verified }} / {{ alerts.length }} 已核验</strong
        >
      </div>
      <div>
        <small>报告状态 · v{{ revision }}</small
        ><StatusTag :value="status" />
      </div>
    </div>
  </section>
  <div class="report-body-grid">
    <section class="panel report-timeline-panel">
      <div class="panel-heading">
        <div>
          <h2>关键时间线</h2>
          <small
            >{{ timeline.length }} 个记录节点 · 冻结于
            {{ date(snapshot?.captured_at) }}</small
          >
        </div>
      </div>
      <div v-if="!timeline.length" class="empty-hint">
        冻结快照中暂无时间线记录
      </div>
      <el-timeline
        ><el-timeline-item
          v-for="t in timeline"
          :key="t.id"
          :timestamp="date(t.occurred_at)"
          :color="
            t.data?.event_type?.includes('execution') ? '#f09a45' : '#4c82ee'
          "
          ><h3 style="font-size: 13px; margin-bottom: 7px">
            {{ events[t.data?.event_type] || label(t.data?.event_type) }}
          </h3>
          <small>{{
            t.data?.actor_type === "user" ? "人工操作" : "系统记录"
          }}</small
          ><el-collapse
            ><el-collapse-item title="查看节点证据"
              ><JsonEvidence
                :value="
                  t.data?.content
                " /></el-collapse-item></el-collapse></el-timeline-item
      ></el-timeline>
    </section>
    <div>
      <section class="panel">
        <div class="panel-heading">
          <div>
            <h2>原因与证据</h2>
            <small>基于冻结证据 · 结论由人工审核</small>
          </div>
          <span class="placeholder-label">5 Why 待接入</span>
        </div>
        <div v-for="(cause, i) in content.causes" :key="i" class="report-cause">
          <h3>
            原因 {{ Number(i) + 1 }} ·
            {{
              cause.confidence === "confirmed"
                ? "已确认"
                : cause.confidence === "suspected"
                  ? "疑似"
                  : "未知"
            }}
          </h3>
          <p>{{ cause.description }}</p>
          <small>引用证据 {{ cause.evidence_ids?.join(" · ") || "—" }}</small>
        </div>
        <div v-if="!content.causes?.length" class="empty-hint">
          尚未记录根因
        </div>
        <div class="report-section">
          <h3>处置与恢复核验</h3>
          <p>{{ content.response_and_recovery }}</p>
        </div>
      </section>
      <section class="panel">
        <h2>经验教训</h2>
        <div
          v-for="(lesson, i) in content.lessons"
          :key="i"
          class="summary-list"
        >
          {{ lesson }}
        </div>
        <div v-if="!content.lessons?.length" class="empty-hint">
          暂无经验记录
        </div>
      </section>
    </div>
  </div>
  <section class="panel">
    <div class="panel-heading">
      <div>
        <h2>改进措施</h2>
        <small
          >{{ content.improvements?.length || 0 }} 项 ·
          负责人及计划时间来自报告正文</small
        >
      </div>
    </div>
    <div
      v-for="(item, i) in content.improvements"
      :key="i"
      class="improvement-row"
    >
      <div>
        <p>{{ item.action }}</p>
        <small>计划完成 {{ item.deadline }}</small>
      </div>
      <el-tag type="info">{{ item.owner }}</el-tag>
    </div>
    <div v-if="!content.improvements?.length" class="empty-hint">
      暂无改进建议
    </div>
  </section>
  <section v-if="content.data_gaps?.length" class="panel">
    <h2>证据缺口</h2>
    <p v-for="gap in content.data_gaps" :key="gap" class="muted">{{ gap }}</p>
  </section>
</template>
