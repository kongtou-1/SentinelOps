<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, onMounted, ref } from "vue";
import "@/features/inspections/styles.css";
import { ElMessage } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import { all, api, type Models } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { date } from "@/utils/display";
import {
  taskDefaults,
  taskFromPlan,
  type TaskInput,
} from "@/features/inspections/task";
import {
  errorText,
  type Plan,
  type DataSource,
  type Run,
} from "@/features/inspections/ui";
import CheckEditor from "@/features/inspections/components/CheckEditor.vue";
import SourceEditor from "@/features/inspections/components/SourceEditor.vue";
import PlanEditor from "@/features/inspections/components/PlanEditor.vue";
import TrialReport from "@/features/inspections/components/TrialReport.vue";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
const route = useRoute(),
  router = useRouter(),
  auth = useAuth();
const form = ref<TaskInput>(taskDefaults()),
  plan = ref<Plan>(),
  step = ref(0),
  busy = ref(""),
  ready = ref(false),
  error = ref("");
const services = ref<Models["ApplicationResponse"][]>([]),
  sources = ref<DataSource[]>([]),
  skills = ref<Models["SkillResponse"][]>([]);
const checks = ref<InstanceType<typeof CheckEditor>>(),
  sourceEditor = ref(false),
  sourceKind = ref<"api" | "database">("api"),
  legacyEditor = ref(false);
const objects = ref([{ id: "object-1", facts: "{}" }]);
const preview = ref<Models["InspectionQueryPreviewResponse"]>(),
  times = ref<string[]>([]),
  trialId = ref("");
