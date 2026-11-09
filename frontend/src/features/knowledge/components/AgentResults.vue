<script setup lang="ts">
import { ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, query, type Models } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { date } from "@/utils/display";

const props = defineProps<{
  business: "diagnosis" | "inspection";
  scopeId: string;
  conversationId?: string;
  runId?: string;
}>();
type Summary = Models["ResultSummary"];
type Detail = Models["ResultDetail"];
const auth = useAuth(),
  router = useRouter();
const rows = ref<Summary[]>([]),
  search = ref(""),
  page = ref(1),
  total = ref(0);
const loading = ref(false),
  error = ref(""),
  selected = ref<Summary>();
const detail = ref<Detail>(),
  section = ref<Detail["section"]>("result");
const drawer = ref(false),
  drafting = ref(false),
  saving = ref(false);
const title = ref(""),
  content = ref("");
let listRevision = 0,
  detailRevision = 0;

async function load() {
  const revision = ++listRevision;
  loading.value = true;
  try {
    const data = await api<Models["ResultPage"]>(
      "/agent-results" +
        query({
          business: props.business,
          scope_id: props.scopeId,
          conversation_id: props.conversationId,
          run_id: props.runId,
          query: search.value,
          offset: (page.value - 1) * 10,
          limit: 10,
        }),
    );
    if (revision !== listRevision) return;
    rows.value = data.items;
    total.value = data.total;
    error.value = "";
  } catch (e) {
    if (revision === listRevision)
      error.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    if (revision === listRevision) loading.value = false;
  }
}
function searchResults() {
  if (page.value !== 1) page.value = 1;
  else void load();
}
watch(page, () => void load());
watch(
  () => [props.business, props.scopeId, props.conversationId, props.runId],
  () => {
    selected.value = undefined;
    drawer.value = false;
    detailRevision++;
    search.value = "";
    searchResults();
  },
  { immediate: true },
);

async function read(offset = 0) {
  if (!selected.value) return;
  const revision = ++detailRevision;
  detail.value = undefined;
  try {
    const data = await api<Detail>(
      `/agent-results/${selected.value.kind}/${selected.value.id}` +
        query({ section: section.value, offset, limit: 6000 }),
    );
    if (revision !== detailRevision) return;
    detail.value = data;
    error.value = "";
  } catch (e) {
    if (revision === detailRevision)
      error.value = e instanceof Error ? e.message : "读取失败";
  }
}
function open(row: Summary) {
  selected.value = row;
  section.value = "result";
  drawer.value = true;
  void read();
}
function draft() {
  if (!selected.value) return;
  title.value = selected.value.title.slice(0, 200);
  content.value = `## 适用场景\n${selected.value.title}\n\n## 结论与依据\n${selected.value.summary}\n\n## 人工核验\n\n## 后续检查\n`;
  drafting.value = true;
}
async function save() {
  if (!selected.value || saving.value) return;
  saving.value = true;
  try {
    const document = await api<Models["DocumentResponse"]>(
      `/agent-results/${selected.value.kind}/${selected.value.id}/knowledge-draft`,
      "POST",
      { title: title.value, content: content.value, tags: [props.business] },
    );
    drafting.value = false;
    ElMessage.success(
      document.enabled ? "已存在对应知识" : "已保存待审核知识草稿",
    );
    await router.push(`/knowledge/${document.id}`);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <section class="agent-results">
    <div class="history-toolbar">
      <el-input
        v-model="search"
        placeholder="搜索结论、对象或检查项"
        clearable
        @keyup.enter="searchResults"
        @clear="searchResults"
      />
      <el-button @click="searchResults">搜索</el-button>
      <el-button @click="load">刷新</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-table
      v-loading="loading"
      :data="rows"
      empty-text="暂无已完成的 Agent 结果"
      @row-click="open"
    >
      <el-table-column label="来源" min-width="190">
        <template #default="{ row }"
          ><el-button link type="primary" @click.stop="open(row)">
            {{ row.title }}</el-button
          >
          <el-tag size="small" type="info">{{
            row.outcome === "conversation"
              ? "历史对话"
              : row.kind === "conversation"
                ? "候选诊断"
                : "历史结果"
          }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column
        prop="summary"
        label="结论"
        min-width="230"
        show-overflow-tooltip
      />
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ date(row.created_at) }}</template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-if="total > 10"
      v-model:current-page="page"
      :page-size="10"
      :total="total"
      layout="prev, pager, next"
    />

    <el-drawer v-model="drawer" title="Agent 结果档案" size="min(760px, 96vw)">
      <template v-if="selected">
        <h3>{{ selected.title }}</h3>
        <div class="history-toolbar">
          <el-tag type="info">历史参考</el-tag>
          <span>{{ date(selected.created_at) }}</span>
          <el-button
            v-if="
              auth.can('knowledge:manage') &&
              selected.outcome !== 'conversation'
            "
            type="primary"
            @click="draft"
            >提炼为知识</el-button
          >
        </div>
        <p class="summary">{{ selected.summary }}</p>
        <el-alert v-if="error" :title="error" type="error" :closable="false" />
        <el-tabs v-model="section" @tab-change="read(0)">
          <el-tab-pane label="结构化结果" name="result" />
          <el-tab-pane label="证据原文" name="evidence" />
          <el-tab-pane label="运行配置与版本" name="configuration" />
          <el-tab-pane
            v-if="selected.kind === 'conversation'"
            label="本轮对话"
            name="messages"
          />
        </el-tabs>
        <template v-if="detail">
          <pre class="content">{{ detail.content }}</pre>
          <div class="history-toolbar">
            <el-button
              :disabled="detail.offset === 0"
              @click="read(Math.max(0, detail.offset - 6000))"
              >上一段</el-button
            >
            <span
              >{{ detail.offset + 1 }}–{{
                detail.offset + detail.content.length
              }}
              / {{ detail.total_chars }}</span
            >
            <el-button
              :disabled="detail.next_offset == null"
              @click="read(detail.next_offset!)"
              >下一段</el-button
            >
          </div>
        </template>
      </template>
    </el-drawer>
    <el-dialog
      v-model="drafting"
      title="提炼为知识草稿"
      width="min(760px, 96vw)"
      append-to-body
    >
      <el-form label-position="top">
        <el-form-item label="标题"
          ><el-input v-model="title" maxlength="200"
        /></el-form-item>
        <el-form-item label="知识正文"
          ><el-input
            v-model="content"
            type="textarea"
            :rows="15"
            maxlength="100000"
        /></el-form-item>
      </el-form>
      <template #footer
        ><el-button @click="drafting = false">取消</el-button>
        <el-button
          type="primary"
          :loading="saving"
          :disabled="!title.trim() || !content.trim()"
          @click="save"
          >保存待审核草稿</el-button
        >
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.history-toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin: 16px 0;
  flex-wrap: wrap;
}
.history-toolbar .el-input {
  flex: 1;
  min-width: 180px;
}
.summary {
  line-height: 1.7;
  white-space: pre-wrap;
}
.content {
  max-height: 52vh;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--el-fill-color-light);
  padding: 18px;
  border-radius: 8px;
  line-height: 1.6;
}
.el-pagination {
  margin-top: 18px;
}
</style>
