<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Models } from "@/api/client";
import { apiSource, type RequestDraft } from "@/features/inspections/request";

const props = defineProps<{
  kind: "api" | "database";
  databases: Models["DatabaseResponse"][];
  headersConfigured?: boolean;
  related?: boolean;
}>();
const source = defineModel<Models["InspectionSource"]>("source", {
  required: true,
});
const request = defineModel<RequestDraft>("request", { required: true });
const urlError = ref("");
const queryParams = computed(() => source.value.query_params || []);
function splitUrl() {
  try {
    source.value = apiSource(source.value);
    urlError.value = "";
  } catch (e) {
    urlError.value = (e as Error).message;
  }
}
function addParameter() {
  source.value.query_params = [...queryParams.value, { name: "", value: "" }];
}
const connection = computed(() =>
  props.databases.find((d) => d.id === source.value.connection_id),
);
const tables = computed(() =>
  Object.keys(connection.value?.allowed_tables || {}),
);
const columns = computed(
  () => connection.value?.allowed_tables[source.value.table] || [],
);
const fields = [
  ["id_column", "对象唯一标识"],
  ["version_column", "版本字段"],
  ["time_column", "更新时间字段"],
  ["decision_column", "原结论字段"],
] as const;
function resetMappings() {
  for (const [key] of fields) source.value[key] = "";
  source.value.fact_columns = [];
  source.value.naive_time_is_utc = false;
}
watch(
  columns,
  (available) => {
    for (const [key] of fields) {
      if (!available.includes(source.value[key])) source.value[key] = "";
    }
    source.value.fact_columns = (source.value.fact_columns || []).filter((c) =>
      available.includes(c),
    );
  },
  { immediate: true },
);
</script>

