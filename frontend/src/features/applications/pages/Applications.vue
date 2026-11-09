<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { confirmOperation } from "@/utils/confirmOperation";
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { all, api, query, request } from "@/api/client";
import type { Row } from "@/types/records";
import { date, short } from "@/utils/display";
import { usePoll } from "@/composables/usePoll";
import {
  sourceLabel,
  sourceReason,
} from "@/features/applications/model/sources";
import { useAuth } from "@/features/auth/store";
import ServiceObservability from "@/features/applications/components/ServiceObservability.vue";
import ServiceDatabases from "@/features/applications/components/ServiceDatabases.vue";
import ServiceGovernance from "@/features/applications/components/ServiceGovernance.vue";
import SourceConnectionFields from "@/features/applications/components/SourceConnectionFields.vue";
import ServiceOwnerSelect from "@/features/applications/components/ServiceOwnerSelect.vue";
import {
  sourceConnectionForm,
  sourceConnectionPayload,
} from "@/features/applications/model/sourceConnection";

const route = useRoute(),
  router = useRouter(),
  auth = useAuth();
const id = computed(() => String(route.params.id || ""));
const detailTab = computed({
  get: () =>
    auth.can("resources:read") && route.query.view === "governance"
      ? "governance"
      : auth.can("resources:read") && route.query.view === "databases"
        ? "databases"
        : auth.can("resources:read") &&
            (route.query.view === "monitoring" || route.query.monitor)
          ? "monitoring"
          : "home",
  set: (value: string) => {
    const next = { ...route.query };
    if (value === "governance") {
      next.view = "governance";
      delete next.monitor;
    } else if (value === "databases") {
      next.view = "databases";
      delete next.monitor;
    } else if (value === "monitoring") next.view = "monitoring";
    else {
      delete next.view;
      delete next.monitor;
    }
    void router.replace({ query: next });
  },
});
const monitoringPanel = ref<InstanceType<typeof ServiceObservability>>(),
  governancePanel = ref<InstanceType<typeof ServiceGovernance>>(),
  pageRefreshing = ref(false);
const rows = ref<Row[]>([]),
  row = ref<Row>(),
  snapshots = ref<Row[]>([]);
const releases = ref<Row[]>([]),
  deployments = ref<Row[]>([]),
  targets = ref<Row[]>([]);
const error = ref(""),
  busy = ref(false),
  search = ref(""),
  total = ref(0),
  page = ref(1);
const dialog = ref(""),
  form = ref<Row>({}),
  formError = ref("");
const currentSnapshot = ref<Row>(),
  files = ref<Row[]>([]),
  fileTotal = ref(0),
  filePage = ref(1);
const fileFilter = ref("."),
  filePath = ref(""),
  fileStart = ref(1),
  fileData = ref<Row>();
const sourceQuery = ref(""),
  hits = ref<Row[]>([]),
  searchMessage = ref("");
const sourceOpen = ref(false),
  sourceError = ref("");
const manifestInput = ref<HTMLInputElement>();
const sourceLoading = ref(false);
const fields = [
  ["code", "服务标识", "例如 inference-api"],
  ["name", "服务名称", "例如推理 API"],
  ["owner", "负责人", ""],
];

