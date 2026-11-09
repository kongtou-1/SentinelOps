<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  watch,
} from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { date } from "@/utils/display";
import { ApiError, authorizedFetch } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { consumeEvents } from "@/api/events";
import {
  askdata,
  isTerminalStatus,
  type AskDataConnection,
  type AskDataRun,
  type AskDataRunDetail,
  type AskDataRunSummary,
  type AskDataStepEvent,
  type ChatTurn,
} from "@/features/askdata/api";
import RunAnswer from "@/features/askdata/components/RunAnswer.vue";

const ALL = "__all__";
const NO_APP = "__none__";

const auth = useAuth();
const canRun = computed(() => auth.can("askdata:run"));

interface ConversationItem {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  runs: AskDataRunSummary[];
}

const connections = ref<AskDataConnection[]>([]);
const connectionsBusy = ref(false);
const appFilter = ref(ALL);
const envFilter = ref(ALL);
const connectionId = ref("");

const conversations = ref<ConversationItem[]>([]);
const conversationsBusy = ref(false);
const activeConversationId = ref("");
const deletingId = ref("");

const turns = ref<ChatTurn[]>([]);
const turnsBusy = ref(false);
const error = ref("");
const input = ref("");
const submitting = ref(false);
const cancelling = ref(false);
const exporting = ref(false);
const scrollHost = ref<HTMLElement>();

let controller: AbortController | null = null;
let conversationLoadSeq = 0;

function appKey(connection: AskDataConnection) {
  return connection.application_id ?? NO_APP;
}
function appLabel(key: string) {
  if (key === ALL) return "全部应用";
  if (key === NO_APP) return "未关联应用";
  return `应用 ${key.slice(0, 8)}`;
}
function dbLabel(item: AskDataConnection) {
  return `${item.name} · ${item.database}${item.askdata_enabled ? "" : "（未启用）"}`;
}
function formatTime(value: string) {
  return date(value);
}
function messageOf(e: unknown, fallback: string) {
  return e instanceof Error && e.message ? e.message : fallback;
}
function fatal(e: unknown) {
  return e instanceof ApiError && (e.status === 401 || e.status === 403);
}

const appOptions = computed(() => {
  const keys = new Set<string>();
  for (const item of connections.value) keys.add(appKey(item));
  return [
    { value: ALL, label: "全部应用" },
    ...[...keys].sort().map((key) => ({ value: key, label: appLabel(key) })),
  ];
});
const envOptions = computed(() => {
  const envs = new Set<string>();
  for (const item of connections.value) {
    if (appFilter.value !== ALL && appKey(item) !== appFilter.value) continue;
    if (item.environment) envs.add(item.environment);
  }
  return [
    { value: ALL, label: "全部环境" },
    ...[...envs].sort().map((value) => ({ value, label: value })),
  ];
});
const databaseOptions = computed(() =>
  connections.value.filter(
    (item) =>
      (appFilter.value === ALL || appKey(item) === appFilter.value) &&
      (envFilter.value === ALL || item.environment === envFilter.value),
  ),
);
const selectedConnection = computed(
  () =>
    connections.value.find((item) => item.id === connectionId.value) || null,
);
const canAsk = computed(
  () =>
    canRun.value &&
    !!selectedConnection.value?.askdata_enabled &&
    !!selectedConnection.value.enabled &&
    !!selectedConnection.value.query_enabled,
);
const activeRun = computed(() => {
  for (let index = turns.value.length - 1; index >= 0; index--) {
    if (!isTerminalStatus(turns.value[index].status)) return turns.value[index];
  }
  return null;
});
const exportableRun = computed(() => {
  const last = turns.value[turns.value.length - 1];
  return last && last.status === "completed" ? last : null;
});

