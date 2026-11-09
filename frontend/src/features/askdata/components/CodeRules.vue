<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import {
  askdata,
  conflictStateLabels,
  parseStateLabels,
  ruleKindLabels,
  sourceOriginLabels,
  type AskDataCodeRule,
  type AskDataCodeRuleDetail,
  type AskDataCodeRulePage,
  type AskDataCodeRuleSummary,
} from "@/features/askdata/api";

const props = defineProps<{ versionId: string; canManage: boolean }>();

const emptyPage: AskDataCodeRulePage = {
  items: [],
  total: 0,
  pending_review: 0,
  conflicts: 0,
};
const emptySummary: AskDataCodeRuleSummary = {
  content_hash: "",
  source_origin: "none",
  source_label: "",
  environment: "",
  rule_count: 0,
  file_count: 0,
  static_sql_count: 0,
  dynamic_count: 0,
  pending_review: 0,
  conflicts: 0,
};

const page = ref<AskDataCodeRulePage>({ ...emptyPage });
const summary = ref<AskDataCodeRuleSummary>({ ...emptySummary });
const busy = ref(false);
const error = ref("");
const language = ref("");
const kind = ref("");
const onlyReview = ref(false);
const currentPage = ref(1);
const pageSize = 50;
let loadSequence = 0;

const detailOpen = ref(false);
const detail = ref<AskDataCodeRuleDetail | null>(null);
const detailBusy = ref(false);
const editorOpen = ref(false);
const conflictOpen = ref(false);
const current = ref<AskDataCodeRule | null>(null);
const saving = ref(false);
const form = reactive({ expression: "", description: "", certified: false });

const codeExpression = computed(() =>
  String(current.value?.detail?.code_expression ?? ""),
);
const hasSnapshot = computed(() => !!summary.value.source_snapshot_id);
const identity = computed(() =>
  summary.value.git_commit || summary.value.svn_revision
    ? String(summary.value.git_commit || summary.value.svn_revision).slice(0, 12)
    : "本地源码",
);

async function load() {
  const sequence = ++loadSequence;
  if (!props.versionId) {
    page.value = { ...emptyPage };
    summary.value = { ...emptySummary };
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const [rulePage, ruleSummary] = await Promise.all([
      askdata.codeRules(props.versionId, {
        limit: pageSize,
        offset: (currentPage.value - 1) * pageSize,
        include_superseded: onlyReview.value,
        language: language.value || undefined,
        kind: kind.value || undefined,
        review_required: onlyReview.value ? true : undefined,
      }),
      askdata.codeRuleSummary(props.versionId),
    ]);
    if (sequence !== loadSequence) return;
    page.value = rulePage;
    summary.value = ruleSummary;
  } catch (e) {
    if (sequence === loadSequence) {
      error.value = e instanceof Error ? e.message : "代码依据加载失败";
    }
  } finally {
    if (sequence === loadSequence) busy.value = false;
  }
}
watch(
  () => [props.versionId, language.value, kind.value, onlyReview.value],
  () => {
    if (currentPage.value === 1) void load();
    else currentPage.value = 1;
  },
  { immediate: true },
);
watch(currentPage, () => void load());

