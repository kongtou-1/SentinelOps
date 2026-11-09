<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api } from "@/api/client";
import {
  askdata,
  sourceOriginLabels,
  type AskDataConnection,
  type AskDataSourceSnapshotPage,
} from "@/features/askdata/api";
import VersionPicker from "@/features/askdata/components/VersionPicker.vue";

const versionId = ref("");
const connectionId = ref("");
const refreshKey = ref(0);
const snapshots = ref<AskDataSourceSnapshotPage>({
  items: [],
  recommended_id: null,
  origin: "none",
  label: "",
  environment: "",
});
const snapshotId = ref("");
const connections = ref<AskDataConnection[]>([]);
const application = ref<{ id: string; name: string } | null>(null);
const busy = ref(false);
const error = ref("");

const selected = computed(
  () => connections.value.find((item) => item.id === connectionId.value) || null,
);
const snapshotLabel = computed(() =>
  snapshots.value.origin === "release" ? "发布版本关联快照（推荐）" : "不自动关联源码",
);

async function loadConnections() {
  try {
    connections.value = await askdata.connections();
  } catch {
    connections.value = [];
  }
}

async function loadApplication() {
  application.value = null;
  const id = selected.value?.application_id;
  if (!id) return;
  try {
    const row = await api<{ id: string; name: string }>(`/applications/${id}`);
    application.value = { id: row.id, name: row.name };
  } catch {
    application.value = { id, name: id.slice(0, 8) };
  }
}

async function loadSnapshots() {
  snapshotId.value = "";
  if (!connectionId.value) {
    snapshots.value = {
      items: [],
      recommended_id: null,
      origin: "none",
      label: "",
      environment: "",
    };
    return;
  }
  try {
    snapshots.value = await askdata.sourceSnapshots(connectionId.value);
  } catch {
    snapshots.value = {
      items: [],
      recommended_id: null,
      origin: "none",
      label: "",
      environment: "",
    };
  }
}

async function build() {
  if (!connectionId.value) return;
  busy.value = true;
  error.value = "";
  try {
    await askdata.createVersion(
      connectionId.value,
      snapshotId.value ? { source_snapshot_id: snapshotId.value } : {},
    );
    ElMessage.success("语义构建任务已提交");
    refreshKey.value += 1;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "构建提交失败";
  } finally {
    busy.value = false;
  }
}

watch(connectionId, async () => {
  await loadApplication();
  await loadSnapshots();
});
void loadConnections();
</script>

<template>
  <section class="applications">
    <div class="heading">
      <h2>应用与源码</h2>
      <el-button type="primary" :disabled="!connectionId || busy" :loading="busy" @click="build">
        使用所选快照构建
      </el-button>
    </div>
    <VersionPicker v-model="versionId" :refresh-key="refreshKey" @connection="connectionId = $event" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-descriptions :column="2" border>
      <el-descriptions-item label="被监测应用">
        <router-link v-if="application" :to="`/applications/${application.id}`">
          {{ application.name }}
        </router-link>
        <span v-else>未绑定应用（仅使用数据库）</span>
      </el-descriptions-item>
      <el-descriptions-item label="数据库连接">
        {{ selected ? `${selected.name} · ${selected.database}` : "—" }}
      </el-descriptions-item>
      <el-descriptions-item label="目录范围">沿用连接的只读授权范围</el-descriptions-item>
      <el-descriptions-item label="固定版本快照">
        <el-select v-model="snapshotId" class="snapshot" :disabled="!connectionId">
          <el-option :label="snapshotLabel" value="" />
          <el-option
            v-for="item in snapshots.items"
            :key="item.id"
            :label="`${item.label}${item.recommended ? ' · 推荐' : ''} · ${sourceOriginLabels[item.origin] || item.origin}`"
            :value="item.id"
          />
        </el-select>
      </el-descriptions-item>
    </el-descriptions>
    <p class="hint">
      源码仅在构建时按固定快照只读解析，不执行目标仓库程序。应用绑定请在数据库连接中维护。
    </p>
  </section>
</template>

<style scoped>
.applications { display: grid; gap: 12px; min-width: 0; }
.heading { display: flex; align-items: center; justify-content: space-between; }
h2 { margin: 0; font-size: 18px; }
.snapshot { width: 100%; }
.hint { margin: 0; color: var(--muted, #8993a4); font-size: 12px; }
</style>
