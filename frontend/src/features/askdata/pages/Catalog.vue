<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, ref, watch } from "vue";
import {
  askdata,
  versionStatus,
  type AskDataCatalogSummary,
  type AskDataTableDetail,
  type AskDataTablePage,
  type AskDataVersion,
} from "@/features/askdata/api";
import VersionPicker from "@/features/askdata/components/VersionPicker.vue";

const versionId = ref("");
const version = ref<AskDataVersion | null>(null);
const summary = ref<AskDataCatalogSummary | null>(null);
const page = ref<AskDataTablePage>({ items: [], total: 0 });
const detail = ref<AskDataTableDetail | null>(null);
const search = ref("");
const appliedSearch = ref("");
const schema = ref("");
const offset = ref(0);
const busy = ref(false);
const detailBusy = ref(false);
const error = ref("");
const drawer = ref(false);
const limit = 50;

const schemas = computed(() => summary.value?.schemas || []);
const currentPage = computed(() => Math.floor(offset.value / limit) + 1);

async function load() {
  if (!versionId.value) {
    summary.value = null;
    page.value = { items: [], total: 0 };
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const [catalog, tables] = await Promise.all([
      askdata.catalog(versionId.value),
      askdata.tables(versionId.value, {
        search: appliedSearch.value,
        schema: schema.value || undefined,
        offset: offset.value,
        limit,
      }),
    ]);
    summary.value = catalog;
    page.value = tables;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "数据目录加载失败";
  } finally {
    busy.value = false;
  }
}
function applySearch(value?: string) {
  if (typeof value === "string") schema.value = value;
  appliedSearch.value = search.value.trim();
  offset.value = 0;
  void load();
}
function changePage(next: number) {
  offset.value = (next - 1) * limit;
  void load();
}
async function open(row: { id: string; name: string }) {
  drawer.value = true;
  detail.value = null;
  detailBusy.value = true;
  try {
    detail.value = await askdata.table(versionId.value, row.id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "表详情加载失败";
  } finally {
    detailBusy.value = false;
  }
}
watch(versionId, () => {
  offset.value = 0;
  void load();
});
</script>

<template>
  <section class="askdata-page">
    <div class="page-heading">
      <h1>数据目录</h1>
      <PageIntro />
      <div class="heading-actions">
        <el-tag v-if="version" size="small" effect="plain">
          {{ version.active ? "当前启用" : versionStatus[version.status] || version.status }}
        </el-tag>
        <el-button :loading="busy" @click="load">刷新</el-button>
      </div>
    </div>
    <VersionPicker v-model="versionId" @change="version = $event" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="versionId">
      <div v-if="summary" class="summary">
        <div class="summary-item"><span>数据表</span><strong>{{ summary.tables }}</strong></div>
        <div class="summary-item"><span>字段</span><strong>{{ summary.columns }}</strong></div>
        <div class="summary-item"><span>业务术语</span><strong>{{ summary.terms }}</strong></div>
        <div class="summary-item"><span>关系</span><strong>{{ summary.relationships }}</strong></div>
        <div class="summary-item"><span>Schema</span><strong>{{ summary.schemas.length }}</strong></div>
      </div>
      <div class="toolbar">
        <el-input
          v-model="search"
          class="search"
          placeholder="搜索表名"
          clearable
          @keyup.enter="applySearch()"
          @clear="applySearch()"
        />
        <el-select
          :model-value="schema"
          class="schema-select"
          placeholder="全部 Schema"
          clearable
          @update:model-value="applySearch"
        >
          <el-option v-for="item in schemas" :key="item.name" :label="item.name" :value="item.name" />
        </el-select>
        <el-button type="primary" @click="applySearch()">搜索</el-button>
      </div>
      <el-table
        v-loading="busy"
        :data="page.items"
        empty-text="当前版本没有数据表"
        border
        @row-click="open"
      >
        <el-table-column prop="schema_name" label="Schema" width="140" />
        <el-table-column prop="name" label="数据表" min-width="180" />
        <el-table-column prop="description" label="说明" min-width="200" show-overflow-tooltip />
        <el-table-column prop="column_count" label="字段" width="90" align="right" />
        <el-table-column prop="term_count" label="术语" width="90" align="right" />
        <el-table-column prop="relationship_count" label="关系" width="90" align="right" />
        <el-table-column label="类型" width="130">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.is_junction_table ? "关联表" : row.table_type || "数据表" }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="page.total > limit"
        class="pager"
        layout="prev, pager, next, total"
        :current-page="currentPage"
        :page-size="limit"
        :total="page.total"
        @current-change="changePage"
      />
    </template>
    <el-empty v-else description="请选择数据库连接和模型版本" :image-size="70" />

    <el-drawer v-model="drawer" size="min(860px, 90%)" :title="detail?.name || '表详情'">
      <div v-loading="detailBusy" class="detail">
        <template v-if="detail">
          <p class="detail-meta">{{ detail.schema_name }}.{{ detail.name }} · {{ detail.table_type }}</p>
          <p class="detail-description">{{ detail.description || "暂无说明" }}</p>
          <h3>字段</h3>
          <el-table :data="detail.columns" empty-text="没有字段" size="small" border>
            <el-table-column prop="name" label="字段" min-width="150" />
            <el-table-column prop="data_type" label="类型" width="130" />
            <el-table-column label="主键" width="70" align="center">
              <template #default="{ row }">{{ row.primary_key ? "是" : "" }}</template>
            </el-table-column>
            <el-table-column prop="description" label="说明" min-width="160" show-overflow-tooltip />
            <el-table-column label="业务属性" min-width="140">
              <template #default="{ row }">{{ row.attribute_name || row.term_name || "" }}</template>
            </el-table-column>
            <el-table-column label="样本值" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.sample_values?.join("、") || "" }}</template>
            </el-table-column>
          </el-table>
          <h3>业务术语</h3>
          <div v-if="detail.terms.length" class="chips">
            <el-tag v-for="item in detail.terms" :key="item.id" effect="plain">{{ item.name }}</el-tag>
          </div>
          <p v-else class="detail-meta">暂无</p>
          <h3>关联关系</h3>
          <el-table :data="detail.foreign_keys" empty-text="没有关系" size="small" border>
            <el-table-column label="来源列">
              <template #default="{ row }">{{ row.columns.join(", ") }}</template>
            </el-table-column>
            <el-table-column label="目标">
              <template #default="{ row }">
                {{ row.referenced_schema }}.{{ row.referenced_table }}.{{ row.referenced_columns.join(", ") }}
              </template>
            </el-table-column>
            <el-table-column label="类型" width="110">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ row.kind === "declared" ? "物理外键" : "语义推断" }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
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
  gap: 10px;
}
h1,
h3 {
  margin: 0;
}
h1 {
  font-size: 24px;
}
h3 {
  font-size: 15px;
  margin: 18px 0 8px;
}
.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.summary-item {
  display: grid;
  gap: 4px;
  min-width: 118px;
  padding: 10px 14px;
  border: 1px solid var(--border, #303644);
  border-radius: 8px;
}
.summary-item span {
  font-size: 12px;
  color: var(--muted, #8993a4);
}
.summary-item strong {
  font-size: 20px;
}
.toolbar {
  display: flex;
  gap: 10px;
}
.search {
  width: min(280px, 100%);
}
.schema-select {
  width: 200px;
}
.pager {
  justify-content: flex-end;
}
.detail {
  min-width: 0;
}
.detail-meta {
  margin: 0;
  color: var(--muted, #8993a4);
  font-size: 13px;
}
.detail-description {
  margin: 8px 0 0;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>