async function load() {
  const selected = id.value;
  try {
    if (selected) {
      const result = await Promise.all([
        api<Row>(`/applications/${selected}`),
        api<Row[]>(`/applications/${selected}/snapshots?limit=100`),
        api<Row[]>(`/applications/${selected}/releases?limit=100`),
        api<Row[]>(`/applications/${selected}/deployments?limit=100`),
      ]);
      if (selected !== id.value) return;
      [row.value, snapshots.value, releases.value, deployments.value] = result;
    } else {
      const result = await request<Row[]>(
        "/applications" +
          query({
            q: search.value,
            offset: (page.value - 1) * 20,
            limit: 20,
          }),
      );
      if (id.value) return;
      rows.value = result.data;
      total.value = result.total || 0;
    }
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () =>
  dialog.value
    ? null
    : snapshots.value.some((s) => ["queued", "running"].includes(s.status))
      ? 5000
      : 30000,
);
async function refreshPage() {
  if (pageRefreshing.value) return;
  pageRefreshing.value = true;
  try {
    await Promise.all([
      refresh(),
      monitoringPanel.value?.refresh(),
      detailTab.value === "governance"
        ? governancePanel.value?.refresh()
        : undefined,
    ]);
  } finally {
    pageRefreshing.value = false;
  }
}
watch(id, () => {
  row.value = undefined;
  snapshots.value = [];
  sourceOpen.value = false;
  void refresh();
});
function openApplication() {
  form.value = {
    code: "",
    name: "",
    owner: "",
    ...(row.value || {}),
    ...sourceConnectionForm(row.value),
  };
  dialog.value = "application";
  formError.value = "";
}
async function openDeployment() {
  try {
    const sets = await Promise.all([
      all<Row>("/containers"),
      all<Row>("/llm-endpoints"),
    ]);
    targets.value = sets.flatMap((items, i) =>
      items.map((t) => ({
        id: (i ? "endpoint:" : "container:") + t.id,
        name: `${i ? "LLM" : "容器"} · ${t.name} · ${short(t.server_id || t.id)}`,
      })),
    );
    form.value = {
      release_id: "",
      target: "",
      environment: "test",
      compose_project: "",
      compose_service: "",
      config_version: "",
      started_at: new Date().toISOString(),
    };
    dialog.value = "deployment";
    formError.value = "";
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "读取部署目标失败");
  }
}
function openRelease() {
  form.value = {
    version: "",
    snapshot_id: null,
    expected_commit: "",
    expected_revision: "",
    image_ref: "",
    image_id: "",
    repo_digest: "",
    change_summary: "",
    test_result: "",
    build_source_status: "declared",
  };
  dialog.value = "release";
  formError.value = "";
}
async function importManifest(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  try {
    if (file.size > 65536) throw new Error("发布清单不能超过 64 KiB");
    const data = JSON.parse(await file.text());
    if (!data || typeof data !== "object" || Array.isArray(data))
      throw new Error("请选择发布清单 JSON 对象");
    openRelease();
    for (const key of Object.keys(form.value))
      if (Object.prototype.hasOwnProperty.call(data, key))
        form.value[key] = data[key];
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "无法读取发布清单");
  } finally {
    input.value = "";
  }
}
async function save() {
  if (busy.value) return;
  busy.value = true;
  formError.value = "";
  try {
    let path: string,
      method = "POST",
      body: Row;
    if (dialog.value === "application") {
      body = Object.fromEntries(fields.map((f) => [f[0]!, form.value[f[0]!]]));
      Object.assign(body, sourceConnectionPayload(form.value));
      path = id.value ? `/applications/${id.value}` : "/applications";
      if (id.value) method = "PUT";
    } else if (dialog.value === "release") {
      body = { ...form.value };
      for (const k of [
        "snapshot_id",
        "expected_commit",
        "expected_revision",
        "image_id",
        "repo_digest",
      ])
        body[k] ||= null;
      path = `/applications/${id.value}/releases`;
    } else {
      const [kind, target] = String(form.value.target).split(":");
      body = {
        ...form.value,
        container_id: kind === "container" ? target : null,
        llm_endpoint_id: kind === "endpoint" ? target : null,
      };
      delete body.target;
      path = `/applications/${id.value}/deployments`;
    }
    const saved = await api<Row>(path, method, body);
    dialog.value = "";
    ElMessage.success("已保存");
    if (!id.value && method === "POST")
      await router.push(`/applications/${saved.id}`);
    else await refresh();
  } catch (e) {
    formError.value = e instanceof Error ? e.message : "保存失败";
  } finally {
    busy.value = false;
  }
}
async function sync(source = "auto") {
  if (busy.value) return;
  busy.value = true;
  try {
    if (
      !(await confirmOperation(
        "按当前源码连接同步新快照？任务将在后台读取源码。",
        "同步源码",
      ))
    )
      return;
    await api(`/applications/${id.value}/source-syncs`, "POST", { source });
    ElMessage.success("同步任务已排队；现有诊断可继续运行");
    await refresh();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "同步失败");
  } finally {
    busy.value = false;
  }
}
async function browse(snapshot: Row) {
  currentSnapshot.value = snapshot;
  filePage.value = 1;
  fileFilter.value = ".";
  fileData.value = undefined;
  filePath.value = "";
  hits.value = [];
  searchMessage.value = "";
  sourceError.value = "";
  sourceOpen.value = true;
  await loadFiles();
}
async function loadFiles() {
  sourceLoading.value = true;
  files.value = [];
  try {
    const result = await api<Row>(
      `/source-snapshots/${currentSnapshot.value?.id}/tree` +
        query({
          path: fileFilter.value || ".",
          offset: (filePage.value - 1) * 100,
          limit: 100,
        }),
    );
    files.value = result.files;
    fileTotal.value = result.total;
    sourceError.value = "";
  } catch (e) {
    sourceError.value = e instanceof Error ? e.message : "读取目录失败";
  } finally {
    sourceLoading.value = false;
  }
}
async function readFile(path = filePath.value, start = 1) {
  filePath.value = path;
  fileStart.value = start;
  try {
    fileData.value = await api<Row>(
      `/source-snapshots/${currentSnapshot.value?.id}/file` +
        query({ path, start_line: start, end_line: start + 199 }),
    );
    sourceError.value = "";
  } catch (e) {
    sourceError.value = e instanceof Error ? e.message : "读取文件失败";
  }
}
async function searchSource() {
  if (!sourceQuery.value.trim()) return;
  try {
    const result = await api<Row>(
      `/source-snapshots/${currentSnapshot.value?.id}/search` +
        query({ q: sourceQuery.value, path: fileFilter.value || "." }),
    );
    hits.value = result.matches;
    searchMessage.value =
      result.data_gaps?.join("、") ||
      (!hits.value.length ? "未找到匹配内容" : "");
  } catch (e) {
    sourceError.value = e instanceof Error ? e.message : "搜索失败";
  }
}
async function endDeployment(item: Row) {
  try {
    await ElMessageBox.confirm(
      "结束这条部署记录，历史关联仍会保留。",
      "结束部署记录",
    );
    await api(`/application-deployments/${item.id}/end`, "POST", {
      ended_at: new Date().toISOString(),
    });
    ElMessage.success("部署记录已结束");
    await refresh();
  } catch (e) {
    if (e !== "cancel" && e !== "close")
      ElMessage.error(e instanceof Error ? e.message : "操作失败");
  }
}
async function attachRelease(item: Row) {
  form.value = { release_id: item.id, snapshot_id: "" };
  dialog.value = "attach";
  formError.value = "";
}
async function attach() {
  try {
    await api(
      `/application-releases/${form.value.release_id}/attach-snapshot` +
        query({ snapshot_id: form.value.snapshot_id }),
      "POST",
    );
    dialog.value = "";
    await refresh();
  } catch (e) {
    formError.value = e instanceof Error ? e.message : "关联失败";
  }
}
const fileLines = computed(() =>
  fileData.value?.total_lines === 0
    ? []
    : String(fileData.value?.content || "").split("\n"),
);
const releaseName = (releaseId: string) =>
  releases.value.find((r) => r.id === releaseId)?.version || short(releaseId);
