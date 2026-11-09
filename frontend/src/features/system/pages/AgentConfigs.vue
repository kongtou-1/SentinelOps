<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { confirmOperation } from "@/utils/confirmOperation";
import { api, query, request, type Models } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import type { Row } from "@/types/records";
import {
  agentModules,
  channelErrors,
  channelPriority,
} from "@/features/system/agentConfig";

type Config = Models["ConfigResponse"];
type Channel = Models["ChannelResponse"];
const auth = useAuth();
const admin = computed(() => auth.user?.role === "admin");
const configs = ref<Config[]>([]),
  module = ref("diagnosis");
const current = computed(() =>
  configs.value.find((c) => c.module === module.value),
);
const error = ref(""),
  busy = ref(false),
  editing = ref(false),
  editingId = ref("");
const testing = ref("");
const form = ref({
  name: "",
  base_url: "",
  model: "",
  api_key: "",
  enabled: true,
  timeout_seconds: 60,
});
const editVersion = ref(0);
const channelId = ref(""),
  model = ref("");
const timeRange = ref<string[] | null>([]),
  page = ref(1),
  total = ref(0);
const usages = ref<Row[]>([]),
  groups = ref<Row[]>([]);
const channels = computed(() => current.value?.channels || []);
const activeIds = computed(() =>
  channels.value.filter((c) => c.enabled).map((c) => c.id),
);
const nameFor = (id: string) =>
  channels.value.find((c) => c.id === id)?.name || "已删除通道";

function filters() {
  const now = new Date();
  return {
    agent_module: module.value,
    agent_channel_id: channelId.value,
    model: model.value,
    start:
      timeRange.value?.[0] ||
      new Date(now.getFullYear(), now.getMonth(), 1).toISOString(),
    end: timeRange.value?.[1] || now.toISOString(),
  };
}
async function load() {
  const selected = module.value;
  try {
    const [configuration, summary, records] = await Promise.all([
      api<Config[]>("/agent-configs"),
      api<{ groups: Row[] }>("/system/model-usage/summary" + query(filters())),
      request<Row[]>(
        "/system/model-usage" +
          query({ ...filters(), offset: (page.value - 1) * 20, limit: 20 }),
      ),
    ]);
    if (selected !== module.value) return;
    configs.value = configuration;
    groups.value = summary.groups;
    usages.value = records.data;
    total.value = records.total || 0;
    error.value = "";
  } catch (e) {
    error.value = String(e);
  }
}
const { refresh } = usePoll(load, () =>
  editing.value || busy.value ? null : 15000,
);
watch(module, () => {
  channelId.value = "";
  model.value = "";
  page.value = 1;
  refresh();
});
function filterChanged() {
  page.value = 1;
  refresh();
}

