<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { api, all, type Models } from "@/api/client";
import { usePoll } from "@/composables/usePoll";
import { date, number, short } from "@/utils/display";
import {
  resourceSlices,
  serviceTitle,
} from "@/features/resources/model/containerServices";
import { summarizeService } from "@/features/applications/model/businessServices";
import { useAuth } from "@/features/auth/store";
import {
  sourceConnectionForm,
  sourceConnectionPayload,
} from "@/features/applications/model/sourceConnection";
import SourceConnectionFields from "@/features/applications/components/SourceConnectionFields.vue";
import ServiceOwnerSelect from "@/features/applications/components/ServiceOwnerSelect.vue";
import StatusTag from "@/components/common/StatusTag.vue";
import ServiceChart from "@/features/resources/components/ServiceChart.vue";
import ServiceHealthDetails from "@/features/resources/components/ServiceHealthDetails.vue";
const props = defineProps<{
    serverId?: string;
    containerId?: string;
    view?: "services" | "containers";
    hideRefresh?: boolean;
    hideCreate?: boolean;
  }>(),
  auth = useAuth(),
  route = useRoute(),
  router = useRouter();
const inventory = computed(() => props.view === "containers"),
  search = ref(""),
  state = ref("");
const emit = defineEmits<{
  synced: [];
  observed: [Models["ServerContainerOverview"] | undefined];
}>();
type Container = Models["ContainerResponse"];
type Service = Models["BusinessServiceResponse"];
type Overview = Models["ServerContainerOverview"];
const servers = ref<Models["ServerResponse"][]>([]),
  options = ref<Service[]>([]),
  selected = ref(
    props.serverId ||
      (route.path === "/containers" && typeof route.query.server_id === "string"
        ? route.query.server_id
        : ""),
  ),
  active = ref(
    route.path === "/containers" && typeof route.query.service_id === "string"
      ? route.query.service_id
      : "",
  ),
  data = ref<Overview>(),
  error = ref(""),
  loading = ref(false),
  saving = ref(false),
  page = ref(1),
  mode = ref<"create" | "edit" | "attach" | null>(null),
  editingId = ref(""),
  attachId = ref(""),
  form = ref({
    code: "",
    name: "",
    purpose: "",
    owner: "",
    depends_on: [] as string[],
    container_ids: [] as string[],
  });
const sourceForm = ref(sourceConnectionForm()),
  hasGitToken = ref(false),
  hasSvnPassword = ref(false),
  sourceLoading = ref(false),
  sourceError = ref("");