function closeDialog(value: boolean) {
  if (!value) dialog.value = "";
}
</script>

<template>
  <section class="page-header">
    <div class="page-heading">
      <div>
        <router-link v-if="id" to="/applications" class="subtle-link"
          >← 应用与服务</router-link
        >
        <div class="heading-copy">
          <h1>{{ row?.name || "应用与服务" }}</h1>
          <PageIntro />
          <p v-if="id">
            {{ row?.code || "" }} · {{ row?.entrypoint || "未登记服务入口" }}
          </p>
        </div>
        <p v-if="id && row?.purpose" class="service-purpose">
          {{ row.purpose }}
        </p>
      </div>

      <div class="actions">
        <el-button :loading="pageRefreshing" @click="refreshPage"
          >刷新</el-button
        >
        <el-button
          v-if="id && detailTab === 'home' && auth.can('sources:sync')"
          :loading="busy"
          :disabled="!row?.enabled"
          @click="sync()"
          >同步源码</el-button
        >
        <el-button
          v-if="auth.can('sources:manage')"
          type="primary"
          @click="openApplication"
          >{{ id ? "编辑服务与源码连接" : "登记服务" }}</el-button
        >
      </div>
    </div>
  </section>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
    class="error-box"
  />
  <template v-if="!id"
    ><section class="panel">
      <div class="actions">
        <el-input
          v-model="search"
          placeholder="搜索服务名称或标识"
          clearable
          @keyup.enter="
            page = 1;
            refresh();
          "
        /><el-button
          @click="
            page = 1;
            refresh();
          "
          >搜索</el-button
        >
      </div>
      <el-table :data="rows"
        ><el-table-column label="服务"
          ><template #default="s"
            ><router-link :to="`/applications/${s.row.id}`">{{
              s.row.name
            }}</router-link>
            <div class="muted">{{ s.row.code }}</div></template
          ></el-table-column
        >
        <el-table-column
          prop="purpose"
          label="业务用途"
          min-width="220"
          show-overflow-tooltip
        />
        <el-table-column prop="owner" label="负责人" /><el-table-column
          prop="entrypoint"
          label="入口"
          show-overflow-tooltip
        />
        <el-table-column label="来源"
          ><template #default="s">{{
            [
              s.row.git_url ? "Git" : "",
              s.row.svn_url ? "SVN" : "",
              s.row.local_path ? "本地目录" : "",
            ]
              .filter(Boolean)
              .join(" / ") || "未配置"
          }}</template></el-table-column
        >
        <el-table-column label="状态"
          ><template #default="s">{{
            s.row.enabled ? "已启用" : "已停用"
          }}</template></el-table-column
        ></el-table
      >
      <el-pagination
        v-model:current-page="page"
        :page-size="20"
        :total="total"
        layout="prev, pager, next"
        @current-change="refresh"
      /></section
  ></template>
  <template v-else-if="row">
    <el-tabs class="page-tabs" v-model="detailTab" aria-label="应用详情">
      <el-tab-pane
        v-if="auth.can('resources:read')"
        label="运行治理"
        name="governance"
        lazy
      >
        <ServiceGovernance
          v-if="id"
          ref="governancePanel"
          :key="id"
          :application-id="id"
        />
      </el-tab-pane>
      <el-tab-pane label="应用主页" name="home">
        <template v-if="detailTab === 'home'">
          <section class="panel">
            <dl class="detail-grid">
              <div>
                <dt>{{ row.svn_url ? "SVN 仓库" : "Git 仓库" }}</dt>
                <dd>{{ row.svn_url || row.git_url || "未配置" }}</dd>
              </div>
              <div>
                <dt>默认引用 / 主要目录</dt>
                <dd>
                  {{ row.svn_url ? row.svn_revision : row.git_ref }} ·
                  {{ row.source_subdir }}
                </dd>
              </div>
              <div>
                <dt>本地目录</dt>
                <dd>{{ row.local_path ?? "未配置" }}</dd>
              </div>
              <div>
                <dt>负责人</dt>
                <dd>{{ row.owner || "未登记" }}</dd>
              </div>
            </dl>
            <div
              v-for="mapping in row.path_mappings"
              :key="mapping.runtime_prefix"
              class="muted"
            >
              {{ mapping.runtime_prefix }} → {{ mapping.repository_prefix }}
            </div>
          </section>
          <el-tabs class="page-tabs">
            <el-tab-pane label="源码快照"
              ><section class="panel">
                <div class="actions">
                  <el-button
                    v-if="auth.can('sources:sync') && row.local_path"
                    @click="sync('local')"
                    >仅同步本地目录</el-button
                  >
                </div>
                <el-table :data="snapshots"
                  ><el-table-column label="快照"
                    ><template #default="s"
                      >{{ date(s.row.created_at) }}
                      <div class="muted">{{ short(s.row.id) }}</div></template
                    ></el-table-column
                  >
                  <el-table-column label="来源与版本"
                    ><template #default="s"
                      >{{ sourceLabel(s.row.source) }}
                      <div class="muted">
                        {{
                          s.row.svn_revision
                            ? `r${s.row.svn_revision}`
                            : short(s.row.git_commit || s.row.content_hash)
                        }}
                      </div></template
                    ></el-table-column
                  >
                  <el-table-column label="状态"
                    ><template #default="s"
                      >{{ sourceLabel(s.row.status) }}
                      <div v-if="s.row.status === 'queued'" class="muted">
                        等待源码服务；不影响现有诊断
                      </div></template
                    ></el-table-column
                  >
                  <el-table-column label="文件"
                    ><template #default="s"
                      >{{ s.row.file_count }} ·
                      {{ Math.round(s.row.total_bytes / 1024) }} KiB</template
                    ></el-table-column
                  >
                  <el-table-column label="资料说明" min-width="200"
                    ><template #default="s"
                      ><div v-for="g in s.row.data_gaps" :key="g">
                        {{ sourceReason(g) }}
                      </div></template
                    ></el-table-column
                  >
                  <el-table-column label="操作"
                    ><template #default="s"
                      ><el-button
                        :disabled="!['ready', 'partial'].includes(s.row.status)"
                        @click="browse(s.row)"
                        >查看源码</el-button
                      ></template
                    ></el-table-column
                  ></el-table
                >
              </section></el-tab-pane
            >
            <el-tab-pane label="发布版本"
              ><section class="panel">
                <div class="actions">
                  <el-button
                    v-if="auth.can('sources:manage')"
                    @click="openRelease"
                    >登记发布</el-button
                  >
                  <el-button
                    v-if="auth.can('sources:manage')"
                    @click="manifestInput?.click()"
                    >导入发布清单</el-button
                  >
                  <input
                    ref="manifestInput"
                    type="file"
                    accept="application/json,.json"
                    hidden
                    @change="importManifest"
                  />
                </div>
                <el-table :data="releases"
                  ><el-table-column
                    prop="version"
                    label="版本"
                  /><el-table-column label="镜像" min-width="180"
                    ><template #default="s"
                      >{{ s.row.image_ref || "未登记" }}
                      <div class="muted">
                        {{ short(s.row.image_id) }}
                      </div></template
                    ></el-table-column
                  >
                  <el-table-column label="源码"
                    ><template #default="s">{{
                      (s.row.expected_revision
                        ? `r${s.row.expected_revision}`
                        : short(s.row.expected_commit || s.row.snapshot_id)) ||
                      "未关联"
                    }}</template></el-table-column
                  >
                  <el-table-column
                    prop="change_summary"
                    label="变更说明"
                    show-overflow-tooltip
                  /><el-table-column label="操作"
                    ><template #default="s"
                      ><el-button
                        v-if="auth.can('sources:manage') && !s.row.snapshot_id"
                        @click="attachRelease(s.row)"
                        >关联快照</el-button
                      ></template
                    ></el-table-column
                  ></el-table
                >
                <p class="muted">
                  构建来源为开发者登记信息；镜像运行身份在部署时单独核对。
                </p>
              </section></el-tab-pane
            >
            <el-tab-pane label="部署历史"
              ><section class="panel">
                <div class="actions">
                  <el-button
                    v-if="auth.can('sources:manage')"
                    @click="openDeployment"
                    >登记部署</el-button
                  >
                </div>
                <el-table :data="deployments"
                  ><el-table-column label="版本 / 环境"
                    ><template #default="s"
                      >{{ releaseName(s.row.release_id) }} ·
                      {{ s.row.environment }}</template
                    ></el-table-column
                  >
                  <el-table-column label="实例"
                    ><template #default="s"
                      ><router-link
                        :to="`/${s.row.container_id ? 'containers' : 'llm-endpoints'}/${s.row.container_id || s.row.llm_endpoint_id}`"
                        >{{
                          s.row.compose_service ||
                          s.row.binding.container_name ||
                          short(s.row.container_id || s.row.llm_endpoint_id)
                        }}</router-link
                      >
                      <div class="muted">
                        {{ s.row.compose_project }}
                      </div></template
                    ></el-table-column
                  >
                  <el-table-column label="部署时间"
                    ><template #default="s"
                      >{{ date(s.row.started_at) }}
                      <div class="muted">
                        {{ s.row.ended_at ? date(s.row.ended_at) : "尚未结束" }}
                      </div></template
                    ></el-table-column
                  >
                  <el-table-column label="登记时核验"
                    ><template #default="s"
                      >{{ sourceLabel(s.row.binding.status) }}
                      <div class="muted">
                        {{ sourceReason(s.row.binding.reason) }}
                      </div></template
                    ></el-table-column
                  >
                  <el-table-column label="操作"
                    ><template #default="s"
                      ><el-button
                        v-if="auth.can('sources:manage') && !s.row.ended_at"
                        @click="endDeployment(s.row)"
                        >结束记录</el-button
                      ></template
                    ></el-table-column
                  ></el-table
                >
              </section></el-tab-pane
            >
          </el-tabs>
        </template>
      </el-tab-pane>
      <el-tab-pane
        v-if="auth.can('resources:read')"
        label="服务监测"
        name="monitoring"
      >
        <ServiceObservability
          v-if="detailTab === 'monitoring'"
          ref="monitoringPanel"
          :key="id"
          :application-id="id"
          hide-refresh
        />
      </el-tab-pane>
      <el-tab-pane
        v-if="auth.can('resources:read')"
        label="数据库"
        name="databases"
      >
        <ServiceDatabases
          v-if="detailTab === 'databases'"
          :key="id"
          :application-id="id"
        />
      </el-tab-pane>
    </el-tabs>
  </template>

  <el-dialog
    :model-value="!!dialog"
    @update:model-value="closeDialog"
    :title="
      (
        {
          application: id ? '编辑服务与源码连接' : '登记服务',
          release: '登记发布版本',
          deployment: '登记实际部署',
          attach: '关联发布源码',
        } as Record<string, string>
      )[dialog]
    "
    width="min(720px, 94vw)"
  >
    <el-alert
      v-if="formError"
      :title="formError"
      type="error"
      :closable="false"
    />
    <el-form label-position="top" @submit.prevent>
      <template v-if="dialog === 'application'">
        <el-form-item
          v-for="field in fields"
          :key="field[0]"
          :label="field[1]"
          :required="field[0] !== 'owner'"
          ><ServiceOwnerSelect
            v-if="field[0] === 'owner'"
            v-model="form.owner" />
          <el-input v-else v-model="form[field[0]!]" :placeholder="field[2]"
        /></el-form-item>
        <SourceConnectionFields
          v-model="form"
          :has-git-token="row?.has_git_token"
          :has-svn-password="row?.has_svn_password"
        />
      </template>
      <template v-else-if="dialog === 'release'">
        <el-form-item label="发布版本" required
          ><el-input v-model="form.version"
        /></el-form-item>
        <el-form-item label="源码快照（可留空）"
          ><el-select v-model="form.snapshot_id" clearable
            ><el-option
              v-for="s in snapshots.filter((s) =>
                ['ready', 'partial'].includes(s.status),
              )"
              :key="s.id"
              :value="s.id"
              :label="`${sourceLabel(s.source)} · ${s.svn_revision ? `r${s.svn_revision}` : short(s.git_commit || s.content_hash)} · ${date(s.created_at)}`" /></el-select
        ></el-form-item>
        <el-form-item label="预期 Git commit（完整值，可留空）"
          ><el-input v-model="form.expected_commit"
        /></el-form-item>
        <el-form-item label="预期 SVN revision（版本号，可留空）">
          <el-input v-model="form.expected_revision" />
        </el-form-item>
        <el-form-item label="镜像名称"
          ><el-input v-model="form.image_ref"
        /></el-form-item>
        <el-form-item label="镜像 config ID（sha256:…）"
          ><el-input v-model="form.image_id"
        /></el-form-item>
        <el-form-item label="RepoDigest（仓库@sha256:…，可留空）"
          ><el-input v-model="form.repo_digest"
        /></el-form-item>
        <el-form-item label="变更交接说明"
          ><el-input v-model="form.change_summary" type="textarea" :rows="4"
        /></el-form-item>
        <el-form-item label="测试结果与已知限制"
          ><el-input v-model="form.test_result" type="textarea" :rows="3"
        /></el-form-item>
      </template>
      <template v-else-if="dialog === 'deployment'">
        <el-form-item label="发布版本" required
          ><el-select v-model="form.release_id"
            ><el-option
              v-for="r in releases"
              :key="r.id"
              :value="r.id"
              :label="r.version" /></el-select
        ></el-form-item>
        <el-form-item label="部署实例" required
          ><el-select v-model="form.target" filterable
            ><el-option
              v-for="t in targets"
              :key="t.id"
              :value="t.id"
              :label="t.name" /></el-select
        ></el-form-item>
        <el-form-item
          v-for="field in [
            ['environment', '环境'],
            ['compose_project', 'Compose 项目'],
            ['compose_service', 'Compose 服务'],
            ['config_version', '配置版本'],
            ['started_at', '部署开始时间（含时区）'],
          ]"
          :key="field[0]"
          :label="field[1]"
          :required="['environment', 'started_at'].includes(field[0]!)"
          ><el-input v-model="form[field[0]!]"
        /></el-form-item>
        <p class="muted">
          此操作只登记对应关系；历史部署与未取得运行身份的实例会标记为未核验。
        </p>
      </template>
      <template v-else-if="dialog === 'attach'"
        ><el-form-item label="源码快照" required
          ><el-select v-model="form.snapshot_id"
            ><el-option
              v-for="s in snapshots.filter((s) =>
                ['ready', 'partial'].includes(s.status),
              )"
              :key="s.id"
              :value="s.id"
              :label="`${sourceLabel(s.source)} · ${s.svn_revision ? `r${s.svn_revision}` : short(s.git_commit || s.content_hash)}`" /></el-select></el-form-item
      ></template>
    </el-form>
    <template #footer
      ><el-button @click="dialog = ''">取消</el-button
      ><el-button
        type="primary"
        :loading="busy"
        @click="dialog === 'attach' ? attach() : save()"
        >保存</el-button
      ></template
    >
  </el-dialog>

  <el-drawer v-model="sourceOpen" title="固定版本源码" size="min(1000px, 96vw)">
    <p>
      {{ sourceLabel(currentSnapshot?.source) }} ·
      {{
        currentSnapshot?.svn_revision
          ? `r${currentSnapshot.svn_revision}`
          : currentSnapshot?.git_commit || currentSnapshot?.content_hash
      }}
    </p>
    <el-alert
      v-if="currentSnapshot?.source === 'local'"
      title="本地目录快照为参考资料，尚未证明对应线上构建版本。"
      type="info"
      :closable="false"
    />
    <el-alert
      v-if="sourceError"
      :title="sourceError"
      type="error"
      :closable="false"
    />
    <div class="actions">
      <el-input
        v-model="fileFilter"
        placeholder="相对目录，默认 ."
        @keyup.enter="
          filePage = 1;
          loadFiles();
        "
      /><el-button
        @click="
          filePage = 1;
          loadFiles();
        "
        >查看目录</el-button
      >
    </div>
    <el-table
      :data="files"
      v-loading="sourceLoading"
      max-height="260"
      @row-click="(f: Row) => readFile(f.path)"
      ><el-table-column prop="path" label="文件路径" /><el-table-column
        prop="lines"
        label="行数"
        width="90"
    /></el-table>
    <el-pagination
      v-model:current-page="filePage"
      :page-size="100"
      :total="fileTotal"
      layout="prev, pager, next"
      @current-change="loadFiles"
    />
    <div class="actions">
      <el-input
        v-model="sourceQuery"
        placeholder="在当前目录搜索文本"
        @keyup.enter="searchSource"
      /><el-button @click="searchSource">搜索源码</el-button>
    </div>
    <p v-if="searchMessage" class="muted">{{ searchMessage }}</p>
    <el-table
      v-if="hits.length"
      :data="hits"
      max-height="200"
      @row-click="(h: Row) => readFile(h.path, Math.max(1, h.line - 10))"
      ><el-table-column prop="path" label="路径" /><el-table-column
        prop="line"
        label="行"
        width="65" /><el-table-column
        prop="text"
        label="匹配"
        show-overflow-tooltip
    /></el-table>
    <template v-if="fileData"
      ><h3>{{ filePath }}</h3>
      <p v-if="fileData.availability !== 'available'">
        {{ sourceReason(fileData.reason) }}
      </p>
      <template v-else>
        <p v-if="fileData.total_lines === 0" class="muted">文件为空</p>
        <div v-else class="actions">
          <el-button
            :disabled="fileStart === 1"
            @click="readFile(filePath, Math.max(1, fileStart - 200))"
            >上一段</el-button
          ><span
            >{{ fileStart }}–{{ fileData.end_line }} /
            {{ fileData.total_lines }}</span
          ><el-button
            :disabled="fileData.end_line >= fileData.total_lines"
            @click="readFile(filePath, fileStart + 200)"
            >下一段</el-button
          >
        </div>
        <pre
          class="source-code"
        ><code v-for="(line, i) in fileLines" :key="i"><span class="line-number">{{ fileStart + i }}</span>{{ line }}{{ '\n' }}</code></pre>
      </template>
    </template>
  </el-drawer>
</template>

<style scoped>
.page-heading .service-purpose {
  margin-top: 10px;
  font-size: 14px;
  color: var(--text-regular);
  white-space: pre-wrap;
}
.actions {
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.actions > .el-input {
  max-width: 420px;
}
.source-code {
  overflow: auto;
  padding: 14px;
  background: var(--el-fill-color-light);
  font-size: 12px;
  line-height: 1.8;
}
.line-number {
  display: inline-block;
  min-width: 4em;
  margin-right: 1em;
  color: var(--el-text-color-secondary);
  user-select: none;
}
.el-select {
  width: 100%;
}
.el-pagination {
  margin-top: 14px;
  margin-bottom: 14px;
}
</style>
