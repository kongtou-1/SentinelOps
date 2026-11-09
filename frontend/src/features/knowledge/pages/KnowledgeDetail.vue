<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
import { ElMessage } from "element-plus";
import { confirmOperation } from "@/utils/confirmOperation";
import { api, type Models } from "@/api/client";
import { date } from "@/utils/display";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import type { Row } from "@/types/records";
import StatusTag from "@/components/common/StatusTag.vue";
import RecordForm from "@/components/records/RecordForm.vue";
const props = defineProps<{ kind: string }>(),
  id = String(useRoute().params.id),
  router = useRouter(),
  auth = useAuth(),
  row = ref<Row>(),
  error = ref(""),
  editing = ref(false),
  busy = ref(false),
  attempt = ref(0),
  md = new MarkdownIt({ html: false, linkify: false });
const rendered = computed(() =>
  DOMPurify.sanitize(md.render(row.value?.content || "")),
);
const agentSource = computed(() =>
  /^agent-result:(diagnosis|conversation|inspection_item|inspection_check):([0-9a-f-]{36})$/.exec(
    row.value?.source || "",
  ),
);
const sourceNames: Record<string, string> = {
  diagnosis: "来自事故诊断",
  conversation: "来自诊断会话",
  inspection_item: "来自巡检对象结果",
  inspection_check: "来自巡检检查项",
};
const visibleTags = computed(() =>
  (row.value?.tags || []).filter((tag: string) => tag !== "agent-result"),
);
async function viewSource() {
  if (!agentSource.value) return;
  try {
    const [, kind, identifier] = agentSource.value;
    const detail = await api<Models["ResultDetail"]>(
      `/agent-results/${kind}/${identifier}?limit=1`,
    );
    await router.push(
      kind.startsWith("inspection")
        ? `/inspections/runs/${detail.record.run_id}`
        : `/incidents/${detail.record.scope_id}/diagnosis`,
    );
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "来源不可用");
  }
}
async function publish() {
  if (!row.value || busy.value) return;
  busy.value = true;
  try {
    if (
      !(await confirmOperation(
        "确认已核验正文、证据和适用范围，启用此知识？",
        "审核并启用",
      ))
    )
      return;
    const value = row.value;
    await api(`/knowledge/${id}`, "PUT", {
      title: value.title,
      content: value.content,
      source: value.source,
      tags: value.tags,
      enabled: true,
    });
    await refresh();
    ElMessage.success("知识已启用，索引任务已排队");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "启用失败");
  } finally {
    busy.value = false;
  }
}
async function load() {
  try {
    row.value = await api("/" + props.kind + "/" + id);
    error.value = "";
    attempt.value++;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () =>
  editing.value
    ? null
    : row.value && ["queued", "running"].includes(row.value.index_status)
      ? Math.min(10000, 2000 * Math.max(1, attempt.value))
      : null,
);
async function reindex() {
  if (busy.value) return;
  busy.value = true;
  try {
    if (
      !(await confirmOperation(
        `重新建立“${row.value?.title || "当前文档"}”的知识索引？`,
        "重新索引",
      ))
    )
      return;
    await api(`/knowledge/${id}/reindex`, "POST");
    await refresh();
    ElMessage.success("索引任务已排队");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "索引失败");
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="page-heading">
    <div>
      <router-link :to="'/' + kind" class="subtle-link">← 返回列表</router-link>
      <div class="heading-copy">
        <h1>
          {{ row?.title || (kind === "skills" ? "Skill 详情" : "知识详情") }}
        </h1>
        <PageIntro />
        <p>
          {{
            row?.code ||
            (agentSource ? sourceNames[agentSource[1]] : row?.source)
          }}
          · v{{ row?.version }} · 更新于
          {{ date(row?.updated_at) }}
        </p>
      </div>
    </div>

    <div class="actions">
      <el-button v-if="agentSource" @click="viewSource">查看来源结果</el-button>
      <el-button
        v-if="agentSource && !row?.enabled && auth.can('knowledge:manage')"
        type="primary"
        :loading="busy"
        @click="publish"
        >审核并启用</el-button
      >
      <el-button @click="refresh">刷新</el-button
      ><el-button v-if="auth.can('knowledge:manage')" @click="editing = true"
        >编辑</el-button
      ><el-button
        v-if="kind === 'knowledge' && auth.can('knowledge:manage')"
        :disabled="['queued', 'running'].includes(row?.index_status)"
        :loading="busy"
        @click="reindex"
        >重新索引</el-button
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
  <div class="badge-line">
    <StatusTag v-if="row?.index_status" :value="row.index_status" /><el-tag
      :type="row?.enabled ? 'success' : 'info'"
      >{{
        row?.enabled ? "已启用" : agentSource ? "待审核草稿" : "已停用"
      }}</el-tag
    ><el-tag v-for="tag in visibleTags" :key="tag" type="info">{{
      { diagnosis: "诊断Agent", inspection: "巡检Agent" }[tag as string] || tag
    }}</el-tag>
  </div>
  <el-alert
    v-if="row?.error_code"
    :title="row.error_code"
    type="error"
    :closable="false"
    class="error-box"
  /><el-alert
    v-if="kind === 'skills'"
    title="Agent 按任务选择并加载此 Skill，遵循其中的检查步骤和输出要求。涉及运维变更的动作仍须人工审批。"
    type="info"
    :closable="false"
    class="error-box"
  />
  <section class="panel">
    <article class="markdown" v-html="rendered"></article>
  </section>
  <RecordForm v-model="editing" :kind="kind" :row="row" @saved="refresh" />
</template>
