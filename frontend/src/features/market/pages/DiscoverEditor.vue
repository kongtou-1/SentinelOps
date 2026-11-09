<script setup lang="ts">
import { confirmOperation } from "@/utils/confirmOperation";
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, type FormInstance } from "element-plus";
import { api } from "@/api/client";
import {
  emptyContent,
  getCategories,
  getEntry,
  pricingNames,
  type Category,
  type MarketKind,
  type Revision,
} from "@/features/market/api";
import { useAuth } from "@/features/auth/store";
const route = useRoute(),
  router = useRouter(),
  auth = useAuth();
const form = reactive(emptyContent()),
  categories = ref<Category[]>([]),
  base = ref<Revision>(),
  kind = ref<MarketKind>(route.query.kind === "tool" ? "tool" : "skill");
const formRef = ref<FormInstance>(),
  busy = ref(false),
  loading = ref(true),
  error = ref("");
const entryId = ref(String(route.params.id || ""));
const disabled = ref(false);
const isSkill = computed(() => kind.value === "skill");
onMounted(async () => {
  try {
    categories.value = await getCategories();
    if (entryId.value) {
      const entry = await getEntry(entryId.value, true);
      if (entry.owner_id !== auth.user?.id)
        throw new Error("只能编辑本人的提交");
      kind.value = entry.kind;
      base.value = entry.revision;
      disabled.value = entry.revision.status === "pending";
      for (const key of Object.keys(emptyContent()) as (keyof typeof form)[])
        Object.assign(form, { [key]: entry.revision[key] });
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
    disabled.value = true;
  } finally {
    loading.value = false;
  }
});
async function save(submit = false) {
  if (busy.value || disabled.value) return;
  try {
    await formRef.value?.validate();
  } catch {
    return;
  }
  busy.value = true;
  try {
    if (
      submit &&
      !(await confirmOperation("保存当前内容并提交管理员审核？", "提交审核"))
    )
      return;
    const result = await api<{ id: string; revision: Revision }>(
      entryId.value
        ? `/market/entries/${entryId.value}/draft`
        : "/market/entries",
      entryId.value ? "PUT" : "POST",
      entryId.value
        ? {
            ...form,
            base_revision_id: base.value?.id,
            expected_version: base.value?.edit_version,
          }
        : { ...form, kind: kind.value },
    );
    entryId.value = result.id;
    base.value = result.revision;
    if (submit) {
      try {
        await api(`/market/revisions/${result.revision.id}/submit`, "POST", {
          expected_version: result.revision.edit_version,
        });
        ElMessage.success("已提交，等待管理员审核");
      } catch (e) {
        ElMessage.warning(
          `草稿已保存；${e instanceof Error ? e.message : "提交审核失败"}`,
        );
        await router.replace({
          path: `/discover/items/${result.id}/edit`,
          query: { kind: kind.value },
        });
        return;
      }
    } else ElMessage.success("草稿已保存");
    await router.push({
      path: `/discover/items/${result.id}`,
      query: { preview: "1", kind: kind.value },
    });
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="page-heading">
    <div>
      <router-link to="/discover/mine">← 我的提交</router-link>
      <h1 class="editor-title">{{ entryId ? "编辑内容" : "提交新内容" }}</h1>
      <p>保存草稿后可以继续修改；提交审核并通过后才会公开展示。</p>
    </div>
  </div>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
    class="error-box"
  />
  <el-alert
    v-if="base?.status === 'pending'"
    title="当前内容正在审核，请从详情页撤回后再修改。"
    type="warning"
    :closable="false"
    class="error-box"
  />
  <el-alert
    v-if="base?.status === 'approved'"
    title="本次修改会生成新版本，审核通过前仍展示已发布版本。"
    type="info"
    :closable="false"
    class="error-box"
  />
  <section v-loading="loading" class="panel market-editor">
    <el-form
      ref="formRef"
      :model="form"
      label-position="top"
      :disabled="busy || loading || disabled"
      @submit.prevent
    >
      <el-form-item label="内容类型" required
        ><el-radio-group v-model="kind" :disabled="!!entryId"
          ><el-radio-button value="skill">Skill 市场</el-radio-button
          ><el-radio-button value="tool"
            >实用工具</el-radio-button
          ></el-radio-group
        ></el-form-item
      >
      <div class="editor-grid">
        <el-form-item
          label="名称"
          prop="title"
          :rules="[{ required: true, message: '请填写名称', trigger: 'blur' }]"
          ><el-input
            v-model="form.title"
            maxlength="100"
            show-word-limit /></el-form-item
        ><el-form-item
          label="分类"
          prop="category_id"
          :rules="[
            { required: true, message: '请选择分类', trigger: 'change' },
          ]"
          ><el-select v-model="form.category_id" placeholder="请选择内置分类"
            ><el-option
              v-for="category in categories"
              :key="category.id"
              :value="category.id"
              :label="category.name + (category.enabled ? '' : '（已停用）')"
              :disabled="!category.enabled" /></el-select
        ></el-form-item>
      </div>
      <el-form-item
        label="一句话简介"
        prop="summary"
        :rules="[{ required: true, message: '请填写简介', trigger: 'blur' }]"
        ><el-input
          v-model="form.summary"
          type="textarea"
          :rows="2"
          maxlength="300"
          show-word-limit
      /></el-form-item>
      <el-form-item label="标签（最多 8 个，输入后回车添加）"
        ><el-select
          v-model="form.tags"
          multiple
          filterable
          allow-create
          default-first-option
          :multiple-limit="8"
          placeholder="例如：Excel、文献、翻译"
      /></el-form-item>
      <el-form-item label="详细介绍（支持 Markdown）"
        ><el-input
          v-model="form.description"
          type="textarea"
          :rows="7"
          maxlength="20000"
          placeholder="介绍能解决的问题、适用场景和主要功能"
      /></el-form-item>
      <el-form-item label="使用说明（支持 Markdown）"
        ><el-input
          v-model="form.usage"
          type="textarea"
          :rows="6"
          maxlength="10000"
          placeholder="说明如何获取、配置和使用"
      /></el-form-item>
      <div class="editor-grid">
        <el-form-item :label="isSkill ? '仓库／下载链接' : '工具官网'"
          ><el-input
            v-model="form.target_url"
            maxlength="2000"
            placeholder="https://..." /></el-form-item
        ><el-form-item label="适用平台"
          ><el-input
            v-model="form.platform"
            maxlength="200"
            :placeholder="
              isSkill ? '例如：Codex、Claude Code' : '例如：Windows、macOS、Web'
            " /></el-form-item
        ><el-form-item label="作者／来源名称"
          ><el-input v-model="form.author" maxlength="150" /></el-form-item
        ><el-form-item label="来源链接（选填）"
          ><el-input
            v-model="form.source_url"
            maxlength="2000"
            placeholder="https://..."
        /></el-form-item>
      </div>
      <template v-if="isSkill"
        ><div class="editor-grid">
          <el-form-item label="Skill 版本（选填）"
            ><el-input
              v-model="form.skill_version"
              maxlength="100" /></el-form-item
          ><el-form-item label="许可证（选填）"
            ><el-input
              v-model="form.license"
              maxlength="100"
              placeholder="例如：MIT；未知可留空"
          /></el-form-item>
        </div>
        <el-form-item label="依赖与所需权限"
          ><el-input
            v-model="form.requirements"
            type="textarea"
            :rows="3"
            maxlength="2000"
            placeholder="说明所需软件、服务账号和访问权限" /></el-form-item
      ></template>
      <template v-else
        ><el-form-item label="收费方式"
          ><el-select v-model="form.pricing"
            ><el-option
              v-for="(label, value) in pricingNames"
              :key="value"
              :label="label"
              :value="value" /></el-select></el-form-item
        ><el-form-item label="推荐理由"
          ><el-input
            v-model="form.reason"
            type="textarea"
            :rows="3"
            maxlength="2000"
            placeholder="说明工具的实用之处和推荐场景" /></el-form-item
      ></template>
      <div class="actions editor-footer">
        <el-button :loading="busy" @click="save(false)">保存草稿</el-button
        ><el-button type="primary" :loading="busy" @click="save(true)"
          >保存并提交审核</el-button
        >
      </div>
    </el-form>
  </section>
</template>
<style scoped>
.editor-title {
  margin-top: 16px;
}
.market-editor {
  max-width: 960px;
  margin: auto;
}
.editor-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 24px;
}
.market-editor :deep(.el-select) {
  width: 100%;
}
.editor-footer {
  border-top: 1px solid var(--border);
  padding-top: 24px;
  justify-content: flex-end;
}
@media (max-width: 700px) {
  .editor-grid {
    grid-template-columns: 1fr;
  }
}
</style>
