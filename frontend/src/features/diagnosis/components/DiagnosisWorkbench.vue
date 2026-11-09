<script setup lang="ts">
import { confirmOperation } from "@/utils/confirmOperation";
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  ChatDotRound,
  MagicStick,
  Plus,
  Document,
  ArrowDown,
  Close,
} from "@element-plus/icons-vue";
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
import { all, api, ApiError, type Models } from "@/api/client";
import { streamEvents } from "@/api/events";
import {
  activeRun,
  mergeEvents,
  messageStatus,
  stage,
  toolNames,
} from "@/features/diagnosis/model/diagnosis";
import { date, label } from "@/utils/display";
import { useAuth } from "@/features/auth/store";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import StatusTag from "@/components/common/StatusTag.vue";
import DiagnosisSources from "@/features/diagnosis/components/DiagnosisSources.vue";
import SourceEvidence from "@/features/diagnosis/components/SourceEvidence.vue";
import AgentResults from "@/features/knowledge/components/AgentResults.vue";

const props = withDefaults(
  defineProps<{
    incidentId: string;
    writable: boolean;
    fullscreen?: boolean;
    visible?: boolean;
  }>(),
  { visible: true },
);
const emit = defineEmits<{ adopted: [] }>();
const auth = useAuth(),
  router = useRouter();
type Conversation = Models["ConversationResponse"];
type Run = Models["RunResponse"];
type Message = Models["MessageResponse"];
type Event = Models["EventResponse"];
type Diagnosis = Models["Diagnosis"];
const conversations = ref<Conversation[]>([]),
  conversation = ref<Conversation>(),
  runs = ref<Run[]>([]),
  selectedRun = ref("");
const messages = ref<Message[]>([]),
  events = ref<Event[]>([]),
  draft = ref(""),
  pendingInput = ref<{ id: string; content: string }>();
const busy = ref(false),
  error = ref(""),
  connection = ref(""),
  ownerFilter = ref("mine"),
  panel = ref("chat");
const chatElement = ref<HTMLElement>(),
  followBottom = ref(true),
  now = ref(Date.now()),
  detailsOpen = ref(false);
const detailSections = ref<string[]>([]);
const historyOpen = ref(false);
const drafts = new Map<string, string>();
const markdown = new MarkdownIt({ html: false, linkify: false });
const prompts = [
  "请检查当前事故的异常指标，分析故障原因。",
  "请核对近期发布与当前故障的关联，并列出证据。",
  "请整理排查步骤，说明还缺少哪些证据。",
];
function renderReply(content: string) {
  return DOMPurify.sanitize(markdown.render(content));
}
function toggleDetails() {
  detailsOpen.value = !detailsOpen.value;
  panel.value = detailsOpen.value ? "details" : "chat";
}
function field(value: unknown, name: string) {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)[name]
    : undefined;
}
function toolSummary(end?: Event) {
  if (!end) return "等待工具结果";
  const result = end.data.result;
  const failure = end.data.error_code || field(result, "error_code");
  if (failure) return `证据不可用：${label(failure)}`;
  return field(result, "id")
    ? "证据已记录，可展开查看或跳转引用"
    : "检查完成，可展开查看结果";
}
async function showEvidence(id: string) {
  detailsOpen.value = true;
  panel.value = "details";
  detailSections.value = ["evidence"];
  await nextTick();
  document
    .getElementById(`evidence-${id}`)
    ?.scrollIntoView({ block: "nearest" });
}
let controller: AbortController | undefined,
  retryTimer: ReturnType<typeof setTimeout> | undefined;
let updateTimer: ReturnType<typeof setTimeout> | undefined,
  clockTimer: ReturnType<typeof setInterval> | undefined,
  revision = 0,
  destroyed = false;
const current = computed(
  () =>
    runs.value.find((r) => r.id === selectedRun.value) ||
    conversation.value?.current_run ||
    undefined,
);
const candidate = computed(
  () => current.value?.candidate as unknown as Diagnosis | undefined,
);
const owned = computed(() => conversation.value?.owner_id === auth.user?.id);
const canWrite = computed(
  () => props.writable && owned.value && auth.can("analyses:run"),
);
const runEvents = computed(() =>
  events.value.filter((e) => e.run_id === current.value?.id),
);
const phase = computed(() => stage(runEvents.value, current.value?.status));
const elapsed = computed(() =>
  current.value?.started_at
    ? Math.max(
        0,
        Math.round(
          ((current.value.finished_at
            ? new Date(current.value.finished_at).getTime()
            : now.value) -
            new Date(current.value.started_at).getTime()) /
            1000,
        ),
      )
    : 0,
);
const displayConversations = computed(() =>
  conversations.value.filter(
    (c) =>
      ownerFilter.value === "all" ||
      (ownerFilter.value === "mine"
        ? c.owner_id === auth.user?.id
        : c.owner_id === ownerFilter.value),
  ),
);
const owners = computed(() => [
  ...new Map(
    conversations.value.map((c) => [c.owner_id, c.owner_name]),
  ).entries(),
]);
const toolCards = computed(() =>
  runEvents.value
    .filter((e) => e.kind === "tool_started")
    .map((e) => ({
      start: e,
      end: runEvents.value.find(
        (x) => x.kind === "tool_finished" && x.data.call_id === e.data.call_id,
      ),
    })),
);
const entries = computed(() =>
  [
    ...messages.value.map((m) => ({
      key: m.id,
      time: m.created_at,
      message: m,
      tool: undefined,
    })),
    ...toolCards.value.map((t) => ({
      key: t.start.id,
      time: t.start.created_at,
      message: undefined,
      tool: t,
    })),
  ].sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime()),
);

