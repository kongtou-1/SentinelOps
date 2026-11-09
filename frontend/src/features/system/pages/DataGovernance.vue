<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { agentModules } from "@/features/system/agentConfig";
import { ElMessageBox } from "element-plus";
import { api, all, download, query, request } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import { storageGroups } from "@/features/system/storageDisplay";
import type { Row } from "@/types/records";
const auth = useAuth(),
  tab = ref(
    ["storage", "archives", "usage"].includes(String(useRoute().query.tab))
      ? String(useRoute().query.tab)
      : "storage",
  ),
  error = ref(""),
  busy = ref(false),
  storage = ref<Row>({}),
  plans = ref<Row[]>([]),
  archives = ref<Row[]>([]),
  usages = ref<Row[]>([]),
  totals = ref<Row>({}),
  services = ref<Row[]>([]),
  applicationId = ref(""),
  purpose = ref(""),
  agentModule = ref(""),
  timeRange = ref<string[] | null>([]),
  page = ref(1),
  total = ref(0);
const previewOpen = ref(false),
  selected = ref<Row>();
const businessStorage = computed(() =>
  storageGroups(storage.value.tables || []),
);
const databaseBytes = computed(() =>
  storage.value.tables
    ? storage.value.tables.reduce((sum: number, row: Row) => sum + row.bytes, 0)
    : null,
);
const kinds: Record<string, string> = {
  conversation: "诊断会话",
  source_snapshot: "源码快照",
  prediction: "预测结果",
  temporary: "临时文件",
};
const purposes: Record<string, string> = {
  business: "业务",
  probe: "探测",
  diagnosis: "诊断",
  inspection: "巡检",
  connection_test: "连接测试",
  report: "报告",
  evaluation: "评测",
};
const sources: Record<string, string> = {
  platform_gateway: "平台网关",
  llm_probe: "平台探测",
  application_metrics: "应用埋点估算",
};
const statuses: Record<string, string> = {
  preview: "预览",
  queued: "等待执行",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  ready: "归档已保存",
  releasing: "释放中",
  complete: "已完成",
};
const reasons: Record<string, string> = {
  active_task: "活动任务",
  adopted_analysis: "已采用诊断",
  recent_activity: "近期活动",
  official_analysis: "正式诊断引用",
  release_reference: "发布版本引用",
  confirmed_incident: "已确认事故引用",
  record_changed: "记录已变化",
  archive_size_limit: "超出单次归档限额",
  active_source_capture: "源码采集中",
  temporary_changed: "临时文件已变化",
  already_absent: "对象已不存在",
};
const directories: Record<string, string> = {
  source_snapshots: "源码快照",
  source_cache: "源码缓存",
  temporary: "临时文件",
  archives: "归档",
};
const bytes = (n: number | null | undefined) =>
  n == null
    ? "未知"
    : n >= 1073741824
      ? (n / 1073741824).toFixed(2) + " GiB"
      : (n / 1048576).toFixed(2) + " MiB";
