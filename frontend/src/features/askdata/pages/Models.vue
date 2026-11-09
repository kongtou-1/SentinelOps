<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, defineAsyncComponent, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { useAuth } from "@/features/auth/store";
import {
  askdata,
  versionStatus,
  type AskDataDefinitionInput,
  type AskDataDefinitionPage,
  type AskDataDefinitionResponse,
  type AskDataMetricPage,
  type AskDataRelationshipPage,
  type AskDataTermDetail,
  type AskDataTermPage,
  type AskDataVersion,
  type AskDataVersionDiff,
} from "@/features/askdata/api";
import CodeRules from "@/features/askdata/components/CodeRules.vue";
import VersionPicker from "@/features/askdata/components/VersionPicker.vue";

const ModelGraph = defineAsyncComponent(
  () => import("@/features/askdata/components/ModelGraph.vue"),
);
const auth = useAuth();
const canManage = computed(() => auth.can("askdata:manage"));
const versionId = ref("");
const version = ref<AskDataVersion | null>(null);
const allVersions = ref<AskDataVersion[]>([]);
const tab = ref("terms");
const busy = ref(false);
const error = ref("");
const saving = ref(false);

const terms = ref<AskDataTermPage>({ items: [], total: 0 });
const metrics = ref<AskDataMetricPage>({ items: [], total: 0 });
const relationships = ref<AskDataRelationshipPage>({ items: [], total: 0, declared: 0, semantic: 0 });
const definitions = ref<AskDataDefinitionPage>({ items: [], total: 0, pending_review: 0 });
const termDetail = ref<AskDataTermDetail | null>(null);
const detailOpen = ref(false);
const detailBusy = ref(false);

const compareId = ref("");
const diff = ref<AskDataVersionDiff | null>(null);
const diffBusy = ref(false);

const editorOpen = ref(false);
const form = reactive({
  object_kind: "term" as "term" | "metric",
  object_key: "",
  description: "",
  synonyms: "",
  expression: "",
});

const definitionMap = computed(() => {
  const map = new Map<string, AskDataDefinitionResponse>();
  for (const item of definitions.value.items) {
    if (item.object_kind === "term") map.set(item.object_key, item);
  }
  return map;
});
const otherVersions = computed(() =>
  allVersions.value.filter((item) => item.id !== versionId.value),
);

async function loadAll() {
  if (!versionId.value) {
    terms.value = { items: [], total: 0 };
    metrics.value = { items: [], total: 0 };
    relationships.value = { items: [], total: 0, declared: 0, semantic: 0 };
    definitions.value = { items: [], total: 0, pending_review: 0 };
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const [termPage, metricPage, relationPage, definitionPage] = await Promise.all([
      askdata.terms(versionId.value, { limit: 200 }),
      askdata.metrics(versionId.value, { limit: 200 }),
      askdata.relationships(versionId.value),
      askdata.definitions(versionId.value, { limit: 200 }),
    ]);
    terms.value = termPage;
    metrics.value = metricPage;
    relationships.value = relationPage;
    definitions.value = definitionPage;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "业务模型加载失败";
  } finally {
    busy.value = false;
  }
}
watch(versionId, () => {
  compareId.value = "";
  diff.value = null;
  void loadAll();
});

