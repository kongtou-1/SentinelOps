<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api, request, query, type Models } from "@/api/client";
import { usePoll } from "@/composables/usePoll";
import { confirmOperation } from "@/utils/confirmOperation";
import { date } from "@/utils/display";
type Entry = Models["RecycleEntry"];
const rows = ref<Entry[]>([]),
  types = ref<Models["RecycleType"][]>([]),
  kind = ref(""),
  search = ref(""),
  page = ref(1),
  total = ref(0),
  loading = ref(false),
  busy = ref(""),
  error = ref("");
function rowKey(row: Entry) {
  return `${row.kind}:${row.id}`;
}
let revision = 0;
async function load() {
  const current = ++revision;
  loading.value = true;
  try {
    const [result, catalogue] = await Promise.all([
      request<Entry[]>(
        "/system/recycle-bin" +
          query({
            kind: kind.value,
            search: search.value,
            offset: (page.value - 1) * 20,
            limit: 20,
          }),
      ),
      api<Models["RecycleType"][]>("/system/recycle-bin/types"),
    ]);
    if (current !== revision) return;
    rows.value = result.data;
    total.value = result.total || 0;
    types.value = catalogue;
    error.value = "";
  } catch (e) {
    if (current === revision)
      error.value = e instanceof Error ? e.message : String(e);
  } finally {
    if (current === revision) loading.value = false;
  }
}
watch([kind, search], () => {
  if (page.value !== 1) page.value = 1;
  else void load();
});
watch(page, () => void load());
usePoll(load, () => (busy.value ? null : 30000));
async function restore(row: Entry) {
  if (busy.value) return;
  busy.value = row.id;
  try {
    if (
      !(await confirmOperation(
        `恢复${row.label}“${row.name}”？${row.restore_note}。`,
        "恢复资源",
      ))
    )
      return;
    await api(`/system/recycle-bin/${row.kind}/${row.id}/restore`, "POST", {
      expected_deleted_at: row.deleted_at,
    });
    ElMessage.success("资源已恢复");
    if (rows.value.length === 1 && page.value > 1) page.value--;
    else await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    ElMessage.error(error.value);
  } finally {
    busy.value = "";
  }
}
async function remove(row: Entry) {
  if (busy.value) return;
  busy.value = row.id;
  try {
    if (
      !(await confirmOperation(
        `永久删除${row.label}“${row.name}”？记录及其附属数据将无法恢复，仍有关联引用时会拒绝删除。`,
        "永久删除",
      ))
    )
      return;
    await api(`/system/recycle-bin/${row.kind}/${row.id}`, "DELETE", {
      expected_deleted_at: row.deleted_at,
    });
    ElMessage.success("资源已永久删除");
    await load();
    if (page.value > 1 && total.value <= (page.value - 1) * 20)
      page.value = Math.max(1, Math.ceil(total.value / 20));
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    ElMessage.error(error.value);
  } finally {
    busy.value = "";
  }
}
</script>
<template>
  <div>
    <header class="page-header">
      <div class="page-heading">
        <h1>回收站</h1>
        <PageIntro />
        <div class="actions">
          <el-button :loading="loading" :disabled="!!busy" @click="load"
            >刷新</el-button
          >
        </div>
      </div>
    </header>
    <section class="panel">
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <div class="filter-bar">
        <el-input
          v-model="search"
          placeholder="搜索资源名称"
          aria-label="搜索已删除资源"
          clearable
        />
        <el-select
          v-model="kind"
          placeholder="全部资源类型"
          aria-label="资源类型"
          filterable
          clearable
          ><el-option
            v-for="type in types"
            :key="type.kind"
            :label="`${type.label}（${type.count}）`"
            :value="type.kind"
        /></el-select>
      </div>
      <el-table
        v-loading="loading"
        :data="rows"
        :row-key="rowKey"
        empty-text="暂无已删除资源"
      >
        <el-table-column
          prop="name"
          label="资源名称"
          min-width="210"
          show-overflow-tooltip
        />
        <el-table-column prop="label" label="资源类型" width="150" />
        <el-table-column label="删除时间" width="185"
          ><template #default="{ row }">{{
            date(row.deleted_at)
          }}</template></el-table-column
        >
        <el-table-column prop="restore_note" label="恢复方式" min-width="280" />
        <el-table-column label="恢复状态" min-width="220"
          ><template #default="{ row }"
            ><span v-if="row.blocked_reason" class="blocked">{{
              row.blocked_reason
            }}</span
            ><span v-else>可恢复</span></template
          ></el-table-column
        >
        <el-table-column label="操作" width="170" fixed="right"
          ><template #default="{ row }"
            ><el-button
              link
              type="primary"
              :disabled="!!busy || !!row.blocked_reason"
              :loading="busy === row.id"
              @click="restore(row)"
              >恢复</el-button
            ><el-button
              link
              type="danger"
              :disabled="!!busy"
              @click="remove(row)"
              >永久删除</el-button
            ></template
          ></el-table-column
        >
      </el-table>
      <div class="pagination">
        <el-pagination
          v-model:current-page="page"
          :page-size="20"
          :total="total"
          layout="prev, pager, next"
        />
      </div>
    </section>
  </div>
</template>
<style scoped>
.blocked {
  color: var(--el-color-warning);
  overflow-wrap: anywhere;
}
.el-alert {
  margin-bottom: 16px;
}
</style>