async function list() {
  conversations.value = await all<Conversation>(
    `/incidents/${props.incidentId}/analysis-conversations`,
  );
}
function disconnect() {
  controller?.abort();
  controller = undefined;
  clearTimeout(retryTimer);
}
async function scroll() {
  if (followBottom.value) {
    await nextTick();
    chatElement.value?.scrollTo({ top: chatElement.value.scrollHeight });
  }
}
function onScroll() {
  const node = chatElement.value;
  if (node)
    followBottom.value =
      node.scrollHeight - node.scrollTop - node.clientHeight < 80;
}
async function sync(id: string, version: number) {
  const [c, m, r] = await Promise.all([
    api<Conversation>(`/analysis-conversations/${id}`),
    all<Message>(`/analysis-conversations/${id}/messages`),
    all<Run>(`/analysis-conversations/${id}/runs`),
  ]);
  if (version !== revision) return;
  const oldLatest = conversation.value?.current_run?.id;
  conversation.value = c;
  conversations.value = conversations.value.map((item) =>
    item.id === c.id ? c : item,
  );
  messages.value = m;
  runs.value = r;
  if (!selectedRun.value || selectedRun.value === oldLatest)
    selectedRun.value = c.current_run?.id || "";
  await scroll();
}
function connect() {
  disconnect();
  if (
    destroyed ||
    document.hidden ||
    props.visible === false ||
    !conversation.value
  )
    return;
  const id = conversation.value.id,
    version = revision,
    local = new AbortController();
  controller = local;
  connection.value = "连接中";
  void streamEvents(
    id,
    events.value.at(-1)?.seq || 0,
    (incoming) => {
      if (version !== revision) return;
      events.value = mergeEvents(events.value, [incoming]);
      connection.value = "实时连接";
      clearTimeout(updateTimer);
      updateTimer = setTimeout(() => {
        void sync(id, version).catch((e) => {
          if (version === revision) error.value = String(e.message || e);
        });
      }, 120);
    },
    local.signal,
    () => {
      if (version === revision) connection.value = "实时连接";
    },
  )
    .catch((e) => {
      if (local.signal.aborted) return;
      connection.value =
        e instanceof ApiError && e.status === 403
          ? "查看权限已失效"
          : "连接中断，正在重连";
      if (e instanceof ApiError && e.status === 403) return;
    })
    .finally(() => {
      if (
        !local.signal.aborted &&
        version === revision &&
        connection.value !== "查看权限已失效"
      )
        retryTimer = setTimeout(connect, 2000);
    });
}
async function select(c: Conversation) {
  panel.value = "chat";
  if (conversation.value) drafts.set(conversation.value.id, draft.value);
  draft.value = drafts.get(c.id) || "";
  disconnect();
  clearTimeout(updateTimer);
  revision++;
  const version = revision;
  conversation.value = c;
  events.value = [];
  messages.value = [];
  runs.value = [];
  selectedRun.value = "";
  followBottom.value = true;
  pendingInput.value = undefined;
  try {
    let cursor = 0;
    for (;;) {
      const batch = await api<Event[]>(
        `/analysis-conversations/${c.id}/events?after_seq=${cursor}&limit=1000`,
      );
      if (version !== revision) return;
      events.value = mergeEvents(events.value, batch);
      if (batch.length < 1000) break;
      cursor = batch.at(-1)!.seq;
    }
    await sync(c.id, version);
    error.value = "";
    connect();
  } catch (e) {
    if (version === revision)
      error.value = e instanceof Error ? e.message : "会话加载失败";
  }
}
async function create() {
  const c = await api<Conversation>(
    `/incidents/${props.incidentId}/analysis-conversations`,
    "POST",
    {
      title: `事故诊断 · ${new Date().toLocaleString("zh-CN", { hour12: false })}`,
    },
  );
  await list();
  await select(c);
  return c;
}
async function remove(c: Conversation) {
  if (busy.value) return;
  try {
    await ElMessageBox.confirm(`删除会话“${c.title}”？`, "删除会话", {
      confirmButtonText: "删除",
      cancelButtonText: "取消",
      type: "warning",
    });
  } catch {
    return;
  }
  await perform(async () => {
    await api(`/analysis-conversations/${c.id}`, "DELETE");
    conversations.value = conversations.value.filter(
      (item) => item.id !== c.id,
    );
    drafts.delete(c.id);
    if (conversation.value?.id === c.id) {
      disconnect();
      clearTimeout(updateTimer);
      revision++;
      conversation.value = undefined;
      messages.value = [];
      events.value = [];
      runs.value = [];
      selectedRun.value = "";
      draft.value = "";
      pendingInput.value = undefined;
      connection.value = "";
      const next = displayConversations.value[0];
      if (next) await select(next);
    }
    ElMessage.success("会话已删除");
  });
}
async function perform(work: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  try {
    await work();
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "操作失败";
  } finally {
    busy.value = false;
  }
}
function send(content = draft.value) {
  void perform(async () => {
    if (!content.trim()) return;
    const c = conversation.value || (await create());
    if (c.owner_id !== auth.user?.id) throw new Error("他人会话仅可查看");
    if (!pendingInput.value || pendingInput.value.content !== content)
      pendingInput.value = { id: crypto.randomUUID(), content };
    const value = pendingInput.value;
    await api(`/analysis-conversations/${c.id}/messages`, "POST", {
      client_message_id: value.id,
      content: value.content,
    });
    if (draft.value === value.content) draft.value = "";
    pendingInput.value = undefined;
    followBottom.value = true;
    await sync(c.id, revision);
    await list();
    connect();
  });
}
function stop() {
  void perform(async () => {
    if (
      !(await confirmOperation(
        "停止本轮 AI 诊断？已产生的诊断记录会保留。",
        "停止诊断",
      ))
    )
      return;
    await api(
      `/analysis-runs/${conversation.value!.current_run!.id}/cancel`,
      "POST",
    );
    await sync(conversation.value!.id, revision);
    ElMessage.success("已提交停止诊断请求");
  });
}
function adopt() {
  void perform(async () => {
    if (
      !(await confirmOperation("将本轮结论采用为事故的正式诊断？", "采用诊断"))
    )
      return;
    await api(`/analysis-runs/${current.value!.id}/adopt`, "POST");
    await sync(conversation.value!.id, revision);
    emit("adopted");
    ElMessage.success("已采用为事故诊断");
  });
}
function approval() {
  void perform(async () => {
    if (
      !(await confirmOperation(
        "依据当前已采用的诊断申请执行审批？批准后仍需人工发起执行。",
        "申请审批",
      ))
    )
      return;
    const r = await api<Models["ApprovalResponse"]>(
      `/incidents/${props.incidentId}/approvals`,
      "POST",
      { analysis_id: current.value!.adopted_analysis_id },
    );
    ElMessage.success("执行审批申请已提交");
    await router.push(`/approvals/${r.id}`);
  });
}
function visibility() {
  if (document.hidden || props.visible === false) disconnect();
  else connect();
}
watch(() => props.visible, visibility);
onMounted(async () => {
  document.addEventListener("visibilitychange", visibility);
  clockTimer = setInterval(() => {
    now.value = Date.now();
  }, 1000);
  try {
    await list();
    const mine = conversations.value.find((c) => c.owner_id === auth.user?.id);
    if (mine) await select(mine);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "会话加载失败";
  }
});
onBeforeUnmount(() => {
  destroyed = true;
  revision++;
  disconnect();
  clearTimeout(updateTimer);
  clearInterval(clockTimer);
  document.removeEventListener("visibilitychange", visibility);
});
</script>