function open(channel?: Channel) {
  if (!current.value) return;
  editingId.value = channel?.id || "";
  editVersion.value = current.value.version;
  form.value = channel
    ? {
        name: channel.name,
        base_url: channel.base_url,
        model: channel.model,
        api_key: "",
        enabled: channel.enabled,
        timeout_seconds: channel.timeout_seconds,
      }
    : {
        name: "",
        base_url: "",
        model: "",
        api_key: "",
        enabled: true,
        timeout_seconds: 60,
      };
  editing.value = true;
}
async function mutate(action: () => Promise<unknown>) {
  if (busy.value) return;
  busy.value = true;
  try {
    await action();
    await refresh();
  } catch (e) {
    error.value = String(e);
  } finally {
    busy.value = false;
  }
}
async function save() {
  if (
    !form.value.name.trim() ||
    !form.value.base_url.trim() ||
    !form.value.model.trim() ||
    (!editingId.value && !form.value.api_key)
  ) {
    ElMessage.error("请填写名称、API 地址、模型和 API Key");
    return;
  }
  await mutate(async () => {
    await api(
      `/agent-configs/${module.value}/channels${editingId.value ? "/" + editingId.value : ""}`,
      editingId.value ? "PUT" : "POST",
      {
        ...form.value,
        api_key: form.value.api_key || null,
        expected_version: editVersion.value,
      },
    );
    form.value.api_key = "";
    editing.value = false;
  });
}
function toggle() {
  const c = current.value!;
  const action = c.enabled ? "停用" : "启用";
  return mutate(async () => {
    if (
      !(await confirmOperation(
        `${action}当前模块的 Agent？${c.enabled ? "停用后该模块将不再发起新的模型任务。" : "启用后该模块可按业务配置调用模型。"}`,
        `${action} Agent`,
      ))
    )
      return;
    await api(`/agent-configs/${c.module}`, "PUT", {
      enabled: !c.enabled,
      expected_version: c.version,
    });
    ElMessage.success(`Agent 已${action}`);
  });
}
async function remove(channel: Channel) {
  const version = current.value!.version;
  try {
    await ElMessageBox.confirm(`删除通道“${channel.name}”？`, "删除通道", {
      type: "warning",
    });
  } catch {
    return;
  }
  await mutate(async () => {
    await api(
      `/agent-configs/${module.value}/channels/${channel.id}`,
      "DELETE",
      {
        expected_version: version,
      },
    );
    ElMessage.success("模型通道已删除");
  });
}
function move(index: number, offset: number) {
  const ids = channels.value.map((c) => c.id);
  [ids[index], ids[index + offset]] = [ids[index + offset]!, ids[index]!];
  return mutate(() =>
    api(`/agent-configs/${module.value}/channels/order`, "PUT", {
      expected_version: current.value!.version,
      channel_ids: ids,
    }),
  );
}
async function test(channel: Channel) {
  if (testing.value) return;
  testing.value = channel.id;
  try {
    if (
      !(await confirmOperation(
        `测试通道“${channel.name}”？将向该通道发送模型请求。`,
        "测试模型通道",
      ))
    )
      return;
    const result = await api<Models["ChannelTestResponse"]>(
      `/agent-configs/${module.value}/channels/${channel.id}/test`,
      "POST",
      {
        expected_version: current.value!.version,
      },
    );
    if (result.success)
      ElMessage.success(`连接成功 · ${Math.round(result.latency_ms)} ms`);
    else
      ElMessage.error(
        channelErrors[result.error_code || ""] ||
          result.error_code ||
          "连接失败",
      );
    await refresh();
  } catch (e) {
    error.value = String(e);
  } finally {
    testing.value = "";
  }
}
</script>

