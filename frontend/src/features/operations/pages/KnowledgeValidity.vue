<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { ref } from "vue";
import { api, all } from "@/api/client";
import { readKnowledgeValidityPage } from "@/features/operations/api";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import type { Row } from "@/types/records";
const auth = useAuth(),
  rows = ref<Row[]>([]),
  services = ref<Row[]>([]),
  users = ref<Row[]>([]),
  selected = ref<Row>(),
  form = ref<Row>({}),
  versions = ref(""),
  error = ref(""),
  page = ref(1),
  pageSize = ref(20),
  total = ref(0),
  loading = ref(false),
  busy = ref(false);
async function load() {
  loading.value = true;
  try {
    const result = await readKnowledgeValidityPage(page.value, pageSize.value);
    total.value = result.total;
    const lastPage = Math.max(1, Math.ceil(total.value / pageSize.value));
    if (page.value > lastPage) {
      page.value = lastPage;
      await load();
      return;
    }
    rows.value = result.data;
    if (auth.can("knowledge:manage"))
      [services.value, users.value] = await Promise.all([
        all<Row>("/business-services"),
        all<Row>("/incidents/assignees"),
      ]);
    error.value = "";
  } catch (e) {
    error.value = String(e);
  } finally {
    loading.value = false;
  }
}
const { refresh } = usePoll(load, () => (selected.value ? null : 60000));
function open(row: Row) {
  selected.value = row;
  form.value = {
    expected_version: row.version,
    application_ids: [...row.application_ids],
    owner_id: row.owner_id,
    review_at: row.review_at,
    expires_at: row.expires_at,
  };
  versions.value = row.applicable_versions.join(",");
}
async function save() {
  busy.value = true;
  try {
    await api(
      `/${selected.value!.kind === "document" ? "knowledge" : "skills"}/${selected.value!.id}/validity`,
      "PUT",
      {
        ...form.value,
        applicable_versions: versions.value
          .split(/[,，\n]/)
          .map((s) => s.trim())
          .filter(Boolean),
      },
    );
    selected.value = undefined;
    await refresh();
  } catch (e) {
    error.value = String(e);
  } finally {
    busy.value = false;
  }
}
function state(row: Row) {
  return !row.enabled
    ? "停用"
    : row.expires_at && new Date(row.expires_at).getTime() <= Date.now()
      ? "已过期"
      : "有效";
}
</script>
<template>
  <div class="page-heading">
    <h1>知识有效期</h1>
    <PageIntro />
    <el-button :disabled="loading" @click="refresh">刷新</el-button>

  </div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <section class="panel">
    <el-table v-loading="loading" :data="rows"
      ><el-table-column prop="title" label="知识" /><el-table-column
        label="类型"
        ><template #default="{ row }">{{
          row.kind === "document" ? "文档" : "Skill"
        }}</template></el-table-column
      ><el-table-column label="状态"
        ><template #default="{ row }">{{
          state(row)
        }}</template></el-table-column
      ><el-table-column label="适用服务"
        ><template #default="{ row }">{{
          row.application_ids
            .map(
              (id: string) =>
                services.find((s) => s.id === id)?.name || id.slice(0, 8),
            )
            .join("、") || "全部服务"
        }}</template></el-table-column
      ><el-table-column label="适用版本"
        ><template #default="{ row }">{{
          row.applicable_versions.join("、") || "全部版本"
        }}</template></el-table-column
      ><el-table-column label="复核时间"
        ><template #default="{ row }">{{
          date(row.review_at)
        }}</template></el-table-column
      ><el-table-column label="到期时间"
        ><template #default="{ row }">{{
          date(row.expires_at)
        }}</template></el-table-column
      ><el-table-column v-if="auth.can('knowledge:manage')" label="操作"
        ><template #default="{ row }"
          ><el-button text @click="open(row)">编辑有效期</el-button></template
        ></el-table-column
      ></el-table
    ><el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      :page-sizes="[20, 50, 100]"
      :disabled="loading"
      layout="sizes, prev, pager, next, jumper"
      @size-change="page = 1"
      @change="refresh"
    />
  </section>
  <el-dialog
    :model-value="!!selected"
    title="知识适用范围与有效期"
    @close="selected = undefined"
    ><el-form label-width="100px"
      ><el-form-item label="适用服务"
        ><el-select v-model="form.application_ids" multiple clearable
          ><el-option
            v-for="s in services"
            :key="s.id"
            :label="s.name"
            :value="s.id" /></el-select></el-form-item
      ><el-form-item label="适用版本"
        ><el-input v-model="versions" /></el-form-item
      ><el-form-item label="负责人"
        ><el-select v-model="form.owner_id" clearable
          ><el-option
            v-for="u in users"
            :key="u.id"
            :value="u.id"
            :label="u.username" /></el-select></el-form-item
      ><el-form-item label="复核时间"
        ><el-date-picker
          v-model="form.review_at"
          type="datetime"
          value-format="YYYY-MM-DDTHH:mm:ssZ"
          clearable /></el-form-item
      ><el-form-item label="到期时间"
        ><el-date-picker
          v-model="form.expires_at"
          type="datetime"
          value-format="YYYY-MM-DDTHH:mm:ssZ"
          clearable /></el-form-item></el-form
    ><template #footer
      ><el-button type="primary" :loading="busy" @click="save"
        >保存</el-button
      ></template
    ></el-dialog
  >
</template>