<template>
  <section
    class="diagnosis-workbench"
    :class="{ expanded: fullscreen, 'show-details': detailsOpen }"
  >
    <div class="workbench-header">
      <div class="workbench-heading">
        <span class="agent-mark"
          ><el-icon><MagicStick /></el-icon
        ></span>
        <div class="workbench-heading-copy">
          <h2>AI 诊断工作台</h2>
          <p class="muted">只读取证 · 持续对话 · 手动采用结论</p>
        </div>
      </div>
      <div class="actions">
        <el-button @click="historyOpen = true">结果档案</el-button>
        <el-button
          v-if="!fullscreen"
          @click="router.push(`/incidents/${incidentId}/diagnosis`)"
          >全屏打开</el-button
        ><el-button
          :aria-expanded="detailsOpen"
          :type="detailsOpen ? 'primary' : 'default'"
          plain
          @click="toggleDetails"
        >
          <el-icon><Document /></el-icon><span>诊断与证据</span>
        </el-button>
      </div>
    </div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      class="error-box"
    />
    <div class="workbench-mobile">
      <el-radio-group
        v-model="panel"
        @change="detailsOpen = panel === 'details'"
        ><el-radio-button value="sessions">会话</el-radio-button
        ><el-radio-button value="chat">对话</el-radio-button
        ><el-radio-button value="details">详情</el-radio-button></el-radio-group
      >
    </div>
    <div class="workbench-grid" :data-panel="panel">
      <aside class="session-rail">
        <div class="panel-heading">
          <h3>
            会话
            <span class="session-count">{{ displayConversations.length }}</span>
          </h3>
          <el-button
            v-if="auth.can('analyses:run') && writable"
            size="small"
            :loading="busy"
            @click="
              perform(async () => {
                await create();
              })
            "
            ><el-icon><Plus /></el-icon><span>新建</span></el-button
          >
        </div>
        <el-select
          v-if="auth.can('analysis_conversations:read_all')"
          v-model="ownerFilter"
          aria-label="会话创建者"
          ><el-option label="我的会话" value="mine" /><el-option
            label="全部用户"
            value="all" /><el-option
            v-for="[id, name] in owners"
            :key="id"
            :label="name"
            :value="id"
        /></el-select>
        <div
          v-for="c in displayConversations"
          :key="c.id"
          class="session-entry"
          :class="{ selected: c.id === conversation?.id }"
        >
          <button
            class="session-item"
            :aria-pressed="c.id === conversation?.id"
            :disabled="busy"
            @click="select(c)"
          >
            <strong>{{ c.title }}</strong
            ><span class="session-owner">{{ c.owner_name }}</span>
            <span class="session-date">{{ date(c.updated_at) }}</span
            ><StatusTag v-if="c.current_run" :value="c.current_run.status" />
          </button>
          <el-button
            v-if="c.owner_id === auth.user?.id && auth.can('analyses:run')"
            class="session-delete"
            text
            type="danger"
            size="small"
            :disabled="busy || activeRun(c.current_run?.status)"
            :aria-label="`删除会话：${c.title}`"
            @click="remove(c)"
            >删除</el-button
          >
        </div>
        <p v-if="!displayConversations.length" class="empty-hint">暂无会话</p>
      </aside>
      <main class="conversation-area">
        <div class="runtime-bar">
          <div class="conversation-heading">
            <strong>{{ conversation?.title || "新诊断会话" }}</strong>
            <small
              v-if="connection"
              class="connection-state"
              :class="{ connected: connection === '实时连接' }"
              >{{ connection }}</small
            >
          </div>
          <div class="badge-line">
            <StatusTag v-if="current" :value="current.status" /><strong>{{
              current ? phase : "尚未开始"
            }}</strong
            ><el-tag v-if="conversation && !owned" type="info"
              >他人会话 · 只读</el-tag
            >
          </div>
          <div v-if="current" class="runtime-meta">
            <span>{{ current.config.model }}</span
            ><span>{{ elapsed }} 秒</span>
            <span>模型请求 {{ current.model_requests }}/13</span>
            <span>工具调用 {{ current.tool_count }}/12</span>
          </div>
        </div>
        <div ref="chatElement" class="conversation-scroll" @scroll="onScroll">
          <div v-if="!entries.length" class="diagnosis-welcome">
            <span class="welcome-mark"
              ><el-icon><MagicStick /></el-icon
            ></span>
            <h3>从事故证据开始诊断</h3>
            <p>
              Agent
              会按需读取指标、容器状态、日志和知识库。你可以补充现象、粘贴日志或追问检查结果。
            </p>
            <el-button
              v-if="
                auth.can('analyses:run') && writable && (!conversation || owned)
              "
              type="primary"
              :loading="busy"
              @click="
                send('请基于当前事故进行诊断，说明检查过程并提交候选诊断。')
              "
              >开始诊断</el-button
            >
            <div
              v-if="
                auth.can('analyses:run') && writable && (!conversation || owned)
              "
              class="prompt-list"
            >
              <span>也可以从这些问题开始</span>
              <button
                v-for="prompt in prompts"
                :key="prompt"
                @click="
                  draft = prompt;
                  panel = 'chat';
                "
              >
                <el-icon><ChatDotRound /></el-icon><span>{{ prompt }}</span>
              </button>
            </div>
          </div>
          <template v-for="entry in entries" :key="entry.key">
            <article
              v-if="entry.message"
              class="chat-message"
              :class="entry.message.role"
            >
              <div class="message-heading">
                <span class="message-author">
                  <span class="message-avatar"
                    ><el-icon
                      ><ChatDotRound
                        v-if="entry.message.role === 'user'" /><MagicStick
                        v-else /></el-icon
                  ></span>
                  <strong>{{
                    entry.message.role === "user"
                      ? conversation?.owner_name
                      : "诊断 Agent"
                  }}</strong></span
                ><small
                  >{{ date(entry.message.created_at)
                  }}<template v-if="messageStatus[entry.message.status]">
                    · {{ messageStatus[entry.message.status] }}</template
                  ></small
                >
              </div>
              <div
                v-if="entry.message.role === 'assistant'"
                class="message-text markdown"
                v-html="renderReply(entry.message.content)"
              ></div>
              <div v-else class="message-text">{{ entry.message.content }}</div>
            </article>
            <details v-else-if="entry.tool" class="tool-card">
              <summary>
                <span
                  >{{
                    entry.tool.start.data.internal ? "系统预采集" : "Agent 工具"
                  }}
                  ·
                  {{
                    toolNames[String(entry.tool.start.data.tool)] ||
                    entry.tool.start.data.tool
                  }}<span class="tool-brief">{{
                    toolSummary(entry.tool.end)
                  }}</span></span
                ><small
                  class="tool-state"
                  :class="{
                    pending: !entry.tool.end && activeRun(current?.status),
                  }"
                  >{{
                    entry.tool.end
                      ? (entry.tool.end.data.error_code ||
                        field(entry.tool.end.data.result, "error_code")
                          ? "不可用"
                          : "已完成") +
                        ` · ${entry.tool.end.data.duration_ms ?? 0} ms`
                      : activeRun(current?.status)
                        ? "运行中"
                        : "未完成"
                  }}</small
                >
              </summary>
              <p>
                {{ date(entry.tool.start.created_at) }} · 目标
                {{
                  field(entry.tool.start.data.arguments, "asset_id") ||
                  "事故范围"
                }}
              </p>
              <p v-if="entry.tool.end?.data.error_code" class="error-text">
                {{ label(entry.tool.end.data.error_code) }}
              </p>
              <button
                v-if="field(entry.tool.end?.data.result, 'id')"
                class="subtle-link"
                @click="
                  showEvidence(String(field(entry.tool.end?.data.result, 'id')))
                "
              >
                查看引用证据</button
              ><JsonEvidence
                :value="{
                  arguments: entry.tool.start.data.arguments,
                  result: entry.tool.end?.data.result,
                  error: entry.tool.end?.data.error_code,
                }"
              />
            </details>
          </template>
          <div
            v-if="
              entries.length && activeRun(conversation?.current_run?.status)
            "
            class="agent-working"
            role="status"
          >
            <span class="working-dot"></span>Agent 正在处理，可继续补充信息
          </div>
        </div>
        <button
          v-if="!followBottom && entries.length"
          class="latest-message"
          @click="
            followBottom = true;
            scroll();
          "
        >
          <el-icon><ArrowDown /></el-icon>回到最新消息
        </button>
        <div
          v-if="auth.can('analyses:run') && (!conversation || owned)"
          class="conversation-input"
        >
          <el-input
            v-model="draft"
            type="textarea"
            :rows="3"
            maxlength="16000"
            :disabled="!writable"
            placeholder="描述现象、粘贴日志或继续追问；运行中也可以补充"
            aria-label="诊断补充内容"
            @keydown.ctrl.enter.prevent="
              !busy && writable && draft.trim() && send()
            "
            @keydown.meta.enter.prevent="
              !busy && writable && draft.trim() && send()
            "
          />
          <div class="input-actions">
            <small>{{
              writable
                ? "Ctrl / ⌘ + Enter 发送 · 运行中可补充"
                : "事故已结束，当前会话只读"
            }}</small>
            <div class="actions">
              <el-button
                v-if="
                  conversation?.current_run &&
                  activeRun(conversation.current_run.status)
                "
                :loading="busy"
                :disabled="conversation.current_run.status === 'cancelling'"
                @click="stop"
                >停止本轮</el-button
              ><el-button
                v-else-if="conversation?.current_run && writable"
                :loading="busy"
                @click="send('请结合上述对话和补充继续诊断，并核对当前证据。')"
                >继续诊断</el-button
              ><el-button
                type="primary"
                :loading="busy"
                :disabled="!draft.trim() || !writable"
                @click="send()"
                >{{
                  conversation?.current_run &&
                  activeRun(conversation.current_run.status)
                    ? "发送补充"
                    : "发送"
                }}</el-button
              >
            </div>
          </div>
        </div>
      </main>
      <aside class="diagnosis-details">
        <div class="details-heading">
          <h3>诊断与证据</h3>
          <el-button
            text
            circle
            aria-label="收起诊断与证据"
            @click="
              detailsOpen = false;
              panel = 'chat';
            "
            ><el-icon><Close /></el-icon
          ></el-button>
        </div>
        <label v-if="runs.length" class="history-label">运行历史</label>
        <el-select
          v-if="runs.length"
          v-model="selectedRun"
          aria-label="运行历史"
          ><el-option
            v-for="r in runs"
            :key="r.id"
            :value="r.id"
            :label="`${date(r.created_at)} · ${label(r.status)}`"
        /></el-select>
        <el-alert
          v-if="current?.error_code"
          :title="
            current.error_code === 'context_limit'
              ? '上下文已超过模型容量，请新建会话'
              : label(current.error_code)
          "
          type="warning"
          :closable="false"
        />
        <template v-if="candidate?.summary"
          ><el-tag :type="current?.adopted_analysis_id ? 'success' : 'info'">{{
            current?.adopted_analysis_id
              ? "已采用为正式诊断"
              : "候选诊断 · 尚未采用"
          }}</el-tag>
          <h3>结论</h3>
          <p>{{ candidate.summary }}</p>
          <h4>影响</h4>
          <p>{{ candidate.impact }}</p>
          <div v-for="(cause, i) in candidate.possible_causes" :key="i">
            <h4>原因 {{ i + 1 }}</h4>
            <p>{{ cause.description }}</p>
            <small>引用 {{ cause.evidence_ids.join(" · ") }}</small>
          </div>
          <h4 v-if="candidate.data_gaps.length">证据缺口</h4>
          <p v-for="gap in candidate.data_gaps" :key="gap">{{ label(gap) }}</p>
          <div v-if="candidate.proposal">
            <h4>
              {{ candidate.proposal.skill }} v{{ candidate.proposal.version }}
            </h4>
            <p>{{ candidate.proposal.rationale }}</p>
            <p>预计影响：{{ candidate.proposal.impact }}</p>
          </div>
          <div class="actions">
            <el-button
              v-if="
                canWrite &&
                current?.status === 'completed' &&
                !current.adopted_analysis_id
              "
              type="primary"
              :loading="busy"
              @click="adopt"
              >采用为事故诊断</el-button
            ><el-button
              v-if="
                canWrite &&
                current?.adopted_analysis_id &&
                candidate.proposal &&
                auth.can('executions:run')
              "
              type="primary"
              :loading="busy"
              @click="approval"
              >申请审批</el-button
            >
          </div></template
        >
        <div v-else class="conclusion-empty">
          <el-icon><Document /></el-icon>
          <h4>等待诊断结论</h4>
          <p>Agent 提交候选诊断后，将在这里展示原因、影响与建议。</p>
        </div>
        <el-collapse v-if="current" v-model="detailSections"
          ><el-collapse-item title="证据与知识引用" name="evidence"
            ><div
              v-for="e in current.evidence"
              :id="`evidence-${field(e, 'id')}`"
              :key="String(field(e, 'id'))"
            >
              <p>
                <strong>{{ field(e, "source") }}</strong> ·
                {{ date(field(e, "observed_at")) }}
              </p>
              <SourceEvidence :evidence="e" />
              <JsonEvidence :value="e" />
            </div>
            <h4>知识版本</h4>
            <JsonEvidence :value="current.knowledge" /></el-collapse-item
          ><el-collapse-item title="开发资料与版本" name="sources"
            ><DiagnosisSources
              :context="current.source_context"
              :database-context="current.database_context" /></el-collapse-item
          ><el-collapse-item title="运行事件详情" name="events"
            ><JsonEvidence :value="runEvents" /></el-collapse-item
        ></el-collapse>
      </aside>
    </div>
    <el-drawer
      v-model="historyOpen"
      title="诊断结果档案"
      size="min(1100px, 96vw)"
      append-to-body
    >
      <AgentResults
        v-if="historyOpen"
        business="diagnosis"
        :scope-id="incidentId"
        :conversation-id="conversation?.id"
      />
    </el-drawer>
  </section>
