<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { date } from "@/utils/display";
import { usePoll } from "@/composables/usePoll";
import { useAuth } from "@/features/auth/store";
import {
  askdata,
  buildStatus,
  sourceOriginLabels,
  stepLabel,
  versionStatus,
  type AskDataSourceSnapshotPage,
  type AskDataVersion,
} from "@/features/askdata/api";
import VersionPicker from "@/features/askdata/components/VersionPicker.vue";

const auth = useAuth();
const canManage = computed(() => auth.can("askdata:manage"));
const versionId = ref("");
const connectionId = ref("");
const refreshKey = ref(0);
const rows = ref<AskDataVersion[]>([]);
const busy = ref(false);
const error = ref("");
const snapshots = ref<AskDataSourceSnapshotPage>({
  items: [],
  recommended_id: null,
  origin: "none",
  label: "",
  environment: "",
});
const snapshotId = ref("");
const autoSnapshotLabel = computed(() =>
  snapshots.value.origin === "release"
    ? "使用发布版本关联快照（推荐）"
    : "不自动关联源码",
);

const errorLabels: Record<string, string> = {
  catalog_empty: "目录为空，未抽取到数据表",
  semantic_empty: "未生成业务术语",
  embedding_empty: "未生成向量索引",
  data_embedding_empty: "未生成数据目录向量索引",
  authorization_revoked: "权限已撤销",
  configuration_changed: "连接配置或结构已变化",
  database_busy: "连接繁忙",
  build_cancelled: "构建已取消",
  build_interrupted: "构建进程中断",
  build_failed: "构建失败",
  carry_over_failed: "人工定义沿用失败",
  source_snapshot_unavailable: "模型源码快照不可用",
  source_snapshot_integrity_error: "源码快照完整性校验失败",
};
const countLabels: Record<string, string> = {
  tables: "表",
  columns: "字段",
  terms: "术语",
  attributes: "属性",
  relationships: "关系",
  embeddings: "向量",
  data_embeddings: "目录向量",
  semantic_embeddings: "语义向量",
  metrics: "指标",
  carried_definitions: "沿用定义",
  pending_reviews: "待复核",
  source_rules: "代码规则",
  source_files: "源码文件",
  code_embeddings: "代码向量",
  source_static_sql: "静态 SQL",
  source_dynamic: "待确认片段",
};
const hasActive = computed(() =>
  rows.value.some((item) => item.build && ["queued", "running"].includes(item.build.status)),
);
usePoll(
  async () => {
    refreshKey.value += 1;
  },
  () => (hasActive.value ? 3000 : null),
);

function refresh() {
  refreshKey.value += 1;
}
function statusType(status: string) {
  if (status === "completed") return "success";
  if (status === "failed" || status === "interrupted") return "danger";
  if (status === "running" || status === "queued") return "warning";
  return "info";
}
function counts(detail: Record<string, unknown> | undefined) {
  return Object.entries(detail || {})
    .filter(([key, value]) => typeof value === "number" && key in countLabels)
    .map(([key, value]) => `${countLabels[key]} ${value}`);
}
function failure(code: string | null | undefined) {
  return code ? errorLabels[code] || code : "";
}
async function run(action: () => Promise<unknown>, message: string) {
  busy.value = true;
  error.value = "";
  try {
    await action();
    ElMessage.success(message);
    refresh();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "操作失败";
  } finally {
    busy.value = false;
  }
}
watch(connectionId, () => void loadSnapshots());
async function loadSnapshots() {
  snapshotId.value = "";
  if (!connectionId.value) {
    snapshots.value = {
      items: [], recommended_id: null, origin: "none", label: "", environment: "",
    };
    return;
  }
  try {
    snapshots.value = await askdata.sourceSnapshots(connectionId.value);
  } catch {
    snapshots.value = {
      items: [], recommended_id: null, origin: "none", label: "", environment: "",
    };
  }
}
function versionBody() {
  return snapshotId.value ? { source_snapshot_id: snapshotId.value } : {};
}
function start() {
  if (!connectionId.value) return;
  return run(
    () => askdata.createVersion(connectionId.value, versionBody()),
    "语义构建任务已提交",
  );
}
const publish = (id: string) => askdata.publish(id);
const retire = (id: string) => askdata.retire(id);
const cancel = (id: string) => askdata.cancel(id);
const rebuild = (connection: string) => askdata.createVersion(connection, versionBody());
</script>

