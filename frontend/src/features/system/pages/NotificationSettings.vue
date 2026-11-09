<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { confirmOperation } from "@/utils/confirmOperation";
import { computed, ref, watch } from "vue";
import { onBeforeRouteLeave, useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { all, api, request } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import type { Row } from "@/types/records";
import PlatformMailSettings from "@/features/system/components/PlatformMailSettings.vue";
import {
  operationActor,
  operationKind,
  operationName,
  operationTarget,
  windowState,
  type OperationRecord,
} from "@/features/system/notificationDisplay";

const auth = useAuth();
const route = useRoute(),
  router = useRouter();
const policy = ref<Row>({ version: 0, managed: false, email_enabled: false });
const savedPolicy = ref<Row>();
const editingPolicy = ref(false);
const loading = ref(true);
const senderSettings = ref<InstanceType<typeof PlatformMailSettings>>();
const deliveryModes = [
  { value: "legacy", label: "原始告警邮件（Alertmanager）" },
  { value: "inbox", label: "平台待办，不发邮件" },
  { value: "email", label: "平台待办 + 邮件" },
];
function modeOf(value: Row) {
  return !value.managed ? "legacy" : value.email_enabled ? "email" : "inbox";
}
const savedModeLabel = computed(() => {
  const value = savedPolicy.value;
  return value
    ? deliveryModes.find((mode) => mode.value === modeOf(value))?.label
    : "";
});
const deliveryMode = computed({
  get: () => modeOf(policy.value),
  set: (value: string) => {
    policy.value.managed = value !== "legacy";
    policy.value.email_enabled = value === "email";
  },
});
const dirty = computed(
  () => !!savedPolicy.value && modeOf(savedPolicy.value) !== deliveryMode.value,
);
onBeforeRouteLeave(async () => {
  if (!dirty.value && !senderSettings.value?.dirty) return true;
  try {
    await ElMessageBox.confirm(
      "平台邮箱或通知设置尚未保存。",
      "放弃修改并离开？",
      {
        confirmButtonText: "放弃并离开",
        cancelButtonText: "继续编辑",
        type: "warning",
      },
    );
    return true;
  } catch {
    return false;
  }
});
const windowFilter = ref("active");
const visibleWindows = computed(() =>
  windows.value.filter(
    (row) =>
      windowFilter.value === "all" ||
      windowState(
        { starts_at: row.starts_at, ends_at: row.ends_at },
        now.value,
      ) !== "已结束",
  ),
);
const windows = ref<Row[]>([]),
  inhibitions = ref<Row[]>([]);
const audit = ref<OperationRecord[]>([]);
const activeTab = computed({
  get: () => {
    const tab = String(route.query.tab || "policy");
    return [
      "maintenance",
      "inhibition",
      ...(auth.can("audit:read") ? ["audit"] : []),
    ].includes(tab)
      ? tab
      : "policy";
  },
  set: (tab: string) => {
    void router.replace({ query: { ...route.query, tab } });
  },
});
const auditPage = ref(1),
  auditTotal = ref(0),
  auditError = ref("");
const auditBusy = ref(false);
watch(activeTab, (tab) => {
  if (tab === "audit" && auth.can("audit:read")) void loadAudit();
});
const now = ref(Date.now());
const services = ref<Row[]>([]),
  servers = ref<Row[]>([]),
  rules = ref<Row[]>([]);
const error = ref(""),
  busy = ref(false);
const dialog = ref(false),
  inhibitionDialog = ref(false),
  editing = ref("");
const form = ref<Row>({}),
  inhibition = ref<Row>({ parent_rule_id: "", child_rule_id: "" });
const targets = computed(() =>
  form.value.scope_kind === "service"
    ? services.value
    : form.value.scope_kind === "server"
      ? servers.value
      : rules.value,
);
async function load() {
  try {
    const values = await Promise.all([
      api<Row>("/system/notification-policy"),
      all<Row>("/maintenance-windows"),
      all<Row>("/alert-inhibitions"),
      all<Row>("/business-services"),
      all<Row>("/servers"),
      all<Row>("/alert-rules"),
    ]);
    const keepDraft = dirty.value;
    [
      savedPolicy.value,
      windows.value,
      inhibitions.value,
      services.value,
      servers.value,
      rules.value,
    ] = values;
    if (!keepDraft) policy.value = { ...savedPolicy.value };
    now.value = Date.now();
    if (activeTab.value === "audit" && auth.can("audit:read"))
      await loadAudit();
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
}
async function loadAudit() {
  auditBusy.value = true;
  try {
    const result = await request<OperationRecord[]>(
      `/system/operations-audit-logs?scope=notifications&limit=20&offset=${(auditPage.value - 1) * 20}`,
    );
    audit.value = result.data;
    auditTotal.value = result.total ?? result.data.length;
    auditError.value = "";
  } catch (e) {
    auditError.value = e instanceof Error ? e.message : "操作记录加载失败";
  } finally {
    auditBusy.value = false;
  }
}
const { refresh } = usePoll(
  async () => {
    await load();
  },
  () =>
    dialog.value ||
    inhibitionDialog.value ||
    dirty.value ||
    senderSettings.value?.dirty
      ? null
      : 30000,
);
async function refreshPage() {
  if (dirty.value || senderSettings.value?.dirty) {
    try {
      await ElMessageBox.confirm(
        "刷新会放弃尚未保存的平台邮箱与通知设置。",
        "放弃修改并刷新？",
        {
          confirmButtonText: "放弃并刷新",
          cancelButtonText: "继续编辑",
          type: "warning",
        },
      );
    } catch {
      return;
    }
    policy.value = { ...savedPolicy.value };
  }
  await Promise.all([refresh(), senderSettings.value?.reload()]);
  editingPolicy.value = false;
}
function cancelPolicy() {
  if (savedPolicy.value) policy.value = { ...savedPolicy.value };
  editingPolicy.value = false;
}
async function savePolicy() {
  busy.value = true;
  try {
    let confirmed = false;
    if (policy.value.managed !== savedPolicy.value?.managed) {
      await ElMessageBox.confirm(
        policy.value.managed
          ? "切换到平台通知，并关闭 Alertmanager 原始告警邮件？"
          : "恢复 Alertmanager 原始告警邮件，并停止平台邮件通知？",
        "切换通知路径",
        {
          type: "warning",
          confirmButtonText: "确认切换",
          cancelButtonText: "继续编辑",
        },
      );
      confirmed = true;
    } else if (
      policy.value.email_enabled !== savedPolicy.value?.email_enabled &&
      policy.value.managed
    ) {
      if (
        !(await confirmOperation(
          policy.value.email_enabled
            ? "启用平台通知邮件？后续通知将向符合接收配置的账号发送邮件。"
            : "停用平台通知邮件？后续通知仅保留平台待办。",
          policy.value.email_enabled ? "启用通知邮件" : "停用通知邮件",
        ))
      )
        return;
    }
    policy.value = await api("/system/notification-policy", "PUT", {
      expected_version: policy.value.version,
      managed: policy.value.managed,
      email_enabled: policy.value.managed && policy.value.email_enabled,
      confirm_legacy_switch: confirmed,
    });
    savedPolicy.value = { ...policy.value };
    editingPolicy.value = false;
    ElMessage.success("已保存");
    await refresh();
  } catch (e) {
    if (e instanceof Error) ElMessage.error(e.message);
  } finally {
    busy.value = false;
  }
}
function openWindow(row?: Row, kind = "maintenance") {
  editing.value = row?.id || "";
  form.value = row
    ? { ...row, expected_version: row.version }
    : {
        scope_kind: "service",
        scope_id: "",
        kind,
        reason: "",
        starts_at: new Date().toISOString(),
        ends_at: new Date(Date.now() + 3600000).toISOString(),
        expected_version: 0,
      };
  dialog.value = true;
}
async function saveWindow() {
  if (!form.value.scope_id) {
    ElMessage.warning("请选择生效对象");
    return;
  }
  if (!form.value.reason?.trim()) {
    ElMessage.warning("请填写原因");
    return;
  }
  if (
    !form.value.starts_at ||
    !form.value.ends_at ||
    new Date(form.value.ends_at).getTime() <=
      new Date(form.value.starts_at).getTime()
  ) {
    ElMessage.warning("结束时间必须晚于开始时间");
    return;
  }
  if (new Date(form.value.ends_at).getTime() <= Date.now()) {
    ElMessage.warning("结束时间必须晚于当前时间");
    return;
  }
  busy.value = true;
  try {
    const keys = [
      "scope_kind",
      "scope_id",
      "kind",
      "reason",
      "starts_at",
      "ends_at",
      "expected_version",
    ];
    await api(
      "/maintenance-windows" + (editing.value ? "/" + editing.value : ""),
      editing.value ? "PUT" : "POST",
      Object.fromEntries(keys.map((k) => [k, form.value[k]])),
    );
    dialog.value = false;
    ElMessage.success(editing.value ? "已保存修改" : "已创建维护或静默时段");
    await refresh();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    busy.value = false;
  }
}
async function removeWindow(row: Row) {
  try {
    await ElMessageBox.confirm(
      `删除“${targetName(row)}”的${row.kind === "maintenance" ? "计划维护" : "临时静默"}？该时段将不再抑制平台事故通知。`,
      "删除维护或静默",
      {
        type: "warning",
        confirmButtonText: "确认删除",
        cancelButtonText: "取消",
      },
    );
    await api(
      `/maintenance-windows/${row.id}?expected_version=${row.version}`,
      "DELETE",
    );
    await refresh();
  } catch (e) {
    if (e instanceof Error) ElMessage.error(e.message);
  }
}
async function saveInhibition() {
  if (!inhibition.value.parent_rule_id || !inhibition.value.child_rule_id) {
    ElMessage.warning("请选择触发告警和被抑制告警");
    return;
  }
  if (inhibition.value.parent_rule_id === inhibition.value.child_rule_id) {
    ElMessage.warning("触发告警与被抑制告警不能相同");
    return;
  }
  busy.value = true;
  try {
    await api("/alert-inhibitions", "POST", inhibition.value);
    inhibitionDialog.value = false;
    ElMessage.success("已添加关联告警抑制");
    await refresh();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    busy.value = false;
  }
}
async function removeInhibition(row: Row) {
  try {
    await ElMessageBox.confirm(
      `移除“${ruleName(row.parent_rule_id)} → ${ruleName(row.child_rule_id)}”的抑制关系？`,
      "移除关联告警抑制",
      {
        type: "warning",
        confirmButtonText: "确认移除",
        cancelButtonText: "取消",
      },
    );
    await api(`/alert-inhibitions/${row.id}`, "DELETE");
    await refresh();
  } catch (e) {
    if (e instanceof Error) ElMessage.error(e.message);
  }
}
function targetName(row: Row) {
  const options =
    row.scope_kind === "service"
      ? services.value
      : row.scope_kind === "server"
        ? servers.value
        : rules.value;
  const item = options.find((i) => i.id === row.scope_id);
  return item?.name || item?.code || "对象已不可用";
}
const ruleName = (id: string) =>
  rules.value.find((i) => i.id === id)?.name ||
  rules.value.find((i) => i.id === id)?.code ||
  "规则已不可用";
</script>
<template>
  <div class="page-heading notification-heading">
    <h1>通知与维护</h1>
    <PageIntro />

    <div class="actions">
      <router-link to="/notifications"
        ><el-button>查看我的待办</el-button></router-link
      >
      <el-button :disabled="busy || loading" @click="refreshPage"
        >刷新</el-button
      >
    </div>
  </div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <section v-loading="loading" class="panel notification-workspace">
    <el-tabs class="workspace-tabs" v-model="activeTab">
      <el-tab-pane label="平台邮箱与通知" name="policy">
        <PlatformMailSettings ref="senderSettings" />
        <div class="section-heading">
          <div class="heading-label">
            <h2>发送方式</h2>
            <el-tag v-if="savedPolicy" :type="dirty ? 'warning' : 'success'">{{
              dirty ? "修改未保存" : "已保存"
            }}</el-tag>
          </div>
          <el-tag v-if="!auth.can('rules:manage')" type="info">只读</el-tag>
          <el-button
            v-else-if="!editingPolicy"
            :disabled="busy || !savedPolicy"
            @click="editingPolicy = true"
            >编辑</el-button
          >
        </div>
        <div v-if="savedPolicy" class="saved-mode">
          <span>当前生效</span><strong>{{ savedModeLabel }}</strong>
        </div>
        <el-radio-group
          v-if="editingPolicy"
          v-model="deliveryMode"
          class="delivery-modes"
          aria-label="发送方式"
          :disabled="!auth.can('rules:manage') || busy || !savedPolicy"
        >
          <el-radio
            v-for="mode in deliveryModes"
            :key="mode.value"
            :value="mode.value"
            border
            >{{ mode.label }}</el-radio
          >
        </el-radio-group>
        <div class="delivery-effects">
          <div>
            <span>原始告警邮件</span
            ><el-tag :type="deliveryMode === 'legacy' ? 'success' : 'info'">{{
              deliveryMode === "legacy" ? "由 Alertmanager 发送" : "停止发送"
            }}</el-tag>
          </div>
          <div>
            <span>平台待办</span><el-tag type="success">保持开启</el-tag>
          </div>
          <div>
            <span>平台邮件</span
            ><el-tag :type="deliveryMode === 'email' ? 'success' : 'info'">{{
              deliveryMode === "email" ? "按业务接收人发送" : "不发送"
            }}</el-tag>
          </div>
        </div>
        <div
          v-if="auth.can('rules:manage') && editingPolicy"
          class="policy-footer"
        >
          <el-button :disabled="busy" @click="cancelPolicy">取消</el-button>
          <el-button
            type="primary"
            :loading="busy"
            :disabled="!dirty || !savedPolicy"
            @click="savePolicy"
            >保存发送设置</el-button
          >
        </div>
      </el-tab-pane>
      <el-tab-pane label="维护与静默" name="maintenance">
        <div v-if="auth.can('rules:manage')" class="section-heading">
          <div v-if="auth.can('rules:manage')" class="actions">
            <el-button @click="openWindow(undefined, 'maintenance')"
              >新增计划维护</el-button
            >
            <el-button type="primary" @click="openWindow(undefined, 'silence')"
              >新增临时静默</el-button
            >
          </div>
        </div>
        <div class="scope-status">
          <span>生效期间</span
          ><el-tag type="warning">暂停匹配的事故通知与超时升级</el-tag
          ><el-tag type="info">告警继续记录</el-tag>
        </div>
        <el-radio-group
          v-model="windowFilter"
          class="table-filter"
          aria-label="维护时段筛选"
        >
          <el-radio-button value="active">生效中与待开始</el-radio-button
          ><el-radio-button value="all">全部时段</el-radio-button>
        </el-radio-group>
        <el-table
          :data="visibleWindows"
          empty-text="暂无符合条件的维护或静默时段"
        >
          <el-table-column label="生效对象" min-width="180"
            ><template #default="{ row }"
              ><div class="object-name">{{ targetName(row) }}</div>
              <el-tag size="small" type="info">{{
                { service: "服务", server: "服务器", rule: "告警规则" }[
                  row.scope_kind as string
                ] || "对象"
              }}</el-tag></template
            ></el-table-column
          >
          <el-table-column label="类型" width="120"
            ><template #default="{ row }">{{
              row.kind === "maintenance" ? "计划维护" : "临时静默"
            }}</template></el-table-column
          >
          <el-table-column label="状态" width="110"
            ><template #default="{ row }"
              ><el-tag
                :type="windowState(row, now) === '生效中' ? 'success' : 'info'"
                >{{ windowState(row, now) }}</el-tag
              ></template
            ></el-table-column
          >
          <el-table-column
            prop="reason"
            label="原因"
            min-width="200"
            show-overflow-tooltip
          />
          <el-table-column label="开始时间" width="180"
            ><template #default="{ row }">{{
              date(row.starts_at)
            }}</template></el-table-column
          >
          <el-table-column label="结束时间" width="180"
            ><template #default="{ row }">{{
              date(row.ends_at)
            }}</template></el-table-column
          >
          <el-table-column
            v-if="auth.can('rules:manage')"
            label="操作"
            width="130"
            fixed="right"
            ><template #default="{ row }"
              ><div class="row-actions">
                <el-button link @click="openWindow(row)">编辑</el-button
                ><el-button link type="danger" @click="removeWindow(row)"
                  >删除</el-button
                >
              </div></template
            ></el-table-column
          >
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="关联告警抑制" name="inhibition">
        <div v-if="auth.can('rules:manage')" class="section-heading">
          <el-button
            v-if="auth.can('rules:manage')"
            type="primary"
            @click="
              inhibition = { parent_rule_id: '', child_rule_id: '' };
              inhibitionDialog = true;
            "
            >新增抑制关系</el-button
          >
        </div>
        <div class="scope-status">
          <span>匹配范围</span
          ><el-tag type="info">同一服务器、模型接口或关联服务</el-tag
          ><el-tag type="warning"
            >触发告警持续时，暂停被抑制告警的事故通知</el-tag
          >
        </div>
        <el-table :data="inhibitions" empty-text="暂无告警抑制规则">
          <el-table-column label="当此告警触发时" min-width="240"
            ><template #default="{ row }">{{
              ruleName(row.parent_rule_id)
            }}</template></el-table-column
          >
          <el-table-column label="抑制此告警的通知" min-width="240"
            ><template #default="{ row }">{{
              ruleName(row.child_rule_id)
            }}</template></el-table-column
          >
          <el-table-column
            v-if="auth.can('rules:manage')"
            label="操作"
            width="100"
            fixed="right"
            ><template #default="{ row }"
              ><el-button link type="danger" @click="removeInhibition(row)"
                >移除</el-button
              ></template
            ></el-table-column
          >
        </el-table>
      </el-tab-pane>
      <el-tab-pane
        v-if="auth.can('audit:read')"
        label="本页操作记录"
        name="audit"
      >
        <el-alert
          v-if="auditError"
          :title="auditError"
          type="error"
          :closable="false"
        />
        <el-table v-loading="auditBusy" :data="audit" empty-text="暂无操作记录">
          <el-table-column label="操作" min-width="220"
            ><template #default="{ row }">{{
              operationName(row)
            }}</template></el-table-column
          >
          <el-table-column label="操作对象" min-width="280"
            ><template #default="{ row }"
              ><div class="object-name">{{ operationTarget(row) }}</div>
              <el-tag type="info" size="small">{{
                operationKind(row)
              }}</el-tag></template
            ></el-table-column
          >
          <el-table-column label="操作人" min-width="140"
            ><template #default="{ row }"
              ><span class="actor">{{ operationActor(row) }}</span></template
            ></el-table-column
          >
          <el-table-column label="操作时间" width="190"
            ><template #default="{ row }">{{
              date(row.created_at)
            }}</template></el-table-column
          >
        </el-table>
        <el-pagination
          v-model:current-page="auditPage"
          :disabled="auditBusy"
          :total="auditTotal"
          :page-size="20"
          layout="prev, pager, next"
          @current-change="loadAudit"
        />
      </el-tab-pane>
    </el-tabs>
  </section>
  <el-dialog
    v-model="dialog"
    :title="`${editing ? '编辑' : '新增'}${form.kind === 'maintenance' ? '计划维护' : '临时静默'}`"
    width="min(600px, 94vw)"
    :close-on-click-modal="false"
    ><el-form label-width="110px" :disabled="busy"
      ><el-form-item label="时段用途" required>
        <el-radio-group v-model="form.kind"
          ><el-radio-button value="maintenance">计划维护</el-radio-button
          ><el-radio-button value="silence"
            >临时静默</el-radio-button
          ></el-radio-group
        >
      </el-form-item>
      <el-form-item label="生效对象类型" required
        ><el-select v-model="form.scope_kind" @change="form.scope_id = ''"
          ><el-option label="服务" value="service" /><el-option
            label="服务器"
            value="server" /><el-option
            label="告警规则"
            value="rule" /></el-select></el-form-item
      ><el-form-item label="生效对象" required
        ><el-select
          v-model="form.scope_id"
          filterable
          placeholder="选择要暂停事故通知的对象"
          ><el-option
            v-for="item in targets"
            :key="item.id"
            :value="item.id"
            :label="item.name || item.code" /></el-select></el-form-item
      ><el-form-item label="开始时间" required
        ><el-date-picker
          v-model="form.starts_at"
          type="datetime"
          placeholder="选择开始时间"
          value-format="YYYY-MM-DDTHH:mm:ssZ" /></el-form-item
      ><el-form-item label="结束时间" required
        ><el-date-picker
          v-model="form.ends_at"
          type="datetime"
          placeholder="选择自动结束时间"
          value-format="YYYY-MM-DDTHH:mm:ssZ" /></el-form-item
      ><el-form-item label="原因" required
        ><el-input
          v-model="form.reason"
          type="textarea"
          :rows="3"
          placeholder="填写维护事项或静默原因"
          maxlength="2000" /></el-form-item
    ></el-form>
    <dl class="setting-facts dialog-effect">
      <div>
        <dt>时段内</dt>
        <dd>暂停匹配的事故通知与超时升级</dd>
      </div>
      <div>
        <dt>到期后</dt>
        <dd>自动解除本时段的抑制</dd>
      </div>
    </dl>
    <template #footer
      ><el-button :disabled="busy" @click="dialog = false">取消</el-button
      ><el-button type="primary" :loading="busy" @click="saveWindow">{{
        editing ? "保存修改" : "创建时段"
      }}</el-button></template
    ></el-dialog
  >
  <el-dialog
    v-model="inhibitionDialog"
    title="新增关联告警抑制"
    width="min(600px, 94vw)"
    :close-on-click-modal="false"
    ><el-form label-width="140px" :disabled="busy"
      ><el-form-item label="当此告警触发时" required
        ><el-select
          v-model="inhibition.parent_rule_id"
          filterable
          placeholder="选择触发抑制的告警规则"
          ><el-option
            v-for="item in rules"
            :key="item.id"
            :value="item.id"
            :label="item.name || item.code" /></el-select></el-form-item
      ><el-form-item label="抑制此告警的通知" required
        ><el-select
          v-model="inhibition.child_rule_id"
          filterable
          placeholder="选择被抑制的告警规则"
          ><el-option
            v-for="item in rules"
            :key="item.id"
            :value="item.id"
            :disabled="item.id === inhibition.parent_rule_id"
            :label="item.name || item.code" /></el-select></el-form-item
    ></el-form>
    <dl class="setting-facts dialog-effect">
      <div>
        <dt>匹配范围</dt>
        <dd>同一服务器、模型接口或关联服务</dd>
      </div>
      <div>
        <dt>抑制结束</dt>
        <dd>触发告警恢复或停用，或移除此关系</dd>
      </div>
    </dl>
    <template #footer
      ><el-button :disabled="busy" @click="inhibitionDialog = false"
        >取消</el-button
      ><el-button type="primary" :loading="busy" @click="saveInhibition"
        >添加抑制关系</el-button
      ></template
    ></el-dialog
  >
