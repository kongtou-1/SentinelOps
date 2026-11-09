<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { date } from "@/utils/display";
import type { Row } from "@/types/records";
import {
  activeTasks,
  capacity,
  duration,
  healthState,
  taskNames,
  workerNames,
  workerState,
  type TaskActivity,
  type WorkerHealth,
} from "@/features/system/healthDisplay";

const props = defineProps<{
  activeTab: string;
  data?: Row;
  live?: Row;
  ready?: Row;
  status?: Row;
  records: Row[];
  backupError: string;
  loading: boolean;
}>();
const emit = defineEmits<{
  refresh: [];
  editing: [value: boolean];
}>();
const auth = useAuth(),
  dialog = ref(false),
  busy = ref(false),
  form = ref<Row>({}),
  formError = ref(""),
  showAllTasks = ref(false);
watch(dialog, (value) => emit("editing", value));
const workers = computed<WorkerHealth[]>(() => props.data?.workers || []);
const expectedWorkers = computed(() =>
  workers.value.filter((row) => row.expected),
);
const healthyWorkers = computed(() =>
  expectedWorkers.value.filter((row) => row.status === "healthy"),
);
const tasks = computed<TaskActivity[]>(() => props.data?.queues || []);
const visibleTasks = computed(() =>
  showAllTasks.value ? tasks.value : activeTasks(tasks.value),
);
const taskTotals = computed(() =>
  tasks.value.reduce(
    (sum, row) => ({
      queued: sum.queued + row.queued,
      running: sum.running + row.running,
      failed: sum.failed + row.failed_24h,
    }),
    { queued: 0, running: 0, failed: 0 },
  ),
);
const backupLabel = computed(() =>
  !props.data?.backup
    ? "未确认"
    : !props.data.backup.backup_completed_at
      ? "尚无已校验备份"
      : props.data.backup.overdue
        ? "备份已超期"
        : "备份有效",
);
const checks = computed(() => [
  {
    name: "平台接口",
    state: props.live?.status,
    result: props.live?.status === "ok" ? "服务响应正常" : "未能确认接口可用",
  },
  {
    name: "数据库连接",
    state: props.status?.database || props.ready?.database,
    result:
      props.status?.database === "ok" || props.ready?.database === "ok"
        ? `已连接 · ${capacity(props.data?.database_bytes)}`
        : "未能确认数据库连接",
  },
  {
    name: "数据库结构",
    state: props.ready?.status,
    result: props.status?.revision
      ? `版本 ${props.status.revision}`
      : "未能读取当前版本",
  },
  {
    name: "指标采集",
    state: props.data?.components?.prometheus?.status,
    result: "Prometheus",
  },
  {
    name: "告警分发",
    state: props.data?.components?.alertmanager?.status,
    result: "Alertmanager",
  },
  {
    name: "文件存储",
    state: props.data?.storage?.status,
    result:
      props.data?.storage?.status === "available"
        ? `可用 ${capacity(props.data.storage.free_bytes)} / 总计 ${capacity(props.data.storage.total_bytes)}`
        : "未能读取存储空间",
  },
]);
const verification: Record<string, string> = {
  verified: "已校验",
  failed: "校验失败",
  not_checked: "未校验",
};
function open(kind: string) {
  form.value = {
    kind,
    backup_id: null,
    completed_at: new Date().toISOString(),
    duration_seconds: null,
    verification: "not_checked",
    evidence: "",
    artifacts: [{ name: "postgres.dump", sha256: "", bytes: 0 }],
  };
  formError.value = "";
  dialog.value = true;
}
async function save() {
  busy.value = true;
  formError.value = "";
  try {
    await api("/system/backup-records", "POST", {
      ...form.value,
      idempotency_key: crypto.randomUUID(),
    });
    dialog.value = false;
    ElMessage.success("记录已保存");
    emit("refresh");
  } catch (e) {
    formError.value = e instanceof Error ? e.message : "保存失败";
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div v-show="activeTab === 'overview'" class="health-overview">
    <div class="health-summary">
      <div>
        <span>平台接口</span>
        <strong :class="`tone-${healthState(live?.status).tone}`">{{
          loading && !live ? "检查中" : healthState(live?.status).label
        }}</strong>
      </div>
      <div>
        <span>数据库就绪</span>
        <strong :class="`tone-${healthState(ready?.status).tone}`">{{
          loading && !ready ? "检查中" : healthState(ready?.status).label
        }}</strong>
      </div>
      <div>
        <span>后台服务正常</span>
        <strong
          :class="
            data && healthyWorkers.length < expectedWorkers.length
              ? 'tone-warning'
              : ''
          "
          >{{
            data ? `${healthyWorkers.length} / ${expectedWorkers.length}` : "—"
          }}</strong
        >
      </div>
      <div>
        <span>数据备份</span>
        <strong :class="data?.backup?.overdue ? 'tone-warning' : ''">{{
          backupLabel
        }}</strong>
      </div>
    </div>
    <div class="health-columns">
      <section class="health-panel">
        <h2>服务检查</h2>
        <ul class="check-list">
          <li v-for="check in checks" :key="check.name">
            <div class="check-title">
              <span>{{ check.name }}</span>
              <el-tag
                :type="healthState(check.state).tone"
                size="small"
                effect="plain"
                >{{ healthState(check.state).label }}</el-tag
              >
            </div>
            <div class="check-result">{{ check.result }}</div>
          </li>
        </ul>
      </section>
      <section class="health-panel">
        <h2>后台服务</h2>
        <ul v-if="workers.length" class="check-list worker-list">
          <li v-for="worker in workers" :key="worker.name">
            <div class="check-title">
              <span>{{ workerNames[worker.name] || worker.name }}</span>
              <el-tag
                :type="workerState(worker).tone"
                size="small"
                effect="plain"
                >{{ workerState(worker).label }}</el-tag
              >
            </div>
            <div class="check-result">
              最近响应 {{ date(worker.observed_at) }}
            </div>
            <div v-if="worker.error_code" class="worker-error">
              运行检查失败 · {{ worker.error_code }}
            </div>
          </li>
        </ul>
        <el-empty
          v-else
          :description="loading ? '正在读取服务状态' : '服务状态未获取'"
          :image-size="52"
        />
      </section>
    </div>
  </div>
  <section v-show="activeTab === 'tasks'" class="health-panel">
    <div class="panel-heading">
      <h2>后台处理进度</h2>
      <el-switch v-model="showAllTasks" active-text="显示全部任务" />
    </div>
    <div class="task-summary">
      <span
        >等待处理 <strong>{{ data ? taskTotals.queued : "—" }}</strong></span
      >
      <span
        >正在处理 <strong>{{ data ? taskTotals.running : "—" }}</strong></span
      >
      <span
        >近 24 小时失败、中断或未知
        <strong :class="{ 'tone-danger': taskTotals.failed > 0 }">{{
          data ? taskTotals.failed : "—"
        }}</strong></span
      >
    </div>
    <el-table v-if="visibleTasks.length" :data="visibleTasks" row-key="name">
      <el-table-column label="任务类型" min-width="150">
        <template #default="{ row }">{{
          taskNames[row.name] || row.name
        }}</template>
      </el-table-column>
      <el-table-column prop="queued" label="等待处理" min-width="100" />
      <el-table-column prop="running" label="正在处理" min-width="100" />
      <el-table-column label="最长等待" min-width="130">
        <template #default="{ row }">{{
          duration(row.oldest_wait_seconds)
        }}</template>
      </el-table-column>
      <el-table-column label="近 24 小时失败、中断或未知" min-width="215">
        <template #default="{ row }"
          ><span :class="{ 'tone-danger': row.failed_24h > 0 }">{{
            row.failed_24h
          }}</span></template
        >
      </el-table-column>
    </el-table>
    <el-empty
      v-else
      :description="
        loading && !data
          ? '正在读取任务状态'
          : !data
            ? '任务状态未获取'
            : '暂无待处理、进行中或近 24 小时异常的任务'
      "
      :image-size="64"
    />
  </section>
  <section v-show="activeTab === 'backup'" class="health-panel">
    <div class="panel-heading">
      <h2>备份与恢复演练</h2>
      <div v-if="auth.can('users:manage')" class="actions">
        <el-button @click="open('backup')">登记备份</el-button>
        <el-button @click="open('restore')">登记恢复演练</el-button>
      </div>
    </div>
    <div class="backup-facts">
      <div>
        <span>备份状态</span
        ><strong :class="data?.backup?.overdue ? 'tone-warning' : ''">{{
          backupLabel
        }}</strong>
      </div>
      <div>
        <span>最近已校验备份</span
        ><strong>{{ date(data?.backup?.backup_completed_at) }}</strong>
      </div>
      <div>
        <span>恢复目标</span
        ><strong>{{ duration(data?.backup?.target_restore_seconds) }}</strong>
      </div>
      <div>
        <span>实际演练耗时</span
        ><strong>{{
          duration(data?.backup?.last_restore_duration_seconds)
        }}</strong>
      </div>
      <div>
        <span>演练校验</span
        ><strong>{{
          verification[data?.backup?.last_restore_verification] || "未记录"
        }}</strong>
      </div>
      <div>
        <span>恢复目标达成</span
        ><strong>{{
          data?.backup?.restore_target_met == null
            ? "未确认"
            : data.backup.restore_target_met
              ? "已达到"
              : "未达到"
        }}</strong>
      </div>
    </div>
    <el-alert
      v-if="backupError"
      :title="backupError"
      type="error"
      :closable="false"
    />
    <el-table v-else-if="records.length" :data="records" row-key="id">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="backup-evidence">
            <h3>校验与演练依据</h3>
            <p>{{ row.evidence || "未记录" }}</p>
            <h3>备份文件</h3>
            <dl v-for="a in row.artifacts" :key="a.name">
              <dt>文件名</dt>
              <dd>{{ a.name }}</dd>
              <dt>大小</dt>
              <dd>{{ capacity(a.bytes) }}</dd>
              <dt>SHA256</dt>
              <dd>{{ a.sha256 }}</dd>
            </dl>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="类型" min-width="120"
        ><template #default="{ row }">{{
          row.kind === "backup" ? "备份" : "恢复演练"
        }}</template></el-table-column
      >
      <el-table-column label="完成时间" min-width="185"
        ><template #default="{ row }">{{
          date(row.completed_at)
        }}</template></el-table-column
      >
      <el-table-column label="校验结果" min-width="110"
        ><template #default="{ row }"
          ><el-tag
            :type="
              row.verification === 'verified'
                ? 'success'
                : row.verification === 'failed'
                  ? 'danger'
                  : 'info'
            "
            >{{ verification[row.verification] || "未记录" }}</el-tag
          ></template
        ></el-table-column
      >
      <el-table-column label="实际耗时" min-width="130"
        ><template #default="{ row }">{{
          duration(row.duration_seconds)
        }}</template></el-table-column
      >
      <el-table-column label="备份文件" min-width="150"
        ><template #default="{ row }"
          ><div v-for="a in row.artifacts" :key="a.name">
            {{ a.name }}
          </div></template
        ></el-table-column
      >
    </el-table>
    <el-empty
      v-else
      :description="loading ? '正在读取备份记录' : '尚无备份或恢复演练记录'"
      :image-size="64"
    />
  </section>
  <el-dialog
    v-model="dialog"
    :title="form.kind === 'backup' ? '人工登记备份' : '人工登记恢复演练'"
    width="720px"
    :close-on-click-modal="false"
  >
    <el-alert
      v-if="formError"
      :title="formError"
      type="error"
      :closable="false"
    />
    <el-form label-width="110px">
      <el-form-item v-if="form.kind === 'restore'" label="来源备份" required>
        <el-select v-model="form.backup_id"
          ><el-option
            v-for="r in records.filter((r) => r.kind === 'backup')"
            :key="r.id"
            :value="r.id"
            :label="date(r.completed_at) + ' · ' + r.id.slice(0, 8)"
        /></el-select>
      </el-form-item>
      <el-form-item label="完成时间" required
        ><el-date-picker
          v-model="form.completed_at"
          type="datetime"
          value-format="YYYY-MM-DDTHH:mm:ssZ"
      /></el-form-item>
      <el-form-item label="耗时（秒）"
        ><el-input-number v-model="form.duration_seconds" :min="0"
      /></el-form-item>
      <el-form-item label="校验结果" required
        ><el-select v-model="form.verification"
          ><el-option
            v-for="key in ['not_checked', 'verified', 'failed']"
            :key="key"
            :value="key"
            :label="verification[key]" /></el-select
      ></el-form-item>
      <el-form-item label="校验依据" required
        ><el-input v-model="form.evidence" type="textarea" :maxlength="8000"
      /></el-form-item>
      <div v-for="(a, index) in form.artifacts" :key="index">
        <el-form-item label="产物名" required
          ><el-input v-model="a.name" :maxlength="200"
        /></el-form-item>
        <el-form-item label="SHA256" required
          ><el-input v-model="a.sha256" :maxlength="64"
        /></el-form-item>
        <el-form-item label="字节" required
          ><el-input-number v-model="a.bytes" :min="0"
        /></el-form-item>
      </div>
      <el-button
        @click="form.artifacts.push({ name: '', sha256: '', bytes: 0 })"
        >增加产物</el-button
      >
    </el-form>
    <template #footer
      ><el-button :disabled="busy" @click="dialog = false">取消</el-button
      ><el-button type="primary" :loading="busy" @click="save"
        >保存记录</el-button
      ></template
    >
  </el-dialog>
