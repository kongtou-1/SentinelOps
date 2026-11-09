<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { all, api, type Models } from "@/api/client";
import { configs } from "@/config/lists";
import { agentModules } from "@/features/system/agentConfig";
import { type Row, type Field } from "@/types/records";
import { metricUnit } from "@/utils/display";
const props = defineProps<{
    kind: string;
    row?: Row | null;
    modelValue: boolean;
  }>(),
  emit = defineEmits(["update:modelValue", "saved"]);
const model = ref<Row>({}),
  choices = ref<Record<string, { label: string; value: string }[]>>({}),
  error = ref(""),
  busy = ref(false),
  form = ref(),
  seriesBusy = ref(false),
  channels = ref<Models["ChannelResponse"][]>([]),
  channelsBusy = ref(false);
const editing = computed(() => !!props.row?.id),
  config = computed(() => configs[props.kind]!),
  fields = computed(
    () =>
      config.value.fields?.filter(
        (f) =>
          !(
            props.kind === "users" &&
            ((editing.value && f.key === "username") ||
              (!editing.value && f.key === "is_active"))
          ),
      ) || [],
  );
const usesManagedKey = computed(
  () => props.kind === "llm-endpoints" && !!model.value.agent_channel_id,
);
const needsNewKey = computed(
  () =>
    !usesManagedKey.value && (!editing.value || !!props.row?.agent_channel_id),
);
function selectChannel(id: string) {
  const channel = channels.value.find((c) => c.id === id);
  if (!channel) return;
  model.value.base_url = channel.base_url;
  model.value.model = channel.model;
  model.value.api_key = "";
}
function fieldRequired(field: Field) {
  if (props.kind === "users" && field.key === "password") return !editing.value;
  if (props.kind === "llm-endpoints" && field.key === "api_key")
    return needsNewKey.value;
  if (props.kind === "servers" && field.key === "ssh_host_key")
    return !!model.value.credential_id;
  if (props.kind === "prediction-tasks" && field.key === "series")
    return needsSeries(model.value.metric);
  return !!field.required;
}
const rules = computed(() =>
  Object.fromEntries(
    fields.value
      .filter(fieldRequired)
      .map((f) => [
        f.key,
        [{ required: true, message: `请填写${f.label}`, trigger: "blur" }],
      ]),
  ),
);
function sourceFields() {
  return fields.value.filter((f) => f.source);
}
watch(
  () => props.modelValue,
  async (open, _, onCleanup) => {
    if (!open) return;
    let current = true;
    onCleanup(() => {
      current = false;
    });
    error.value = "";
    choices.value = {};
    model.value = {};
    channels.value = [];
    channelsBusy.value = false;
    for (const f of fields.value)
      model.value[f.key] =
        props.row?.[f.key] ??
        f.default ??
        (f.type === "tags" ? [] : f.type === "switch" ? false : "");
    for (const f of fields.value.filter((f) => f.type === "json"))
      model.value[f.key] = JSON.stringify(model.value[f.key] || {}, null, 2);
    if (props.kind === "alert-rules")
      model.value.auto_diagnose =
        props.row?.auto_diagnose == null ? "" : String(props.row.auto_diagnose);
    if (props.kind === "prediction-tasks" && props.row) {
      const key = ["server_id", "container_id", "llm_endpoint_id"].find(
        (k) => props.row?.[k],
      );
      if (key) model.value.resource = key + ":" + props.row[key];
      model.value.series = JSON.stringify(props.row.selector || {});
    }
    try {
      if (props.kind === "llm-endpoints") {
        channelsBusy.value = true;
        try {
          const configs =
            await api<Models["ConfigResponse"][]>("/agent-configs");
          if (current) {
            channels.value = configs.flatMap((c) =>
              c.channels.filter((channel) => channel.enabled),
            );
            const linked = channels.value.find(
              (channel) => channel.id === model.value.agent_channel_id,
            );
            if (linked) model.value.base_url = linked.base_url;
          }
        } finally {
          if (current) channelsBusy.value = false;
        }
        if (!current) return;
      }
      for (const f of sourceFields()) {
        if (f.source === "resources") {
          const sets = await Promise.all(
            ["/servers", "/containers", "/llm-endpoints"].map((p) =>
              all<Row>(p),
            ),
          );
          choices.value[f.key] = sets.flatMap((rows, i) =>
            rows.map((r) => ({
              label: ["服务器", "容器", "LLM"][i] + " · " + r.name,
              value:
                ["server_id", "container_id", "llm_endpoint_id"][i] +
                ":" +
                r.id,
            })),
          );
        } else
          choices.value[f.key] = (await all<Row>(f.source!)).map((r) => ({
            label: r.name || r.title || r.username,
            value: r.id,
          }));
      }
      if (props.kind === "prediction-tasks") await loadSeries();
    } catch (e) {
      if (current) error.value = String(e instanceof Error ? e.message : e);
    }
  },
);
function needsSeries(metric: string) {
  return (
    metric === "host_disk_percent" ||
    metric?.startsWith("host_network_") ||
    metric?.startsWith("container_network_")
  );
}
let seriesGeneration = 0;
async function loadSeries() {
  const token = ++seriesGeneration;
  choices.value.series = [];
  if (!needsSeries(model.value.metric)) {
    model.value.series = "{}";
    return;
  }
  if (!model.value.resource || !model.value.metric) return;
  seriesBusy.value = true;
  try {
    const [kind, id] = model.value.resource.split(":");
    const path =
      kind === "server_id"
        ? "servers"
        : kind === "container_id"
          ? "containers"
          : "llm-endpoints";
    const d = await api<Row>(
      `/${path}/${id}/metrics?metric=${encodeURIComponent(model.value.metric)}`,
    );
    if (token !== seriesGeneration) return;
    choices.value.series = (d.result || []).map((r: Row) => {
      const keys =
        model.value.metric === "host_disk_percent"
          ? ["device", "mountpoint", "fstype"]
          : kind === "server_id"
            ? ["device"]
            : ["interface"];
      const value = Object.fromEntries(keys.map((k) => [k, r.metric[k]]));
      return {
        label: Object.values(value).join(" · "),
        value: JSON.stringify(value),
      };
    });
    if (!choices.value.series.length)
      error.value = "没有可选择的实际采样序列，请先核对采集配置";
  } catch (e) {
    if (token === seriesGeneration)
      error.value = e instanceof Error ? e.message : "序列加载失败";
  } finally {
    if (token === seriesGeneration) seriesBusy.value = false;
  }
}
watch(
  () => [model.value.resource, model.value.metric],
  () => {
    if (props.kind === "prediction-tasks" && props.modelValue)
      void loadSeries();
  },
);
const fieldOptions = (f: Field) =>
  f.options?.filter(
    (o) =>
      props.kind !== "prediction-tasks" ||
      f.key !== "metric" ||
      o.value.startsWith(
        model.value.resource?.startsWith("server_id:")
          ? "host_"
          : model.value.resource?.startsWith("container_id:")
            ? "container_"
            : "llm_",
      ),
  ) ||
  choices.value[f.key] ||
  [];