const canCreate = computed(
  () => !!selected.value && auth.can("resources:manage"),
);
const stateLabels: Record<string, string> = {
  healthy: "正常",
  failed: "异常",
  unknown: "无法判断",
};
function viewService(id: string) {
  if (route.path === "/containers") {
    active.value = id;
    return;
  }
  void router.push({
    path: "/containers",
    query: { server_id: selected.value, service_id: id },
  });
}
async function load() {
  loading.value = true;
  try {
    if (!servers.value.length)
      servers.value = await all<Models["ServerResponse"]>("/servers");
    selected.value ||= servers.value[0]?.id || "";
    if (!selected.value) return;
    const id = selected.value;
    const [next, profiles] = await Promise.all([
      api<Overview>(`/servers/${id}/container-overview`),
      all<Service>("/business-services"),
    ]);
    if (id !== selected.value) return;
    data.value = next;
    emit("observed", next);
    options.value = profiles;
    error.value = "";
    if (props.containerId && !focusInitialized) {
      active.value =
        groups.value.find((g) => g.container_ids.includes(props.containerId!))
          ?.id || "";
      focusInitialized = true;
    }
    if (active.value && !groups.value.some((g) => g.id === active.value))
      active.value = "";
  } catch (e) {
    data.value = undefined;
    emit("observed", undefined);
    error.value = e instanceof Error ? e.message : "服务加载失败";
  } finally {
    loading.value = false;
  }
}
let focusInitialized = false;
const { refresh } = usePoll(load, () =>
  mode.value || containerNote.value ? null : 30000,
);
watch(selected, () => {
  data.value = undefined;
  active.value = "";
  page.value = 1;
  void refresh();
});
watch(active, () => {
  page.value = 1;
});
const observations = computed(() => data.value?.observations || []);
const observationMap = computed(
  () => new Map(observations.value.map((o) => [o.container_id, o])),
);
const groups = computed(() => {
  const values = (data.value?.services || []).map((s) => ({
    ...s,
    unassigned: false,
    stats: summarizeService(s.container_ids, observations.value),
  }));
  const ids = data.value?.unassigned_container_ids || [];
  if (ids.length)
    values.push({
      id: "unassigned",
      code: "",
      name: "未归属服务的容器",
      purpose: "这些容器尚未归入业务服务，可选择容器后登记归属。",
      owner: "",
      depends_on: [],
      created_at: "",
      updated_at: "",
      container_ids: ids,
      unassigned: true,
      stats: summarizeService(ids, observations.value),
    });
  return values;
});
const current = computed(() => groups.value.find((g) => g.id === active.value));
function groupRows(ids: string[]) {
  const term = search.value.trim().toLowerCase();
  return (data.value?.containers || [])
    .filter((c) => ids.includes(c.id))
    .filter(
      (c) =>
        !term ||
        [c.name, c.image, c.service_name, c.purpose, c.docker_id].some(
          (value) => value?.toLowerCase().includes(term),
        ),
    )
    .filter(
      (c) =>
        !state.value ||
        (observationMap.value.get(c.id)?.state || "unknown") === state.value,
    )
    .sort((a, b) => a.name.localeCompare(b.name));
}
const containerGroups = computed(() =>
  (inventory.value
    ? current.value
      ? [current.value]
      : groups.value
    : current.value
      ? [current.value]
      : []
  )
    .map((group) => ({ ...group, rows: groupRows(group.container_ids) }))
    .filter(
      (group) =>
        !inventory.value ||
        (!search.value.trim() && !state.value) ||
        group.rows.length,
    ),
);
const scopeContainers = computed(() =>
  (data.value?.containers || []).filter(
    (c) => !current.value || current.value.container_ids.includes(c.id),
  ),
);
const detailRows = computed(() =>
  [...scopeContainers.value]
    .sort(
      (a, b) =>
        Number(observationMap.value.get(b.id)?.state === "running") -
          Number(observationMap.value.get(a.id)?.state === "running") ||
        a.name.localeCompare(b.name),
    )
    .slice((page.value - 1) * 9, page.value * 9),
);
const containerLinks = computed(() =>
  scopeContainers.value.flatMap((c) =>
    (c.depends_on || [])
      .filter((id) => scopeContainers.value.some((other) => other.id === id))
      .map((id) => ({ source: c.id, target: id })),
  ),
);
function slices(metric: "cpu" | "memory") {
  const rows = current.value
    ? scopeContainers.value.map((c) => ({
        name: `${serviceTitle(c)} · ${short(c.id)}`,
        value: observationMap.value.get(c.id)?.[
          metric === "cpu" ? "cpu_host_percent" : "memory_bytes"
        ],
      }))
    : groups.value.map((g) => ({
        name:
          g.name +
          (g.stats[metric].sampled < g.stats.total ? "（已采样部分）" : ""),
        value: g.stats[metric].value,
      }));
  const result = resourceSlices(
    metric === "cpu" ? 100 : data.value?.host_memory_total_bytes,
    metric === "cpu"
      ? data.value?.host_cpu_percent
      : data.value?.host_memory_used_bytes,
    rows,
  );
  return result?.map((s) =>
    s.name === "系统及其他占用" || s.name === "其他占用 / 未采样服务"
      ? {
          ...s,
          name: current.value
            ? "其他服务、系统及未采样占用"
            : "系统及未采样占用",
        }
      : s,
  );
}
const cpu = computed(() => slices("cpu")),
  memory = computed(() => slices("memory"));
const memoryPercent = computed(() =>
  data.value?.host_memory_total_bytes &&
  data.value.host_memory_used_bytes != null
    ? (100 * data.value.host_memory_used_bytes) /
      data.value.host_memory_total_bytes
    : null,
);
const graphServices = computed(() => groups.value.filter((g) => !g.unassigned));
const links = computed(() =>
  graphServices.value.flatMap((s) =>
    s.depends_on
      .filter((id) => graphServices.value.some((g) => g.id === id))
      .map((id) => ({ source: s.id, target: id })),
  ),
);
const dependencyNames = (ids: string[]) =>
  ids
    .map((id) => options.value.find((s) => s.id === id)?.name || "已退役服务")
    .join("、");
