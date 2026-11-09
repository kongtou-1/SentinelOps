<script setup lang="ts">
import { computed, ref, toRaw, watch } from "vue";
import { all, api, type Models } from "@/api/client";
import {
  emptySource,
  errorText,
  type Plan,
  type DataSource,
} from "@/features/inspections/ui";
import InspectionDuration from "./InspectionDuration.vue";
import InspectionSourceFields from "./InspectionSourceFields.vue";
import {
  newRequestDraft,
  requestHeaders,
} from "@/features/inspections/request";
const open = defineModel<boolean>({ required: true });
const props = defineProps<{ plan?: Plan }>();
const emit = defineEmits<{ saved: [] }>();
type Input = Models["InspectionPlanInput"];
type Rule = Models["InspectionRule-Input"];
const defaults = (): Input => ({
  name: "",
  application_id: null,
  environment: "test",
  enabled: false,
  data_source_id: null,
  source_kind: "api",
  source_config: emptySource(),
  related_sources: [],
  rules: [],
  interval_seconds: 3600,
  schedule_kind: "interval",
  schedule_timezone: "Asia/Shanghai",
  cron_expression: "0 * * * *",
  scheduled_at: null,
  timeout_seconds: 300,
  max_retries: 0,
  lookback_seconds: 86400,
  max_records: 100,
  execution_mode: "records",
  object_mode: "dynamic",
  fixed_objects: [],
  check_items: [],
  agent_source_ids: [],
  agent_parameters: {},
  agent_enabled: false,
  max_agent_items: 20,
  skill_id: null,
});
const form = ref<Input>(defaults()),
  tab = ref("basic"),
  error = ref(""),
  busy = ref(false),
  ready = ref(false),
  activeRule = ref("0");
const sources = ref<DataSource[]>([]),
  services = ref<Models["ApplicationResponse"][]>([]),
  skills = ref<Models["SkillResponse"][]>([]),
  databases = ref<Models["DatabaseResponse"][]>([]);