</template>

<style scoped>
.diagnosis-workbench {
  container: workbench / inline-size;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  overflow: hidden;
  margin: 16px 0;
}
.workbench-header,
.workbench-heading,
.workbench-heading-copy,
.conversation-heading,
.message-author,
.details-heading {
  display: flex;
  align-items: center;
  gap: 12px;
}
.workbench-header {
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}
.workbench-heading {
  min-width: 0;
}
.workbench-heading-copy {
  flex-wrap: wrap;
  gap: 4px 16px;
}
.workbench-header h2 {
  margin: 0;
  font-size: 18px;
  white-space: nowrap;
}
.workbench-header p {
  margin: 0;
  font-size: 12px;
}
.agent-mark,
.welcome-mark,
.message-avatar {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  background: var(--primary-soft);
  color: var(--el-color-primary);
  border: 1px solid var(--primary-border);
  border-radius: 10px;
}
.agent-mark {
  width: 38px;
  height: 38px;
  font-size: 20px;
}
.workbench-grid {
  position: relative;
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr);
  height: 720px;
  height: clamp(560px, 76dvh, 900px);
}
.show-details .workbench-grid {
  grid-template-columns: 200px minmax(0, 1fr) 320px;
}
.expanded .workbench-grid {
  height: max(560px, calc(100dvh - 310px));
}
.session-rail {
  padding: 18px 12px;
  background: var(--surface-soft);
  border-right: 1px solid var(--border);
  min-width: 0;
  min-height: 0;
  overflow: auto;
}
.session-rail .panel-heading {
  gap: 6px;
  margin-bottom: 16px;
}
.session-rail h3,
.details-heading h3 {
  margin: 0;
  font-size: 14px;
}
.session-count {
  color: var(--muted);
  font-size: 12px;
  font-weight: 400;
  margin-left: 4px;
}
.session-rail .el-select {
  margin-bottom: 8px;
}
.session-entry {
  margin-top: 6px;
  border: 1px solid transparent;
  border-radius: 10px;
}
.session-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  width: 100%;
  padding: 12px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  text-align: left;
  font: inherit;
  cursor: pointer;
  color: var(--text);
  overflow-wrap: anywhere;
}
.session-entry:hover {
  background: var(--surface);
}
.session-entry.selected {
  background: var(--primary-soft);
  border-color: var(--primary-border);
}
.session-delete {
  margin: 0 0 6px 6px;
}
.session-item strong {
  font-size: 13px;
  font-weight: 500;
  line-height: 1.6;
}
.session-item .session-owner,
.session-item .session-date {
  font-size: 12px;
  color: var(--muted);
}
.session-rail .empty-hint {
  padding: 24px 8px;
}
.conversation-area {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  position: relative;
}
.runtime-bar {
  padding: 16px 22px;
  border-bottom: 1px solid var(--border);
  display: grid;
  gap: 10px;
}
.conversation-heading {
  justify-content: space-between;
  align-items: flex-start;
}
.conversation-heading > strong {
  font-size: 14px;
  overflow-wrap: anywhere;
}
.connection-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
  flex-shrink: 0;
}
.connection-state::before {
  content: "";
  width: 6px;
  height: 6px;
  background: var(--muted);
  border-radius: 50%;
}
.connection-state.connected::before {
  background: var(--el-color-success);
}
.runtime-bar .badge-line {
  font-size: 13px;
}
.runtime-bar .badge-line > strong {
  font-weight: 400;
}
.runtime-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  color: var(--muted);
  font-size: 12px;
}
.conversation-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 24px;
  overscroll-behavior: contain;
  scrollbar-width: thin;
}
.chat-message {
  padding: 0 0 24px;
  margin-bottom: 20px;
  border-bottom: 1px solid var(--border);
}
.chat-message.user {
  padding: 16px 18px;
  margin-left: 36px;
  background: var(--primary-soft);
  border: 1px solid var(--primary-border);
  border-radius: 12px;
}
.message-heading {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.message-author {
  gap: 8px;
}
.message-avatar {
  width: 26px;
  height: 26px;
  font-size: 14px;
  border-radius: 8px;
}
.user .message-avatar {
  background: var(--surface);
  border-color: var(--border);
  color: var(--muted);
}
.message-heading small {
  font-size: 12px;
  color: var(--muted);
}
.message-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.85;
  margin-top: 12px;
  font-size: 14px;
}
.message-text.markdown {
  white-space: normal;
}
.message-text :deep(> :first-child) {
  margin-top: 0;
}
.message-text :deep(> :last-child) {
  margin-bottom: 0;
}
.message-text :deep(h1),
.message-text :deep(h2),
.message-text :deep(h3) {
  font-size: 16px;
  margin: 18px 0 10px;
}
.message-text :deep(pre) {
  border: 1px solid var(--border);
  border-radius: 8px;
  margin: 12px 0;
  white-space: pre;
  font-size: 12px;
}
.message-text :deep(code) {
  font-family: Consolas, monospace;
}
.message-text :deep(table) {
  display: block;
  max-width: 100%;
  overflow: auto;
}
.message-text :deep(blockquote) {
  margin: 12px 0;
  padding-left: 14px;
  border-left: 3px solid var(--primary-border);
  color: var(--muted);
}
.tool-card {
  border: 1px solid var(--border);
  border-radius: 10px;
  margin: 10px 0 18px;
  padding: 12px 14px;
  background: var(--surface-soft);
  font-size: 13px;
  overflow: hidden;
}
.tool-card summary {
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  color: var(--text-regular);
}
.tool-card summary::before {
  content: "›";
  color: var(--muted);
  font-size: 18px;
  line-height: 20px;
}
.tool-card[open] summary::before {
  transform: rotate(90deg);
}
.tool-card summary > span {
  min-width: 0;
  flex: 1;
}
.tool-brief {
  display: block;
  margin-top: 5px;
  color: var(--muted);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tool-state {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}
.tool-state.pending {
  color: var(--el-color-primary);
}
.tool-card p {
  overflow-wrap: anywhere;
}
.agent-working {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
  padding: 10px 0;
}
.working-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--el-color-primary);
  animation: working 1.6s ease-in-out infinite;
}
@keyframes working {
  50% {
    opacity: 0.35;
  }
}
.latest-message {
  align-self: center;
  display: flex;
  align-items: center;
  gap: 6px;
  margin: -36px 0 8px;
  z-index: 1;
  padding: 6px 12px;
  border-radius: 20px;
  border: 1px solid var(--primary-border);
  background: var(--surface);
  color: var(--el-color-primary);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
}
.conversation-input {
  padding: 16px 20px;
  border-top: 1px solid var(--border);
  background: var(--surface);
}
.conversation-input :deep(.el-textarea__inner) {
  background: var(--surface-soft);
  padding: 12px 14px;
  line-height: 1.7;
  resize: none;
}
.input-actions {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin-top: 12px;
  align-items: center;
}
.input-actions small {
  font-size: 12px;
  color: var(--muted);
}
.input-actions .actions {
  flex-shrink: 0;
  margin-left: auto;
}
.diagnosis-details {
  display: none;
  border-left: 1px solid var(--border);
  padding: 18px;
  min-height: 0;
  overflow: auto;
  min-width: 0;
  overflow-wrap: anywhere;
  background: var(--surface);
  scrollbar-width: thin;
}
.show-details .diagnosis-details {
  display: block;
}
.details-heading {
  justify-content: space-between;
  margin-bottom: 16px;
}
.history-label {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 8px;
}
.diagnosis-details h3 {
  font-size: 15px;
}
.diagnosis-details h4 {
  font-size: 13px;
  margin-bottom: 8px;
}
.diagnosis-details p {
  font-size: 13px;
  line-height: 1.8;
}
.diagnosis-details small {
  color: var(--muted);
  font-size: 12px;
}
.diagnosis-details .el-select {
  margin-bottom: 16px;
}
.diagnosis-details .actions {
  flex-wrap: wrap;
  margin: 18px 0;
}
.conclusion-empty {
  text-align: center;
  color: var(--muted);
  padding: 36px 8px;
}
.conclusion-empty > .el-icon {
  font-size: 28px;
}
.conclusion-empty h4 {
  color: var(--text);
}
.diagnosis-welcome {
  max-width: 560px;
  margin: auto;
  padding: 28px 8px;
  text-align: center;
  color: var(--muted);
}
.welcome-mark {
  width: 56px;
  height: 56px;
  margin: 0 auto 20px;
  font-size: 26px;
  border-radius: 16px;
}
.diagnosis-welcome h3 {
  color: var(--text);
  font-size: 22px;
  margin: 0 0 12px;
}
.diagnosis-welcome > p {
  line-height: 1.85;
  margin: 0 auto 20px;
  max-width: 420px;
  font-size: 13px;
}
.prompt-list {
  display: grid;
  gap: 8px;
  margin: 28px auto 0;
  max-width: 420px;
  text-align: left;
}
.prompt-list > span {
  font-size: 12px;
  margin-bottom: 4px;
}
.prompt-list button {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  background: var(--surface-soft);
  border-radius: 10px;
  color: var(--text-regular);
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  text-align: left;
  line-height: 1.6;
}
.prompt-list button:hover {
  border-color: var(--primary-border);
  background: var(--primary-soft);
}
.prompt-list .el-icon {
  color: var(--el-color-primary);
  flex-shrink: 0;
}
.workbench-mobile {
  display: none;
}
.error-text {
  color: var(--el-color-danger);
}
button:focus-visible,
summary:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}
@container workbench (max-width: 1100px) {
  .workbench-grid,
  .show-details .workbench-grid {
    grid-template-columns: 180px minmax(0, 1fr);
  }
  .diagnosis-details {
    position: absolute;
    top: 0;
    right: 0;
    bottom: 0;
    width: min(360px, 100%);
    z-index: 3;
    box-shadow: -12px 0 32px #0002;
  }
  .workbench-heading-copy {
    display: block;
  }
  .workbench-header p {
    margin-top: 5px;
  }
}
@container workbench (max-width: 700px) {
  .workbench-grid,
  .show-details .workbench-grid {
    display: block;
    height: 660px;
    height: max(520px, 70dvh);
  }
  .workbench-mobile {
    display: block;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
  }
  .session-rail,
  .conversation-area,
  .show-details .diagnosis-details,
  .diagnosis-details {
    display: none;
    border: 0;
    height: 100%;
  }
  .diagnosis-details {
    position: static;
    width: auto;
    box-shadow: none;
  }
  .workbench-grid[data-panel="sessions"] .session-rail,
  .workbench-grid[data-panel="chat"] .conversation-area,
  .workbench-grid[data-panel="details"] .diagnosis-details {
    display: flex;
    flex-direction: column;
  }
  .workbench-header {
    flex-wrap: wrap;
    padding: 14px 16px;
  }
  .workbench-header > .actions {
    margin-left: auto;
  }
  .conversation-heading {
    flex-wrap: wrap;
  }
  .runtime-bar,
  .conversation-input {
    padding: 14px 16px;
  }
  .conversation-scroll {
    padding: 20px 16px;
  }
  .chat-message.user {
    margin-left: 16px;
  }
  .input-actions {
    flex-wrap: wrap;
  }
  .input-actions small {
    width: 100%;
  }
  .tool-card summary {
    flex-wrap: wrap;
    gap: 6px;
  }
  .tool-state {
    margin-left: 18px;
  }
  .diagnosis-welcome h3 {
    font-size: 20px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .working-dot {
    animation: none;
  }
}
</style>