const eligible = computed(() =>
  (data.value?.containers || []).filter(
    (c) =>
      (data.value?.unassigned_container_ids || []).includes(c.id) ||
      (mode.value === "edit" && c.application_id === editingId.value),
  ),
);
function openCreate() {
  sourceForm.value = sourceConnectionForm();
  hasGitToken.value = false;
  hasSvnPassword.value = false;
  sourceError.value = "";
  editingId.value = "";
  form.value = {
    code: "",
    name: "",
    purpose: "",
    owner: "",
    depends_on: [],
    container_ids: [],
  };
  mode.value = "create";
}
async function openEdit(id: string) {
  const group = groups.value.find((g) => g.id === id);
  if (!group || group.unassigned) return;
  editingId.value = id;
  form.value = {
    code: group.code,
    name: group.name,
    purpose: group.purpose,
    owner: group.owner,
    depends_on: [...group.depends_on],
    container_ids: [...group.container_ids],
  };
  mode.value = "edit";
  await loadSourceConnection();
}
async function loadSourceConnection() {
  const id = editingId.value;
  sourceForm.value = sourceConnectionForm();
  hasGitToken.value = false;
  hasSvnPassword.value = false;
  sourceError.value = "";
  if (!auth.can("sources:manage")) return;
  sourceLoading.value = true;
  try {
    const application = await api<Models["ApplicationResponse"]>(
      `/applications/${id}`,
    );
    if (mode.value !== "edit" || editingId.value !== id) return;
    sourceForm.value = sourceConnectionForm(application);
    hasGitToken.value = application.has_git_token;
    hasSvnPassword.value = application.has_svn_password;
  } catch (e) {
    if (mode.value === "edit" && editingId.value === id)
      sourceError.value = e instanceof Error ? e.message : "源码连接加载失败";
  } finally {
    sourceLoading.value = false;
  }
}
function openAttach() {
  attachId.value = "";
  form.value.container_ids = [];
  mode.value = "attach";
}
async function save() {
  if (
    saving.value ||
    sourceLoading.value ||
    (mode.value !== "attach" && sourceError.value)
  )
    return;
  if (mode.value !== "edit" && !form.value.container_ids.length) {
    ElMessage.warning("请至少选择一个容器");
    return;
  }
  if (
    mode.value === "attach"
      ? !attachId.value
      : !form.value.name.trim() ||
        (mode.value === "create" && !form.value.code.trim())
  ) {
    ElMessage.warning("请填写服务信息");
    return;
  }
  saving.value = true;
  try {
    let result: Service;
    if (mode.value === "attach") {
      const previous =
        data.value?.services.find((s) => s.id === attachId.value)
          ?.container_ids || [];
      result = await api<Service>(
        `/servers/${selected.value}/business-services/${attachId.value}/containers`,
        "PUT",
        {
          container_ids: [
            ...new Set([...previous, ...form.value.container_ids]),
          ],
        },
      );
    } else {
      const { code, ...body } = form.value;
      const source = auth.can("sources:manage")
        ? { source_connection: sourceConnectionPayload(sourceForm.value) }
        : {};
      result =
        mode.value === "create"
          ? await api<Service>(
              `/servers/${selected.value}/business-services`,
              "POST",
              { ...body, ...source, code: code.trim() },
            )
          : await api<Service>(
              `/servers/${selected.value}/business-services/${editingId.value}`,
              "PUT",
              { ...body, ...source },
            );
    }
    mode.value = null;
    active.value = result.id;
    ElMessage.success("服务配置已保存");
    await refresh();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    saving.value = false;
  }
}
const containerNote = ref<Container>(),
  noteText = ref(""),
  noteName = ref(""),
  noteEdges = ref<string[]>([]);
