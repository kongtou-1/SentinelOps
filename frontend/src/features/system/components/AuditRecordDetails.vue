<script setup lang="ts">
import { useAuth } from "@/features/auth/store";
import { date } from "@/utils/display";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import {
  auditActionName,
  auditActorName,
  auditIncidentName,
  auditReason,
  type AuditRecord,
} from "@/features/system/auditDisplay";

defineProps<{ record: AuditRecord }>();
const auth = useAuth();
</script>
<template>
  <el-descriptions :column="1" border>
    <el-descriptions-item label="操作">{{
      auditActionName(record.action)
    }}</el-descriptions-item>
    <el-descriptions-item label="操作人">{{
      auditActorName(record)
    }}</el-descriptions-item>
    <el-descriptions-item label="关联事故">
      <router-link
        v-if="auth.can('incidents:read')"
        :to="'/incidents/' + record.incident_id"
        >{{ auditIncidentName(record) }}</router-link
      >
      <span v-else>{{ auditIncidentName(record) }}</span>
    </el-descriptions-item>
    <el-descriptions-item label="操作时间">{{
      date(record.created_at)
    }}</el-descriptions-item>
    <el-descriptions-item v-if="record.detail.comment" label="备注">{{
      record.detail.comment
    }}</el-descriptions-item>
    <el-descriptions-item v-if="record.detail.revision != null" label="报告版本"
      >第 {{ record.detail.revision }} 版</el-descriptions-item
    >
    <el-descriptions-item v-if="auditReason(record)" label="原因">{{
      auditReason(record)
    }}</el-descriptions-item>
  </el-descriptions>
  <div class="related-records">
    <router-link
      v-if="record.approval_id && auth.can('executions:read')"
      :to="'/approvals/' + record.approval_id"
      >查看关联审批</router-link
    >
    <router-link
      v-if="record.execution_id && auth.can('executions:read')"
      :to="'/executions/' + record.execution_id"
      >查看执行记录</router-link
    >
    <router-link
      v-if="record.report_id && auth.can('reports:read')"
      :to="'/reports/' + record.report_id"
      >查看复盘报告</router-link
    >
  </div>
  <details class="technical-details">
    <summary>排查信息</summary>
    <el-descriptions :column="1" border>
      <el-descriptions-item label="内部追踪编号"
        ><code>{{ record.request_id }}</code></el-descriptions-item
      >
      <el-descriptions-item v-if="record.ip" label="来源地址">{{
        record.ip
      }}</el-descriptions-item>
    </el-descriptions>
    <JsonEvidence :value="record" />
  </details>
</template>
<style scoped>
.related-records {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  margin-top: 20px;
}
.technical-details {
  border-top: 1px solid var(--border);
  margin-top: 24px;
  padding-top: 20px;
}
summary {
  cursor: pointer;
  color: var(--text-regular);
}
details[open] summary {
  margin-bottom: 20px;
}
code {
  user-select: all;
  overflow-wrap: anywhere;
}
</style>
