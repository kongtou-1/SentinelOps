<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { all, api } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import type { Row } from "@/types/records";
const props = defineProps<{ applicationId: string }>();
const auth = useAuth();
const data = ref<Row>(),
  policy = ref<Row>({ recovery_seconds: 180, version: 0 });
const people = ref<Row[]>([]);
const error = ref(""),
  editing = ref(false),
  busy = ref(false);
const states: Record<string, string> = {
  failed: "已观测异常",
  healthy: "当前正常",
  unknown: "观测未知",
  stale: "采集过期",
  disabled: "已暂停",
};
async function load() {
  const selected = props.applicationId;
  try {
    const values = await Promise.all([
      api<Row>(`/business-services/${selected}/impact`),
      api<Row>(`/business-services/${selected}/operations-policy`),
    ]);
    if (selected !== props.applicationId) return;
    [data.value, policy.value] = values;
    people.value = await all<Row>(
      auth.can("users:manage") ? "/users" : "/incidents/assignees",
    );
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () => (editing.value ? null : 30000));
watch(
  () => props.applicationId,
  () => {
    data.value = undefined;
    editing.value = false;
    void refresh();
  },
);
const draft = ref<Row>({});
const relatedServices = computed(() => [
  ...(data.value?.upstream || []),
  ...(data.value?.downstream || []),
  ...(data.value?.shared || []),
]);
const availablePeople = computed(() =>
  people.value.filter((person) => person.is_active !== false),
);
const handlers = computed(() =>
  availablePeople.value.filter((person) => person.role !== "viewer"),
);
function personLabel(id: string | null, fallback = "管理员（默认）") {
  if (!id) return fallback;
  const person = people.value.find((item) => item.id === id);
  if (!person) return "账号不可用";
  return (
    person.username +
    (person.notification_email
      ? " · " + person.notification_email
      : person.notification_email === ""
        ? " · 未设收件邮箱"
        : "")
  );
}
function duration(seconds: number) {
  return seconds % 60 === 0 ? `${seconds / 60} 分钟` : `${seconds} 秒`;
}
function openSettings() {
  draft.value = {
    ...policy.value,
    response: {
      ...policy.value.response,
      notify_user_ids: [...(policy.value.response?.notify_user_ids || [])],
    },
  };
  editing.value = true;
}
async function save() {
  busy.value = true;
  const selected = props.applicationId;
  try {
    const result = await api<Row>(
      `/business-services/${selected}/operations-policy`,
      "PUT",
      {
        recovery_seconds: draft.value.recovery_seconds,
        expected_version: draft.value.version,
        response: draft.value.response,
      },
    );
    if (selected !== props.applicationId) return;
    policy.value = result;
    editing.value = false;
    ElMessage.success("服务响应设置已保存");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    busy.value = false;
  }
}
defineExpose({ refresh });
</script>
<template>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
    class="policy-error"
  />
  <section class="panel response-policy">
    <div class="section-heading">
      <h2>响应与恢复</h2>
      <el-button
        v-if="auth.can('resources:manage')"
        :disabled="!policy.response"
        @click="openSettings"
        >编辑设置</el-button
      >
    </div>
    <div v-if="policy.response" class="policy-summary">
      <div class="recipients">
        <h3>通知接收人</h3>
        <dl>
          <div>
            <dt>主要负责人</dt>
            <dd>{{ personLabel(policy.response.primary_id) }}</dd>
          </div>
          <div>
            <dt>备用负责人</dt>
            <dd>{{ personLabel(policy.response.backup_id) }}</dd>
          </div>
          <div>
            <dt>通知关注人</dt>
            <dd>
              {{
                policy.response.notify_user_ids
                  .map((id: string) => personLabel(id))
                  .join("、") || "未配置"
              }}
            </dd>
          </div>
        </dl>
      </div>
      <div class="timing">
        <h3>响应时限</h3>
        <div class="timing-grid">
          <div>
            <span>严重告警接单</span
            ><strong>{{ duration(policy.response.critical_seconds) }}</strong>
          </div>
          <div>
            <span>警告告警接单</span
            ><strong>{{ duration(policy.response.warning_seconds) }}</strong>
          </div>
          <div>
            <span>恢复确认时长</span
            ><strong>{{ duration(policy.recovery_seconds) }}</strong>
          </div>
        </div>
      </div>
    </div>
  </section>
  <slot />
  <section class="panel dependencies">
    <div class="section-heading">
      <h2>关联与依赖</h2>
      <el-tag v-if="data?.truncated" type="warning">结果已截断</el-tag>
    </div>
    <el-tabs>
      <el-tab-pane :label="`服务依赖 ${relatedServices.length}`">
        <el-table :data="relatedServices" empty-text="暂无关联服务">
          <el-table-column label="关联服务" min-width="200"
            ><template #default="{ row }"
              ><router-link :to="`/applications/${row.id}?view=governance`">{{
                row.name
              }}</router-link></template
            ></el-table-column
          >
          <el-table-column label="关系" min-width="160"
            ><template #default="{ row }">{{
              row.relation === "upstream"
                ? "依赖的上游"
                : row.relation === "shared_llm"
                  ? "共用模型接口"
                  : "依赖本服务的下游"
            }}</template></el-table-column
          >
          <el-table-column prop="depth" label="层级" width="80" />
          <el-table-column label="当前状态" min-width="140"
            ><template #default="{ row }"
              ><el-tag
                :type="row.observed_status === 'failed' ? 'danger' : 'info'"
                >{{ states[row.observed_status] || "观测未知" }}</el-tag
              ></template
            ></el-table-column
          >
          <el-table-column label="影响判断" min-width="140"
            ><template #default="{ row }">{{
              row.impact === "observed_failure" ? "已观测故障" : "潜在关联"
            }}</template></el-table-column
          >
        </el-table>
      </el-tab-pane>
      <el-tab-pane :label="`容器与模型 ${data?.resources?.length || 0}`">
        <el-table
          :data="data?.resources || []"
          empty-text="暂无关联容器或模型接口"
        >
          <el-table-column label="资源" min-width="200"
            ><template #default="{ row }"
              ><router-link
                :to="
                  (row.kind === 'llm' ? '/llm-endpoints/' : '/containers/') +
                  row.id
                "
                >{{ row.name }}</router-link
              ></template
            ></el-table-column
          >
          <el-table-column label="类型" min-width="140"
            ><template #default="{ row }">{{
              row.kind === "llm" ? "模型接口" : "容器"
            }}</template></el-table-column
          >
          <el-table-column label="当前状态" min-width="140"
            ><template #default="{ row }">{{
              states[row.observed_status] || "无新鲜观测"
            }}</template></el-table-column
          >
          <el-table-column label="观测时间" min-width="180"
            ><template #default="{ row }">{{
              date(row.observed_at)
            }}</template></el-table-column
          >
        </el-table>
      </el-tab-pane>
    </el-tabs>
  </section>
  <el-dialog
    v-model="editing"
    title="服务响应设置"
    width="min(760px, 94vw)"
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    :show-close="!busy"
  >
    <el-form
      v-if="draft.response"
      label-position="top"
      :disabled="busy"
      @submit.prevent="save"
    >
      <h3>通知接收人</h3>
      <div class="settings-grid">
        <el-form-item label="主要负责人"
          ><el-select
            v-model="draft.response.primary_id"
            clearable
            filterable
            placeholder="默认由管理员接收"
            @clear="draft.response.primary_id = null"
            ><el-option
              v-for="person in handlers"
              :key="person.id"
              :value="person.id"
              :label="personLabel(person.id)" /></el-select
        ></el-form-item>
        <el-form-item label="备用负责人"
          ><el-select
            v-model="draft.response.backup_id"
            clearable
            filterable
            placeholder="默认由管理员接收"
            @clear="draft.response.backup_id = null"
            ><el-option
              v-for="person in handlers"
              :key="person.id"
              :value="person.id"
              :label="personLabel(person.id)" /></el-select
        ></el-form-item>
        <el-form-item label="通知关注人" class="full-width"
          ><el-select
            v-model="draft.response.notify_user_ids"
            multiple
            filterable
            clearable
            placeholder="选择同时接收通知的账号"
            ><el-option
              v-for="person in availablePeople"
              :key="person.id"
              :value="person.id"
              :label="personLabel(person.id)" /></el-select
        ></el-form-item>
      </div>
      <h3 class="timing-title">接单与恢复</h3>
      <div class="settings-grid timing-fields">
        <el-form-item label="严重告警接单时限" required
          ><div class="number-field">
            <el-input-number
              v-model="draft.response.critical_seconds"
              :min="60"
              :max="86400"
              controls-position="right"
            /><span>秒</span>
          </div></el-form-item
        >
        <el-form-item label="警告告警接单时限" required
          ><div class="number-field">
            <el-input-number
              v-model="draft.response.warning_seconds"
              :min="60"
              :max="86400"
              controls-position="right"
            /><span>秒</span>
          </div></el-form-item
        >
        <el-form-item label="恢复确认时长" required
          ><div class="number-field">
            <el-input-number
              v-model="draft.recovery_seconds"
              :min="30"
              :max="1800"
              controls-position="right"
            /><span>秒</span>
          </div></el-form-item
        >
      </div>
    </el-form>
    <template #footer
      ><el-button :disabled="busy" @click="editing = false">取消</el-button
      ><el-button type="primary" :loading="busy" @click="save"
        >保存设置</el-button
      ></template
    >
  </el-dialog>
