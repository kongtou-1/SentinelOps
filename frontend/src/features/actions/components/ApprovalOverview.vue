<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { api } from "@/api/client";
import { date, effectiveApproval, short } from "@/utils/display";
import { useAuth } from "@/features/auth/store";
import type { Row } from "@/types/records";
import StatusTag from "@/components/common/StatusTag.vue";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import { MagicStick } from "@element-plus/icons-vue";
const props = defineProps<{ rows: Row[] }>(),
  auth = useAuth();
const selectedId = ref(""),
  analysis = ref<Row>(),
  error = ref(""),
  loading = ref(false);
const selected = computed(() =>
    props.rows.find((r) => r.id === selectedId.value),
  ),
  diagnosis = computed(() => analysis.value?.result || {});
let controller: AbortController | undefined;
watch(
  () => props.rows,
  (rows) => {
    if (!rows.some((r) => r.id === selectedId.value))
      selectedId.value =
        rows.find((r) => effectiveApproval(r) === "pending")?.id ||
        rows[0]?.id ||
        "";
  },
  { immediate: true },
);
watch(
  () => selected.value?.id,
  async (approvalId) => {
    controller?.abort();
    analysis.value = undefined;
    error.value = "";
    if (!approvalId) return;
    controller = new AbortController();
    const signal = controller.signal;
    loading.value = true;
    try {
      const detail = await api<Row>(
        `/approvals/${approvalId}`,
        "GET",
        undefined,
        signal,
      );
      const result = await api<Row>(
        `/analyses/${detail.analysis_id}`,
        "GET",
        undefined,
        signal,
      );
      if (!signal.aborted) analysis.value = result;
    } catch (e) {
      if (!signal.aborted)
        error.value = e instanceof Error ? e.message : "诊断加载失败";
    } finally {
      if (!signal.aborted) loading.value = false;
    }
  },
  { immediate: true },
);
onUnmounted(() => controller?.abort());
</script>
<template>
  <div class="decision-layout">
    <section class="panel" v-loading="loading">
      <div class="panel-heading">
        <div>
          <h2>AI 诊断结论</h2>
          <small>{{
            selected
              ? "事故 " + short(selected.incident_id) + " · 审批绑定的诊断"
              : "选择审批事项查看关联诊断"
          }}</small>
        </div>
        <el-icon style="color: #4c82ee"><MagicStick /></el-icon>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <div class="confidence-placeholder">
        <span>诊断置信度 —</span><span class="placeholder-label">待接入</span>
      </div>
      <template v-if="diagnosis.summary"
        ><p class="decision-summary">{{ diagnosis.summary }}</p>
        <div class="report-section">
          <h3>影响范围</h3>
          <p>{{ diagnosis.impact }}</p>
        </div>
        <div class="report-section">
          <h3>原因与证据</h3>
          <div
            v-for="(cause, i) in diagnosis.possible_causes"
            :key="i"
            class="text-block"
          >
            <p>{{ cause.description || cause.cause }}</p>
            <small>证据 {{ cause.evidence_ids?.join(" · ") || "—" }}</small>
          </div>
        </div>
        <el-collapse
          ><el-collapse-item title="查看完整只读证据与工具记录"
            ><JsonEvidence
              :value="{
                evidence: analysis?.evidence,
                knowledge: analysis?.knowledge,
                tool_calls: analysis?.tool_calls,
              }" /></el-collapse-item></el-collapse
      ></template>
      <div v-else class="placeholder-state">
        <el-icon><MagicStick /></el-icon><strong>暂无已采用的诊断结论</strong
        ><span>在事故诊断工作台完成诊断，并手动采用结论</span>
      </div>
      <router-link
        v-if="selected"
        :to="'/incidents/' + selected.incident_id"
        class="subtle-link"
        >{{
          auth.can("analyses:run") ? "进入事故与诊断 →" : "查看关联事故 →"
        }}</router-link
      >
    </section>
    <section class="panel queue-panel">
      <div class="panel-heading">
        <div>
          <h2>审批队列</h2>
          <small>当前筛选页 {{ rows.length }} 条 · 人工审批</small>
        </div>
      </div>
      <div v-if="!rows.length" class="empty-hint">暂无审批事项</div>
      <div v-for="row in rows" :key="row.id" class="approval-queue-item">
        <div class="event-meta">
          <StatusTag :value="effectiveApproval(row)" /><span>{{
            date(row.created_at)
          }}</span>
        </div>
        <h3>
          {{ row.action || "restart_container" }} ·
          {{ row.container_name || short(row.container_id) }}
        </h3>
        <small
          >{{ row.actor_username }} 申请 · {{ short(row.incident_id) }}</small
        >
        <div class="actions" style="margin-top: 12px">
          <el-button
            :type="selectedId === row.id ? 'primary' : 'default'"
            @click="selectedId = row.id"
            >查看诊断</el-button
          ><router-link :to="'/approvals/' + row.id"
            ><el-button>{{
              effectiveApproval(row) === "pending" &&
              auth.can("approvals:decide")
                ? "审批"
                : "详情"
            }}</el-button></router-link
          >
        </div>
      </div>
    </section>
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>受控执行确认</h2>
          <small>restart_container v1 · medium 风险</small>
        </div>
      </div>
      <div class="control-note">
        容器重启将改变运行状态。必须经过 Admin 审批，再由 Operator / Admin
        人工发起执行。
      </div>
      <template v-if="selected"
        ><dl class="detail-grid" style="grid-template-columns: 1fr">
          <div>
            <dt>目标容器</dt>
            <dd>
              {{ selected.container_name || short(selected.container_id) }}
            </dd>
          </div>
          <div>
            <dt>审批状态</dt>
            <dd><StatusTag :value="effectiveApproval(selected)" /></dd>
          </div>
          <div>
            <dt>授权有效期</dt>
            <dd>{{ date(selected.expires_at) }}</dd>
          </div>
        </dl>
        <router-link :to="'/approvals/' + selected.id"
          ><el-button type="primary">核对审批与执行</el-button></router-link
        ></template
      >
      <div class="todo-placeholder">
        双人复核 <span class="placeholder-label">待接入</span
        ><br />当前执行以平台持久化审批为准。
      </div>
    </section>
  </div>
</template>