function editNote(c: Container) {
  containerNote.value = c;
  noteText.value = c.purpose || "";
  noteName.value = c.service_name || "";
  noteEdges.value = [...(c.depends_on || [])];
}
async function saveNote() {
  if (!containerNote.value || saving.value) return;
  saving.value = true;
  try {
    const c = containerNote.value;
    await api(`/containers/${c.id}/service`, "PUT", {
      service_name: noteName.value,
      purpose: noteText.value,
      depends_on: noteEdges.value,
    });
    containerNote.value = undefined;
    await refresh();
    ElMessage.success("容器备注已保存");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    saving.value = false;
  }
}
async function syncContainers() {
  if (!selected.value || saving.value) return;
  saving.value = true;
  try {
    await api(`/servers/${selected.value}/sync-containers`, "POST");
    await refresh();
    ElMessage.success("容器清单已同步");
    emit("synced");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "容器同步失败");
  } finally {
    saving.value = false;
  }
}
defineExpose({ refresh, openCreate, canCreate, loading });
</script>
<template>
  <section
    class="panel business-workspace"
    :class="{ 'container-inventory': inventory }"
    v-loading="loading"
  >
    <div v-if="inventory" class="inventory-toolbar">
      <div class="inventory-controls">
        <el-select
          v-if="!serverId"
          v-model="selected"
          aria-label="选择服务所在服务器"
          placeholder="选择服务器"
        >
          <el-option
            v-for="s in servers"
            :key="s.id"
            :value="s.id"
            :label="s.name + ' · ' + s.host"
          />
        </el-select>
        <el-select
          v-model="active"
          filterable
          clearable
          aria-label="搜索业务服务"
          placeholder="搜索 / 选择服务"
          no-match-text="未找到服务"
        >
          <el-option
            v-for="group in groups"
            :key="group.id"
            :value="group.id"
            :label="group.name"
          />
        </el-select>
        <el-input
          v-model="search"
          clearable
          aria-label="搜索容器"
          placeholder="搜索容器名称 / 镜像 / 别名"
        />
        <el-select
          v-model="state"
          clearable
          aria-label="筛选容器当前状态"
          placeholder="全部运行状态"
        >
          <el-option
            v-for="item in [
              'running',
              'exited',
              'stopped',
              'restarting',
              'paused',
              'created',
              'dead',
              'unknown',
            ]"
            :key="item"
            :value="item"
            :label="
              {
                running: '运行中',
                exited: '已退出',
                stopped: '已停止',
                restarting: '重启中',
                paused: '已暂停',
                created: '已创建',
                dead: '故障',
                unknown: '未知 / 未采集',
              }[item]
            "
          />
        </el-select>
      </div>
      <div class="actions">
        <el-button v-if="!hideRefresh" @click="refresh">刷新</el-button>
        <el-button
          v-if="!hideCreate && canCreate"
          type="primary"
          @click="openCreate"
          >新建服务</el-button
        >
        <el-button
          v-if="selected && auth.can('containers:sync')"
          :loading="saving"
          @click="syncContainers"
          >同步容器清单</el-button
        >
      </div>
    </div>
    <p v-if="inventory && data" class="inventory-observed muted">
      观测时间：{{ date(data.collected_at) }}
    </p>
    <div v-if="!inventory" class="business-toolbar">
      <el-button v-if="current && !inventory" text @click="active = ''"
        >← 返回服务总览</el-button
      >
      <div class="actions">
        <el-select
          v-if="!serverId"
          v-model="selected"
          aria-label="选择服务所在服务器"
          placeholder="选择服务器"
          ><el-option
            v-for="s in servers"
            :key="s.id"
            :value="s.id"
            :label="s.name + ' · ' + s.host" /></el-select
        ><el-button v-if="!hideRefresh" @click="refresh">刷新</el-button>
        <el-button
          v-if="!hideCreate && canCreate"
          type="primary"
          @click="openCreate"
          >新建服务</el-button
        >
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div v-if="!selected" class="empty-hint">请先添加服务器并同步容器。</div>
    <template v-if="data">
      <div v-if="!inventory && !current" class="business-cards">
        <article
          v-for="s in groups"
          :key="s.id"
          class="business-card"
          :class="[
            'business-card-' + s.stats.status,
            { 'unassigned-card': s.unassigned },
          ]"
        >
          <div class="business-heading">
            <h3>{{ s.name }}</h3>
            <StatusTag
              :value="s.stats.status"
              :text="stateLabels[s.stats.status]"
            />
          </div>
          <p class="business-purpose">
            {{
              s.purpose || "尚未填写业务用途，可说明该服务为谁提供什么功能。"
            }}
          </p>
          <ServiceHealthDetails
            :stats="s.stats"
            :containers="data.containers"
          />
          <div class="business-usage">
            <div>
              <span>容器数量</span>
              <strong
                >{{ s.stats.total
                }}<span class="business-unit"> 个</span></strong
              >
              <small>{{ s.stats.running }} / {{ s.stats.total }} 运行中</small>
            </div>
            <div>
              <span>CPU 占服务器</span
              ><strong
                >{{ s.stats.cpu.value == null ? "—" : number(s.stats.cpu.value)
                }}<span v-if="s.stats.cpu.value != null" class="business-unit">
                  %</span
                ></strong
              ><small
                >采样 {{ s.stats.cpu.sampled }} / {{ s.stats.total }}</small
              >
            </div>
            <div>
              <span>内存合计</span
              ><strong
                >{{
                  s.stats.memory.value == null
                    ? "—"
                    : number(s.stats.memory.value / 1024 ** 2)
                }}<span
                  v-if="s.stats.memory.value != null"
                  class="business-unit"
                >
                  MiB</span
                ></strong
              ><small
                >采样 {{ s.stats.memory.sampled }} / {{ s.stats.total }}</small
              >
            </div>
          </div>
          <div class="business-card-footer">
            <div class="business-card-meta">
              <span v-if="s.unassigned">归属：待登记</span>
              <span v-else>负责人：{{ s.owner || "未设置" }}</span>
              <span v-if="s.depends_on.length">
                依赖：{{ dependencyNames(s.depends_on) }}
              </span>
            </div>
            <div class="actions">
              <el-button type="primary" plain @click="viewService(s.id)"
                >查看 {{ s.stats.total }} 个容器</el-button
              ><el-button
                v-if="!s.unassigned"
                @click="router.push('/applications/' + s.id)"
                >服务监测</el-button
              ><el-button
                v-if="auth.can('resources:manage')"
                text
                @click="s.unassigned ? openAttach() : openEdit(s.id)"
                >{{ s.unassigned ? "归入已有服务" : "编辑服务" }}</el-button
              >
            </div>
          </div>
        </article>
        <div v-if="!groups.length" class="empty-hint">
          还没有容器。请在服务器详情同步 Docker 清单。
        </div>
      </div>
      <div v-else-if="!inventory && current" class="business-focus">
        <div>
          <h3>{{ current.name }}</h3>
          <p class="business-purpose">
            {{ current.purpose || "尚未填写业务用途" }}
          </p>
          <p v-if="current.depends_on.length" class="form-hint">
            依赖业务服务：{{ dependencyNames(current.depends_on) }}
          </p>
        </div>
        <div class="actions">
          <StatusTag
            :value="current.stats.status"
            :text="stateLabels[current.stats.status]"
          /><el-button
            v-if="auth.can('resources:manage')"
            @click="current.unassigned ? openAttach() : openEdit(current.id)"
            >{{ current.unassigned ? "归入已有服务" : "编辑服务" }}</el-button
          ><router-link
            v-if="!current.unassigned && auth.can('sources:read')"
            :to="'/applications/' + current.id"
            class="subtle-link"
            >源码与发布 →</router-link
          >
        </div>
      </div>
      <ServiceHealthDetails
        v-if="!inventory && current"
        :stats="current.stats"
        :containers="data.containers"
      />
      <div v-if="!inventory" class="business-charts">
        <section>
          <h3>
            {{
              current ? "服务内 Docker 占服务器 CPU" : "各业务服务占服务器 CPU"
            }}
          </h3>
          <ServiceChart
            v-if="cpu"
            :slices="cpu"
            unit="percent"
            :used-percent="data.host_cpu_percent"
          />
          <div v-else class="empty-hint">暂无有效 CPU 分布数据</div>
        </section>
        <section>
          <h3>
            {{
              current ? "服务内 Docker 占服务器内存" : "各业务服务占服务器内存"
            }}
          </h3>
          <ServiceChart
            v-if="memory"
            :slices="memory"
            unit="bytes"
            :used-percent="memoryPercent"
          />
          <div v-else class="empty-hint">暂无有效内存分布数据</div>
        </section>
      </div>
      <p v-if="!inventory" class="form-hint">
        以整台服务器容量为
        100%。服务占用汇总已采样容器；缺失部分计入“系统及未采样占用”。CPU 为近 2
        分钟平均值，内存为工作集。点击服务查看容器明细。
      </p>
      <section v-if="!inventory && !current" class="business-relations">
        <h3>业务服务依赖</h3>
        <ServiceChart
          v-if="links.length"
          :nodes="graphServices.map((s) => ({ id: s.id, name: s.name }))"
          :links="links"
        />
        <p v-else class="empty-hint">
          尚未填写本机业务服务之间的依赖，可在“编辑服务”中维护。
        </p>
        <p class="form-hint">
          箭头从使用方指向依赖服务。关系图显示当前服务器的服务；完整依赖列在服务说明中。
        </p>
      </section>
      <section
        v-if="!inventory && current && containerLinks.length"
        class="business-relations"
      >
        <h3>服务内 Docker 连接关系</h3>
        <ServiceChart
          :nodes="
            scopeContainers.map((c) => ({ id: c.id, name: serviceTitle(c) }))
          "
          :links="containerLinks"
        />
        <p class="form-hint">
          箭头依据已登记的容器依赖。网络归属见下方明细，共享网络不等于真实调用。
        </p>
      </section>
      <p v-if="inventory && !containerGroups.length" class="empty-hint">
        {{
          groups.length
            ? "没有符合筛选条件的容器。"
            : "还没有容器，请同步所选服务器的容器清单。"
        }}
      </p>
      <section
        v-for="group in containerGroups"
        :key="group.id"
        class="business-container-panel"
      >
        <div class="business-heading">
          <div>
            <h3>{{ group.name }} › Docker 容器</h3>
            <p v-if="inventory" class="form-hint">
              共 {{ group.stats.total }} 个 · 运行 {{ group.stats.running }} ·
              其他状态
              {{
                group.stats.total - group.stats.running - group.stats.unknown
              }}
              <template v-if="group.stats.unknown">
                · {{ group.stats.unknown }} 个未取得运行状态</template
              >
            </p>
          </div>
          <div class="actions">
            <StatusTag
              v-if="inventory"
              :value="group.stats.status"
              :text="stateLabels[group.stats.status]"
            />
            <el-button
              v-if="inventory && auth.can('resources:manage')"
              text
              @click="group.unassigned ? openAttach() : openEdit(group.id)"
              >{{ group.unassigned ? "归入已有服务" : "编辑服务" }}</el-button
            >
            <span v-if="!inventory" class="muted"
              >{{ group.container_ids.length }} 个容器</span
            >
          </div>
        </div>
        <ServiceHealthDetails
          v-if="inventory"
          :stats="group.stats"
          :containers="data.containers"
        />
        <el-table
          :data="inventory ? group.rows : detailRows"
          :max-height="inventory ? 480 : undefined"
          row-key="id"
          empty-text="此服务暂无容器"
          ><el-table-column label="容器 / 镜像" min-width="220"
            ><template #default="{ row: c }"
              ><router-link :to="'/containers/' + c.id">{{
                c.name
              }}</router-link>
              <div class="form-hint">{{ c.image }}</div>
              <div v-if="c.service_name" class="form-hint">
                别名：{{ c.service_name }}
              </div></template
            ></el-table-column
          ><el-table-column label="当前状态" :width="inventory ? 155 : 110"
            ><template #default="{ row: c }"
              ><StatusTag
                :value="observationMap.get(c.id)?.state || 'unknown'"
              />
              <div v-if="inventory" class="form-hint">
                {{
                  observationMap.get(c.id)?.sampled_at
                    ? date(observationMap.get(c.id)?.sampled_at)
                    : "暂无实时状态采样"
                }}
              </div>
            </template></el-table-column
          ><el-table-column label="CPU 占主机" width="130"
            ><template #default="{ row: c }">{{
              observationMap.get(c.id)?.cpu_host_percent == null
                ? "—"
                : number(observationMap.get(c.id)?.cpu_host_percent) + "%"
            }}</template></el-table-column
          ><el-table-column label="内存" width="130"
            ><template #default="{ row: c }">{{
              observationMap.get(c.id)?.memory_bytes == null
                ? "—"
                : number(observationMap.get(c.id)!.memory_bytes! / 1024 ** 2) +
                  " MiB"
            }}</template></el-table-column
          ><el-table-column label="健康 / 网络" min-width="200"
            ><template #default="{ row: c }"
              ><div>
                {{
                  observationMap.get(c.id)?.health === "healthy"
                    ? "健康"
                    : observationMap.get(c.id)?.health === "unhealthy"
                      ? "异常"
                      : observationMap.get(c.id)?.health === "none"
                        ? "未配置健康检查"
                        : observationMap.get(c.id)?.health === "starting"
                          ? "启动检查中"
                          : observationMap.get(c.id)?.health === "stopped"
                            ? "已停止"
                            : "未采集"
                }}
              </div>
              <div class="form-hint">
                {{
                  observationMap.get(c.id)?.networks?.join("、") ||
                  "网络未采集 / 无网络"
                }}
              </div></template
            ></el-table-column
          ><el-table-column
            prop="purpose"
            label="容器备注"
            min-width="220"
          /><el-table-column
            v-if="auth.can('resources:manage')"
            label="备注"
            width="90"
            ><template #default="{ row: c }"
              ><el-button text type="primary" @click="editNote(c)"
                >编辑</el-button
              ></template
            ></el-table-column
          ></el-table
        ><el-pagination
          v-if="!inventory && scopeContainers.length > 9"
          v-model:current-page="page"
          :page-size="9"
          :total="scopeContainers.length"
          layout="prev, pager, next"
        />
      </section>
    </template>
  </section>
  <el-dialog
    :model-value="mode !== null"
    :title="
      mode === 'create'
        ? '新建业务服务'
        : mode === 'attach'
          ? '容器归入已有服务'
          : '编辑业务服务'
    "
    width="min(720px, 94vw)"
    @close="mode = null"
  >
    <el-form label-position="top" @submit.prevent="save"
      ><template v-if="mode !== 'attach'"
        ><div class="business-form-row">
          <el-form-item label="业务服务名称" required
            ><el-input
              v-model="form.name"
              maxlength="100"
              placeholder="例如：知识问答服务" /></el-form-item
          ><el-form-item label="服务标识" :required="mode === 'create'"
            ><el-input
              v-model="form.code"
              :disabled="mode === 'edit'"
              maxlength="100"
              placeholder="例如：knowledge-qa"
          /></el-form-item>
        </div>
        <el-form-item label="服务用途"
          ><el-input
            v-model="form.purpose"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="说明整个业务服务的功能，容器是它的组成部分" /></el-form-item
        ><el-form-item label="负责人"
          ><ServiceOwnerSelect
            v-if="mode === 'create' || mode === 'edit'"
            v-model="form.owner" /></el-form-item
        ><el-form-item label="依赖的业务服务"
          ><el-select
            v-model="form.depends_on"
            multiple
            filterable
            collapse-tags
            style="width: 100%"
            ><el-option
              v-for="s in options.filter((s) => s.id !== editingId)"
              :key="s.id"
              :value="s.id"
              :label="s.name" /></el-select></el-form-item></template
      ><el-form-item v-else label="选择已有业务服务" required
        ><el-select v-model="attachId" filterable style="width: 100%"
          ><el-option
            v-for="s in options"
            :key="s.id"
            :value="s.id"
            :label="s.name + ' · ' + s.code" /></el-select></el-form-item
      ><el-form-item label="此服务器上组成服务的 Docker 容器"
        ><el-select
          v-model="form.container_ids"
          multiple
          filterable
          collapse-tags
          collapse-tags-tooltip
          style="width: 100%"
          ><el-option
            v-for="c in eligible"
            :key="c.id"
            :value="c.id"
            :label="c.name + ' · ' + short(c.id)" /></el-select
      ></el-form-item>
      <p class="form-hint">
        每个容器只归属一个服务，避免重复统计。编辑时移出的容器会回到“未归属”；其他服务器的归属保持不变。
      </p>
      <template v-if="mode !== 'attach' && auth.can('sources:manage')">
        <el-divider content-position="left"
          >代码仓库与源码连接（选填）</el-divider
        >
        <p class="form-hint">
          配置此服务的代码来源。保存后可在“源码与发布”同步源码、登记发布和部署版本。
        </p>
        <el-alert
          v-if="sourceError"
          :title="sourceError"
          type="error"
          :closable="false"
        />
        <el-button
          v-if="sourceError"
          link
          type="primary"
          @click="loadSourceConnection"
          >重新加载源码连接</el-button
        >
        <el-skeleton v-if="sourceLoading" :rows="3" animated />
        <SourceConnectionFields
          v-else-if="!sourceError"
          :model-value="sourceForm"
          :has-git-token="hasGitToken"
          :has-svn-password="hasSvnPassword"
        />
      </template>
    </el-form>
    <template #footer
      ><el-button @click="mode = null">取消</el-button
      ><el-button
        type="primary"
        :loading="saving"
        :disabled="sourceLoading || (mode !== 'attach' && !!sourceError)"
        @click="save"
        >保存</el-button
      ></template
    >
  </el-dialog>
  <el-dialog
    :model-value="!!containerNote"
    title="编辑容器备注"
    width="520px"
    @close="containerNote = undefined"
    ><el-form label-position="top"
      ><el-form-item label="容器别名"
        ><el-input v-model="noteName" maxlength="100" /></el-form-item
      ><el-form-item label="容器用途备注"
        ><el-input
          v-model="noteText"
          type="textarea"
          :rows="4"
          maxlength="2000"
      /></el-form-item>
      <el-form-item label="依赖的 Docker 容器（技术连接）"
        ><el-select v-model="noteEdges" multiple filterable style="width: 100%"
          ><el-option
            v-for="c in scopeContainers.filter(
              (c) => c.id !== containerNote?.id,
            )"
            :key="c.id"
            :value="c.id"
            :label="
              c.name + ' · ' + short(c.id)
            " /></el-select></el-form-item></el-form
    ><template #footer
      ><el-button @click="containerNote = undefined">取消</el-button
      ><el-button type="primary" :loading="saving" @click="saveNote"
        >保存</el-button
      ></template
    ></el-dialog
  >