</template>
<style scoped>
.policy-error {
  margin-bottom: 16px;
}
.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
  min-height: 36px;
}
.section-heading h2 {
  margin: 0;
}
.policy-summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr);
  gap: 32px;
}
h3 {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-regular);
  margin: 0 0 16px;
}
.recipients dl {
  margin: 0;
  display: grid;
  gap: 14px;
}
.recipients dl > div {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 12px;
  align-items: baseline;
}
dt {
  color: var(--muted);
}
dd {
  margin: 0;
  overflow-wrap: anywhere;
  line-height: 1.5;
}
.timing-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.timing-grid > div {
  background: var(--surface-soft);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px 16px;
}
.timing-grid span {
  display: block;
  color: var(--text-regular);
  white-space: nowrap;
  margin-bottom: 16px;
}
.timing-grid strong {
  display: block;
  font-size: 23px;
  font-weight: 600;
  white-space: nowrap;
}
.settings-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 20px;
}
.settings-grid .el-select {
  width: 100%;
}
.full-width {
  grid-column: 1 / -1;
}
.timing-title {
  border-top: 1px solid var(--border);
  padding-top: 20px;
  margin-top: 4px;
}
.timing-fields {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.number-field {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}
.number-field .el-input-number {
  flex: 1;
  min-width: 0;
}
@media (max-width: 1100px) {
  .policy-summary {
    grid-template-columns: 1fr;
    gap: 24px;
  }
}
@media (max-width: 600px) {
  .settings-grid {
    grid-template-columns: 1fr;
  }
  .timing-grid {
    gap: 8px;
  }
  .timing-grid > div {
    padding: 14px 10px;
  }
  .timing-grid strong {
    font-size: 19px;
  }
  .timing-grid span {
    white-space: normal;
  }
}
</style>