function editTerm(row: { name: string; description: string; synonyms: string[] }) {
  form.object_kind = "term";
  form.object_key = row.name;
  form.description = row.description;
  form.synonyms = row.synonyms.join("、");
  form.expression = "";
  editorOpen.value = true;
}
function editMetric(row: { name: string; description: string; expression: string }) {
  form.object_kind = "metric";
  form.object_key = row.name;
  form.description = row.description;
  form.expression = row.expression;
  form.synonyms = "";
  editorOpen.value = true;
}
async function save() {
  saving.value = true;
  error.value = "";
  const body: AskDataDefinitionInput = {
    object_kind: form.object_kind,
    object_key: form.object_key,
    description: form.description,
  };
  if (form.object_kind === "term")
    body.synonyms = form.synonyms
      .split(/[、,，\n]/)
      .map((item) => item.trim())
      .filter(Boolean);
  else body.expression = form.expression;
  try {
    await askdata.reviseDefinition(versionId.value, body);
    editorOpen.value = false;
    ElMessage.success("业务口径已保存");
    await loadAll();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "保存失败";
  } finally {
    saving.value = false;
  }
}
async function certify(kind: "term" | "metric", key: string, current: boolean) {
  try {
    await askdata.reviseDefinition(versionId.value, {
      object_kind: kind,
      object_key: key,
      certified: !current,
    });
    ElMessage.success(current ? "已取消认证" : "已认证");
    await loadAll();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "认证失败";
  }
}
async function resolve(row: AskDataDefinitionResponse) {
  try {
    await askdata.resolveDefinition(versionId.value, row.id);
    ElMessage.success("已确认复核");
    await loadAll();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "复核失败";
  }
}
async function showDetail(row: { id: string }) {
  detailOpen.value = true;
  termDetail.value = null;
  detailBusy.value = true;
  try {
    termDetail.value = await askdata.term(versionId.value, row.id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "术语详情加载失败";
  } finally {
    detailBusy.value = false;
  }
}
async function compare() {
  if (!compareId.value) return;
  diffBusy.value = true;
  error.value = "";
  try {
    diff.value = await askdata.diff(versionId.value, compareId.value);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "版本对比失败";
  } finally {
    diffBusy.value = false;
  }
}
function describe(value: Record<string, unknown> | undefined) {
  if (!value) return "—";
  const parts = [value.description, value.expression].filter(Boolean).map(String);
  return parts.join(" · ") || "—";
}
</script>

<template>
  <section class="askdata-page">
    <div class="page-heading">
      <h1>业务模型</h1>
      <PageIntro />
      <div class="heading-actions">
        <el-tag v-if="version" size="small" effect="plain">
          {{ versionStatus[version.status] || version.status }}
        </el-tag>
        <el-tag v-if="definitions.pending_review" type="warning" size="small" effect="plain">
          {{ definitions.pending_review }} 项待复核
        </el-tag>
        <el-button :loading="busy" @click="loadAll">刷新</el-button>
      </div>
    </div>
    <VersionPicker
      v-model="versionId"
      @change="version = $event"
      @versions="allVersions = $event"
    />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="versionId">
      <el-tabs v-model="tab">
        <el-tab-pane label="业务术语" name="terms">
          <el-table v-loading="busy" :data="terms.items" empty-text="当前版本没有业务术语" border>
            <el-table-column prop="name" label="术语" min-width="150" />
            <el-table-column prop="description" label="定义" min-width="220" show-overflow-tooltip />
            <el-table-column label="别名" min-width="160" show-overflow-tooltip>
              <template #default="{ row }">{{ row.synonyms?.join("、") || "" }}</template>
            </el-table-column>
            <el-table-column prop="attribute_count" label="属性" width="80" align="right" />
            <el-table-column label="认证" width="90" align="center">
              <template #default="{ row }">
                <el-tag v-if="definitionMap.get(row.name)?.certified" type="success" size="small" effect="plain">已认证</el-tag>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="210" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="showDetail(row)">详情</el-button>
                <el-button v-if="canManage" link type="primary" @click="editTerm(row)">编辑</el-button>
                <el-button
                  v-if="canManage"
                  link
                  type="primary"
                  @click="certify('term', row.name, !!definitionMap.get(row.name)?.certified)"
                >
                  {{ definitionMap.get(row.name)?.certified ? "取消认证" : "认证" }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="指标口径" name="metrics">
          <el-table v-loading="busy" :data="metrics.items" empty-text="当前版本没有指标表达式" border>
            <el-table-column prop="name" label="指标" min-width="150" />
            <el-table-column prop="description" label="口径说明" min-width="200" show-overflow-tooltip />
            <el-table-column prop="expression" label="表达式" min-width="220" show-overflow-tooltip />
            <el-table-column prop="source" label="来源" width="110" />
            <el-table-column label="认证" width="90" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.certified" type="success" size="small" effect="plain">已认证</el-tag>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="170" fixed="right">
              <template #default="{ row }">
                <el-button v-if="canManage" link type="primary" @click="editMetric(row)">编辑</el-button>
                <el-button
                  v-if="canManage"
                  link
                  type="primary"
                  @click="certify('metric', row.name, !!row.certified)"
                >
                  {{ row.certified ? "取消认证" : "认证" }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="关系图谱" name="graph">
          <p class="section-meta">
            物理外键 {{ relationships.declared }} 条 · 语义推断 {{ relationships.semantic }} 条 · 虚线表示语义推断
          </p>
          <ModelGraph :relationships="relationships.items" />
        </el-tab-pane>

        <el-tab-pane label="代码依据" name="code-rules">
          <CodeRules :version-id="versionId" :can-manage="canManage" />
        </el-tab-pane>

        <el-tab-pane label="版本对比" name="diff">
          <div class="toolbar">
            <el-select v-model="compareId" placeholder="选择对比版本" class="compare-select">
              <el-option
                v-for="item in otherVersions"
                :key="item.id"
                :label="`${String(item.created_at).slice(0, 16).replace('T', ' ')} · ${versionStatus[item.status] || item.status}`"
                :value="item.id"
              />
            </el-select>
            <el-button type="primary" :disabled="!compareId" :loading="diffBusy" @click="compare">对比</el-button>
          </div>
          <template v-if="diff">
            <p class="section-meta">
              新增 {{ diff.summary.added }} · 移除 {{ diff.summary.removed }} · 变更 {{ diff.summary.changed }} ·
              术语 {{ diff.summary.compare_terms }} → {{ diff.summary.terms }} · 指标
              {{ diff.summary.compare_metrics }} → {{ diff.summary.metrics }}
            </p>
            <el-table :data="[...diff.added, ...diff.removed, ...diff.changed]" empty-text="两个版本定义一致" border>
              <el-table-column label="变化" width="90">
                <template #default="{ row }">
                  <el-tag v-if="diff?.added.includes(row)" type="success" size="small" effect="plain">新增</el-tag>
                  <el-tag v-else-if="diff?.removed.includes(row)" type="danger" size="small" effect="plain">移除</el-tag>
                  <el-tag v-else type="warning" size="small" effect="plain">变更</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="object_kind" label="类型" width="110" />
              <el-table-column prop="object_key" label="对象" min-width="150" />
              <el-table-column label="对比版本" min-width="180" show-overflow-tooltip>
                <template #default="{ row }">{{ describe(row.before) }}</template>
              </el-table-column>
              <el-table-column label="当前版本" min-width="180" show-overflow-tooltip>
                <template #default="{ row }">{{ describe(row.after) }}</template>
              </el-table-column>
            </el-table>
          </template>
          <el-empty v-else-if="!otherVersions.length" description="暂无其他版本可对比" :image-size="60" />
        </el-tab-pane>

        <el-tab-pane name="reviews">
          <template #label>待复核 <span v-if="definitions.pending_review">({{ definitions.pending_review }})</span></template>
          <el-table :data="definitions.items.filter((item) => item.review_required)" empty-text="没有待复核定义" border>
            <el-table-column prop="object_kind" label="类型" width="110" />
            <el-table-column prop="object_key" label="对象" min-width="160" />
            <el-table-column prop="review_note" label="原因" min-width="220" show-overflow-tooltip />
            <el-table-column label="认证" width="90" align="center">
              <template #default="{ row }">
                <el-tag v-if="row.certified" type="success" size="small" effect="plain">已认证</el-tag>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button v-if="canManage" link type="primary" @click="resolve(row)">已复核</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </template>
    <el-empty v-else description="请选择数据库连接和模型版本" :image-size="70" />

    <el-dialog v-model="editorOpen" :title="form.object_kind === 'term' ? '编辑业务术语' : '编辑指标口径'" width="520px">
      <el-form label-width="88px">
        <el-form-item label="名称">{{ form.object_key }}</el-form-item>
        <el-form-item label="定义说明">
          <el-input v-model="form.description" type="textarea" :rows="3" maxlength="2000" show-word-limit />
        </el-form-item>
        <el-form-item v-if="form.object_kind === 'term'" label="别名">
          <el-input v-model="form.synonyms" placeholder="多个别名用顿号分隔" />
        </el-form-item>
        <el-form-item v-else label="表达式">
          <el-input v-model="form.expression" type="textarea" :rows="3" maxlength="4000" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailOpen" size="min(720px, 90%)" title="业务术语详情">
      <div v-loading="detailBusy">
        <template v-if="termDetail">
          <h3>{{ termDetail.name }}</h3>
          <p class="section-meta">{{ termDetail.description || "暂无定义" }}</p>
          <p class="section-meta">别名：{{ termDetail.synonyms.join("、") || "暂无" }}</p>
          <el-table :data="termDetail.attributes" empty-text="没有关联属性" size="small" border>
            <el-table-column prop="name" label="属性" min-width="130" />
            <el-table-column prop="description" label="说明" min-width="180" show-overflow-tooltip />
            <el-table-column prop="source_column" label="来源字段" min-width="130" />
            <el-table-column prop="table_name" label="数据表" min-width="130" />
            <el-table-column label="认证" width="90" align="center">
              <template #default="{ row }">{{ row.certified ? "已认证" : "—" }}</template>
            </el-table-column>
          </el-table>
          <h3 class="subheading">关联表</h3>
          <div class="chips">
            <el-tag v-for="item in termDetail.tables" :key="item.id" effect="plain">{{ item.name }}</el-tag>
          </div>
        </template>
      </div>
    </el-drawer>
  </section>
</template>

<style scoped>
.askdata-page {
  display: grid;
  gap: 14px;
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
h1,
h3 {
  margin: 0;
}
h1 {
  font-size: 24px;
}
h3 {
  font-size: 16px;
}
.subheading {
  margin-top: 18px;
}
.section-meta {
  margin: 0 0 10px;
  color: var(--muted, #8993a4);
  font-size: 13px;
}
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}
.compare-select {
  width: 300px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>