function recentQuestion(item: ConversationItem) {
  const run = item.runs[item.runs.length - 1];
  return run ? run.question : "暂无提问";
}
function mergeSteps(
  existing: AskDataStepEvent[],
  incoming: AskDataStepEvent[],
) {
  const map = new Map<number, AskDataStepEvent>();
  for (const event of [...existing, ...incoming]) map.set(event.seq, event);
  return [...map.values()].sort((a, b) => a.seq - b.seq);
}
function maxSeq(turn: ChatTurn) {
  return turn.steps.reduce((max, event) => Math.max(max, event.seq), 0);
}
function toTurn(detail: AskDataRunDetail): ChatTurn {
  return {
    runId: detail.id,
    connectionId: detail.connection_id,
    modelVersionId: detail.model_version_id,
    question: detail.question,
    status: detail.status,
    errorCode: detail.error_code,
    clarification: detail.result?.clarification ?? null,
    steps: mergeSteps([], detail.events ?? []),
    result: detail.result ?? null,
    streaming: false,
    createdAt: detail.created_at,
  };
}
function newTurn(run: AskDataRun): ChatTurn {
  return {
    runId: run.id,
    connectionId: run.connection_id,
    modelVersionId: run.model_version_id,
    question: run.question,
    status: run.status,
    errorCode: run.error_code,
    clarification: null,
    steps: [],
    result: null,
    streaming: true,
    createdAt: run.created_at,
  };
}

function abortStream() {
  controller?.abort();
  controller = null;
}
function scrollToBottom() {
  void nextTick(() => {
    if (scrollHost.value)
      scrollHost.value.scrollTop = scrollHost.value.scrollHeight;
  });
}

async function loadConnections() {
  connectionsBusy.value = true;
  try {
    connections.value = await askdata.connections();
    const valid = databaseOptions.value.filter(
      (item) => item.enabled && item.query_enabled && item.askdata_enabled,
    );
    const current = databaseOptions.value.find(
      (item) => item.id === connectionId.value,
    );
    const next =
      current?.id || valid[0]?.id || databaseOptions.value[0]?.id || "";
    if (next !== connectionId.value) switchDatabase(next);
  } catch (e) {
    error.value = messageOf(e, "数据库连接加载失败");
  } finally {
    connectionsBusy.value = false;
  }
}

async function loadConversations() {
  const seq = ++conversationLoadSeq;
  if (!connectionId.value) {
    conversations.value = [];
    return;
  }
  conversationsBusy.value = true;
  try {
    const list = await askdata.conversations({
      connection_id: connectionId.value,
      limit: 50,
    });
    const items: ConversationItem[] = await Promise.all(
      list.map(async (conv) => {
        try {
          const detail = await askdata.conversation(conv.id);
          return {
            id: conv.id,
            title: conv.title,
            createdAt: conv.created_at,
            updatedAt: conv.updated_at,
            runs: detail.runs ?? [],
          };
        } catch {
          return {
            id: conv.id,
            title: conv.title,
            createdAt: conv.created_at,
            updatedAt: conv.updated_at,
            runs: [] as AskDataRunSummary[],
          };
        }
      }),
    );
    if (seq === conversationLoadSeq) conversations.value = items;
  } catch (e) {
    if (seq === conversationLoadSeq)
      error.value = messageOf(e, "会话列表加载失败");
  } finally {
    if (seq === conversationLoadSeq) conversationsBusy.value = false;
  }
}

function switchDatabase(value: unknown) {
  const id = typeof value === "string" ? value : "";
  connectionId.value = id;
  conversationLoadSeq += 1;
  conversationsBusy.value = false;
  abortStream();
  activeConversationId.value = "";
  turns.value = [];
  turnsBusy.value = false;
  conversations.value = [];
  error.value = "";
  if (id) void loadConversations();
}

async function selectConversation(id: string) {
  if (id === activeConversationId.value) return;
  abortStream();
  activeConversationId.value = id;
  turns.value = [];
  error.value = "";
  turnsBusy.value = true;
  try {
    const detail = await askdata.conversation(id);
    const details = await Promise.all(
      (detail.runs ?? []).map((item) => askdata.run(item.id).catch(() => null)),
    );
    if (activeConversationId.value !== id) return;
    turns.value = details
      .filter((item): item is AskDataRunDetail => !!item)
      .map(toTurn);
    const active = turns.value.find((turn) => !isTerminalStatus(turn.status));
    if (active) void startWatch(active.runId, maxSeq(active));
    scrollToBottom();
  } catch (e) {
    if (activeConversationId.value === id)
      error.value = messageOf(e, "会话加载失败");
  } finally {
    if (activeConversationId.value === id) turnsBusy.value = false;
  }
}

