<script setup lang="ts">
import { ref, watch } from "vue";
import { ElMessage } from "element-plus";
import {
  analysisActionLabels,
  askdata,
  type AskDataImportPreview,
  type AskDataImportRecord,
} from "@/features/askdata/api";

const props = defineProps<{ connectionId: string }>();
const preview = ref<AskDataImportPreview | null>(null);
const history = ref<AskDataImportRecord[]>([]);
const busy = ref(false);
const applying = ref(false);
const exporting = ref(false);
const policy = ref<"skip" | "overwrite">("skip");

function reportValue(row: AskDataImportRecord, key: string) {
  const value = (row.report as Record<string, unknown> | undefined)?.[key];
  return value === undefined || value === null ? "—" : String(value);
}
const error = ref("");

async function loadHistory() {
  if (!props.connectionId) {
    history.value = [];
    return;
  }
  try {
    history.value = await askdata.imports(props.connectionId);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "导入记录加载失败";
  }
}

async function exportModel() {
  if (!props.connectionId) return;
  exporting.value = true;
  error.value = "";
  try {
    await askdata.exportModel(props.connectionId);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "导出失败";
  } finally {
    exporting.value = false;
  }
}

async function pickFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file || !props.connectionId) return;
  busy.value = true;
  error.value = "";
  preview.value = null;
  try {
    const text = await file.text();
    const document = JSON.parse(text);
    preview.value = await askdata.previewImport(props.connectionId, {
      filename: file.name,
      document,
    });
  } catch (e) {
    error.value = e instanceof Error ? e.message : "导入预览失败";
  } finally {
    busy.value = false;
  }
}

async function apply() {
  if (!preview.value) return;
  applying.value = true;
  error.value = "";
  try {
    await askdata.applyImport(props.connectionId, preview.value.import_id, {
      conflict_policy: policy.value,
    });
    ElMessage.success("导入已执行");
    preview.value = null;
    await loadHistory();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "导入失败";
  } finally {
    applying.value = false;
  }
}

watch(() => props.connectionId, () => {
  preview.value = null;
  void loadHistory();
}, { immediate: true });
</script>

<template>
  <section class="interchange">
    <div class="heading">
      <h2>导入导出</h2>
      <div class="actions">
        <el-button :loading="exporting" :disabled="!connectionId" @click="exportModel">导出业务模型</el-button>
        <label class="upload">
          <input type="file" accept="application/json,.json" :disabled="!connectionId || busy" @change="pickFile" />
          <el-button :loading="busy" :disabled="!connectionId">选择导入文件</el-button>
        </label>
      </div>
    </div>
    <el-alert
      title="导出仅包含业务定义、代码规则与问数设置；不含连接凭据、问数结果与个人分析。"
      type="info"
      :closable="false"
    />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />

    <div v-if="preview" class="preview">
      <div class="summary">
        <el-tag type="success" effect="plain">新增 {{ preview.counts.create ?? 0 }}</el-tag>
        <el-tag type="warning" effect="plain">冲突 {{ preview.counts.conflict ?? 0 }}</el-tag>
        <el-tag type="info" effect="plain">跳过 {{ preview.counts.skip ?? 0 }}</el-tag>
        <span class="hint">上游提交 {{ preview.upstream_commit.slice(0, 7) }}</span>
      </div>
      <el-alert
        v-for="warning in preview.warnings"
        :key="warning"
        :title="warning"
        type="warning"
        :closable="false"
      />
      <el-table :data="preview.items" max-height="280" border size="small">
        <el-table-column prop="asset" label="资产" width="130" />
        <el-table-column prop="key" label="对象" min-width="200" show-overflow-tooltip />
        <el-table-column label="处理" width="90">
          <template #default="{ row }">{{ analysisActionLabels[row.action] || row.action }}</template>
        </el-table-column>
        <el-table-column prop="reason" label="说明" min-width="180" show-overflow-tooltip />
      </el-table>
      <div class="apply">
        <el-radio-group v-model="policy">
          <el-radio value="skip">保留目标现有内容</el-radio>
          <el-radio value="overwrite">用导入内容覆盖冲突项</el-radio>
        </el-radio-group>
        <el-button type="primary" :loading="applying" @click="apply">执行导入</el-button>
      </div>
    </div>

    <el-table :data="history" empty-text="还没有导入记录" border>
      <el-table-column prop="filename" label="文件" min-width="220" show-overflow-tooltip />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.status === 'applied' ? 'success' : row.status === 'failed' ? 'danger' : 'info'" size="small" effect="plain">
            {{ row.status === "applied" ? "已导入" : row.status === "failed" ? "失败" : "待执行" }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="写入" width="90">
        <template #default="{ row }">{{ reportValue(row, "applied") }}</template>
      </el-table-column>
      <el-table-column label="时间" width="150">
        <template #default="{ row }">{{ String(row.applied_at || row.created_at).slice(0, 16).replace("T", " ") }}</template>
      </el-table-column>
    </el-table>
  </section>
</template>

<style scoped>
.interchange { display: grid; gap: 12px; min-width: 0; }
.heading { display: flex; align-items: center; justify-content: space-between; }
h2 { margin: 0; font-size: 18px; }
.actions { display: flex; gap: 8px; align-items: center; }
.upload input { display: none; }
.preview { display: grid; gap: 10px; padding: 12px; border: 1px solid var(--border, #303644); border-radius: 8px; }
.summary { display: flex; gap: 8px; align-items: center; }
.apply { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.hint { color: var(--muted, #8993a4); font-size: 12px; }
</style>