async function save() {
  if (busy.value) return;
  try {
    await form.value.validate();
  } catch {
    return;
  }
  error.value = "";
  busy.value = true;
  try {
    const body: Row = {};
    for (const f of fields.value) {
      if (f.key === "api_key" && usesManagedKey.value) continue;
      let v = model.value[f.key];
      if (f.type === "json") {
        try {
          v = JSON.parse(v || "{}");
        } catch {
          throw new Error(`${f.label} 必须为有效 JSON`);
        }
      }
      if (f.nullable && v === "") v = null;
      if (
        props.kind === "alert-rules" &&
        f.key === "auto_diagnose" &&
        v !== null
      )
        v = v === "true";
      if (["api_key", "password"].includes(f.key) && !v) continue;
      body[f.key] = v;
    }
    if (props.kind === "users") {
      if (!editing.value) delete body.is_active;
      if (!editing.value && !body.password)
        throw new Error("新增用户必须设置密码");
    }
    if (props.kind === "llm-endpoints" && needsNewKey.value && !body.api_key)
      throw new Error("请填写 API Key");
    if (props.kind === "ssh-credentials" && body.auth_type === "password")
      delete body.passphrase;
    if (props.kind === "prediction-tasks") {
      const [key, id] = String(body.resource).split(":");
      const expected =
        key === "server_id"
          ? "host_"
          : key === "container_id"
            ? "container_"
            : "llm_";
      if (!body.metric.startsWith(expected))
        throw new Error("指标与目标资源类型不一致");
      body.server_id = null;
      body.container_id = null;
      body.llm_endpoint_id = null;
      body[key] = id;
      body.selector = needsSeries(body.metric)
        ? JSON.parse(body.series || "null")
        : {};
      if (!body.selector) throw new Error("请选择实际采样序列");
      delete body.resource;
      delete body.series;
    }
    if (
      props.kind === "alert-rules" &&
      body.prediction_horizon_hours !== null &&
      body.prediction_horizon_hours !== ""
    )
      body.prediction_horizon_hours = Number(body.prediction_horizon_hours);
    await api(
      "/" + props.kind + (editing.value ? "/" + props.row!.id : ""),
      editing.value ? (props.kind === "users" ? "PATCH" : "PUT") : "POST",
      body,
    );
    model.value = {};
    emit("saved");
    emit("update:modelValue", false);
    ElMessage.success(
      props.kind === "alert-rules" ? "规则已保存，请显式应用" : "已保存",
    );
  } catch (e) {
    error.value = e instanceof Error ? e.message : "保存失败";
  } finally {
    busy.value = false;
  }
}
function beforeClose(done: () => void) {
  if (!busy.value) done();
}
</script>
<template>
  <el-dialog
    :model-value="modelValue"
    :title="(editing ? '编辑' : '新增') + ' · ' + config.title"
    width="640px"
    :close-on-click-modal="false"
    :before-close="beforeClose"
    @update:model-value="emit('update:modelValue', $event)"
    ><el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="error-box"
    /><el-form
      ref="form"
      :model="model"
      :rules="rules"
      label-position="top"
      @submit.prevent="save"
      ><el-form-item
        v-for="f in fields"
        :key="f.key"
        :label="f.label"
        :required="fieldRequired(f)"
        :prop="f.key"
        :class="{
          'schedule-switch': kind === 'prediction-tasks' && f.key === 'enabled',
        }"
        v-show="
          (f.key !== 'series' || needsSeries(model.metric)) &&
          (f.key !== 'api_key' || !usesManagedKey)
        "
        ><el-switch
          v-if="f.type === 'switch'"
          v-model="model[f.key]"
          :aria-label="f.label"
        /><el-input-number
          v-else-if="f.type === 'number'"
          v-model="model[f.key]"
          :min="f.min"
          :max="f.max"
          :controls="false"
          style="width: 100%"
        /><el-select
          v-else-if="f.key === 'agent_channel_id'"
          v-model="model.agent_channel_id"
          filterable
          :empty-values="[null, undefined]"
          :loading="channelsBusy"
          style="width: 100%"
          @change="selectChannel"
        >
          <el-option label="使用独立 Key" value="" />
          <el-option
            v-if="
              usesManagedKey &&
              !channels.some((c) => c.id === model.agent_channel_id)
            "
            :label="channelsBusy ? '正在加载模型通道' : '已关联通道不可用'"
            :value="model.agent_channel_id"
            disabled
          />
          <el-option-group v-if="channels.length" label="Agent 配置">
            <el-option
              v-for="channel in channels"
              :key="channel.id"
              :label="`${agentModules[channel.module]} · ${channel.name} · ${channel.model}`"
              :value="channel.id"
            />
          </el-option-group> </el-select
        ><el-select
          v-else-if="f.type === 'select'"
          v-model="model[f.key]"
          filterable
          clearable
          :loading="f.key === 'series' && seriesBusy"
          style="width: 100%"
          ><el-option
            v-for="o in fieldOptions(f)"
            :key="o.value"
            :label="o.label"
            :value="o.value" /></el-select
        ><el-select
          v-else-if="f.type === 'tags'"
          v-model="model[f.key]"
          multiple
          filterable
          allow-create
          default-first-option
          style="width: 100%"
          ><el-option
            v-for="tag in model[f.key]"
            :key="tag"
            :label="tag"
            :value="tag" /></el-select
        ><el-input
          v-else-if="f.type === 'textarea' || f.type === 'json'"
          v-model="model[f.key]"
          type="textarea"
          :rows="f.key === 'content' ? 12 : 4"
          :maxlength="f.key === 'content' ? 100000 : 32768"
        /><el-input
          v-else
          v-model="model[f.key]"
          :type="f.type === 'password' ? 'password' : 'text'"
          :show-password="f.type === 'password'"
          :autocomplete="f.type === 'password' ? 'new-password' : 'off'"
          :readonly="f.key === 'base_url' && usesManagedKey"
          :placeholder="
            f.key === 'api_key' && !needsNewKey ? '已配置，留空保留' : undefined
          "
        />
        <div v-if="f.hint" class="form-hint">{{ f.hint }}</div>
        <div v-if="f.key === 'threshold' && model.metric" class="form-hint">
          输入单位：{{
            ["llm_success_rate", "llm_error_rate"].includes(model.metric)
              ? "0–1 比例"
              : metricUnit(model.metric) === "GB"
                ? "bytes"
                : metricUnit(model.metric) || "数值"
          }}
        </div></el-form-item
      ></el-form
    ><template #footer
      ><el-button :disabled="busy" @click="emit('update:modelValue', false)"
        >取消</el-button
      ><el-button type="primary" :loading="busy" @click="save"
        >保存</el-button
      ></template
    ></el-dialog
  >
</template>

<style scoped>
.schedule-switch {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
}
.schedule-switch :deep(.el-form-item__label) {
  margin-bottom: 0;
}
.schedule-switch :deep(.el-form-item__content) {
  display: contents;
}
.schedule-switch .form-hint {
  grid-column: 1 / -1;
}
</style>
