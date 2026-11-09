<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { sourceListState } from "@/features/inspections/navigation";
import { computed, onUnmounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { all, api, query, request, type Models } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { date } from "@/utils/display";
import { usePoll } from "@/composables/usePoll";
import {
  errorNames,
  errorText,
  type DataSource,
  type Plan,
} from "@/features/inspections/ui";
import SourceEditor from "@/features/inspections/components/SourceEditor.vue";
import ServiceDatabases from "@/features/applications/components/ServiceDatabases.vue";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
const auth = useAuth();
const rows = ref<DataSource[]>([]),
  preview = ref<Models["InspectionQueryPreviewResponse"]>(),
  previewName = ref(""),
  previewOpen = ref(false),
  search = ref(sourceListState.search),
  page = ref(sourceListState.page),
  total = ref(0),
  error = ref(""),
  loading = ref(false),
  busy = ref("");
const editor = ref(false),
  editing = ref<DataSource>(),
  references = ref(false),
  plans = ref<Plan[]>([]),
  referenceName = ref("");
const view = ref<"api" | "database" | "databases">(sourceListState.view),
  databaseManager = ref<InstanceType<typeof ServiceDatabases>>();
const sourceLabel = computed(() =>
  view.value === "api" ? "API" : "数据库查询",
);
const creatingKind = ref<"api" | "database">("api");
onUnmounted(() => {
  sourceListState.search = search.value;
  sourceListState.page = page.value;
  sourceListState.view = view.value;
});
let revision = 0;
async function load() {
  if (view.value === "databases") return;
  const current = ++revision;
  loading.value = true;
  try {
    const r = await request<DataSource[]>(
      "/inspection-data-sources" +
        query({
          search: search.value,
          kind: view.value,
          offset: (page.value - 1) * 20,
          limit: 20,
        }),
    );
    if (current !== revision) return;
    rows.value = r.data;
    total.value = r.total || 0;
    error.value = "";
  } catch (e) {
    if (current === revision) error.value = errorText(e);
  } finally {
    if (current === revision) loading.value = false;
  }
}
watch(search, () => {
  page.value = 1;
  void load();
});
watch(page, () => void load());
watch(view, (value) => {
  if (page.value !== 1) page.value = 1;
  else if (value !== "databases") void load();
});
usePoll(
  () => load(),
  () => null,
);
function edit(row?: DataSource) {
  creatingKind.value = view.value === "database" ? "database" : "api";
  editing.value = row;
  editor.value = true;
}
async function queryPreview(row: DataSource) {
  busy.value = row.id;
  error.value = "";
  try {
    preview.value = await api(
      `/inspection-data-sources/${row.id}/preview`,
      "POST",
      { limit: 3 },
    );
    previewName.value = row.name;
    previewOpen.value = true;
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = "";
  }
}
async function test(row: DataSource) {
  busy.value = row.id;
  try {
    const r = await api<Models["InspectionConnectionTestResponse"]>(
      `/inspection-data-sources/${row.id}/test`,
      "POST",
    );
    if (r.success) ElMessage.success("测试成功，已取得数据");
    else
      ElMessage.error(
        errorNames[r.error_code || ""] || `连接失败：${r.error_code}`,
      );
    await load();
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = "";
  }
}
async function refs(row: DataSource) {
  try {
    plans.value = await all<Plan>(`/inspection-data-sources/${row.id}/plans`);
    referenceName.value = row.name;
    references.value = true;
  } catch (e) {
    error.value = errorText(e);
  }
}
async function remove(row: DataSource) {
  try {
    await ElMessageBox.confirm(
      `删除数据源“${row.name}”？可从回收站恢复。`,
      "删除数据源",
      {
        type: "warning",
      },
    );
    await api(`/inspection-data-sources/${row.id}`, "DELETE");
    ElMessage.success("数据源已移入回收站");
    await load();
  } catch (e) {
    if (e !== "cancel" && e !== "close") error.value = errorText(e);
  }
}
</script>
<template>
  <section class="inspection-page">
    <header class="page-header">
      <div class="page-heading">
        <h1>数据源管理</h1>
        <PageIntro />

        <div class="actions">
          <el-button
            v-if="view !== 'databases'"
            :loading="loading"
            @click="load"
            >刷新</el-button
          >
          <el-button
            v-if="view !== 'databases' && auth.can('inspections:manage')"
            type="primary"
            @click="edit()"
            >新增{{ sourceLabel }}</el-button
          >
          <el-button
            v-if="view === 'databases' && auth.can('resources:manage')"
            type="primary"
            @click="databaseManager?.create()"
            >接入数据库</el-button
          >
        </div>
      </div>
    </header>
    <el-radio-group v-model="view" aria-label="数据源类型">
      <el-radio-button value="api">API</el-radio-button>
      <el-radio-button value="database">数据库查询</el-radio-button>
      <el-radio-button
        v-if="
          auth.can('service_databases:read') || auth.can('resources:manage')
        "
        value="databases"
        >已接入数据库</el-radio-button
      >
    </el-radio-group>
    <ServiceDatabases
      v-if="view === 'databases'"
      ref="databaseManager"
      embedded
    />
    <template v-else>
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <div class="inspection-toolbar">
        <div class="inspection-filters">
          <el-input
            v-model="search"
            :placeholder="`搜索${sourceLabel}名称`"
            clearable
          />
        </div>
        <span class="inspection-count">共 {{ total }} 个{{ sourceLabel }}</span>
      </div>
      <el-table
        v-loading="loading"
        :data="rows"
        :empty-text="`尚未添加${sourceLabel}`"
        ><el-table-column
          prop="name"
          :label="`${sourceLabel}名称`"
          min-width="190"
        /><el-table-column
          prop="environment"
          label="环境"
          width="100"
        /><el-table-column label="巡检任务使用" width="130"
          ><template #default="{ row }"
            ><el-tag :type="row.enabled ? 'success' : 'info'">{{
              row.enabled ? "允许使用" : "禁止使用"
            }}</el-tag></template
          ></el-table-column
        ><el-table-column label="取数测试" min-width="150"
          ><template #default="{ row }"
            ><el-tag
              :type="
                row.last_test_success === true
                  ? 'success'
                  : row.last_test_success === false
                    ? 'danger'
                    : 'info'
              "
              >{{
                row.last_test_success === true
                  ? "取数成功"
                  : row.last_test_success === false
                    ? "取数失败"
                    : "未测试"
              }}</el-tag
            ></template
          ></el-table-column
        ><el-table-column label="上次测试" min-width="185"
          ><template #default="{ row }">{{
            date(row.last_test_at)
          }}</template></el-table-column
        ><el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }"
            ><div class="inspection-row-actions">
              <el-button
                v-if="auth.can('inspections:manage')"
                link
                type="primary"
                :disabled="!!busy"
                :loading="busy === row.id"
                @click="test(row)"
                >测试取数</el-button
              >
              <el-dropdown trigger="click"
                ><el-button link :disabled="!!busy">更多</el-button
                ><template #dropdown
                  ><el-dropdown-menu>
                    <el-dropdown-item
                      v-if="auth.can('inspections:manage')"
                      @click="queryPreview(row)"
                      >预览数据</el-dropdown-item
                    >
                    <el-dropdown-item @click="refs(row)"
                      >关联任务</el-dropdown-item
                    >
                    <el-dropdown-item
                      v-if="auth.can('inspections:manage')"
                      @click="edit(row)"
                      >编辑</el-dropdown-item
                    >
                    <el-dropdown-item
                      v-if="auth.can('inspections:manage')"
                      divided
                      @click="remove(row)"
                      >删除</el-dropdown-item
                    >
                  </el-dropdown-menu></template
                ></el-dropdown
              >
            </div></template
          ></el-table-column
        >
      </el-table>
      <el-pagination
        v-model:current-page="page"
        :page-size="20"
        :total="total"
        layout="total, prev, pager, next"
      />
    </template>
    <SourceEditor
      v-model="editor"
      :source="editing"
      :initial-kind="creatingKind"
      @saved="load"
    />
    <el-drawer
      v-model="previewOpen"
      :title="`${previewName} · 数据预览`"
      size="min(760px,100vw)"
      ><template v-if="preview"
        ><el-tag
          >{{ preview.records.length }} 个样本 ·
          {{
            Object.keys(preview.next_cursor).length
              ? "还有后续数据"
              : "已到末页"
          }}</el-tag
        ><el-table :data="preview.records"
          ><el-table-column prop="id" label="对象编号" /><el-table-column
            label="映射数据"
            ><template #default="{ row }"
              ><JsonEvidence
                :value="
                  row.facts
                " /></template></el-table-column></el-table></template
    ></el-drawer>
    <el-drawer
      v-model="references"
      :title="`${referenceName} · 使用此配置的任务`"
      size="min(620px,100vw)"
      ><el-table :data="plans" empty-text="暂无任务使用此配置"
        ><el-table-column label="任务名称"
          ><template #default="{ row }"
            ><router-link :to="`/inspections/plans/${row.id}`">{{
              row.name
            }}</router-link></template
          ></el-table-column
        ><el-table-column label="状态" width="100"
          ><template #default="{ row }">{{
            row.enabled ? "已启用" : "已暂停"
          }}</template></el-table-column
        ></el-table
      ></el-drawer
    >
  </section>
</template>