<template>
  <div v-if="kind === 'api'" class="api-source-fields">
    <el-tag v-if="source.http_connection_id" type="success"
      >使用连接中保存的地址与认证</el-tag
    >
    <el-form-item
      v-if="!source.http_connection_id"
      label="API地址"
      required
      :error="urlError"
    >
      <el-input
        v-model="source.url"
        placeholder="https://api.example.com/records"
        @blur="splitUrl"
      >
        <template #prepend>{{ source.request_method || "GET" }}</template>
      </el-input>
    </el-form-item>
    <el-form-item label="允许Agent调用此API">
      <el-switch
        v-model="source.agent_api_enabled"
        active-text="允许"
        inactive-text="不允许"
      />
    </el-form-item>
    <template
      v-if="
        source.agent_api_enabled ||
        source.http_connection_id ||
        source.path_template
      "
    >
      <el-form-item label="请求方式" required>
        <el-select v-model="source.request_method"
          ><el-option label="GET" value="GET" /><el-option
            label="POST 查询"
            value="POST"
        /></el-select>
      </el-form-item>
      <el-form-item label="动态路径模板"
        ><el-input
          v-model="source.path_template"
          placeholder="如 projects/{id}/detail"
      /></el-form-item>
      <el-form-item label="允许动态查询参数"
        ><el-select
          v-model="source.allowed_query_params"
          multiple
          filterable
          allow-create
          default-first-option
      /></el-form-item>
      <el-form-item
        v-if="source.request_method === 'POST'"
        label="允许动态请求体字段"
        ><el-select
          v-model="source.allowed_body_fields"
          multiple
          filterable
          allow-create
          default-first-option
      /></el-form-item>
    </template>
    <el-form-item label="查询参数">
      <div class="request-fields">
        <div v-if="queryParams.length" class="request-row">
          <span>参数名</span><span>参数值</span><span>操作</span>
        </div>
        <div
          v-for="(param, index) in queryParams"
          :key="index"
          class="request-row"
        >
          <el-input
            v-model="param.name"
            placeholder="参数名，如 status"
            :aria-label="`查询参数 ${index + 1} 名称`"
          />
          <el-input
            v-model="param.value"
            placeholder="参数值，如 completed"
            :aria-label="`查询参数 ${index + 1} 值`"
          />
          <el-button
            type="danger"
            link
            @click="source.query_params!.splice(index, 1)"
            >移除</el-button
          >
        </div>
        <el-button :disabled="queryParams.length >= 20" @click="addParameter"
          >添加查询参数</el-button
        >
      </div>
    </el-form-item>
    <template v-if="!source.http_connection_id">
      <el-form-item label="认证方式">
        <div class="request-fields">
          <el-select v-model="request.auth">
            <el-option
              v-if="headersConfigured"
              label="保留已保存的认证与请求头"
              value="keep"
            />
            <el-option
              :label="headersConfigured ? '清除已保存认证 / 无认证' : '无认证'"
              value="none"
            />
            <el-option label="Bearer Token" value="bearer" />
            <el-option label="Basic Auth（用户名与密码）" value="basic" />
            <el-option label="API Key（请求头）" value="api_key" />
          </el-select>
          <el-tag
            v-if="request.auth === 'keep'"
            :type="source.url === request.savedUrl ? 'success' : 'warning'"
          >
            {{
              source.url === request.savedUrl
                ? "认证与请求头已加密保存"
                : "地址已变更，旧认证与请求头将清除"
            }}
          </el-tag>
          <el-tag v-else-if="headersConfigured" type="warning"
            >保存后替换原认证与请求头</el-tag
          >
        </div>
      </el-form-item>
      <el-form-item v-if="request.auth === 'bearer'" label="Token" required>
        <el-input
          v-model="request.token"
          type="password"
          autocomplete="new-password"
          placeholder="填写 Token，自动添加 Bearer 前缀"
        />
      </el-form-item>
      <template v-if="request.auth === 'basic'">
        <el-form-item label="用户名" required
          ><el-input v-model="request.username" autocomplete="off"
        /></el-form-item>
        <el-form-item label="密码" required
          ><el-input
            v-model="request.password"
            type="password"
            autocomplete="new-password"
        /></el-form-item>
      </template>
      <template v-if="request.auth === 'api_key'">
        <el-form-item label="密钥请求头" required
          ><el-input v-model="request.keyName" placeholder="X-API-Key"
        /></el-form-item>
        <el-form-item label="API Key" required
          ><el-input
            v-model="request.keyValue"
            type="password"
            autocomplete="new-password"
        /></el-form-item>
      </template>
      <el-form-item v-if="request.auth !== 'keep'" label="自定义请求头">
        <div class="request-fields">
          <div v-if="request.headers.length" class="request-row">
            <span>请求头名称</span><span>请求头值</span><span>操作</span>
          </div>
          <div
            v-for="(header, index) in request.headers"
            :key="index"
            class="request-row"
          >
            <el-input
              v-model="header.name"
              placeholder="名称，如 X-Tenant-Id"
              :aria-label="`请求头 ${index + 1} 名称`"
            />
            <el-input
              v-model="header.value"
              type="password"
              autocomplete="new-password"
              placeholder="请求头值"
              :aria-label="`请求头 ${index + 1} 值`"
            />
            <el-button
              type="danger"
              link
              @click="request.headers.splice(index, 1)"
              >移除</el-button
            >
          </div>
          <el-button
            :disabled="request.headers.length >= 19"
            @click="request.headers.push({ name: '', value: '' })"
            >添加请求头</el-button
          >
        </div>
      </el-form-item>
    </template>
    <el-form-item label="返回格式" required>
      <el-select v-model="source.response_format">
        <el-option
          label="巡检记录（items / facts / next_cursor）"
          value="records"
        />
        <el-option label="普通 JSON（对象 / 数组）" value="json" />
      </el-select>
    </el-form-item>
    <template v-if="source.response_format === 'json'">
      <el-form-item label="对象列表路径"
        ><el-input
          v-model="source.items_path"
          placeholder="如 data.items；根对象或数组可留空"
      /></el-form-item>
      <el-form-item label="对象编号字段"
        ><el-input v-model="source.object_id_path" placeholder="id"
      /></el-form-item>
    </template>
    <el-collapse v-if="source.response_format === 'json'"
      ><el-collapse-item title="分页设置" name="pagination"
        ><el-form-item label="分页方式"
          ><el-select v-model="source.pagination"
            ><el-option label="单次读取" value="none" /><el-option
              label="游标"
              value="cursor" /><el-option
              label="页码 / 每页数量"
              value="page" /><el-option
              label="偏移量 / 每页数量"
              value="offset" /></el-select></el-form-item
        ><template v-if="source.pagination !== 'none'"
          ><el-form-item label="页码 / 游标 / 偏移参数名"
            ><el-input v-model="source.page_parameter" /></el-form-item
          ><el-form-item label="每页数量参数名"
            ><el-input v-model="source.size_parameter" /></el-form-item
          ><el-form-item
            v-if="source.pagination === 'cursor'"
            label="下一页游标路径"
            ><el-input v-model="source.next_cursor_path" /></el-form-item
          ><el-form-item label="是否有下一页字段路径"
            ><el-input
              v-model="source.has_more_path"
              placeholder="如 data.has_more；无此字段时按返回数量判断" /></el-form-item></template></el-collapse-item
    ></el-collapse>
  </div>
  <template v-else>
    <el-form-item label="从哪个数据库读取" required>
      <el-select
        v-model="source.connection_id"
        filterable
        placeholder="选择已允许读取数据的数据库"
        no-data-text="请先接入数据库，并设置允许读取的数据"
        @change="
          source.table = '';
          resetMappings();
        "
      >
        <el-option
          v-for="d in databases"
          :key="d.id"
          :label="`${d.name} · ${d.environment}`"
          :value="d.id"
        />
      </el-select>
    </el-form-item>
    <el-form-item label="数据表" required>
      <el-select
        v-model="source.table"
        filterable
        :disabled="!connection"
        placeholder="选择已授权的数据表"
        no-data-text="暂无已授权的数据表"
        @change="resetMappings"
      >
        <el-option v-for="table in tables" :key="table" :value="table" />
      </el-select>
    </el-form-item>
    <div class="source-grid">
      <el-form-item
        v-for="[key, label] in fields"
        :key="key"
        :label="label"
        :required="key === 'id_column'"
      >
        <el-select
          v-model="source[key]"
          filterable
          clearable
          :disabled="!source.table"
          :placeholder="
            key === 'id_column'
              ? '选择主键或唯一业务编号'
              : key === 'version_column'
                ? '无版本列，按读取内容识别变化'
                : key === 'time_column'
                  ? '无时间列，按对象标识扫描'
                  : '不使用原结论'
          "
          no-data-text="暂无已授权字段"
        >
          <el-option
            v-if="key !== 'id_column'"
            value=""
            :label="
              key === 'version_column'
                ? '无版本列，按读取内容识别变化'
                : key === 'time_column'
                  ? '无时间列，按对象标识扫描'
                  : '不使用原结论'
            "
          />
          <el-option v-for="column in columns" :key="column" :value="column" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="source.decision_column" label="合格原值"
        ><el-input v-model="source.decision_pass_value"
      /></el-form-item>
      <el-form-item v-if="source.decision_column" label="不合格原值"
        ><el-input v-model="source.decision_fail_value"
      /></el-form-item>
    </div>
    <el-form-item label="检查数据字段" required>
      <el-select
        v-model="source.fact_columns"
        multiple
        filterable
        :disabled="!source.table"
        placeholder="选择规则和 Agent 需要读取的已有字段"
        no-data-text="暂无已授权字段"
      >
        <el-option v-for="column in columns" :key="column" :value="column" />
      </el-select>
    </el-form-item>
    <el-form-item v-if="source.time_column" label="无时区时间按 UTC 读取"
      ><el-switch v-model="source.naive_time_is_utc"
    /></el-form-item>
    <el-collapse
      ><el-collapse-item title="登记参数化只读 SQL" name="sql"
        ><el-form-item label="SELECT 查询"
          ><el-input
            v-model="source.query_sql"
            type="textarea"
            :rows="5"
            placeholder="SELECT id, amount FROM orders WHERE status = :status ORDER BY id" /></el-form-item
        ><slot name="sql-parameters" /><el-form-item
          label="允许 Agent 覆盖的参数名"
          ><el-select
            v-model="source.allowed_query_params"
            multiple
            filterable
            allow-create
            default-first-option /></el-form-item></el-collapse-item
    ></el-collapse>
  </template>
</template>

<style scoped>
.request-fields {
  display: grid;
  gap: 10px;
  width: 100%;
  justify-items: start;
}
.request-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  gap: 10px;
  width: 100%;
}
.request-contract {
  margin-bottom: 22px;
}
.request-contract pre {
  overflow-x: auto;
  padding: 12px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}
.source-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 20px;
}
.el-select {
  width: 100%;
}
@media (max-width: 700px) {
  .api-source-fields :deep(.el-form-item) {
    display: block;
  }
  .api-source-fields :deep(.el-form-item__label) {
    display: block;
    width: auto !important;
    text-align: left;
  }
  .api-source-fields :deep(.el-form-item__content) {
    margin-left: 0 !important;
  }
  .source-grid {
    grid-template-columns: 1fr;
  }
}
</style>
