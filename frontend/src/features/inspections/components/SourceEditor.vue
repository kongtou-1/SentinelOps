<script setup lang="ts">
import { computed, ref, toRaw, watch } from "vue";
import { all, api, type Models } from "@/api/client";
import {
  emptySource,
  errorText,
  type DataSource,
} from "@/features/inspections/ui";
import {
  newRequestDraft,
  requestHeaders,
  apiSource,
} from "@/features/inspections/request";
import { databaseSource } from "@/features/inspections/database";
import InspectionSourceFields from "./InspectionSourceFields.vue";
const open = defineModel<boolean>({ required: true });
const props = defineProps<{
  source?: DataSource;
  initialKind?: "api" | "database";
}>();
const emit = defineEmits<{ saved: [source: DataSource] }>();
const defaults = (): Models["InspectionDataSourceInput"] => ({
  name: "",
  environment: "test",
  application_id: null,
  enabled: true,
  source_kind: props.initialKind || "api",
  source_config: { ...emptySource(), response_format: "json" },
});
const form = ref(defaults()),
  request = ref(newRequestDraft()),
  tab = ref("basic"),
  error = ref(""),
  ready = ref(false),
  headersConfigured = ref(false),
  busy = ref(false);
const sourceLabel = computed(() =>
  form.value.source_kind === "api" ? "API" : "数据库查询",
);
const services = ref<Models["ApplicationResponse"][]>([]),
  databases = ref<Models["DatabaseResponse"][]>([]),
  sqlParameters = ref("{}");
watch(open, async (value) => {
  if (!value) return;
  const row = props.source;
  ready.value = false;
  headersConfigured.value = Boolean(row?.headers_configured);
  form.value = row
    ? {
        name: row.name,
        environment: row.environment,
        application_id: row.application_id,
        enabled: row.enabled,
        source_kind: row.source_kind as "api" | "database",
        source_config: structuredClone(toRaw(row.source_config)),
        expected_version: row.version,
      }
    : defaults();
  sqlParameters.value = JSON.stringify(
    form.value.source_config.query_parameters || {},
    null,
    2,
  );
  request.value = newRequestDraft(
    Boolean(row?.headers_configured),
    row?.source_config.url,
  );
  error.value = "";
  tab.value = "basic";
  try {
    [services.value, databases.value] = await Promise.all([
      all<Models["ApplicationResponse"]>("/business-services"),
      api<Models["DatabaseResponse"][]>("/inspection-connections"),
    ]);
    if (row?.source_config.http_connection_id) {
      const connections = await all<Models["InspectionHTTPConnectionResponse"]>(
        "/inspection-http-connections",
      );
      const connection = connections.find(
        (c) => c.id === row.source_config.http_connection_id,
      );
      if (!connection) throw new Error("原 HTTP 连接不可用，无法加载 API 配置");
      form.value.source_config.url = connection.base_url;
      form.value.source_config.http_connection_id = null;
      headersConfigured.value = Boolean(connection.headers_configured);
      request.value = newRequestDraft(
        Boolean(connection.headers_configured),
        connection.base_url,
      );
    }
    ready.value = true;
  } catch (e) {
    error.value = errorText(e);
  }
});
async function save() {
  if (!ready.value || busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    if (
      form.value.source_kind === "database" &&
      form.value.source_config.query_sql
    ) {
      const parameters = JSON.parse(sqlParameters.value || "{}");
      if (
        !parameters ||
        typeof parameters !== "object" ||
        Array.isArray(parameters)
      )
        throw new Error("SQL 参数需要有效的 JSON 对象");
      form.value.source_config.query_parameters = parameters;
    }
    const config =
      form.value.source_kind === "api"
        ? apiSource(form.value.source_config)
        : databaseSource(form.value.source_config, databases.value);
    const saved = await api<DataSource>(
      props.source
        ? `/inspection-data-sources/${props.source.id}`
        : "/inspection-data-sources",
      props.source ? "PUT" : "POST",
      {
        ...form.value,
        source_config: config,
        headers:
          form.value.source_kind === "api"
            ? form.value.source_config.http_connection_id
              ? undefined
              : requestHeaders(request.value)
            : undefined,
      },
    );
    emit("saved", saved);
    open.value = false;
  } catch (e) {
    error.value = errorText(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <el-drawer
    v-model="open"
    :title="`${source ? '编辑' : '新增'}${sourceLabel}`"
    size="min(800px, 100vw)"
    class="inspection-drawer"
    destroy-on-close
    :close-on-click-modal="false"
  >
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-form label-position="top"
      ><el-tabs v-model="tab">
        <el-tab-pane label="基本信息" name="basic">
          <div class="inspection-grid">
            <el-form-item :label="`${sourceLabel}名称`" required
              ><el-input v-model="form.name" maxlength="100"
            /></el-form-item>
            <el-form-item label="环境" required
              ><el-input v-model="form.environment"
            /></el-form-item>
          </div>
          <el-form-item label="关联服务（可选）"
            ><el-select
              v-model="form.application_id"
              clearable
              :value-on-clear="null"
              filterable
              placeholder="不关联服务"
              ><el-option
                v-for="s in services"
                :key="s.id"
                :label="s.name"
                :value="s.id" /></el-select></el-form-item
          ><el-form-item v-if="source" label="允许巡检任务使用"
            ><el-radio-group v-model="form.enabled"
              ><el-radio-button :value="true">允许使用</el-radio-button
              ><el-radio-button :value="false"
                >禁止使用</el-radio-button
              ></el-radio-group
            ></el-form-item
          ></el-tab-pane
        >
        <el-tab-pane
          :label="
            form.source_kind === 'api'
              ? '接口地址与请求设置'
              : '选择数据表与字段'
          "
          name="query"
          ><InspectionSourceFields
            v-model:source="form.source_config"
            v-model:request="request"
            :kind="form.source_kind"
            :databases="
              databases.filter(
                (d) =>
                  d.environment === form.environment &&
                  (!form.application_id ||
                    !d.application_id ||
                    d.application_id === form.application_id),
              )
            "
            :headers-configured="headersConfigured"
            ><template #sql-parameters
              ><el-form-item label="SQL 参数（JSON）"
                ><el-input
                  v-model="sqlParameters"
                  type="textarea"
                  :rows="3" /></el-form-item></template></InspectionSourceFields
        ></el-tab-pane> </el-tabs
    ></el-form>
    <template #footer
      ><el-button @click="open = false">取消</el-button
      ><el-button v-if="tab === 'basic'" type="primary" @click="tab = 'query'"
        >下一步</el-button
      ><el-button
        v-else
        type="primary"
        :loading="busy"
        :disabled="!ready"
        @click="save"
        >保存{{ sourceLabel }}</el-button
      ></template
    >
  </el-drawer>
</template>