</template>
<style scoped>
.health-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px 0;
  margin-bottom: 20px;
}
.health-summary > div {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0 20px;
}
.health-summary > div + div {
  border-left: 1px solid var(--border);
}
.health-summary span,
.backup-facts span {
  color: var(--el-text-color-secondary);
}
.health-summary strong {
  font-size: 20px;
  font-weight: 600;
}
.health-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
}
.health-panel {
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 20px;
}
.check-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.check-list li {
  padding: 13px 0;
}
.check-list li + li {
  border-top: 1px solid var(--border);
}
.check-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.check-result {
  margin-top: 7px;
  color: var(--el-text-color-secondary);
  overflow-wrap: anywhere;
}
.worker-list li {
  padding: 10px 0;
}
.worker-error {
  color: var(--el-color-danger);
  margin-top: 6px;
  overflow-wrap: anywhere;
}
.tone-success {
  color: var(--el-color-success);
}
.tone-warning {
  color: var(--el-color-warning);
}
.tone-danger {
  color: var(--el-color-danger);
}
.tone-info {
  color: var(--el-text-color-secondary);
}
.task-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 28px;
  padding: 4px 0 20px;
}
.task-summary strong {
  margin-left: 8px;
  font-size: 20px;
}
.backup-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 24px;
  margin: 24px 0;
}
.backup-facts > div {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.backup-facts strong {
  font-weight: 500;
}
.backup-evidence {
  padding: 20px;
}
.backup-evidence p {
  white-space: pre-wrap;
}
.backup-evidence dl {
  display: grid;
  grid-template-columns: 70px minmax(0, 1fr);
  gap: 10px;
}
.backup-evidence dd {
  margin: 0;
  overflow-wrap: anywhere;
}
@media (max-width: 900px) {
  .health-columns {
    grid-template-columns: minmax(0, 1fr);
  }
  .health-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    row-gap: 20px;
  }
  .health-summary > div:nth-child(3) {
    border-left: 0;
  }
}
@media (max-width: 600px) {
  .backup-facts {
    grid-template-columns: minmax(0, 1fr);
  }
  .health-panel {
    padding: 16px;
  }
  .health-summary > div {
    padding: 0 14px;
  }
  .health-summary strong {
    font-size: 18px;
  }
}
</style>
