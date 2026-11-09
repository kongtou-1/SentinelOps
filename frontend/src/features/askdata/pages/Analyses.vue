<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, defineAsyncComponent, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useAuth } from "@/features/auth/store";
import {
  askdata,
  runErrorLabel,
  type AskDataAnalysis,
  type AskDataAnalysisRun,
  type AskDataConnection,
  type AskDataResult,
} from "@/features/askdata/api";

const ResultChart = defineAsyncComponent(
  () => import("@/features/askdata/components/ResultChart.vue"),
);

const auth = useAuth();
const canRun = computed(() => auth.can("askdata:run"));

const connections = ref<AskDataConnection[]>([]);
const connectionId = ref("");
const search = ref("");
const category = ref("");
const categories = ref<string[]>([]);
const rows = ref<AskDataAnalysis[]>([]);
const total = ref(0);
const busy = ref(false);
const error = ref("");
const dialog = ref(false);
const saving = ref(false);
const editingId = ref("");
const runningId = ref("");
const execution = ref<AskDataAnalysisRun | null>(null);
const result = computed<AskDataResult | null>(() => execution.value?.result ?? null);
const resultColumns = computed(() => result.value?.columns ?? []);
const resultRows = computed(() =>
  (result.value?.rows ?? []).map((row) => {
    const item: Record<string, unknown> = {};
    resultColumns.value.forEach((_, index) => {
      item[`c${index}`] = row[index] ?? null;
    });
    return item;
  }),
);

const form = reactive({
  connection_id: "",
  name: "",
  category: "",
  question: "",
  sql_text: "",
  explanation: "",
});

function connectionLabel(row: AskDataConnection) {
  return `${row.name} · ${row.database}（${row.environment}）`;
}
function formatTime(value: string | null | undefined) {
  return value ? String(value).slice(0, 16).replace("T", " ") : "—";
}
function statusType(status: string): "success" | "danger" | "info" {
  if (status === "completed") return "success";
  if (status === "failed") return "danger";
  return "info";
}
function statusText(row: AskDataAnalysis) {
  if (!row.last_status) return "未执行";
  if (row.last_status === "completed") return "执行成功";
  return runErrorLabel(row.last_error_code) || "执行失败";
}

async function loadConnections() {
  try {
    connections.value = await askdata.connections();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "数据库连接加载失败";
  }
}

async function load() {
  busy.value = true;
  error.value = "";
  try {
    const page = await askdata.analyses({
      connection_id: connectionId.value || undefined,
      search: search.value || undefined,
      category: category.value || undefined,
      limit: 100,
    });
    rows.value = page.items;
    total.value = page.total;
    categories.value = page.categories;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "分析库加载失败";
  } finally {
    busy.value = false;
  }
}

function reset() {
  editingId.value = "";
  form.connection_id = connectionId.value || connections.value[0]?.id || "";
  form.name = "";
  form.category = "";
  form.question = "";
  form.sql_text = "";
  form.explanation = "";
}

function openCreate() {
  reset();
  dialog.value = true;
}

function openEdit(row: AskDataAnalysis) {
  editingId.value = row.id;
  form.connection_id = row.connection_id;
  form.name = row.name;
  form.category = row.category;
  form.question = row.question;
  form.sql_text = row.sql_text;
  form.explanation = row.explanation;
  dialog.value = true;
}

async function save() {
  saving.value = true;
  error.value = "";
  try {
    const body = {
      name: form.name,
      category: form.category,
      question: form.question,
      sql_text: form.sql_text,
      explanation: form.explanation,
      chart: {},
      evidence: [],
    };
    if (editingId.value) await askdata.updateAnalysis(editingId.value, body);
    else await askdata.createAnalysis({ ...body, connection_id: form.connection_id });
    dialog.value = false;
    ElMessage.success("分析已保存");
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "保存失败";
  } finally {
    saving.value = false;
  }
}

async function execute(row: AskDataAnalysis) {
  runningId.value = row.id;
  error.value = "";
  execution.value = null;
  try {
    execution.value = await askdata.executeAnalysis(row.id);
    ElMessage.success("已按当前权限重新执行");
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "执行失败";
  } finally {
    runningId.value = "";
  }
}

async function remove(row: AskDataAnalysis) {
  try {
    await ElMessageBox.confirm(`删除分析「${row.name}」？`, "确认删除", {
      confirmButtonText: "删除",
      cancelButtonText: "取消",
      type: "warning",
    });
  } catch {
    return;
  }
  try {
    await askdata.deleteAnalysis(row.id);
    if (execution.value) execution.value = null;
    ElMessage.success("已删除");
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "删除失败";
  }
}