function label(map: Record<string, string>, key: string) {
  return map[key] || key;
}
function location(row: AskDataCodeRule) {
  return `${row.path}:${row.start_line}`;
}
function snippetOf(row: AskDataCodeRule) {
  return String(row.detail?.snippet ?? "");
}
function rawSql(row: AskDataCodeRule) {
  return String(row.detail?.raw_sql ?? "");
}
function scope(row: AskDataCodeRule) {
  return String(row.detail?.parse_scope ?? "");
}
async function showDetail(row: AskDataCodeRule) {
  detailOpen.value = true;
  detail.value = null;
  detailBusy.value = true;
  try {
    detail.value = await askdata.codeRule(props.versionId, row.id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "规则详情加载失败";
  } finally {
    detailBusy.value = false;
  }
}
function edit(row: AskDataCodeRule) {
  current.value = row;
  form.expression = row.expression;
  form.description = String(row.detail?.description ?? "");
  form.certified = row.certified;
  editorOpen.value = true;
}
async function run(action: () => Promise<unknown>, message: string) {
  saving.value = true;
  error.value = "";
  try {
    await action();
    ElMessage.success(message);
    editorOpen.value = false;
    conflictOpen.value = false;
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "操作失败";
  } finally {
    saving.value = false;
  }
}
function save() {
  if (!current.value) return;
  const id = current.value.id;
  return run(
    () =>
      askdata.reviseCodeRule(props.versionId, id, {
        expression: form.expression,
        description: form.description,
        certified: form.certified,
      }),
    "代码规则已保存",
  );
}
function certify(row: AskDataCodeRule) {
  return run(
    () =>
      askdata.reviseCodeRule(props.versionId, row.id, {
        certified: !row.certified,
      }),
    row.certified ? "已取消认证" : "已认证",
  );
}
function resolve(row: AskDataCodeRule) {
  return run(
    () => askdata.resolveCodeRule(props.versionId, row.id),
    "已确认复核",
  );
}
function openConflict(row: AskDataCodeRule) {
  current.value = row;
  conflictOpen.value = true;
}
function settle(action: "keep_manual" | "accept_code") {
  if (!current.value) return;
  const id = current.value.id;
  return run(
    () => askdata.resolveCodeRuleConflict(props.versionId, id, action),
    action === "accept_code" ? "已采用代码口径" : "已保留人工口径",
  );
}
</script>

<template>
  <section class="code-rules">
    <div class="summary">
      <el-tag size="small" effect="plain">
        {{ label(sourceOriginLabels, summary.source_origin) }}
      </el-tag>
      <span class="meta">快照 {{ hasSnapshot ? identity : "未关联" }}</span>
      <span class="meta" v-if="summary.environment">环境 {{ summary.environment }}</span>
      <span class="meta">规则 {{ summary.rule_count }}</span>
      <span class="meta">文件 {{ summary.file_count }}</span>
      <span class="meta">静态 SQL {{ summary.static_sql_count }}</span>
      <span class="meta">待确认 {{ summary.dynamic_count }}</span>
      <el-tag v-if="summary.pending_review" type="warning" size="small" effect="plain">
        {{ summary.pending_review }} 项待复核
      </el-tag>
      <el-tag v-if="summary.conflicts" type="danger" size="small" effect="plain">
        {{ summary.conflicts }} 项冲突
      </el-tag>
    </div>
    <p v-if="hasSnapshot" class="meta">{{ summary.source_label }} · {{ identity }}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />

    <div class="toolbar">
      <el-select v-model="language" placeholder="语言" clearable class="filter">
        <el-option label="Python" value="python" />
        <el-option label="Java" value="java" />
      </el-select>
      <el-select v-model="kind" placeholder="规则类型" clearable class="filter">
        <el-option
          v-for="(text, value) in ruleKindLabels"
          :key="value"
          :label="text"
          :value="value"
        />
      </el-select>
      <el-checkbox v-model="onlyReview">只看待复核</el-checkbox>
      <el-button :loading="busy" @click="load">刷新</el-button>
    </div>

    <el-table v-loading="busy" :data="page.items" empty-text="当前版本没有源码业务规则" border>
      <el-table-column label="类型" width="110">
        <template #default="{ row }">{{ label(ruleKindLabels, row.rule_kind) }}</template>
      </el-table-column>
      <el-table-column prop="object_key" label="关联对象" min-width="150" show-overflow-tooltip />
      <el-table-column prop="expression" label="业务口径" min-width="220" show-overflow-tooltip />
      <el-table-column label="代码出处" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">
          <el-button link type="primary" @click="showDetail(row)">{{ location(row) }}</el-button>
        </template>
      </el-table-column>
      <el-table-column label="语言" width="80" prop="language" />
      <el-table-column label="解析" width="110">
        <template #default="{ row }">
          <el-tag :type="row.parse_state === 'static' ? 'success' : 'info'" size="small" effect="plain">
            {{ label(parseStateLabels, row.parse_state) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="认证" width="90" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.certified && row.review_required" type="warning" size="small" effect="plain">认证待复核</el-tag>
          <el-tag v-else-if="row.certified" type="success" size="small" effect="plain">已认证</el-tag>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.conflict_state !== 'none'" :type="row.conflict_state === 'pending' ? 'danger' : 'info'" size="small" effect="plain">
            {{ label(conflictStateLabels, row.conflict_state) }}
          </el-tag>
          <span v-else-if="row.review_required" class="review">待复核</span>
          <span v-else-if="row.superseded" class="review">源码已移除</span>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column v-if="canManage" label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button v-if="!row.superseded" link type="primary" @click="edit(row)">修订</el-button>
          <el-button v-if="!row.superseded" link type="primary" @click="certify(row)">
            {{ row.certified ? "取消认证" : "认证" }}
          </el-button>
          <el-button v-if="row.review_required" link type="primary" @click="resolve(row)">已复核</el-button>
          <el-button v-if="row.conflict_state === 'pending' && !row.superseded" link type="danger" @click="openConflict(row)">处理冲突</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-model:current-page="currentPage"
      :page-size="pageSize"
      :total="page.total"
      layout="total, prev, pager, next"
      hide-on-single-page
    />

    <el-drawer v-model="detailOpen" size="min(760px, 90%)" title="代码依据详情">
      <div v-loading="detailBusy">
        <template v-if="detail">
          <h3>{{ detail.object_key || detail.rule_kind }}</h3>
          <p class="meta">
            {{ label(ruleKindLabels, detail.rule_kind) }} · {{ detail.language }} ·
            {{ location(detail) }} · {{ label(parseStateLabels, detail.parse_state) }}
          </p>
          <p class="meta">
            快照 {{ String(detail.source_snapshot_id || "—").slice(0, 8) }} ·
            提交 {{ detail.git_commit || detail.svn_revision || "本地源码" }} ·
            内容哈希 {{ String(detail.content_hash || "—").slice(0, 12) }}
          </p>
          <p class="meta">解析范围 {{ scope(detail) || "—" }} · 片段哈希 {{ String(detail.snippet_hash || "").slice(0, 12) }}</p>
          <h4>业务口径</h4>
          <pre class="code">{{ detail.expression || "—" }}</pre>
          <template v-if="detail.sql_text">
            <h4>静态 SQL 片段</h4>
            <pre class="code">{{ detail.sql_text }}</pre>
          </template>
          <template v-else-if="rawSql(detail)">
            <h4>未解析片段（仅作依据，不执行）</h4>
            <pre class="code">{{ rawSql(detail) }}</pre>
          </template>
          <h4>源码片段</h4>
          <pre class="code">{{ detail.snippet || snippetOf(detail) || "—" }}</pre>
          <p v-if="String(detail.detail?.code_expression ?? '')" class="meta">
            当前代码口径：{{ String(detail.detail?.code_expression) }}
          </p>
        </template>
      </div>
    </el-drawer>

    <el-dialog v-model="editorOpen" title="修订代码规则" width="560px">
      <el-form label-width="88px">
        <el-form-item label="生效口径">
          <el-input v-model="form.expression" type="textarea" :rows="3" maxlength="4000" />
        </el-form-item>
        <el-form-item label="口径说明">
          <el-input v-model="form.description" type="textarea" :rows="3" maxlength="2000" />
        </el-form-item>
        <el-form-item label="认证">
          <el-switch v-model="form.certified" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="conflictOpen" title="处理口径冲突" width="600px">
      <p class="meta">代码版本变化后，人工口径与当前源码规则不一致。请选择生效口径。</p>
      <h4>当前人工口径</h4>
      <pre class="code">{{ current?.expression || "—" }}</pre>
      <h4>当前代码口径</h4>
      <pre class="code">{{ codeExpression || "—" }}</pre>
      <template #footer>
        <el-button @click="conflictOpen = false">取消</el-button>
        <el-button :loading="saving" @click="settle('keep_manual')">保留人工口径</el-button>
        <el-button type="primary" :loading="saving" @click="settle('accept_code')">采用代码口径</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.code-rules {
  display: grid;
  gap: 12px;
  min-width: 0;
}
.summary,
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.filter {
  width: 160px;
}
.meta {
  margin: 0;
  color: var(--muted, #8993a4);
  font-size: 13px;
}
h3 {
  margin: 0;
  font-size: 16px;
}
h4 {
  margin: 14px 0 6px;
  font-size: 13px;
}
.code {
  margin: 0;
  padding: 10px;
  overflow-x: auto;
  border: 1px solid var(--border, #303644);
  border-radius: 6px;
  background: rgba(20, 26, 36, 0.6);
  color: var(--text, #e6e9ef);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
.review {
  color: #e6a23c;
  font-size: 12px;
}
</style>