</template>
<style scoped>
.business-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.business-toolbar > .actions {
  margin-left: auto;
}
.business-toolbar .el-select {
  width: 260px;
}
.business-heading,
.business-focus {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.business-heading h3,
.business-focus h3 {
  margin: 0;
  font-size: 17px;
}
.business-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr));
  gap: 16px;
}
.business-card {
  display: grid;
  grid-template-rows: subgrid;
  grid-row: span 5;
  gap: 16px;
  border: 1px solid var(--border);
  border-top-width: 3px;
  border-radius: 12px;
  padding: 20px;
  min-width: 0;
}
.business-card-healthy {
  border-top-color: var(--el-color-success);
}
.business-card-unknown {
  border-top-color: var(--el-color-warning);
}
.business-card-failed {
  border-top-color: var(--el-color-danger);
}
.business-card .business-heading {
  flex-wrap: nowrap;
  align-items: flex-start;
  gap: 12px;
}
.business-card .business-heading h3 {
  min-width: 0;
  overflow-wrap: anywhere;
  line-height: 1.5;
}
.business-card .business-heading :deep(.el-tag) {
  flex-shrink: 0;
  margin-top: 2px;
}
.unassigned-card {
  border-style: dashed;
}
.business-purpose {
  color: var(--muted);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.7;
}
.business-card .business-purpose {
  margin: 0;
  font-size: 13px;
  color: var(--text-regular);
}
.business-card :deep(.service-health-details) {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--surface-soft);
  align-self: start;
}
.business-usage {
  display: grid;
  grid-template-columns: 0.85fr 1fr 1.25fr;
  gap: 12px;
  padding: 16px 0;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}
