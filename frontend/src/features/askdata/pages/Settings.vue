<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, defineAsyncComponent, onMounted, ref } from "vue";
import { askdata, type AskDataConnection } from "@/features/askdata/api";

const activeTab = ref("connections");
const connections = ref<AskDataConnection[]>([]);
const connectionId = ref("");
const error = ref("");

const ServiceDatabases = defineAsyncComponent(
  () => import("@/features/applications/components/ServiceDatabases.vue"),
);
const SemanticBuilds = defineAsyncComponent(
  () => import("@/features/askdata/components/SemanticBuilds.vue"),
);
const SettingsApplications = defineAsyncComponent(
  () => import("@/features/askdata/components/SettingsApplications.vue"),
);
const SettingsAssets = defineAsyncComponent(
  () => import("@/features/askdata/components/SettingsAssets.vue"),
);
const SettingsExecution = defineAsyncComponent(
  () => import("@/features/askdata/components/SettingsExecution.vue"),
);
const SettingsInterchange = defineAsyncComponent(
  () => import("@/features/askdata/components/SettingsInterchange.vue"),
);

const needsConnection = computed(() =>
  [
    "zones",
    "tags",
    "rules",
    "sensitive",
    "supplements",
    "execution",
    "interchange",
  ].includes(activeTab.value),
);

function connectionLabel(row: AskDataConnection) {
  return `${row.name} · ${row.database}（${row.environment}）${row.enabled && row.query_enabled && row.askdata_enabled ? "" : " · 已停用"}`;
}

async function loadConnections() {
  try {
    connections.value = await askdata.connections();
    if (!connectionId.value && connections.value.length) {
      connectionId.value = (connections.value.find(
        (row) => row.enabled && row.query_enabled && row.askdata_enabled,
      ) || connections.value[0])!.id;
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : "数据库连接加载失败";
  }
}

onMounted(() => void loadConnections());
</script>

<template>
  <section class="askdata-settings">
    <div class="page-heading">
      <h1>问数设置</h1>
      <PageIntro />
      <el-select
        v-if="needsConnection"
        v-model="connectionId"
        class="connection"
        placeholder="选择数据库连接"
      >
        <el-option
          v-for="row in connections"
          :key="row.id"
          :label="connectionLabel(row)"
          :value="row.id"
        />
      </el-select>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-tabs v-model="activeTab">
      <el-tab-pane label="数据库连接" name="connections">
        <ServiceDatabases ask-data />
      </el-tab-pane>
      <el-tab-pane label="应用与源码" name="applications" lazy>
        <SettingsApplications />
      </el-tab-pane>
      <el-tab-pane label="业务分区" name="zones" lazy>
        <SettingsAssets
          v-if="connectionId"
          kind="zones"
          :connection-id="connectionId"
        />
        <el-empty v-else description="请选择数据库连接" />
      </el-tab-pane>
      <el-tab-pane label="标签管理" name="tags" lazy>
        <SettingsAssets
          v-if="connectionId"
          kind="tags"
          :connection-id="connectionId"
        />
        <el-empty v-else description="请选择数据库连接" />
      </el-tab-pane>
      <el-tab-pane label="业务规则" name="rules" lazy>
        <SettingsAssets
          v-if="connectionId"
          kind="rules"
          :connection-id="connectionId"
        />
        <el-empty v-else description="请选择数据库连接" />
      </el-tab-pane>
      <el-tab-pane label="敏感数据" name="sensitive" lazy>
        <SettingsAssets
          v-if="connectionId"
          kind="sensitive"
          :connection-id="connectionId"
        />
        <el-empty v-else description="请选择数据库连接" />
      </el-tab-pane>
      <el-tab-pane label="补充定义" name="supplements" lazy>
        <SettingsAssets
          v-if="connectionId"
          kind="supplements"
          :connection-id="connectionId"
        />
        <el-empty v-else description="请选择数据库连接" />
      </el-tab-pane>
      <el-tab-pane label="语义构建" name="builds" lazy>
        <SemanticBuilds />
      </el-tab-pane>
      <el-tab-pane label="模型与执行" name="execution" lazy>
        <SettingsExecution v-if="connectionId" :connection-id="connectionId" />
        <el-empty v-else description="请选择数据库连接" />
      </el-tab-pane>
      <el-tab-pane label="导入导出" name="interchange" lazy>
        <SettingsInterchange
          v-if="connectionId"
          :connection-id="connectionId"
        />
        <el-empty v-else description="请选择数据库连接" />
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<style scoped>
.askdata-settings {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  min-width: 0;
  gap: 16px;
}
.page-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
h1 {
  margin: 0;
  font-size: 24px;
}
.connection {
  width: 300px;
}
</style>
