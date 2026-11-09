<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox, type FormInstance } from "element-plus";
import { api } from "@/api/client";
import {
  auditNames,
  getCategories,
  listEntries,
  statusNames,
  type Category,
  type Entry,
  type MarketAudit,
} from "@/features/market/api";
import { date } from "@/utils/display";
const props = defineProps<{ manage?: boolean }>(),
  route = useRoute(),
  router = useRouter();
const categories = ref<Category[]>([]),
  entries = ref<Entry[]>([]),
  logs = ref<MarketAudit[]>([]),
  total = ref(0),
  loading = ref(false),
  error = ref("");
const categoryPanel = computed(
  () => props.manage && route.query.panel === "categories",
);
const page = computed(() => Math.max(1, Number(route.query.page) || 1));
const status = ref(
    String(route.query.status ?? (props.manage ? "pending" : "")),
  ),
  kind = ref(String(route.query.kind || "")),
  search = ref(String(route.query.q || ""));
const editingCategory = ref(false),
  categoryId = ref(""),
  saving = ref(false),
  formRef = ref<FormInstance>();
const form = reactive({
  name: "",
  description: "",
  sort_order: 0,
  enabled: true,
  expected_updated_at: "",
});
let sequence = 0;
async function load() {
  const run = ++sequence;
  loading.value = true;
  error.value = "";
  status.value = String(route.query.status ?? (props.manage ? "pending" : ""));
  kind.value = String(route.query.kind || "");
  search.value = String(route.query.q || "");
  try {
    if (categoryPanel.value) {
      const [cats, audit] = await Promise.all([
        getCategories(),
        api<MarketAudit[]>("/market/category-audit"),
      ]);
      if (run === sequence) {
        categories.value = cats;
        logs.value = audit;
      }
    } else {
      const result = await listEntries({
        scope: props.manage ? "manage" : "mine",
        status: status.value,
        kind: kind.value,
        q: search.value,
        limit: 20,
        offset: (page.value - 1) * 20,
      });
      if (run === sequence) {
        entries.value = result.data;
        total.value = result.total || 0;
      }
    }
  } catch (e) {
    if (run === sequence) {
      entries.value = [];
      categories.value = [];
      error.value = e instanceof Error ? e.message : "加载失败";
    }
  } finally {
    if (run === sequence) loading.value = false;
  }
}
function filter(values: Record<string, string | undefined> = {}) {
  void router.push({
    path: route.path,
    query: {
      ...route.query,
      status: status.value,
      kind: kind.value || undefined,
      q: search.value.trim() || undefined,
      page: undefined,
      ...values,
    },
  });
}
function editCategory(category?: Category) {
  categoryId.value = category?.id || "";
  Object.assign(form, {
    name: category?.name || "",
    description: category?.description || "",
    sort_order: category?.sort_order || 0,
    enabled: category?.enabled ?? true,
    expected_updated_at: category?.updated_at || "",
  });
  editingCategory.value = true;
}
async function saveCategory() {
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }
  saving.value = true;
  try {
    await api(
      "/market/categories" + (categoryId.value ? "/" + categoryId.value : ""),
      categoryId.value ? "PUT" : "POST",
      form,
    );
    editingCategory.value = false;
    ElMessage.success("分类已保存");
    await load();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    saving.value = false;
  }
}
async function removeCategory(category: Category) {
  try {
    await ElMessageBox.confirm(
      `删除「${category.name}」？已有内容或历史版本引用的分类请使用停用。`,
      "删除分类",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  try {
    await api(`/market/categories/${category.id}`, "DELETE");
    ElMessage.success("分类已删除");
    await load();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "删除失败");
  }
}
watch(() => route.fullPath, load);
onMounted(load);
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>{{ manage ? "内容管理" : "我的提交" }}</h1>
      <p>
        {{
          manage
            ? "审核 Skill 与实用工具，维护分类和上架内容。"
            : "查看审核进度，继续编辑草稿或更新已发布内容。"
        }}
      </p>
    </div>
    <el-button @click="load">刷新</el-button>
  </div>
  <div v-if="manage" class="actions management-tabs">
    <el-button
      :type="!categoryPanel ? 'primary' : 'default'"
      @click="filter({ panel: undefined })"
      >内容审核与管理</el-button
    ><el-button
      :type="categoryPanel ? 'primary' : 'default'"
      @click="filter({ panel: 'categories' })"
      >分类管理</el-button
    >
  </div>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
    class="error-box"
  />
  <section v-if="categoryPanel" v-loading="loading" class="panel">
    <div class="panel-heading">
      <div>
        <h2>分类管理</h2>
        <p class="muted">
          两个 Tab 共用分类，停用后不可用于新投稿，已有发布内容继续显示。
        </p>
      </div>
      <el-button type="primary" @click="editCategory()">新增分类</el-button>
    </div>
    <el-table :data="categories"
      ><el-table-column
        prop="sort_order"
        label="排序"
        width="80"
      /><el-table-column
        prop="name"
        label="名称"
        min-width="150"
      /><el-table-column
        prop="description"
        label="说明"
        min-width="230"
      /><el-table-column label="状态" width="100"
        ><template #default="{ row }"
          ><el-tag :type="row.enabled ? 'success' : 'info'">{{
            row.enabled ? "启用" : "停用"
          }}</el-tag></template
        ></el-table-column
      ><el-table-column
        prop="count"
        label="已发布"
        width="90"
      /><el-table-column label="操作" width="150"
        ><template #default="{ row }"
          ><div class="table-actions">
            <el-button text type="primary" @click="editCategory(row)"
              >编辑</el-button
            ><el-button text type="danger" @click="removeCategory(row)"
              >删除</el-button
            >
          </div></template
        ></el-table-column
      ></el-table
    >
  </section>
  <section v-else v-loading="loading" class="panel">
    <form class="submission-filters" @submit.prevent="filter()">
      <el-select v-model="kind" aria-label="内容类型" @change="filter()"
        ><el-option label="全部类型" value="" /><el-option
          label="Skill 市场"
          value="skill" /><el-option label="实用工具" value="tool" /></el-select
      ><el-select v-model="status" aria-label="审核状态" @change="filter()"
        ><el-option label="全部状态" value="" /><el-option
          v-for="(label, value) in statusNames"
          :key="value"
          :label="label"
          :value="value" /></el-select
      ><el-input
        v-model="search"
        placeholder="搜索名称、简介或标签"
        clearable
        aria-label="搜索提交内容"
      /><el-button native-type="submit">搜索</el-button>
    </form>
    <el-table :data="entries" empty-text="暂无符合条件的提交"
      ><el-table-column label="内容" min-width="230"
        ><template #default="{ row }"
          ><router-link
            :to="{
              path: `/discover/items/${row.id}`,
              query: { preview: '1', kind: row.kind },
            }"
            >{{ row.revision.title }}</router-link
          >
          <div class="muted row-summary">
            {{ row.revision.summary }}
          </div></template
        ></el-table-column
      ><el-table-column label="类型" width="120"
        ><template #default="{ row }">{{
          row.kind === "skill" ? "Skill 市场" : "实用工具"
        }}</template></el-table-column
      ><el-table-column
        prop="category_name"
        label="分类"
        width="130"
      /><el-table-column
        v-if="manage"
        prop="owner_name"
        label="提交者"
        width="120"
      /><el-table-column label="审核／上架" width="150"
        ><template #default="{ row }"
          ><div>
            {{ statusNames[row.revision.status] }} · v{{ row.revision.number }}
          </div>
          <small>{{
            row.listed
              ? "已上架"
              : row.published_revision_id
                ? "已下架"
                : "未上架"
          }}</small></template
        ></el-table-column
      ><el-table-column label="更新时间" width="180"
        ><template #default="{ row }">{{
          date(row.revision.updated_at)
        }}</template></el-table-column
      ><el-table-column label="操作" width="110"
        ><template #default="{ row }"
          ><router-link
            :to="{
              path: `/discover/items/${row.id}`,
              query: { preview: '1', kind: row.kind },
            }"
            >查看详情</router-link
          ></template
        ></el-table-column
      ></el-table
    >
    <div class="submission-pagination">
      <el-pagination
        v-if="total > 20"
        :current-page="page"
        :page-size="20"
        :total="total"
        layout="prev, pager, next"
        @current-change="filter({ page: String($event) })"
      />
    </div>
  </section>
  <section v-if="categoryPanel && logs.length" class="panel">
    <h2>分类操作记录</h2>
    <el-table :data="logs"
      ><el-table-column label="操作" min-width="160"
        ><template #default="{ row }">{{
          auditNames[row.action] || row.action
        }}</template></el-table-column
      ><el-table-column
        prop="user_name"
        label="操作人"
        min-width="120"
      /><el-table-column label="时间" min-width="180"
        ><template #default="{ row }">{{
          date(row.created_at)
        }}</template></el-table-column
      ></el-table
    >
  </section>
  <el-dialog
    v-model="editingCategory"
    :title="categoryId ? '编辑分类' : '新增分类'"
    width="min(560px, 94vw)"
    :close-on-click-modal="!saving"
    :show-close="!saving"
    ><el-form
      ref="formRef"
      :model="form"
      label-position="top"
      :disabled="saving"
      ><el-form-item
        label="分类名称"
        prop="name"
        :rules="[
          {
            required: true,
            whitespace: true,
            message: '请填写分类名称',
            trigger: 'blur',
          },
        ]"
        ><el-input v-model="form.name" maxlength="60" /></el-form-item
      ><el-form-item label="分类说明"
        ><el-input
          v-model="form.description"
          type="textarea"
          :rows="3"
          maxlength="300" /></el-form-item
      ><el-form-item label="排序（较小的排在前面）" required
        ><el-input-number
          v-model="form.sort_order"
          :min="0"
          :max="10000" /></el-form-item
      ><el-form-item label="启用分类"
        ><el-switch v-model="form.enabled" /></el-form-item></el-form
    ><template #footer
      ><el-button :disabled="saving" @click="editingCategory = false"
        >取消</el-button
      ><el-button type="primary" :loading="saving" @click="saveCategory"
        >保存</el-button
      ></template
    ></el-dialog
  >
</template>
<style scoped>
.management-tabs {
  margin-bottom: 24px;
}
.submission-filters {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
}
.submission-filters .el-select {
  width: 160px;
  flex-shrink: 0;
}
.submission-filters .el-input {
  max-width: 340px;
}
.submission-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 24px;
}
.row-summary {
  margin-top: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
@media (max-width: 700px) {
  .submission-filters {
    flex-wrap: wrap;
  }
  .submission-filters .el-select {
    width: calc(50% - 6px);
  }
}
</style>
