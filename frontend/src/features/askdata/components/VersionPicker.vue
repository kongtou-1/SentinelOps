<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { date } from "@/utils/display";
import {
  askdata,
  versionStatus,
  type AskDataConnection,
  type AskDataVersion,
} from "@/features/askdata/api";

const props = defineProps<{ modelValue?: string; refreshKey?: number }>();
const emit = defineEmits<{
  "update:modelValue": [value: string];
  change: [value: AskDataVersion | null];
  versions: [value: AskDataVersion[]];
  connection: [value: string];
}>();

const connections = ref<AskDataConnection[]>([]);
const versions = ref<AskDataVersion[]>([]);
const connectionId = ref("");
const error = ref("");
const busy = ref(false);
const current = computed(
  () => versions.value.find((item) => item.id === props.modelValue) || null,
);
const connection = computed(() =>
  connections.value.find((item) => item.id === connectionId.value),
);

function connectionLabel(row: AskDataConnection) {
  return `${row.name} · ${row.database}（${row.environment}）${row.enabled && row.query_enabled && row.askdata_enabled ? "" : " · 已停用"}`;
}
function versionState(row: AskDataVersion) {
  const source = connection.value;
  if (
    source &&
    (!source.enabled || !source.query_enabled || !source.askdata_enabled)
  )
    return "连接已停用";
  if (
    source &&
    (row.config_version !== source.config_version ||
      row.schema_hash !== source.schema_hash)
  )
    return "配置已变更";
  return row.active ? "当前启用" : versionStatus[row.status] || row.status;
}
function versionLabel(row: AskDataVersion) {
  return `${date(row.created_at)} · ${versionState(row)}`;
}
function selectVersion(value: unknown) {
  const id = String(value);
  emit("update:modelValue", id);
  emit("change", versions.value.find((item) => item.id === id) || null);
}
async function loadConnections() {
  busy.value = true;
  error.value = "";
  try {
    connections.value = await askdata.connections();
    if (!connectionId.value && connections.value.length)
      connectionId.value = (connections.value.find(
        (row) => row.enabled && row.query_enabled && row.askdata_enabled,
      ) || connections.value[0])!.id;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "数据库连接加载失败";
  } finally {
    busy.value = false;
  }
}
async function reload() {
  if (!connectionId.value) {
    versions.value = [];
    emit("versions", []);
    selectVersion("");
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const rows = await askdata.versions(connectionId.value);
    versions.value = rows;
    emit("versions", rows);
    const selected =
      rows.find((item) => item.id === props.modelValue) ||
      rows.find((item) => item.active) ||
      rows[0] ||
      null;
    if ((selected?.id || "") !== (props.modelValue || ""))
      emit("update:modelValue", selected?.id || "");
    emit("change", selected);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "模型版本加载失败";
    versions.value = [];
    emit("versions", []);
  } finally {
    busy.value = false;
  }
}
watch(connectionId, (value) => {
  emit("connection", value);
  void reload();
});
watch(
  () => props.refreshKey,
  () => void reload(),
);
onMounted(() => void loadConnections());
defineExpose({ reload, connectionId, versions, current });
</script>

<template>
  <div class="version-picker">
    <el-select
      v-model="connectionId"
      class="connection-select"
      placeholder="选择数据库连接"
      :loading="busy"
    >
      <el-option
        v-for="row in connections"
        :key="row.id"
        :label="connectionLabel(row)"
        :value="row.id"
      />
    </el-select>
    <el-select
      :model-value="modelValue || ''"
      class="version-select"
      placeholder="选择模型版本"
      :loading="busy"
      @update:model-value="selectVersion"
    >
      <el-option
        v-for="row in versions"
        :key="row.id"
        :label="versionLabel(row)"
        :value="row.id"
      />
    </el-select>
    <el-tag
      v-if="current"
      :type="versionState(current) === '当前启用' ? 'success' : 'info'"
      effect="plain"
    >
      {{ versionState(current) }}
    </el-tag>
    <el-button :loading="busy" @click="reload">刷新</el-button>
    <span v-if="error" class="picker-error">{{ error }}</span>
  </div>
</template>

<style scoped>
.version-picker {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.connection-select {
  width: min(320px, 100%);
}
.version-select {
  width: min(280px, 100%);
}
.picker-error {
  color: var(--danger, #f2557f);
  font-size: 12px;
}
</style>
