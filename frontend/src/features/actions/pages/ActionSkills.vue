<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { onMounted, ref } from "vue";
import { api } from "@/api/client";
import type { Row } from "@/types/records";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import { actionName, precheckName, riskName } from "@/features/actions/catalog";

const rows = ref<Row[]>([]),
  error = ref(""),
  loading = ref(false),
  selected = ref<Row>();
async function load() {
  loading.value = true;
  try {
    rows.value = await api<Row[]>("/action-skills");
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
}
onMounted(load);
</script>
<template>
  <div class="list-workspace">
    <section class="page-header">
      <div class="page-heading">
        <h1>执行动作目录</h1>
        <PageIntro />

        <div class="actions">
          <el-button :loading="loading" @click="load">刷新</el-button>
        </div>
      </div>
    </section>
    <section class="panel">
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        class="error-box"
      />
      <el-table
        v-loading="loading"
        :data="rows"
        row-key="code"
        empty-text="暂无可用执行动作"
      >
        <el-table-column label="执行动作" min-width="220">
          <template #default="{ row }">
            <el-button link type="primary" @click="selected = row">{{
              actionName(row.code)
            }}</el-button>
          </template>
        </el-table-column>
        <el-table-column label="适用对象" min-width="140">
          <template #default="{ row }">{{
            row.parameters?.container_id ? "已登记容器" : "以动作参数为准"
          }}</template>
        </el-table-column>
        <el-table-column label="风险等级" width="120">
          <template #default="{ row }"
            ><el-tag
              :type="
                row.risk === 'high'
                  ? 'danger'
                  : row.risk === 'medium'
                    ? 'warning'
                    : 'info'
              "
              >{{ riskName(row.risk) }}风险</el-tag
            ></template
          >
        </el-table-column>
        <el-table-column label="执行授权" min-width="180">
          <template #default="{ row }">{{
            row.requires_human_approval
              ? row.risk === "medium"
                ? "管理员审批后人工发起"
                : "人工审批后发起"
              : "以平台执行规则为准"
          }}</template>
        </el-table-column>
        <el-table-column label="版本" width="90"
          ><template #default="{ row }"
            >v{{ row.version }}</template
          ></el-table-column
        >
        <el-table-column label="操作" width="100" fixed="right"
          ><template #default="{ row }"
            ><el-button link @click="selected = row"
              >查看详情</el-button
            ></template
          ></el-table-column
        >
      </el-table>
    </section>
    <el-drawer
      :model-value="!!selected"
      :title="selected ? actionName(selected.code) : '动作详情'"
      size="min(680px, 96vw)"
      @close="selected = undefined"
    >
      <div v-if="selected" class="action-details">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="动作标识">{{
            selected.code
          }}</el-descriptions-item>
          <el-descriptions-item label="版本"
            >v{{ selected.version }}</el-descriptions-item
          >
          <el-descriptions-item label="风险等级"
            >{{ riskName(selected.risk) }}风险</el-descriptions-item
          >
          <el-descriptions-item label="人工审批">{{
            selected.requires_human_approval ? "必须审批" : "以平台执行规则为准"
          }}</el-descriptions-item>
        </el-descriptions>
        <section v-if="selected.code === 'restart_container'">
          <h2>用途与影响</h2>
          <p>重启已登记的 Docker 容器。重启期间容器服务会短暂中断。</p>
          <el-steps :active="-1" simple
            ><el-step title="申请审批" /><el-step title="管理员批准" /><el-step
              title="人工发起"
          /></el-steps>
        </section>
        <section>
          <h2>执行前检查</h2>
          <ul>
            <li v-for="check in selected.precheck || []" :key="check">
              {{ precheckName(check) }}
            </li>
          </ul>
        </section>
        <section v-if="selected.verify">
          <h2>恢复验证</h2>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="验证时限"
              >{{ selected.verify.timeout_seconds }} 秒</el-descriptions-item
            >
            <el-descriptions-item label="稳定采样"
              >连续 {{ selected.verify.stable_samples }} 次，每次间隔
              {{ selected.verify.interval_seconds }} 秒</el-descriptions-item
            >
            <el-descriptions-item
              v-if="selected.verify.running"
              label="运行状态"
              >容器须恢复运行</el-descriptions-item
            >
            <el-descriptions-item
              v-if="selected.verify.healthy_if_configured"
              label="健康检查"
              >容器已配置健康检查时，须通过检查</el-descriptions-item
            >
          </el-descriptions>
        </section>
        <section>
          <h2>回滚</h2>
          <p>
            {{ selected.rollback ? "以动作定义为准" : "此动作未配置自动回滚" }}
          </p>
        </section>
        <el-collapse
          ><el-collapse-item title="技术定义" name="definition"
            ><JsonEvidence :value="selected" /></el-collapse-item
        ></el-collapse>
      </div>
    </el-drawer>
  </div>
</template>
<style scoped>
.action-details {
  display: grid;
  gap: 24px;
}
.action-details h2 {
  font-size: 16px;
  margin: 0 0 16px;
}
.action-details p {
  margin: 0 0 16px;
}
.action-details ul {
  margin: 0;
  padding-left: 20px;
  line-height: 2;
}
</style>