function range() {
  const now = new Date();
  return timeRange.value?.length === 2
    ? timeRange.value
    : [
        new Date(
          Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1),
        ).toISOString(),
        now.toISOString(),
      ];
}
async function load() {
  try {
    const [start, end] = range();
    const filters = {
      start,
      end,
      application_id: applicationId.value,
      purpose: purpose.value,
      agent_module: agentModule.value,
    };
    const results = await Promise.all([
      api<Row>("/system/storage"),
      all<Row>("/archives"),
      request<Row[]>(
        "/system/model-usage" +
          query({ ...filters, offset: (page.value - 1) * 20, limit: 20 }),
      ),
      api<Row>("/system/model-usage/summary" + query(filters)),
      auth.can("users:manage")
        ? all<Row>("/system/cleanup-plans")
        : Promise.resolve([]),
      all<Row>("/business-services"),
    ]);
    storage.value = results[0];
    archives.value = results[1];
    usages.value = results[2].data;
    total.value = results[2].total || 0;
    totals.value = results[3];
    plans.value = results[4];
    services.value = results[5];
    if (selected.value)
      selected.value =
        plans.value.find((p) => p.id === selected.value!.id) || selected.value;
    error.value = "";
  } catch (e) {
    error.value = String(e);
  }
}
const { refresh } = usePoll(load, () => (busy.value ? null : 30000));
const candidates = computed(() => selected.value?.candidates || []);
const eligible = computed(() =>
  candidates.value.filter((c: Row) => !c.protected_reasons.length),
);
function reason(value: string) {
  return (
    reasons[value] ||
    (value.startsWith("evidence_reference:") ? "冻结证据引用" : "受保护")
  );
}
function resultFor(row: Row) {
  return selected.value?.result.find(
    (r: Row) => r.kind === row.kind && r.id === row.id,
  );
}
async function preview() {
  busy.value = true;
  try {
    selected.value = await api("/system/cleanup-plans", "POST", {});
    previewOpen.value = true;
    await refresh();
  } catch (e) {
    error.value = String(e);
  } finally {
    busy.value = false;
  }
}
async function execute() {
  busy.value = true;
  try {
    await ElMessageBox.confirm("归档并清理预览中的候选对象。", "确认清理", {
      confirmButtonText: "确认执行",
      cancelButtonText: "取消",
    });
    selected.value = await api(
      `/system/cleanup-plans/${selected.value!.id}/execute`,
      "POST",
      { expected_version: selected.value!.version, confirm: true },
    );
    await refresh();
  } catch (e) {
    if (e !== "cancel") error.value = String(e);
  } finally {
    busy.value = false;
  }
}
async function readArchive(row: Row) {
  try {
    await download(`/archives/${row.id}/content`);
  } catch (e) {
    error.value = String(e);
  }
}
</script>
<template>
  <div class="page-heading">
    <h1>数据管理</h1>
    <PageIntro />
    <el-button @click="refresh">刷新</el-button>

  </div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <el-tabs class="page-tabs" v-model="tab">
    <el-tab-pane label="存储与清理" name="storage">
      <section class="storage-overview">
        <div class="storage-card">
          <span>数据库</span><strong>{{ bytes(databaseBytes) }}</strong>
        </div>
        <div
          v-for="(label, key) in directories"
          :key="key"
          class="storage-card"
        >
          <span>{{ label }}</span
          ><strong>{{ bytes(storage.directories?.[key]?.bytes) }}</strong>
        </div>
      </section>
      <div class="storage-columns">
        <section class="panel">
          <div class="section-heading"><h2>数据占用</h2></div>
          <el-table :data="businessStorage" empty-text="暂无存储数据">
            <el-table-column prop="name" label="业务数据" />
            <el-table-column label="占用空间" width="140" align="right"
              ><template #default="{ row }">{{
                bytes(row.bytes)
              }}</template></el-table-column
            >
          </el-table>
          <div class="storage-limits">
            <span
              >源码缓存上限
              <b>{{ bytes(storage.source_cache_limit_bytes) }}</b></span
            ><span
              >源码存储上限
              <b>{{ bytes(storage.source_store_limit_bytes) }}</b></span
            >
          </div>
        </section>
        <section class="panel">
          <div class="section-heading">
            <h2>历史数据清理</h2>
            <el-button
              v-if="auth.can('users:manage')"
              type="primary"
              :loading="busy"
              @click="preview"
              >预览可清理数据</el-button
            >
          </div>
          <el-table
            :data="
              Object.entries(storage.retention_days || {}).map(
                ([kind, days]) => ({ name: kinds[kind] || '其他数据', days }),
              )
            "
            empty-text="暂无清理规则"
          >
            <el-table-column prop="name" label="数据类型" />
            <el-table-column label="保留时间" width="140" align="right"
              ><template #default="{ row }"
                >{{ row.days }} 天</template
              ></el-table-column
            >
          </el-table>
        </section>
      </div>
      <section v-if="auth.can('users:manage')" class="panel">
        <div class="section-heading"><h2>清理记录</h2></div>
        <el-table :data="plans" empty-text="尚未生成清理预览">
          <el-table-column label="创建时间" min-width="180"
            ><template #default="{ row }">{{
              date(row.created_at)
            }}</template></el-table-column
          >
          <el-table-column label="状态" width="140"
            ><template #default="{ row }"
              ><el-tag :type="row.status === 'failed' ? 'danger' : 'info'">{{
                statuses[row.status] || "未知状态"
              }}</el-tag></template
            ></el-table-column
          >
          <el-table-column label="检查对象" min-width="120"
            ><template #default="{ row }"
              >{{ row.candidates.length }} 项</template
            ></el-table-column
          >
          <el-table-column label="操作" width="140"
            ><template #default="{ row }"
              ><el-button
                link
                @click="
                  selected = row;
                  previewOpen = true;
                "
                >查看详情</el-button
              ></template
            ></el-table-column
          >
        </el-table>
      </section>
    </el-tab-pane>
    <el-tab-pane label="历史归档" name="archives">
      <section class="panel">
        <el-table :data="archives" empty-text="暂无归档记录">
          <el-table-column label="数据类型" min-width="160"
            ><template #default="{ row }">{{
              kinds[row.resource_kind] || "历史数据"
            }}</template></el-table-column
          >
          <el-table-column label="归档时间" min-width="180"
            ><template #default="{ row }">{{
              date(row.created_at)
            }}</template></el-table-column
          >
          <el-table-column label="状态" min-width="130"
            ><template #default="{ row }">{{
              statuses[row.status] || "未知状态"
            }}</template></el-table-column
          >
          <el-table-column label="归档大小" min-width="130"
            ><template #default="{ row }">{{
              bytes(row.archive_bytes)
            }}</template></el-table-column
          >
          <el-table-column label="已释放文件空间" min-width="150"
            ><template #default="{ row }">{{
              bytes(row.released_file_bytes)
            }}</template></el-table-column
          >
          <el-table-column label="操作" width="120"
            ><template #default="{ row }"
              ><el-button link @click="readArchive(row)"
                >下载归档</el-button
              ></template
            ></el-table-column
          >
        </el-table>
      </section>
    </el-tab-pane>
    <el-tab-pane label="模型调用" name="usage">
      <section class="panel">
        <div class="usage-filters">
          <el-date-picker
            v-model="timeRange"
            type="datetimerange"
            value-format="YYYY-MM-DDTHH:mm:ssZ"
            start-placeholder="本月开始"
            end-placeholder="当前时间"
            clearable
            @change="
              page = 1;
              refresh();
            "
          />
          <el-select
            v-model="applicationId"
            clearable
            placeholder="全部服务"
            @change="
              page = 1;
              refresh();
            "
            ><el-option
              v-for="s in services"
              :key="s.id"
              :value="s.id"
              :label="s.name"
          /></el-select>
          <el-select
            v-model="agentModule"
            clearable
            placeholder="全部 Agent 模块"
            @change="
              page = 1;
              refresh();
            "
            ><el-option
              v-for="(name, key) in agentModules"
              :key="key"
              :value="key"
              :label="name"
          /></el-select>
          <el-select
            v-model="purpose"
            clearable
            placeholder="全部用途"
            @change="
              page = 1;
              refresh();
            "
            ><el-option
              v-for="(name, key) in purposes"
              :key="key"
              :value="key"
              :label="name"
          /></el-select>
        </div>
        <div class="section-heading"><h2>调用汇总</h2></div>
        <el-table
          :data="totals.groups || []"
          empty-text="所选时间内暂无调用记录"
        >
          <el-table-column label="Agent 模块" min-width="140"
            ><template #default="{ row }">{{
              agentModules[row.agent_module] || "业务应用"
            }}</template></el-table-column
          >
          <el-table-column prop="model" label="模型" min-width="170" />
          <el-table-column label="用途" min-width="110"
            ><template #default="{ row }">{{
              purposes[row.purpose] || "其他调用"
            }}</template></el-table-column
          >
          <el-table-column prop="calls" label="请求数" width="100" />
          <el-table-column label="输入 / 输出 Token" min-width="180"
            ><template #default="{ row }"
              >{{ row.known_input_tokens }} /
              {{ row.known_output_tokens }}</template
            ></el-table-column
          >
          <el-table-column
            prop="unknown_usage_records"
            label="未返回 Token 的请求"
            min-width="170"
          />
        </el-table>
        <div class="section-heading detail-heading"><h2>调用明细</h2></div>
        <el-table :data="usages" empty-text="所选时间内暂无调用记录">
          <el-table-column label="时间" width="180"
            ><template #default="{ row }">{{
              date(row.occurred_at)
            }}</template></el-table-column
          >
          <el-table-column prop="model" label="模型" min-width="170" />
          <el-table-column label="用途" min-width="110"
            ><template #default="{ row }">{{
              purposes[row.purpose] || "其他调用"
            }}</template></el-table-column
          >
          <el-table-column label="来源" min-width="130"
            ><template #default="{ row }">{{
              sources[row.source] || "其他来源"
            }}</template></el-table-column
          >
          <el-table-column label="结果" width="120"
            ><template #default="{ row }">{{
              row.outcome === "success"
                ? "成功"
                : row.outcome === "failed"
                  ? "失败"
                  : row.outcome === "observed"
                    ? "已采集"
                    : "未记录结果"
            }}</template></el-table-column
          >
          <el-table-column label="输入 / 输出 Token" min-width="180"
            ><template #default="{ row }"
              >{{ row.input_tokens ?? "未知" }} /
              {{ row.output_tokens ?? "未知" }}</template
            ></el-table-column
          >
          <el-table-column label="耗时" width="110"
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
    </el-tab-pane>
  </el-tabs>
  <el-dialog v-model="previewOpen" title="清理预览与执行结果" width="90%"
    ><el-descriptions :column="3" border
      ><el-descriptions-item label="状态">{{
        statuses[selected?.status || ""]
      }}</el-descriptions-item
      ><el-descriptions-item label="初始可清理"
        >{{ eligible.length }} 项</el-descriptions-item
      ><el-descriptions-item label="预计释放">{{
        bytes(eligible.reduce((sum: number, row: Row) => sum + row.bytes, 0))
      }}</el-descriptions-item></el-descriptions
    ><el-table :data="candidates"
      ><el-table-column label="类型"
        ><template #default="{ row }">{{
          kinds[row.kind]
        }}</template></el-table-column
      ><el-table-column label="对象"
        ><template #default="{ row }">{{
          row.name || row.id
        }}</template></el-table-column
      ><el-table-column label="估计容量"
        ><template #default="{ row }">{{
          bytes(row.bytes)
        }}</template></el-table-column
      ><el-table-column label="保护原因"
        ><template #default="{ row }">{{
          (resultFor(row)?.reasons || row.protected_reasons)
            .map(reason)
            .join("、") || "无引用"
        }}</template></el-table-column
      ><el-table-column label="执行结果"
        ><template #default="{ row }">{{
          resultFor(row)?.status === "archived"
            ? "已归档并清理"
            : resultFor(row)?.status === "protected"
              ? "已保护"
              : "待执行"
        }}</template></el-table-column
      ></el-table
    ><template #footer
      ><el-button @click="previewOpen = false">关闭</el-button
      ><el-button
        v-if="selected && ['preview', 'failed'].includes(selected.status)"
        type="primary"
        :loading="busy"
        :disabled="!eligible.length"
        @click="execute"
        >{{
          selected.status === "failed" ? "重新执行" : "确认执行清理"
        }}</el-button
      ></template
    ></el-dialog
  >
</template>
<style scoped>
.page-heading h1,
.section-heading h2 {
  margin: 0;
}
.storage-overview {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 16px;
  margin: 4px 0 20px;
}
.storage-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.storage-card span {
  display: block;
  color: var(--text-regular);
  margin-bottom: 16px;
}
.storage-card strong {
  font-size: 24px;
  font-weight: 600;
}
.storage-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  align-items: start;
}
.storage-columns > .panel {
  min-width: 0;
}
.section-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  min-height: 36px;
  margin-bottom: 20px;
}
.storage-limits {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 24px;
  padding-top: 20px;
  color: var(--text-regular);
}
.storage-limits b {
  font-weight: 500;
  margin-left: 8px;
}
.usage-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 24px;
}
.usage-filters > .el-select {
  width: 180px;
}
.usage-filters :deep(.el-date-editor) {
  flex: 0 1 380px;
  min-width: 280px;
}
.detail-heading {
  margin-top: 28px;
}
.el-pagination {
  justify-content: flex-end;
  margin-top: 20px;
}
@media (max-width: 1100px) {
  .storage-columns {
    grid-template-columns: 1fr;
  }
  .storage-overview {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 600px) {
  .storage-overview {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