<template>
  <div class="list-workspace">
    <section class="page-header">
      <div class="page-heading">
        <h1>
          Agent 配置
          <el-tag
            v-if="configs.length"
            :type="configs[0]?.platform_enabled ? 'success' : 'info'"
            >{{
              configs[0]?.platform_enabled ? "平台 AI 已启用" : "平台 AI 已停用"
            }}</el-tag
          >
        </h1>
        <PageIntro />
        <el-button @click="refresh">刷新</el-button>
      </div>
    </section>
    <div class="panel workspace-content">
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
      <div class="agent-modules">
        <el-tabs
          class="workspace-tabs"
          v-model="module"
          aria-label="Agent 模块"
        >
          <el-tab-pane
            v-for="(name, key) in agentModules"
            :key="key"
            :name="key"
            :label="name"
          />
        </el-tabs>
      </div>
      <section v-if="current" class="agent-section">
        <div class="panel-heading">
          <h2>
            模型通道
            <el-tag :type="current.enabled ? 'success' : 'info'">{{
              current.enabled ? "模块已启用" : "模块已停用"
            }}</el-tag>
          </h2>
          <div v-if="admin" class="actions">
            <el-button :disabled="busy" @click="toggle">{{
              current.enabled ? "停用模块" : "启用模块"
            }}</el-button>
            <el-button type="primary" :disabled="busy" @click="open()"
              >新增通道</el-button
            >
          </div>
        </div>
        <el-table :data="channels" empty-text="未配置模型通道">
          <el-table-column label="顺序" width="105"
            ><template #default="{ row }">{{
              row.enabled ? channelPriority(activeIds.indexOf(row.id)) : "—"
            }}</template></el-table-column
          >
          <el-table-column prop="name" label="通道" min-width="120" />
          <el-table-column
            prop="base_url"
            label="API 地址"
            min-width="200"
            show-overflow-tooltip
          />
          <el-table-column prop="model" label="模型" min-width="140" />
          <el-table-column label="API Key" width="95"
            ><template #default>已配置</template></el-table-column
          >
          <el-table-column label="状态" width="90"
            ><template #default="{ row }"
              ><el-tag :type="row.enabled ? 'success' : 'info'">{{
                row.enabled ? "启用" : "停用"
              }}</el-tag></template
            ></el-table-column
          >
          <el-table-column label="超时" width="85"
            ><template #default="{ row }"
              >{{ row.timeout_seconds }} 秒</template
            ></el-table-column
          >
          <el-table-column label="最近请求" min-width="170"
            ><template #default="{ row }">
              {{
                row.last_success == null
                  ? "未调用"
                  : row.last_success
                    ? "成功"
                    : channelErrors[row.last_error] || row.last_error
              }}
              <span v-if="row.last_latency_ms != null">
                · {{ Math.round(row.last_latency_ms) }} ms</span
              >
              <div v-if="row.last_used_at">{{ date(row.last_used_at) }}</div>
            </template></el-table-column
          >
          <el-table-column v-if="admin" label="操作" width="270" fixed="right"
            ><template #default="{ row, $index }">
              <div class="channel-actions">
                <el-button link :disabled="busy" @click="open(row)"
                  >编辑</el-button
                >
                <el-button
                  text
                  :disabled="!!testing || busy"
                  :loading="testing === row.id"
                  @click="test(row)"
                  >测试</el-button
                >
                <el-button
                  text
                  :disabled="busy || $index === 0"
                  @click="move($index, -1)"
                  >上移</el-button
                >
                <el-button
                  text
                  :disabled="busy || $index === channels.length - 1"
                  @click="move($index, 1)"
                  >下移</el-button
                >
                <el-button
                  text
                  type="danger"
                  :disabled="busy"
                  @click="remove(row)"
                  >删除</el-button
                >
              </div>
            </template></el-table-column
          >
        </el-table>
      </section>
      <section class="agent-section">
        <div class="panel-heading">
          <h2>调用统计</h2>
        </div>
        <div class="actions filters">
          <el-date-picker
            v-model="timeRange"
            type="datetimerange"
            value-format="YYYY-MM-DDTHH:mm:ssZ"
            start-placeholder="本月开始"
            end-placeholder="当前时间"
            @change="filterChanged"
          />
          <el-select
            v-model="channelId"
            clearable
            placeholder="全部通道"
            style="width: 180px"
            @change="filterChanged"
            ><el-option
              v-for="c in channels"
              :key="c.id"
              :value="c.id"
              :label="c.name"
          /></el-select>
          <el-input
            v-model="model"
            clearable
            placeholder="模型"
            style="width: 180px"
            @change="filterChanged"
          />
        </div>
        <el-table :data="groups">
          <el-table-column label="通道"
            ><template #default="{ row }">{{
              row.channel_name ||
              row.detail?.channel_name ||
              nameFor(row.agent_channel_id)
            }}</template></el-table-column
          >
          <el-table-column prop="model" label="模型" />
          <el-table-column label="用途"
            ><template #default="{ row }">{{
              row.purpose === "connection_test" ? "连接测试" : "业务调用"
            }}</template></el-table-column
          >
          <el-table-column prop="calls" label="请求数" />
          <el-table-column label="成功 / 失败"
            ><template #default="{ row }"
              >{{ row.success_calls }} / {{ row.failed_calls }}</template
            ></el-table-column
          >
          <el-table-column prop="fallback_attempts" label="切换备用通道次数" />
          <el-table-column label="已知输入 / 输出 Token"
            ><template #default="{ row }"
              >{{ row.known_input_tokens }} /
              {{ row.known_output_tokens }}</template
            ></el-table-column
          >
          <el-table-column
            prop="unknown_usage_records"
            label="未返回 Token 的请求"
          />
        </el-table>
        <h2>请求明细</h2>
        <el-table :data="usages">
          <el-table-column label="时间" min-width="170"
            ><template #default="{ row }">{{
              date(row.occurred_at)
            }}</template></el-table-column
          >
          <el-table-column label="通道"
            ><template #default="{ row }">{{
              row.channel_name ||
              row.detail?.channel_name ||
              nameFor(row.agent_channel_id)
            }}</template></el-table-column
          >
          <el-table-column prop="model" label="实际模型" />
          <el-table-column label="用途"
            ><template #default="{ row }">{{
              row.purpose === "connection_test" ? "连接测试" : "业务调用"
            }}</template></el-table-column
          >
          <el-table-column label="结果"
            ><template #default="{ row }">{{
              row.outcome === "success"
                ? "成功"
                : row.outcome === "failed"
                  ? channelErrors[row.detail.error_code] || "失败"
                  : "未记录结果"
            }}</template></el-table-column
          >
          <el-table-column label="输入 / 输出 Token"
            ><template #default="{ row }"
              >{{ row.input_tokens ?? "未知" }} /
              {{ row.output_tokens ?? "未知" }}</template
            ></el-table-column
          >
          <el-table-column label="耗时"
            ><template #default="{ row }">{{
              row.duration_seconds == null
                ? "未知"
                : row.duration_seconds.toFixed(2) + " 秒"
            }}</template></el-table-column
          >
        </el-table>
        <el-pagination
          v-model:current-page="page"
          :total="total"
          :page-size="20"
          layout="prev, pager, next"
          @current-change="refresh"
        />
      </section>
    </div>
  </div>
  <el-dialog
    v-model="editing"
    :title="editingId ? '编辑通道' : '新增通道'"
    width="600px"
    @closed="form.api_key = ''"
  >
    <el-form label-width="110px" @submit.prevent="save">
      <el-form-item label="名称" required
        ><el-input v-model="form.name" :maxlength="100"
      /></el-form-item>
      <el-form-item label="API 地址" required
        ><el-input
          v-model="form.base_url"
          placeholder="https://api.example.com/v1"
          :maxlength="2000"
      /></el-form-item>
      <el-form-item label="模型" required
        ><el-input v-model="form.model" :maxlength="100"
      /></el-form-item>
      <el-form-item label="API Key" :required="!editingId"
        ><el-input
          v-model="form.api_key"
          type="password"
          autocomplete="new-password"
          :placeholder="editingId ? '已配置，留空保留' : '填写 API Key'"
      /></el-form-item>
      <el-form-item label="请求超时"
        ><el-input-number
          v-model="form.timeout_seconds"
          :min="1"
          :max="300"
        /><span>秒</span></el-form-item
      >
      <el-form-item label="启用"
        ><el-switch v-model="form.enabled"
      /></el-form-item>
    </el-form>
    <template #footer
      ><el-button @click="editing = false">取消</el-button
      ><el-button type="primary" :loading="busy" @click="save"
        >保存</el-button
      ></template
    >
  </el-dialog>
</template>

<style scoped>
.agent-modules {
  margin-bottom: 20px;
  max-width: 100%;
  overflow-x: auto;
}
.agent-section + .agent-section {
  border-top: 1px solid var(--border);
  padding-top: 24px;
  margin-top: 24px;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
}
.filters :deep(.el-date-editor) {
  flex: 0 1 380px;
  min-width: 280px;
}
.channel-actions {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 16px;
  white-space: nowrap;
}
.channel-actions :deep(.el-button) {
  margin: 0;
  padding: 0;
}
.panel-heading h2,
.page-heading h1 {
  margin-bottom: 0;
}
.agent-section > h2 {
  margin-top: 28px;
}
</style>