</template>

<style scoped>
.notification-heading h1,
.section-heading h2 {
  margin: 0;
}
.section-heading,
.heading-label {
  display: flex;
  align-items: center;
  gap: 12px;
}
.section-heading {
  justify-content: space-between;
  min-height: 52px;
  margin: 8px 0 20px;
}
.recipient-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-top: 28px;
}
.setup-card {
  min-width: 0;
  padding: 20px;
  background: var(--surface-soft);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.setup-card h3 {
  margin: 0;
}
.saved-mode,
.scope-status,
.service-link,
.policy-footer {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.saved-mode {
  padding: 14px 16px;
  border-radius: 8px;
  background: var(--surface-soft);
  margin-bottom: 20px;
}
.saved-mode > span,
.setting-facts dt,
.delivery-effects span {
  color: var(--muted);
}
.delivery-modes {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  width: 100%;
}
.delivery-modes :deep(.el-radio) {
  width: 100%;
  height: auto;
  min-height: 64px;
  margin: 0;
  padding: 16px;
}
.delivery-modes :deep(.el-radio.is-checked) {
  background: var(--el-color-primary-light-9);
}
.delivery-modes :deep(.el-radio__label) {
  white-space: normal;
  line-height: 1.6;
}
.delivery-effects {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 18px;
}
.delivery-effects > div {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}
.policy-footer {
  justify-content: flex-end;
  margin-top: 22px;
}
.setting-facts {
  margin: 18px 0;
}
.setting-facts > div {
  display: grid;
  grid-template-columns: 126px minmax(0, 1fr);
  gap: 12px;
  margin-top: 14px;
  line-height: 1.6;
}
.setting-facts dd {
  margin: 0;
}
.service-link .el-select {
  flex: 1;
  min-width: 180px;
}
.compact-heading {
  min-height: 36px;
  margin: 0 0 16px;
  flex-wrap: wrap;
}
.mailbox-status {
  margin-bottom: 12px;
}
.scope-status {
  margin: 0 0 20px;
}
.table-filter {
  margin-bottom: 16px;
}
.dialog-effect {
  padding: 16px;
  background: var(--surface-soft);
  border-radius: 8px;
}
.dialog-effect > div:first-child {
  margin-top: 0;
}
:deep(.el-dialog .el-select),
:deep(.el-dialog .el-date-editor.el-input) {
  width: 100%;
}
.section-heading {
  flex-wrap: wrap;
}
.notification-heading {
  flex-wrap: wrap;
  gap: 12px;
}
.object-name {
  color: var(--text);
  margin-bottom: 6px;
  overflow-wrap: anywhere;
}
.actor {
  font-weight: 500;
}
.row-actions {
  display: flex;
  gap: 16px;
  white-space: nowrap;
}
.row-actions .el-button {
  margin: 0;
}
.el-pagination {
  margin-top: 20px;
  justify-content: flex-end;
}
@media (max-width: 900px) {
  .recipient-grid,
  .delivery-modes,
  .delivery-effects {
    grid-template-columns: 1fr;
  }
}
</style>