<template>
  <section class="builds">
    <div class="page-heading">
      <h2>语义构建</h2>
      <div class="heading-actions">
        <el-select
          v-if="canManage"
          v-model="snapshotId"
          class="snapshot-select"
          :disabled="!connectionId"
        >
          <el-option :label="autoSnapshotLabel" value="" />
          <el-option
            v-for="item in snapshots.items"
            :key="item.id"
            :label="`${item.label}${item.recommended ? ' · 推荐' : ''} · ${sourceOriginLabels[item.origin] || item.origin}`"
            :value="item.id"
          />
        </el-select>
        <el-button
          v-if="canManage"
          type="primary"
          :disabled="!connectionId || busy"
          @click="start"
        >发起构建</el-button>
      </div>
    </div>
    <VersionPicker
      v-model="versionId"
      :refresh-key="refreshKey"
      @versions="rows = $event"
      @connection="connectionId = $event"
    />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-table v-loading="busy" :data="rows" empty-text="该连接还没有构建任务" border>
      <el-table-column label="创建时间" width="150">
        <template #default="{ row }">{{ date(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="模型版本" width="120">
        <template #default="{ row }">
          <el-tag :type="row.active ? 'success' : 'info'" size="small" effect="plain">
            {{ row.active ? "当前启用" : versionStatus[row.status] || row.status }}
          </el-tag>
          <div v-if="row.context?.source_origin && row.context.source_origin !== 'none'" class="source-tag">
            {{ sourceOriginLabels[String(row.context.source_origin)] || row.context.source_origin }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="构建状态" width="120">
        <template #default="{ row }">
          <el-tag v-if="row.build" :type="statusType(row.build.status)" size="small" effect="plain">
            {{ buildStatus[row.build.status] || row.build.status }}
          </el-tag>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="当前步骤" width="150">
        <template #default="{ row }">{{ stepLabel(row.build?.step || "") }}</template>
      </el-table-column>
      <el-table-column label="结果与原因" min-width="240">
        <template #default="{ row }">
          <div class="result-cells">
            <span v-for="item in counts(row.build?.detail)" :key="item" class="count-chip">{{ item }}</span>
            <span v-if="failure(row.build?.error_code)" class="failure">
              {{ failure(row.build?.error_code) }}
            </span>
            <span v-if="row.build?.cancel_requested && row.build.status === 'running'" class="failure">正在取消</span>
            <span v-if="!counts(row.build?.detail).length && !row.build?.error_code">—</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="canManage && row.status === 'ready' && !row.active"
            link
            type="primary"
            @click="run(() => publish(row.id), '已启用该模型版本')"
          >启用</el-button>
          <el-button
            v-if="canManage && row.active"
            link
            type="primary"
            @click="run(() => retire(row.id), '已停用该模型版本')"
          >停用</el-button>
          <el-button
            v-if="canManage && row.build && ['queued', 'running'].includes(row.build.status)"
            link
            type="primary"
            @click="run(() => cancel(row.id), '已请求取消')"
          >取消</el-button>
          <el-button
            v-if="canManage && !['queued', 'running'].includes(row.build?.status || '')"
            link
            type="primary"
            @click="run(() => rebuild(row.connection_id), '重新构建已提交')"
          >重新构建</el-button>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<style scoped>
.builds {
  display: grid;
  gap: 12px;
  min-width: 0;
}
.page-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.heading-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.snapshot-select {
  width: 260px;
}
.source-tag {
  margin-top: 4px;
  color: var(--muted, #8993a4);
  font-size: 12px;
}
h2 {
  margin: 0;
  font-size: 18px;
}
.result-cells {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.count-chip {
  padding: 1px 6px;
  border: 1px solid var(--border, #303644);
  border-radius: 4px;
  font-size: 12px;
  color: var(--muted, #8993a4);
}
.failure {
  color: #f2557f;
  font-size: 12px;
}
</style>