const auth = ref(newRequestDraft());
const parameters = ref("{}");
const autonomous = computed(() => form.value.execution_mode === "agent");
watch(autonomous, (value) => {
  if (value) form.value.agent_enabled = true;
});
const available = computed(() =>
  sources.value
    .filter(
      (s) =>
        (s.enabled || s.id === form.value.data_source_id) &&
        s.environment === form.value.environment &&
        (!s.application_id || s.application_id === form.value.application_id),
    )
    .filter(
      (s) =>
        !autonomous.value ||
        (s.source_kind === "api" && s.source_config.agent_api_enabled),
    ),
);
watch(open, async (value) => {
  if (!value) return;
  tab.value = "basic";
  error.value = "";
  ready.value = false;
  const plan = props.plan;
  form.value = plan
    ? ({
        ...defaults(),
        ...Object.fromEntries(
          Object.keys(defaults()).map((k) => [k, plan[k as keyof Plan]]),
        ),
        expected_version: plan.version,
        source_config: structuredClone(toRaw(plan.source_config)),
        rules: structuredClone(toRaw(plan.rules)),
        related_sources: structuredClone(toRaw(plan.related_sources)).map(
          ({ headers_configured, ...source }) => source,
        ),
      } as Input)
    : defaults();
  auth.value = newRequestDraft(
    Boolean(plan?.headers_configured),
    plan?.source_config.url,
  );
  parameters.value = JSON.stringify(form.value.agent_parameters || {}, null, 2);
  try {
    [sources.value, services.value, skills.value, databases.value] =
      await Promise.all([
        all<DataSource>("/inspection-data-sources"),
        all<Models["ApplicationResponse"]>("/business-services"),
        all<Models["SkillResponse"]>("/skills"),
        api<Models["DatabaseResponse"][]>("/inspection-connections"),
      ]);
    ready.value = true;
  } catch (e) {
    error.value = errorText(e);
  }
});
function addRule() {
  const rules = form.value.rules!;
  let n = rules.length + 1;
  while (rules.some((r) => r.code === `check_${n}`)) n++;
  rules.push({
    code: `check_${n}`,
    title: "",
    kind: "required",
    path: "",
    basis: "",
    expected: null,
    minimum: null,
    maximum: null,
    compare_path: "",
    absolute: false,
    unit: "",
    unit_path: "",
  });
  activeRule.value = String(rules.length - 1);
}
function addRelated() {
  form.value.related_sources!.push({
    name: `related_${form.value.related_sources!.length + 1}`,
    data_source_id: null,
    source_kind: "api",
    source_config: emptySource(),
    main_field: "",
    match_field: "",
    cardinality: "one",
    required: true,
    max_records: 20,
  });
}
function expectedType(rule: Rule, value: string) {
  rule.expected = value === "number" ? 0 : value === "boolean" ? true : "";
}
async function save() {
  busy.value = true;
  error.value = "";
  try {
    if (!form.value.name.trim()) {
      tab.value = "basic";
      throw new Error("请填写计划名称");
    }
    if (!form.value.data_source_id && !props.plan) {
      tab.value = "checks";
      throw new Error("请选择数据源");
    }
    if (!form.value.rules?.length && !form.value.agent_enabled) {
      tab.value = "checks";
      throw new Error("请添加规则或启用 Agent Skill");
    }
    if (
      form.value.data_source_id &&
      !available.value.some((s) => s.id === form.value.data_source_id)
    ) {
      tab.value = "checks";
      throw new Error("请选择与当前服务和环境匹配的数据源");
    }
    for (const [index, rule] of (autonomous.value
      ? []
      : form.value.rules!
    ).entries()) {
      if (!rule.title.trim() || !rule.path.trim() || !rule.basis.trim()) {
        tab.value = "checks";
        activeRule.value = String(index);
        throw new Error(`请补全第 ${index + 1} 项检查的名称、字段和判断依据`);
      }
    }
    const body = {
      ...form.value,
      agent_parameters: autonomous.value
        ? JSON.parse(parameters.value || "{}")
        : {},
      agent_source_ids: autonomous.value ? form.value.agent_source_ids : [],
      related_sources: autonomous.value ? [] : form.value.related_sources,
      rules: form.value.rules!.map((rule) => ({
        ...rule,
        minimum: rule.minimum === "" ? null : rule.minimum,
        maximum: rule.maximum === "" ? null : rule.maximum,
      })),
      headers: form.value.data_source_id
        ? undefined
        : requestHeaders(auth.value),
    };
    if (autonomous.value) {
      body.agent_enabled = true;
      body.rules = [];
    }
    await api(
      props.plan ? `/inspection-plans/${props.plan.id}` : "/inspection-plans",
      props.plan ? "PUT" : "POST",
      body,
    );
    emit("saved");
    open.value = false;
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <el-drawer
    v-model="open"
    :title="plan ? '编辑巡检计划' : '创建巡检计划'"
    size="min(860px, 100vw)"
    class="inspection-drawer"
    destroy-on-close
    :close-on-click-modal="false"
  >
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-form v-if="ready" label-position="top" @submit.prevent="save">
      <el-tabs v-model="tab">
        <el-tab-pane label="基本信息" name="basic">
          <el-form-item label="计划名称" required
            ><el-input v-model="form.name" maxlength="100"
          /></el-form-item>
          <div class="inspection-grid">
            <el-form-item label="关联服务"
              ><el-select
                v-model="form.application_id"
                clearable
                filterable
                placeholder="独立巡检"
                ><el-option
                  v-for="s in services"
                  :key="s.id"
                  :value="s.id"
                  :label="s.name" /></el-select></el-form-item
            ><el-form-item label="环境" required
              ><el-input v-model="form.environment"
            /></el-form-item>
          </div>
          <el-form-item label="启用计划"
            ><el-switch v-model="form.enabled"
          /></el-form-item>
        </el-tab-pane>
        <el-tab-pane label="数据与检查项" name="checks">
          <el-form-item label="巡检模式" required
            ><el-select v-model="form.execution_mode"
              ><el-option label="采集后逐条检查" value="records" /><el-option
                label="Agent Skill 自主巡检"
                value="agent" /></el-select
          ></el-form-item>
          <el-form-item label="数据源" required
            ><el-select
              v-model="form.data_source_id"
              filterable
              placeholder="选择已有数据源"
              ><el-option
                v-for="s in available"
                :key="s.id"
                :value="s.id"
                :label="s.name" /></el-select
          ></el-form-item>
          <template v-if="autonomous">
            <el-form-item label="其他授权 API"
              ><el-select v-model="form.agent_source_ids" multiple filterable
                ><el-option
                  v-for="s in available.filter(
                    (s) => s.id !== form.data_source_id,
                  )"
                  :key="s.id"
                  :value="s.id"
                  :label="s.name" /></el-select
            ></el-form-item>
            <el-form-item label="业务参数（JSON）"
              ><el-input v-model="parameters" type="textarea" :rows="6"
            /></el-form-item>
          </template>
          <InspectionSourceFields
            v-if="!form.data_source_id && plan"
            v-model:source="form.source_config!"
            v-model:request="auth"
            :kind="form.source_kind!"
            :databases="databases"
            :headers-configured="plan.headers_configured"
          />
          <template v-if="!autonomous">
            <div class="inspection-heading">
              <h3>规则检查</h3>
              <el-button :disabled="form.rules!.length >= 30" @click="addRule"
                >添加检查项</el-button
              >
            </div>
            <el-empty
              v-if="!form.rules?.length"
              description="尚未配置规则"
              :image-size="60"
            />
            <el-collapse v-model="activeRule" accordion>
              <el-collapse-item
                v-for="(rule, index) in form.rules"
                :key="rule.code"
                :name="String(index)"
                :title="`${index + 1}. ${rule.title || '未命名检查项'}`"
              >
                <el-form-item label="检查项名称" required
                  ><el-input v-model="rule.title"
                /></el-form-item>
                <div class="inspection-grid">
                  <el-form-item label="检查字段" required
                    ><el-input
                      v-model="rule.path"
                      placeholder="如 status、metrics.latency" /></el-form-item
                  ><el-form-item label="检查条件" required
                    ><el-select v-model="rule.kind"
                      ><el-option label="必须有值" value="required" /><el-option
                        label="等于指定值"
                        value="equals" /><el-option
                        label="数值范围"
                        value="numeric_range" /><el-option
                        label="与另一字段相等"
                        value="field_equals" /></el-select
                  ></el-form-item>
                </div>
                <div v-if="rule.kind === 'equals'" class="inspection-grid">
                  <el-form-item label="值类型"
                    ><el-select
                      :model-value="typeof rule.expected"
                      @change="expectedType(rule, $event)"
                      ><el-option label="文本" value="string" /><el-option
                        label="数字"
                        value="number" /><el-option
                        label="是 / 否"
                        value="boolean" /></el-select></el-form-item
                  ><el-form-item label="预期值" required
                    ><el-switch
                      v-if="typeof rule.expected === 'boolean'"
                      v-model="rule.expected" /><el-input-number
                      v-else-if="typeof rule.expected === 'number'"
                      v-model="rule.expected"
                      :controls="false" /><el-input
                      v-else
                      :model-value="String(rule.expected ?? '')"
                      @update:model-value="rule.expected = $event"
                  /></el-form-item>
                </div>
                <el-form-item
                  v-if="rule.kind === 'field_equals'"
                  label="对比字段"
                  required
                  ><el-input v-model="rule.compare_path"
                /></el-form-item>
                <template v-if="rule.kind === 'numeric_range'"
                  ><div class="inspection-grid">
                    <el-form-item label="下限（含）"
                      ><el-input
                        v-model="rule.minimum"
                        placeholder="不限" /></el-form-item
                    ><el-form-item label="上限（含）"
                      ><el-input v-model="rule.maximum" placeholder="不限"
                    /></el-form-item>
                  </div>
                  <el-form-item label="取绝对值"
                    ><el-switch v-model="rule.absolute"
                  /></el-form-item>
                  <div class="inspection-grid">
                    <el-form-item label="单位"
                      ><el-input
                        v-model="rule.unit"
                        placeholder="可选，如 ms" /></el-form-item
                    ><el-form-item v-if="rule.unit" label="单位字段" required
                      ><el-input v-model="rule.unit_path"
                    /></el-form-item></div
                ></template>
                <el-form-item label="判断依据" required
                  ><el-input
                    v-model="rule.basis"
                    type="textarea"
                    :rows="2"
                    placeholder="填写业务标准或阈值依据"
                /></el-form-item>
                <el-button
                  type="danger"
                  link
                  @click="form.rules!.splice(index, 1)"
                  >移除检查项</el-button
                >
              </el-collapse-item>
            </el-collapse>
          </template>
          <div class="config-card" style="margin-top: 24px">
            <el-form-item label="Agent Skill 智能检查"
              ><el-switch
                v-model="form.agent_enabled"
                :disabled="autonomous" /></el-form-item
            ><template v-if="form.agent_enabled"
              ><el-form-item label="绑定 Skill" required
                ><el-select v-model="form.skill_id" filterable
                  ><el-option
                    v-for="s in skills.filter((s) => s.enabled)"
                    :key="s.id"
                    :label="s.title"
                    :value="s.id" /></el-select></el-form-item
              ><el-form-item v-if="!autonomous" label="每次最多分析对象数"
                ><el-input-number
                  v-model="form.max_agent_items"
                  :min="1"
                  :max="200" /></el-form-item
            ></template>
          </div>
          <el-collapse v-if="!autonomous"
            ><el-collapse-item title="关联数据源" name="related"
              ><div
                v-for="(related, index) in form.related_sources"
                :key="index"
                class="config-card"
              >
                <el-form-item label="关联数据源" required
                  ><el-select v-model="related.data_source_id" filterable
                    ><el-option
                      v-for="s in available"
                      :key="s.id"
                      :label="s.name"
                      :value="s.id" /></el-select
                ></el-form-item>
                <div class="inspection-grid">
                  <el-form-item label="数据别名" required
                    ><el-input v-model="related.name" /></el-form-item
                  ><el-form-item label="主对象关联字段"
                    ><el-input
                      v-model="related.main_field"
                      placeholder="留空使用对象编号" /></el-form-item
                  ><el-form-item label="关联源匹配字段" required
                    ><el-input v-model="related.match_field" /></el-form-item
                  ><el-form-item label="关联数量"
                    ><el-select v-model="related.cardinality"
                      ><el-option label="单条" value="one" /><el-option
                        label="多条"
                        value="many" /></el-select
                  ></el-form-item>
                </div>
                <el-form-item label="必须存在关联数据"
                  ><el-switch v-model="related.required" /></el-form-item
                ><el-button
                  link
                  type="danger"
                  @click="form.related_sources!.splice(index, 1)"
                  >移除</el-button
                >
              </div>
              <el-button
                :disabled="form.related_sources!.length >= 4"
                @click="addRelated"
                >添加关联源</el-button
              ></el-collapse-item
            ></el-collapse
          >
        </el-tab-pane>
        <el-tab-pane label="调度与执行" name="schedule">
          <el-form-item label="执行方式" required
            ><el-select v-model="form.schedule_kind"
              ><el-option label="周期 / 间隔" value="interval" /><el-option
                label="Cron"
                value="cron" /><el-option
                label="固定时间（单次）"
                value="once" /><el-option
                label="仅手动执行"
                value="manual" /><el-option
                label="事件触发"
                value="event" /></el-select
          ></el-form-item>
          <el-form-item
            v-if="form.schedule_kind === 'interval'"
            label="执行间隔"
            required
            ><InspectionDuration
              v-model="form.interval_seconds!"
              :max="604800"
              :presets="[
                { label: '每 5 分钟', value: 300 },
                { label: '每小时', value: 3600 },
                { label: '每天', value: 86400 },
              ]"
          /></el-form-item>
          <template v-if="form.schedule_kind === 'cron'"
            ><el-form-item label="Cron（分 时 日 月 周）" required
              ><el-input
                v-model="form.cron_expression"
                placeholder="0 9 * * 1-5" /></el-form-item
            ><el-form-item label="时区" required
              ><el-select
                v-model="form.schedule_timezone"
                filterable
                allow-create
                ><el-option
                  label="Asia/Shanghai"
                  value="Asia/Shanghai" /><el-option
                  label="UTC"
                  value="UTC" /></el-select></el-form-item
          ></template>
          <el-form-item
            v-if="form.schedule_kind === 'once'"
            label="执行时间"
            required
            ><el-date-picker
              v-model="form.scheduled_at"
              type="datetime"
              value-format="YYYY-MM-DDTHH:mm:ssZ"
          /></el-form-item>
          <div class="inspection-grid">
            <el-form-item label="执行超时（秒）"
              ><el-input-number
                v-model="form.timeout_seconds"
                :min="10"
                :max="3600" /></el-form-item
            ><el-form-item label="数据获取失败重试次数"
              ><el-input-number
                v-model="form.max_retries"
                :min="0"
                :max="3" /></el-form-item
            ><el-form-item label="每批最多检查对象数"
              ><el-input-number
                v-model="form.max_records"
                :min="1"
                :max="autonomous ? 200 : 10000"
            /></el-form-item>
          </div>
          <el-form-item label="数据扫描时间范围" v-if="!autonomous"
            ><InspectionDuration
              v-model="form.lookback_seconds!"
              :max="2592000"
              :presets="[
                { label: '最近 1 小时', value: 3600 },
                { label: '最近 1 天', value: 86400 },
                { label: '最近 7 天', value: 604800 },
              ]"
          /></el-form-item>
        </el-tab-pane>
      </el-tabs>
    </el-form>
    <template #footer
      ><el-button @click="open = false">取消</el-button
      ><el-button
        v-if="tab !== 'basic'"
        @click="tab = tab === 'schedule' ? 'checks' : 'basic'"
        >上一步</el-button
      ><el-button
        v-if="tab !== 'schedule'"
        type="primary"
        @click="tab = tab === 'basic' ? 'checks' : 'schedule'"
        >下一步</el-button
      ><el-button
        v-else
        type="primary"
        :loading="busy"
        :disabled="!ready"
        @click="save"
        >保存计划</el-button
      ></template
    >
  </el-drawer>
</template>