const available = computed(() =>
  sources.value.filter(
    (s) =>
      s.enabled &&
      s.environment === form.value.environment &&
      (!s.application_id || s.application_id === form.value.application_id),
  ),
);
const discovery = computed(() =>
  available.value.filter(
    (s) =>
      s.source_kind === "database" ||
      (s.source_config.request_method === "GET" &&
        !s.source_config.path_template?.includes("{")),
  ),
);
const selected = computed(() =>
  sources.value.find((s) => s.id === form.value.data_source_id),
);
const availableSkills = computed(() =>
  skills.value.filter(
    (s) =>
      !s.application_ids?.length ||
      (form.value.application_id &&
        s.application_ids.includes(form.value.application_id)),
  ),
);
onMounted(async () => {
  try {
    [services.value, sources.value, skills.value] = await Promise.all([
      all<Models["ApplicationResponse"]>("/business-services"),
      all<DataSource>("/inspection-data-sources"),
      all<Models["SkillResponse"]>("/skills"),
    ]);
    if (route.params.planId) {
      plan.value = await api<Plan>(`/inspection-plans/${route.params.planId}`);
      form.value = taskFromPlan(plan.value);
      objects.value = (plan.value.fixed_objects || []).map((o) => ({
        id: o.id,
        facts: JSON.stringify(o.facts, null, 2),
      }));
    } else if (route.query.application_id)
      form.value.application_id = String(route.query.application_id);
    ready.value = true;
  } catch (e) {
    error.value = errorText(e);
  }
});
function body(): TaskInput {
  if (!form.value.name.trim() || !form.value.environment.trim()) {
    step.value = 0;
    throw new Error("请填写任务名称和环境");
  }
  if (
    form.value.object_mode === "dynamic" &&
    !discovery.value.some((s) => s.id === form.value.data_source_id)
  ) {
    step.value = 0;
    throw new Error("请选择当前服务和环境下可用的 API 或数据库查询");
  }
  let fixed: NonNullable<TaskInput["fixed_objects"]> = [];
  if (form.value.object_mode === "fixed") {
    step.value = 0;
    fixed = objects.value.map((object, index) => {
      if (!object.id.trim())
        throw new Error(`请填写第 ${index + 1} 个对象编号`);
      let facts;
      try {
        facts = JSON.parse(object.facts);
      } catch {
        throw new Error(`对象“${object.id}”的输入数据需要有效的 JSON`);
      }
      if (!facts || typeof facts !== "object" || Array.isArray(facts))
        throw new Error(`对象“${object.id}”的输入数据需要 JSON 对象`);
      return { id: object.id.trim(), facts };
    });
    if (!fixed.length || new Set(fixed.map((o) => o.id)).size !== fixed.length)
      throw new Error("请添加对象并确保编号不重复");
  }
  step.value = 1;
  checks.value?.validate();
  return {
    ...form.value,
    data_source_id:
      form.value.object_mode === "fixed" ? null : form.value.data_source_id,
    fixed_objects: fixed,
    check_items: form.value.check_items!.map((check) => ({
      ...check,
      conditions: check.conditions?.map((condition) => ({
        ...condition,
        minimum: condition.minimum === "" ? null : condition.minimum,
        maximum: condition.maximum === "" ? null : condition.maximum,
      })),
    })),
  };
}
async function save() {
  if (busy.value) return;
  const previousStep = step.value;
  busy.value = "save";
  error.value = "";
  try {
    const values = body();
    step.value = previousStep;
    const saved = await api<Plan>(
      plan.value ? `/inspection-plans/${plan.value.id}` : "/inspection-plans",
      plan.value ? "PUT" : "POST",
      values,
    );
    ElMessage.success("任务已保存");
    await router.push(`/inspections/plans/${saved.id}`);
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = "";
  }
}
async function trial() {
  if (busy.value) return;
  const previousStep = step.value;
  busy.value = "trial";
  error.value = "";
  try {
    const values = body();
    step.value = previousStep;
    const run = await api<Run>("/inspection-trials", "POST", {
      plan: values,
      sample_size: 3,
    });
    trialId.value = run.id;
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = "";
  }
}
async function previewQuery() {
  busy.value = "preview";
  error.value = "";
  preview.value = undefined;
  try {
    preview.value = await api(
      `/inspection-data-sources/${form.value.data_source_id}/preview`,
      "POST",
      { limit: 3 },
    );
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = "";
  }
}
async function schedulePreview() {
  busy.value = "schedule";
  error.value = "";
  times.value = [];
  try {
    const result = await api<Models["InspectionScheduleResponse"]>(
      "/inspection-schedule-preview",
      "POST",
      {
        schedule_kind: form.value.schedule_kind,
        interval_seconds: form.value.interval_seconds,
        cron_expression: form.value.cron_expression,
        schedule_timezone: form.value.schedule_timezone,
        scheduled_at: form.value.scheduled_at,
      },
    );
    times.value = result.times;
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = "";
  }
}
async function sourceSaved(source: DataSource) {
  sources.value = await all<DataSource>("/inspection-data-sources");
  if (available.value.some((s) => s.id === source.id))
    form.value.data_source_id = source.id;
}
</script>
<template>
  <section class="inspection-page task-editor">
    <header class="page-header">
      <div class="page-heading">
        <div>
          <el-button class="subtle-link" link @click="router.back()"
            >← 返回</el-button
          >
          <div class="heading-copy">
            <h1>{{ plan ? "编辑巡检任务" : "创建巡检任务" }}</h1>
            <PageIntro />
          </div>
        </div>
      </div>
    </header>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="ready && plan && plan.execution_mode !== 'checks'">
      <el-alert
        title="旧版任务沿用原有配置与判定方式"
        type="info"
        :closable="false"
      />
      <el-button @click="legacyEditor = true">编辑旧版任务</el-button>
      <PlanEditor
        v-model="legacyEditor"
        :plan="plan"
        @saved="router.push(`/inspections/plans/${plan.id}`)"
      />
    </template>
    <el-form
      class="inspection-wizard"
      v-else-if="ready"
      label-position="top"
      :disabled="!!busy"
      @submit.prevent="save"
    >
      <aside class="wizard-navigation" aria-label="创建步骤">
        <div class="inspection-count">创建步骤</div>
        <button
          v-for="(title, index) in ['对象与数据', '检查内容', '执行安排']"
          :key="title"
          type="button"
          :class="{ selected: step === index, complete: step > index }"
          :aria-current="step === index ? 'step' : undefined"
          @click="step = index"
        >
          <span>{{ index + 1 }}</span
          >{{ title }}
        </button>
      </aside>
      <section v-show="step === 0" class="inspection-panel step-content">
        <h3>基本信息</h3>
        <div class="inspection-grid">
          <el-form-item label="任务名称" required
            ><el-input
              v-model="form.name"
              maxlength="100"
              placeholder="如：项目材料夜间巡检" /></el-form-item
          ><el-form-item label="环境" required
            ><el-input
              v-model="form.environment"
              maxlength="50"
              @change="preview = undefined"
          /></el-form-item>
          <el-form-item label="关联服务"
            ><el-select
              v-model="form.application_id"
              clearable
              filterable
              placeholder="独立巡检"
              @change="preview = undefined"
              ><el-option
                v-for="service in services"
                :key="service.id"
                :value="service.id"
                :label="service.name" /></el-select
          ></el-form-item>
          <el-form-item label="检查对象"
            ><el-radio-group v-model="form.object_mode"
              ><el-radio-button value="dynamic"
                >从API或数据库获取</el-radio-button
              ><el-radio-button value="fixed"
                >固定对象</el-radio-button
              ></el-radio-group
            ></el-form-item
          >
        </div>
        <template v-if="form.object_mode === 'dynamic'">
          <el-form-item label="从哪里获取检查对象" required
            ><div class="source-picker">
              <el-select
                v-model="form.data_source_id"
                filterable
                placeholder="选择API或数据库查询"
                @change="preview = undefined"
                ><el-option
                  v-for="source in discovery"
                  :key="source.id"
                  :value="source.id"
                  :label="`${source.name} · ${source.source_kind === 'api' ? 'API' : '数据库查询'}`" /></el-select
              ><el-button
                @click="
                  sourceKind = 'api';
                  sourceEditor = true;
                "
                >新增API</el-button
              ><el-button
                @click="
                  sourceKind = 'database';
                  sourceEditor = true;
                "
                >新增数据库查询</el-button
              ><el-button
                :disabled="!form.data_source_id"
                :loading="busy === 'preview'"
                @click="previewQuery"
                >预览 3 个对象</el-button
              >
            </div></el-form-item
          >
          <el-descriptions v-if="selected" :column="2" border
            ><el-descriptions-item label="编号字段">{{
              selected.source_kind === "database"
                ? selected.source_config.id_column
                : selected.source_config.object_id_path
            }}</el-descriptions-item
            ><el-descriptions-item label="分页方式">{{
              {
                none: "单次读取",
                cursor: "游标",
                page: "页码",
                offset: "偏移量",
              }[selected.source_config.pagination || "none"]
            }}</el-descriptions-item></el-descriptions
          >
          <section v-if="preview" class="query-preview">
            <h3>取得的数据 · {{ preview.records.length }} 个对象</h3>
            <el-table :data="preview.records"
              ><el-table-column
                prop="id"
                label="对象编号"
                min-width="150" /><el-table-column
                label="映射数据"
                min-width="300"
                ><template #default="{ row }"
                  ><JsonEvidence
                    :value="row.facts" /></template></el-table-column></el-table
            ><el-tag
              :type="
                Object.keys(preview.next_cursor).length ? 'info' : 'success'
              "
              >{{
                Object.keys(preview.next_cursor).length
                  ? "还有后续数据"
                  : "已到查询末页"
              }}</el-tag
            >
          </section>
        </template>
        <template v-else
          ><div
            v-for="(object, index) in objects"
            :key="index"
            class="fixed-object"
          >
            <el-form-item label="对象编号" required
              ><el-input v-model="object.id" maxlength="200" /></el-form-item
            ><el-form-item label="输入数据（JSON）"
              ><el-input
                v-model="object.facts"
                type="textarea"
                :rows="4" /></el-form-item
            ><el-button link type="danger" @click="objects.splice(index, 1)"
              >移除对象</el-button
            >
          </div>
          <el-button
            :disabled="objects.length >= 1000"
            @click="objects.push({ id: '', facts: '{}' })"
            >添加对象</el-button
          ></template
        >
      </section>
      <div v-show="step === 1" class="step-content">
        <CheckEditor
          ref="checks"
          v-model="form.check_items!"
          :skills="availableSkills"
          :sources="available"
        />
      </div>
      <section v-show="step === 2" class="inspection-panel step-content">
        <el-form-item label="触发方式"
          ><el-radio-group v-model="form.schedule_kind" @change="times = []"
            ><el-radio-button value="manual">手动</el-radio-button
            ><el-radio-button value="interval">固定间隔</el-radio-button
            ><el-radio-button value="cron">Cron</el-radio-button
            ><el-radio-button value="once">指定时间</el-radio-button
            ><el-radio-button value="event"
              >鉴权 API</el-radio-button
            ></el-radio-group
          ></el-form-item
        >
        <div class="inspection-grid">
          <el-form-item
            v-if="form.schedule_kind === 'interval'"
            label="间隔（秒）"
            ><el-input-number
              v-model="form.interval_seconds"
              :min="60"
              :max="604800" /></el-form-item
          ><el-form-item
            v-if="form.schedule_kind === 'cron'"
            label="Cron 表达式"
            ><el-input
              v-model="form.cron_expression"
              placeholder="0 2 * * *" /></el-form-item
          ><el-form-item v-if="form.schedule_kind === 'once'" label="执行时间"
            ><el-date-picker
              v-model="form.scheduled_at"
              type="datetime"
              value-format="YYYY-MM-DDTHH:mm:ssZ" /></el-form-item
          ><el-form-item
            v-if="!['manual', 'event'].includes(form.schedule_kind!)"
            label="时区"
            ><el-input v-model="form.schedule_timezone"
          /></el-form-item>
        </div>
        <template v-if="!['manual', 'event'].includes(form.schedule_kind!)"
          ><el-button :loading="busy === 'schedule'" @click="schedulePreview"
            >预览下三次执行</el-button
          >
          <div class="schedule-times">
            <el-tag v-for="time in times" :key="time">{{ date(time) }}</el-tag>
          </div></template
        >
        <el-collapse class="budget"
          ><el-collapse-item title="执行预算" name="budget"
            ><div class="inspection-grid">
              <el-form-item label="每轮最多对象"
                ><el-input-number
                  v-model="form.max_records"
                  :min="1"
                  :max="10000" /></el-form-item
              ><el-form-item label="每轮最多 Agent 检查"
                ><el-input-number
                  v-model="form.max_agent_items"
                  :min="1"
                  :max="200" /></el-form-item
              ><el-form-item label="总超时（秒）"
                ><el-input-number
                  v-model="form.timeout_seconds"
                  :min="10"
                  :max="3600" /></el-form-item
              ><el-form-item label="读取失败重试"
                ><el-input-number v-model="form.max_retries" :min="0" :max="3"
              /></el-form-item></div></el-collapse-item
        ></el-collapse>
        <el-tag :type="form.enabled ? 'success' : 'info'">{{
          form.enabled ? "保存后保持启用" : "保存后保持暂停"
        }}</el-tag>
      </section>
      <footer class="editor-footer">
        <div>
          <el-button v-if="step > 0" @click="step--">上一步</el-button
          ><el-button v-if="step < 2" @click="step++">下一步</el-button>
        </div>
        <div>
          <el-button
            v-if="auth.can('inspections:run')"
            :loading="busy === 'trial'"
            @click="trial"
            >试运行 3 个对象</el-button
          ><el-button type="primary" :loading="busy === 'save'" @click="save"
            >保存任务</el-button
          >
        </div>
      </footer>
    </el-form>
    <TrialReport v-if="trialId" :key="trialId" :run-id="trialId" />
    <SourceEditor
      v-model="sourceEditor"
      :initial-kind="sourceKind"
      @saved="sourceSaved"
    />
  </section>
</template>
<style scoped>
.step-content {
  margin-top: 0;
}
.source-picker {
  display: flex;
  gap: 12px;
  width: 100%;
  flex-wrap: wrap;
}
.source-picker .el-select {
  flex: 1;
  min-width: 250px;
}
.fixed-object {
  padding: 20px;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  margin-bottom: 16px;
}
.query-preview {
  margin-top: 24px;
}
.query-preview .el-tag {
  margin-top: 12px;
}
.editor-footer {
  grid-column: 1 / -1;
  position: sticky;
  bottom: 0;
  padding: 18px 24px;
  margin-top: 24px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 12px;
  z-index: 3;
}
.budget {
  margin: 24px 0;
}
.schedule-times {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 16px;
}
.el-select {
  width: 100%;
}
</style>
