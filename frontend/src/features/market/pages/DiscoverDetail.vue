<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { confirmOperation } from "@/utils/confirmOperation";
import { api } from "@/api/client";
import {
  auditNames,
  getCategories,
  getEntry,
  statusNames,
  type Category,
  type Entry,
} from "@/features/market/api";
import { useAuth } from "@/features/auth/store";
import { date } from "@/utils/display";
import MarketRevisionBody from "@/features/market/components/MarketRevisionBody.vue";
const route = useRoute(),
  router = useRouter(),
  auth = useAuth(),
  entry = ref<Entry>(),
  categories = ref<Category[]>([]),
  error = ref(""),
  busy = ref(false),
  loading = ref(true),
  selectedId = ref("");
const preview = computed(() => route.query.preview === "1");
const selected = computed(
  () =>
    entry.value?.revisions?.find((r) => r.id === selectedId.value) ||
    entry.value?.revision,
);
const published = computed(() =>
  entry.value?.revisions?.find(
    (r) => r.id === entry.value?.published_revision_id,
  ),
);
const own = computed(() => entry.value?.owner_id === auth.user?.id);
const showComparison = ref(false);
const categoryName = computed(
  () =>
    categories.value.find((c) => c.id === selected.value?.category_id)?.name ||
    entry.value?.category_name,
);
async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [value, cats] = await Promise.all([
      getEntry(String(route.params.id), preview.value),
      getCategories(),
    ]);
    entry.value = value;
    categories.value = cats;
    selectedId.value = value.revision.id;
  } catch (e) {
    entry.value = undefined;
    error.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
}
watch(() => route.fullPath, load);
onMounted(load);
async function action(
  name: "submit" | "withdraw" | "approve" | "reject" | "unpublish",
) {
  if (!entry.value || busy.value) return;
  const latest = entry.value.revision;
  let note = "";
  if (name === "reject" || name === "unpublish") {
    try {
      const result = await ElMessageBox.prompt(
        name === "reject" ? "请填写退回原因" : "请填写下架原因",
        name === "reject" ? "退回修改" : "下架内容",
        {
          inputType: "textarea",
          inputPattern: /\S/,
          inputErrorMessage: "原因不能为空",
          confirmButtonText: "确认",
          cancelButtonText: "取消",
        },
      );
      note = result.value;
    } catch {
      return;
    }
  }
  busy.value = true;
  try {
    if (
      name === "approve" &&
      !(await confirmOperation(
        "审核通过并发布当前资源版本？发布后其他用户可见。",
        "审核并发布",
      ))
    )
      return;
    if (
      name === "withdraw" &&
      !(await confirmOperation("撤回当前资源版本的审核申请？", "撤回审核"))
    )
      return;
    if (
      name === "submit" &&
      !(await confirmOperation("将当前资源版本提交管理员审核？", "提交审核"))
    )
      return;
    if (name === "unpublish")
      await api(`/market/entries/${entry.value.id}/unpublish`, "POST", {
        note,
      });
    else
      await api(
        `/market/revisions/${latest.id}/${name === "approve" || name === "reject" ? "review" : name}`,
        "POST",
        {
          expected_version: latest.edit_version,
          ...(name === "approve" || name === "reject"
            ? { decision: name, note }
            : {}),
        },
      );
    ElMessage.success(
      {
        submit: "已提交审核",
        withdraw: "已撤回",
        approve: "已审核发布",
        reject: "已退回",
        unpublish: "已下架",
      }[name],
    );
    await load();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "操作失败");
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="page-heading">
    <div>
      <router-link
        :to="
          preview
            ? auth.can('market:review') && !own
              ? '/discover/manage'
              : '/discover/mine'
            : entry?.kind === 'tool'
              ? '/discover/tools'
              : '/discover/skills'
        "
        >← 返回列表</router-link
      >
      <h1 class="detail-title">
        {{ preview ? "内容与审核详情" : entry?.revision.title || "内容详情" }}
      </h1>
      <p v-if="entry">
        {{ entry.kind === "skill" ? "Skill 市场" : "实用工具" }} · 提交者
        {{ entry.owner_name }} ·
        {{
          preview
            ? "更新于 " + date(entry.revision.updated_at)
            : "发布于 " + date(entry.published_at)
        }}
      </p>
    </div>
    <div v-if="entry" class="actions">
      <el-button @click="load">刷新</el-button
      ><el-button
        v-if="!preview && (own || auth.can('market:review'))"
        @click="
          router.push({
            path: route.path,
            query: { preview: '1', kind: entry.kind },
          })
        "
        >{{ own ? "管理我的内容" : "审核与管理" }}</el-button
      ><el-button
        v-if="preview && own && entry.revision.status !== 'pending'"
        @click="
          router.push({
            path: `/discover/items/${entry.id}/edit`,
            query: { kind: entry.kind },
          })
        "
        >{{
          entry.revision.status === "approved" ? "创建修改版本" : "编辑内容"
        }}</el-button
      >
    </div>
  </div>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
    class="error-box"
  />
  <div v-loading="loading" class="detail-loading">
    <template v-if="entry && selected">
      <section v-if="preview" class="panel review-controls">
        <div class="actions">
          <el-tag
            :type="
              entry.revision.status === 'rejected'
                ? 'danger'
                : entry.revision.status === 'pending'
                  ? 'warning'
                  : 'info'
            "
            >{{ statusNames[entry.revision.status] }}</el-tag
          ><el-tag :type="entry.listed ? 'success' : 'info'">{{
            entry.listed
              ? "已上架"
              : entry.published_revision_id
                ? "已下架"
                : "未上架"
          }}</el-tag
          ><span class="muted">最新版本 v{{ entry.revision.number }}</span>
        </div>
        <p v-if="entry.revision.review_note" class="review-note">
          审核意见：{{ entry.revision.review_note }}
        </p>
        <div class="actions review-buttons">
          <el-button
            v-if="own && entry.revision.status === 'draft'"
            type="primary"
            :disabled="selected.id !== entry.revision.id"
            :loading="busy"
            @click="action('submit')"
            >提交审核</el-button
          ><el-button
            v-if="own && entry.revision.status === 'pending'"
            :disabled="selected.id !== entry.revision.id"
            :loading="busy"
            @click="action('withdraw')"
            >撤回审核</el-button
          ><template
            v-if="
              auth.can('market:review') && entry.revision.status === 'pending'
            "
            ><el-button
              type="success"
              :disabled="selected.id !== entry.revision.id"
              :loading="busy"
              @click="action('approve')"
              >审核通过并发布</el-button
            ><el-button
              type="danger"
              plain
              :disabled="selected.id !== entry.revision.id"
              :loading="busy"
              @click="action('reject')"
              >退回修改</el-button
            ></template
          ><el-button
            v-if="auth.can('market:review') && entry.listed"
            type="danger"
            plain
            :loading="busy"
            @click="action('unpublish')"
            >下架内容</el-button
          >
        </div>
      </section>
      <div v-if="preview" class="actions history-controls">
        <span>查看版本</span
        ><el-select
          v-model="selectedId"
          style="width: 240px"
          aria-label="查看内容版本"
          ><el-option
            v-for="revision in entry.revisions"
            :key="revision.id"
            :value="revision.id"
            :label="`v${revision.number} · ${statusNames[revision.status]}${revision.id === entry.published_revision_id ? ' · 发布版本' : ''}`" /></el-select
        ><el-checkbox
          v-if="published && selected.id !== published.id"
          v-model="showComparison"
          >对比当前发布版本</el-checkbox
        >
      </div>
      <el-alert
        v-if="preview && selected.id !== entry.revision.id"
        title="当前查看的是历史版本；请切换到最新版本后进行审核操作。"
        type="info"
        :closable="false"
        class="error-box"
      />
      <div
        :class="{
          'revision-comparison':
            preview &&
            showComparison &&
            published &&
            selected.id !== published.id,
        }"
      >
        <section
          v-if="
            preview &&
            showComparison &&
            published &&
            selected.id !== published.id
          "
          class="panel"
        >
          <h2 class="comparison-label">当前发布版本 v{{ published.number }}</h2>
          <MarketRevisionBody
            :revision="published"
            :kind="entry.kind"
            :category-name="
              categories.find((c) => c.id === published?.category_id)?.name
            "
          />
        </section>
        <section class="panel">
          <h2 v-if="preview" class="comparison-label">
            {{
              selected.id === entry.published_revision_id
                ? "发布版本"
                : "提交版本"
            }}
            v{{ selected.number }}
          </h2>
          <MarketRevisionBody
            :revision="selected"
            :kind="entry.kind"
            :category-name="categoryName"
          />
          <p v-if="selected.review_note" class="review-note">
            审核意见：{{ selected.review_note }}
          </p>
        </section>
      </div>
      <section v-if="preview && entry.audit?.length" class="panel">
        <h2>操作记录</h2>
        <el-timeline
          ><el-timeline-item
            v-for="event in entry.audit"
            :key="event.id"
            :timestamp="date(event.created_at)"
            >{{ auditNames[event.action] || event.action }} ·
            {{ event.user_name
            }}<span v-if="event.detail.number">
              · v{{ event.detail.number }}</span
            >
            <p v-if="event.detail.note" class="review-note">
              {{ event.detail.note }}
            </p></el-timeline-item
          ></el-timeline
        >
      </section>
    </template>
  </div>
</template>
<style scoped>
.detail-title {
  margin-top: 16px;
}
.detail-loading {
  min-height: 200px;
}
.review-buttons {
  margin-top: 16px;
}
.review-note {
  margin: 14px 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.history-controls {
  margin-bottom: 20px;
}
.revision-comparison {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  align-items: start;
}
.comparison-label {
  font-size: 14px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
  color: var(--muted);
}
@media (max-width: 850px) {
  .revision-comparison {
    grid-template-columns: 1fr;
  }
}
</style>