.business-usage div {
  display: grid;
  align-content: start;
  min-width: 0;
  gap: 6px;
}
.business-usage > div > span,
.business-usage small {
  color: var(--muted);
  font-size: 12px;
}
.business-usage strong {
  font-size: 22px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  line-height: 1.4;
}
.business-unit {
  font-size: 12px;
  font-weight: 400;
  color: var(--muted);
}
.business-card-footer {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 16px;
  min-width: 0;
}
.business-card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.business-card .actions {
  margin-top: 0;
  justify-content: space-between;
  gap: 8px;
}
.business-card .actions > .el-button {
  max-width: 100%;
}
.business-charts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 24px;
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--border);
}
.business-charts h3 {
  font-size: 15px;
  margin: 0 0 12px;
}
.business-relations,
.business-container-panel {
  margin-top: 24px;
  border-top: 1px solid var(--border);
  padding-top: 20px;
}
.business-container-panel .el-pagination {
  margin-top: 16px;
}
.inventory-toolbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 16px;
}
.inventory-controls {
  display: grid;
  grid-template-columns: 1.1fr 1fr 1.4fr 0.8fr;
  gap: 12px;
}
.inventory-controls > * {
  min-width: 0;
  width: 100%;
}
.inventory-toolbar > .actions {
  margin: 0;
  flex-wrap: nowrap;
}
.inventory-observed {
  margin: 10px 0 0;
  text-align: right;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.container-inventory .business-container-panel {
  margin-top: 20px;
}
.business-focus {
  padding: 16px 20px;
  border: 1px solid var(--border);
  border-radius: 12px;
}
.business-focus > div {
  min-width: 0;
}
.business-form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
@media (max-width: 1100px) {
  .inventory-toolbar {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }
  .inventory-toolbar > .actions {
    justify-content: flex-end;
  }
}
@media (max-width: 760px) {
  .business-card {
    padding: 16px;
  }
  .business-usage {
    gap: 8px;
  }
  .business-usage strong {
    font-size: 20px;
  }
  .inventory-controls {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .inventory-toolbar > .actions {
    flex-wrap: wrap;
  }
  .business-charts,
  .business-form-row {
    grid-template-columns: minmax(0, 1fr);
  }
  .business-toolbar > .actions {
    width: 100%;
  }
  .business-toolbar .el-select {
    width: 100%;
  }
  .business-focus {
    padding: 16px;
  }
}
@media (max-width: 420px) {
  .business-usage {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    row-gap: 16px;
  }
  .business-usage > div:last-child {
    grid-column: 1 / -1;
  }
}
</style>