async function deleteConversation(item: ConversationItem) {
  if (deletingId.value || submitting.value) return;
  const database = connectionId.value;
  try {
    await ElMessageBox.confirm(
      `删除会话「${item.title || "新问数"}」及其中的问答？正在进行的问数将取消。`,
      "删除会话",
      { confirmButtonText: "删除", cancelButtonText: "取消", type: "warning" },
    );
  } catch {
    return;
  }
  if (deletingId.value || submitting.value) return;
  deletingId.value = item.id;
  try {
    await askdata.deleteConversation(item.id);
    if (connectionId.value === database) {
      conversationLoadSeq += 1;
      conversationsBusy.value = false;
      conversations.value = conversations.value.filter(
        (row) => row.id !== item.id,
      );
    }
    if (activeConversationId.value === item.id) {
      abortStream();
      activeConversationId.value = "";
      turns.value = [];
      turnsBusy.value = false;
      input.value = "";
      error.value = "";
    }
    ElMessage.success("会话已删除");
  } catch (e) {
    error.value = messageOf(e, "删除会话失败");
  } finally {
    deletingId.value = "";
  }
}

async function applyRunDetail(runId: string) {
  const detail = await askdata.run(runId);
  const turn = turns.value.find((item) => item.runId === runId);
  if (!turn) return;
  turn.status = detail.status;
  turn.errorCode = detail.error_code;
  turn.clarification = detail.result?.clarification ?? null;
  turn.result = detail.result ?? null;
  turn.steps = mergeSteps(turn.steps, detail.events ?? []);
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

async function startWatch(runId: string, startSeq: number) {
  abortStream();
  const ctrl = new AbortController();
  controller = ctrl;
  const signal = ctrl.signal;
  let afterSeq = startSeq;
  let retries = 0;
  const current = () => turns.value.find((item) => item.runId === runId);
  const active = current();
  if (active) active.streaming = true;
  try {
    while (!signal.aborted) {
      const inner = new AbortController();
      const onOuterAbort = () => inner.abort();
      signal.addEventListener("abort", onOuterAbort, { once: true });
      // A stream that stays open after "event: done" must not block reconciliation.
      const streamTimer = setTimeout(() => inner.abort(), 45000);
      try {
        const response = await authorizedFetch(
          askdata.runStream(runId, afterSeq),
          {
            signal: inner.signal,
          },
        );
        await consumeEvents(response, (raw) => {
          const event = raw as unknown as AskDataStepEvent;
          const turn = current();
          if (!turn) return;
          turn.steps = mergeSteps(turn.steps, [event]);
          if (event.seq > afterSeq) afterSeq = event.seq;
        });
      } catch (e) {
        if (signal.aborted) return;
        if (fatal(e)) {
          error.value = messageOf(e, "事件连接失败");
          return;
        }
      } finally {
        clearTimeout(streamTimer);
        signal.removeEventListener("abort", onOuterAbort);
      }

      try {
        const page = await askdata.runEvents(runId, afterSeq, 200);
        const turn = current();
        if (turn && page.items.length)
          turn.steps = mergeSteps(turn.steps, page.items);
        if (page.latest_seq > afterSeq) afterSeq = page.latest_seq;
        for (const event of page.items)
          if (event.seq > afterSeq) afterSeq = event.seq;
        if (page.terminal) {
          await applyRunDetail(runId);
          scrollToBottom();
          return;
        }
      } catch (e) {
        if (signal.aborted) return;
        if (fatal(e)) {
          error.value = messageOf(e, "事件连接失败");
          return;
        }
      }

      retries += 1;
      if (retries > 40) {
        error.value = "事件连接中断，请刷新后重试";
        return;
      }
      await delay(700, signal);
    }
  } finally {
    const turn = current();
    if (turn) turn.streaming = false;
    if (controller === ctrl) controller = null;
  }
}

async function newConversation() {
  if (!canAsk.value || submitting.value || deletingId.value) return;
  submitting.value = true;
  error.value = "";
  try {
    const conv = await askdata.createConversation({
      connection_id: connectionId.value,
      title: "新问数",
    });
    abortStream();
    activeConversationId.value = conv.id;
    turns.value = [];
    await loadConversations();
  } catch (e) {
    error.value = messageOf(e, "新建会话失败");
  } finally {
    submitting.value = false;
  }
}

async function submit() {
  const question = input.value.trim();
  if (
    !question ||
    !canAsk.value ||
    submitting.value ||
    deletingId.value ||
    activeRun.value
  )
    return;
  submitting.value = true;
  error.value = "";
  try {
    let convId = activeConversationId.value;
    if (!convId) {
      const conv = await askdata.createConversation({
        connection_id: connectionId.value,
        title: question.slice(0, 40),
      });
      convId = conv.id;
      activeConversationId.value = convId;
    }
    const run = await askdata.createRun(convId, {
      question,
      idempotency_key: crypto.randomUUID(),
    });
    input.value = "";
    turns.value.push(newTurn(run));
    scrollToBottom();
    void startWatch(run.id, 0);
    void loadConversations();
  } catch (e) {
    error.value = messageOf(e, "问题提交失败");
  } finally {
    submitting.value = false;
  }
}

function onEnter(event: Event | KeyboardEvent) {
  const keyEvent = event as KeyboardEvent;
  if (keyEvent.shiftKey || keyEvent.isComposing) return;
  keyEvent.preventDefault();
  void submit();
}

async function cancelActive() {
  const run = activeRun.value;
  if (!run) return;
  cancelling.value = true;
  try {
    await askdata.cancelRun(run.runId);
    await applyRunDetail(run.runId);
  } catch (e) {
    error.value = messageOf(e, "取消失败");
  } finally {
    cancelling.value = false;
  }
}

async function exportCsv() {
  const run = exportableRun.value;
  if (!run) return;
  exporting.value = true;
  error.value = "";
  try {
    await askdata.exportRun(run.runId);
  } catch (e) {
    error.value = messageOf(e, "导出失败");
  } finally {
    exporting.value = false;
  }
}

const saveDialog = ref(false);
const saving = ref(false);
const saveTurn = ref<ChatTurn | null>(null);
const saveForm = reactive({ name: "", category: "" });

function openSave() {
  const run = exportableRun.value;
  if (!run) return;
  saveTurn.value = run;
  saveForm.name = run.question.slice(0, 200);
  saveForm.category = "";
  saveDialog.value = true;
}

async function submitSave() {
  const run = saveTurn.value;
  if (!run || !run.result) return;
  saving.value = true;
  error.value = "";
  try {
    await askdata.createAnalysis({
      connection_id: run.connectionId,
      name: saveForm.name.trim(),
      category: saveForm.category.trim(),
      question: run.question,
      sql_text: run.result.executed_sql || run.result.sql,
      explanation: run.result.answer ?? "",
      chart: (run.result.chart ?? {}) as Record<string, unknown>,
      evidence: run.result.evidence ?? [],
      model_version_id: run.modelVersionId || null,
      source_run_id: run.runId,
    });
    saveDialog.value = false;
    ElMessage.success("已保存到分析库");
  } catch (e) {
    error.value = messageOf(e, "保存分析失败");
  } finally {
    saving.value = false;
  }
}

async function refreshAll() {
  error.value = "";
  await loadConnections();
  if (connectionId.value) await loadConversations();
}

watch([appFilter, envFilter], () => {
  if (
    envFilter.value !== ALL &&
    !envOptions.value.some((option) => option.value === envFilter.value)
  ) {
    envFilter.value = ALL;
  }
  const valid = databaseOptions.value.filter((item) => item.askdata_enabled);
  const next = valid[0]?.id || databaseOptions.value[0]?.id || "";
  if (next !== connectionId.value) switchDatabase(next);
});
watch(
  () => [
    turns.value.length,
    turns.value.reduce((sum, turn) => sum + turn.steps.length, 0),
  ],
  () => scrollToBottom(),
);

onMounted(() => void loadConnections());
onUnmounted(() => abortStream());
</script>

<template>
  <section class="chat-page">
    <div class="page-heading">
      <h1>问数对话</h1>
      <PageIntro />
      <el-button
        type="primary"
        :disabled="!canAsk || submitting || !!deletingId"
        @click="newConversation"
        >新建会话</el-button
      >
    </div>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />

    <div class="context-bar">
      <el-select v-model="appFilter" class="ctx-select" placeholder="应用">
        <el-option
          v-for="option in appOptions"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </el-select>
      <el-select v-model="envFilter" class="ctx-select" placeholder="环境">
        <el-option
          v-for="option in envOptions"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </el-select>
      <el-select
        :model-value="connectionId"
        class="ctx-select db-select"
        placeholder="数据库"
        :loading="connectionsBusy"
        @update:model-value="switchDatabase"
      >
        <el-option
          v-for="item in databaseOptions"
          :key="item.id"
          :label="dbLabel(item)"
          :value="item.id"
        />
      </el-select>
      <el-tag
        v-if="selectedConnection"
        :type="selectedConnection.askdata_enabled ? 'success' : 'info'"
        size="small"
        effect="plain"
      >
        {{ selectedConnection.askdata_enabled ? "已启用问数" : "未启用问数" }}
      </el-tag>
      <el-button :loading="connectionsBusy" @click="refreshAll">刷新</el-button>
    </div>

    <div class="chat-body">
      <aside class="conversation-panel">
        <div class="panel-head">
          <span>会话</span>
          <el-button
            link
            type="primary"
            :disabled="!connectionId"
            @click="loadConversations"
            >刷新</el-button
          >
        </div>
        <div v-loading="conversationsBusy" class="conversation-list">
          <div
            v-for="item in conversations"
            :key="item.id"
            class="conversation-item"
            :class="{ active: item.id === activeConversationId }"
          >
            <button
              type="button"
              class="conversation-select"
              @click="selectConversation(item.id)"
            >
              <span class="conversation-title">{{
                item.title || "新问数"
              }}</span>
              <span class="conversation-meta"
                >{{ item.runs.length }} 个问题</span
              >
              <span class="conversation-recent">{{
                recentQuestion(item)
              }}</span>
            </button>
            <el-button
              v-if="canRun"
              link
              type="danger"
              size="small"
              :aria-label="`删除会话 ${item.title || '新问数'}`"
              :loading="deletingId === item.id"
              :disabled="!!deletingId || submitting"
              @click="deleteConversation(item)"
              >删除</el-button
            >
          </div>
          <el-empty
            v-if="!conversations.length && !conversationsBusy"
            description="暂无会话"
            :image-size="60"
          />
        </div>
      </aside>

      <div class="chat-main">
        <div v-loading="turnsBusy" ref="scrollHost" class="message-scroll">
          <template v-if="turns.length">
            <div v-for="turn in turns" :key="turn.runId" class="turn">
              <div class="bubble user-bubble">
                <div class="bubble-text">{{ turn.question }}</div>
                <span class="bubble-time">{{
                  formatTime(turn.createdAt)
                }}</span>
              </div>
              <div class="bubble answer-bubble">
                <RunAnswer :run="turn" />
              </div>
            </div>
          </template>
          <el-empty
            v-else-if="!connectionId"
            description="请选择启用问数的数据库"
            :image-size="70"
          />
          <el-empty
            v-else-if="!activeConversationId"
            description="选择或新建会话开始提问"
            :image-size="70"
          />
          <el-empty
            v-else
            description="暂无消息，输入问题开始提问"
            :image-size="70"
          />
        </div>

        <div class="composer">
          <el-input
            v-model="input"
            type="textarea"
            :rows="3"
            resize="none"
            :disabled="!canAsk || submitting"
            placeholder="输入问题，按 Enter 发送"
            @keydown.enter="onEnter"
          />
          <div class="composer-actions">
            <el-button
              :disabled="!activeRun"
              :loading="cancelling"
              @click="cancelActive"
              >取消</el-button
            >
            <el-button
              :disabled="!exportableRun"
              :loading="exporting"
              @click="exportCsv"
              >导出 CSV</el-button
            >
            <el-button
              v-if="canRun"
              :disabled="!exportableRun || !exportableRun.result"
              @click="openSave"
              >存为分析</el-button
            >
            <el-button
              type="primary"
              :loading="submitting"
              :disabled="
                !canAsk || !input.trim() || !!activeRun || !!deletingId
              "
              @click="submit"
              >发送</el-button
            >
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="saveDialog" title="保存到分析库" width="520px">
      <el-form label-width="72px" label-position="left">
        <el-form-item label="名称">
          <el-input v-model="saveForm.name" maxlength="200" show-word-limit />
        </el-form-item>
        <el-form-item label="分类">
          <el-input
            v-model="saveForm.category"
            maxlength="60"
            placeholder="可选，例如：订单分析"
          />
        </el-form-item>
        <el-form-item label="问题">
          <el-input
            :model-value="saveTurn?.question ?? ''"
            type="textarea"
            :rows="2"
            readonly
          />
        </el-form-item>
        <el-form-item label="SQL">
          <el-input
            :model-value="saveTurn?.result?.sql ?? ''"
            type="textarea"
            :rows="4"
            readonly
          />
        </el-form-item>
      </el-form>
      <p class="save-hint">
        将绑定当前数据库、业务模型版本与来源问数记录；重新执行时按当时授权重新校验并脱敏。
      </p>
      <template #footer>
        <el-button @click="saveDialog = false">取消</el-button>
        <el-button
          type="primary"
          :loading="saving"
          :disabled="!saveForm.name.trim()"
          @click="submitSave"
          >保存</el-button
        >
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.chat-page {
  display: grid;
  gap: 14px;
  min-width: 0;
}
.page-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
h1 {
  margin: 0;
  font-size: 24px;
}
.context-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.save-hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--muted, #8b93a7);
}
.ctx-select {
  width: min(190px, 100%);
}
.db-select {
  width: min(280px, 100%);
}
.chat-body {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  gap: 12px;
  height: calc(var(--app-viewport-height, 100vh) - 280px);
  min-height: 480px;
}
.conversation-panel,
.chat-main {
  display: grid;
  min-width: 0;
  min-height: 0;
  border: 1px solid var(--border, #303644);
  border-radius: 12px;
  background: var(--surface, #1b2130);
  overflow: hidden;
}
.conversation-panel {
  grid-template-rows: auto minmax(0, 1fr);
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border, #303644);
  font-size: 14px;
}
.conversation-list {
  overflow-y: auto;
  padding: 8px;
}
.conversation-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  margin-bottom: 6px;
  padding: 10px;
  text-align: left;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--text, #e6e9ef);
}
.conversation-select {
  display: grid;
  gap: 4px;
  flex: 1;
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.conversation-item:hover {
  background: rgba(76, 130, 238, 0.08);
}
.conversation-item.active {
  border-color: rgba(76, 130, 238, 0.5);
  background: rgba(76, 130, 238, 0.14);
}
.conversation-title {
  font-size: 13px;
  font-weight: 550;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conversation-meta,
.conversation-recent {
  font-size: 12px;
  color: var(--muted, #8993a4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chat-main {
  grid-template-rows: minmax(0, 1fr) auto;
}
.message-scroll {
  display: grid;
  gap: 18px;
  align-content: start;
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
}
.turn {
  display: grid;
  gap: 10px;
}
.bubble {
  padding: 12px 14px;
  border: 1px solid var(--border, #303644);
  border-radius: 10px;
  font-size: 14px;
}
.user-bubble {
  justify-self: end;
  max-width: 78%;
  border-color: rgba(76, 130, 238, 0.35);
  background: rgba(76, 130, 238, 0.16);
}
.answer-bubble {
  max-width: 100%;
  background: var(--surface, #1b2130);
}
.bubble-text {
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.bubble-time {
  display: block;
  margin-top: 6px;
  font-size: 11px;
  color: var(--muted, #8993a4);
}
.composer {
  display: grid;
  gap: 10px;
  padding: 12px;
  border-top: 1px solid var(--border, #303644);
}
.composer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
@media (max-width: 1080px) {
  .chat-body {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: 190px minmax(0, 1fr);
  }
}
</style>