onMounted(async () => {
  await loadConnections();
  await load();
});
</script>

<template>
  <section class="analyses">
    <div class="page-heading">
      <h1>分析库</h1>
      <PageIntro />
      <el-button v-if="canRun" type="primary" @click="openCreate">保存分析</el-button>
    </div>
    <div class="filters">
      <el-select v-model="connectionId" clearable placeholder="全部数据库连接" class="filter" @change="load">
        <el-option v-for="row in connections" :key="row.id" :label="connectionLabel(row)" :value="row.id" />
      </el-select>
      <el-select v-model="category" clearable placeholder="全部分类" class="filter" @change="load">
        <el-option v-for="item in categories" :key="item" :label="item" :value="item" />
      </el-select>
      <el-input v-model="search" class="search" placeholder="搜索名称或问题" clearable @change="load" />
      <el-button :loading="busy" @click="load">刷新</el-button>
      <span class="count">共 {{ total }} 条</span>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />

    <el-table v-loading="busy" :data="rows" empty-text="还没有保存的分析" border>
      <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip />
      <el-table-column prop="question" label="问题" min-width="220" show-overflow-tooltip />
      <el-table-column prop="category" label="分类" width="100">
        <template #default="{ row }">{{ row.category || "—" }}</template>
      </el-table-column>
      <el-table-column label="最近执行" width="150">
        <template #default="{ row }">
          <el-tag :type="statusType(row.last_status)" size="small" effect="plain">
            {{ statusText(row) }}
          </el-tag>
          <div class="muted">{{ formatTime(row.last_executed_at) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="结果" width="110">
        <template #default="{ row }">
          <span v-if="row.last_row_count !== null && row.last_row_count !== undefined">{{ row.last_row_count }} 行</span>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column v-if="canRun" label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button
            link
            type="primary"
            :loading="runningId === row.id"
            @click="execute(row)"
          >执行</el-button>
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <section v-if="execution && result" class="result">
      <div class="result-heading">
        <h2>执行结果</h2>
        <span class="muted">{{ execution.duration_ms }}ms · {{ result.row_count ?? 0 }} 行 · {{ result.truncated ? "结果已截断" : "结果完整" }}</span>
      </div>
      <el-alert v-if="result.answer" :title="result.answer" type="success" :closable="false" />
      <ResultChart v-if="result.chart?.y?.length" :result="result" />
      <el-table :data="resultRows" border size="small" max-height="380">
        <el-table-column
          v-for="(column, index) in resultColumns"
          :key="column.name"
          :prop="`c${index}`"
          :label="column.name"
          min-width="120"
          show-overflow-tooltip
        />
      </el-table>
      <el-collapse>
        <el-collapse-item title="实际 SQL" name="sql">
          <pre class="sql">{{ result.executed_sql || result.sql }}</pre>
        </el-collapse-item>
      </el-collapse>
    </section>

    <el-dialog v-model="dialog" :title="editingId ? '编辑分析' : '保存分析'" width="720px">
      <el-form label-width="80px">
        <el-form-item label="连接">
          <el-select v-model="form.connection_id" :disabled="Boolean(editingId)" style="width: 100%">
            <el-option v-for="row in connections" :key="row.id" :label="connectionLabel(row)" :value="row.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" maxlength="200" />
        </el-form-item>
        <el-form-item label="分类">
          <el-input v-model="form.category" maxlength="60" placeholder="如 订单、财务" />
        </el-form-item>
        <el-form-item label="问题">
          <el-input v-model="form.question" maxlength="2000" />
        </el-form-item>
        <el-form-item label="SQL">
          <el-input v-model="form.sql_text" type="textarea" :rows="8" placeholder="只读查询；执行时会重新校验授权范围" />
        </el-form-item>
        <el-form-item label="业务说明">
          <el-input v-model="form.explanation" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.analyses { display: grid; gap: 12px; min-width: 0; }
.page-heading { display: flex; align-items: center; justify-content: space-between; }
h1 { margin: 0; font-size: 24px; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.filter { width: 220px; }
.search { width: 240px; }
.count { color: var(--muted, #8993a4); font-size: 12px; }
.result { display: grid; gap: 10px; }
.result-heading { display: flex; align-items: baseline; justify-content: space-between; }
.result-heading h2 { margin: 0; font-size: 18px; }
.muted { color: var(--muted, #8993a4); font-size: 12px; }
.sql { margin: 0; padding: 10px; background: var(--panel, #151a23); border-radius: 6px; font-size: 12px; white-space: pre-wrap; }
</style>
