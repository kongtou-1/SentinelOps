<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { confirmOperation } from "@/utils/confirmOperation";
import { all, api, type Models } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { date, label } from "@/utils/display";

const tokenCountFields = new Set([
  "input_tokens",
  "output_tokens",
  "total_tokens",
  "prompt_tokens",
  "completion_tokens",
  "total_input_tokens",
  "total_output_tokens",
]);

const props = defineProps<{
  applicationId?: string;
  summary?: boolean;
  embedded?: boolean;
  askData?: boolean;
}>();
type Connection = Models["DatabaseResponse"] &
  Partial<
    Pick<
      Models["AskDataConnectionResponse"],
      "askdata_enabled" | "askdata_version"
    >
  >;
type Form = Models["DatabaseInput"];
interface Table {
  columns: {
    name: string;
    type: string;
    nullable: boolean;
    comment?: string;
  }[];
  indexes: string[];
  comment: string;
  primary_key?: string[];
  foreign_keys?: {
    name: string;
    columns: string[];
    referenced_table: string;
    referenced_columns: string[];
  }[];
}
const rows = ref<Connection[]>([]),
  services = ref<Models["ApplicationResponse"][]>([]),
  error = ref(""),
  drawer = ref(false),
  busy = ref(false),
  activeTab = ref("connection"),
  current = ref<Connection>(),
  tables = ref<Record<string, Table>>({}),
  result = ref<Models["DatabaseTestResponse"]>();
const auth = useAuth(),
  canManage = computed(() => auth.can("resources:manage"));
const defaults = (): Form => ({
  application_id: props.applicationId || null,
  name: "",
  engine: "postgresql",
  host: "",
  port: 5432,
  database: "",
  username: "",
  environment: "test",
  tls_mode: "verify-full",
  purpose: "",
  description: "",
  table_notes: {},
  allowed_tables: {},
  enabled: true,
  query_enabled: false,
});
const form = ref<Form>(defaults()),
  password = ref(""),
  ca = ref(""),
  formError = ref("");
const statusNames: Record<string, string> = {
  verified: "只读权限已核验",
  unsafe: "账号权限过高",
  unverified: "权限待核验",
};
async function load() {
  try {
    rows.value = await api<Connection[]>(
      props.askData
        ? "/askdata/settings/connections"
        : props.applicationId
          ? `/business-services/${props.applicationId}/databases`
          : "/service-databases",
    );
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "数据库加载失败";
  }
}
watch(() => props.applicationId, load, { immediate: true });
async function toggleAskData(row: Connection, enabled: boolean) {
  busy.value = true;
  try {
    await api(`/askdata/settings/connections/${row.id}`, "PUT", {
      enabled,
      expected_version: row.askdata_version || 0,
    });
    ElMessage.success(enabled ? "已启用问数" : "已停用问数");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "问数设置保存失败";
  } finally {
    busy.value = false;
    await load();
  }
}
watch(drawer, async (value) => {
  if (!value || props.applicationId) return;
  try {
    services.value =
      await all<Models["ApplicationResponse"]>("/business-services");
  } catch (e) {
    formError.value = e instanceof Error ? e.message : "服务加载失败";
  }
});
async function edit(row?: Connection) {
  current.value = row;
  form.value = row
    ? ({
        ...defaults(),
        ...Object.fromEntries(
          Object.keys(defaults()).map((k) => [k, row[k as keyof Connection]]),
        ),
        allowed_tables: Object.fromEntries(
          Object.entries(row.allowed_tables).map(([k, v]) => [k, [...v]]),
        ),
        table_notes: { ...row.table_notes },
      } as Form)
    : defaults();
  password.value = ca.value = formError.value = "";
  result.value = undefined;
  tables.value = {};
  activeTab.value = "connection";
  drawer.value = true;
  if (row) await readSchema(row.id);
}
async function readSchema(id: string) {
  try {
    const schema = await api<Models["DatabaseSchemaResponse"]>(
      `/service-databases/${id}/schema`,
    );
    tables.value = schema.tables as unknown as Record<string, Table>;
  } catch (e) {
    formError.value = e instanceof Error ? e.message : "表结构加载失败";
  }
}
function payload() {
  return {
    ...form.value,
    ...(password.value ? { password: password.value } : {}),
    ...(ca.value ? { ca_certificate: ca.value } : {}),
  };
}
async function save() {
  busy.value = true;
  formError.value = "";
  try {
    const row = await api<Connection>(
      current.value
        ? `/service-databases/${current.value.id}`
        : props.applicationId
          ? `/business-services/${props.applicationId}/databases`
          : "/service-databases",
      current.value ? "PUT" : "POST",
      payload(),
    );
    await load();
    await edit(row);
    ElMessage.success("数据库配置已保存");
  } catch (e) {
    formError.value = e instanceof Error ? e.message : "保存失败";
  } finally {
    busy.value = false;
  }
}
async function test(row?: Connection) {
  if (row) await edit(row);
  busy.value = true;
  formError.value = "";
  try {
    result.value = await api<Models["DatabaseTestResponse"]>(
      "/service-databases/test",
      "POST",
      { ...payload(), connection_id: current.value?.id },
    );
    await load();
  } catch (e) {
    formError.value = e instanceof Error ? e.message : "连接测试失败";
  } finally {
    busy.value = false;
  }
}
async function refreshSchema() {
  if (!current.value) return;
  busy.value = true;
  formError.value = "";
  try {
    await api(`/service-databases/${current.value.id}/schema/refresh`, "POST");
    await load();
    current.value = rows.value.find((r) => r.id === current.value?.id);
    if (current.value) await readSchema(current.value.id);
    ElMessage.success("已获取数据表与字段");
  } catch (e) {
    formError.value = e instanceof Error ? e.message : "刷新失败";
  } finally {
    busy.value = false;
  }
}
async function remove(row: Connection) {
  try {
    await ElMessageBox.confirm(
      `移除已接入的数据库「${row.name}」？`,
      "移除数据库",
    );
    await api(`/service-databases/${row.id}`, "DELETE");
    ElMessage.success("数据库已移除");
    await load();
  } catch (e) {
    if (e !== "cancel" && e !== "close")
      error.value = e instanceof Error ? e.message : "删除失败";
  }
}
async function toggle(row: Connection) {
  if (busy.value) return;
  busy.value = true;
  try {
    if (
      !(await confirmOperation(
        row.enabled
          ? `停止使用数据库“${row.name}”？平台将不再从此数据库读取数据或进行诊断。`
          : `恢复使用数据库“${row.name}”？平台仍只能读取已授权的数据。`,
        row.enabled ? "停止使用数据库" : "恢复使用数据库",
      ))
    )
      return;
    await edit(row);
    form.value.enabled = !row.enabled;
    await save();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "操作失败";
  } finally {
    busy.value = false;
  }
}
function selectColumns(key: string, values: string[]) {
  const allowed = { ...form.value.allowed_tables };
  if (values.length) allowed[key] = values;
  else delete allowed[key];
  form.value.allowed_tables = allowed;
  if (!Object.keys(allowed).length) form.value.query_enabled = false;
}
watch(
  () => [
    form.value.engine,
    form.value.host,
    form.value.port,
    form.value.database,
    form.value.username,
    form.value.tls_mode,
    password.value,
    ca.value,
  ],
  () => {
    result.value = undefined;
    if (
      current.value &&
      (password.value ||
        ca.value ||
        ["engine", "host", "port", "database", "username", "tls_mode"].some(
          (k) =>
            form.value[k as keyof Form] !==
            current.value?.[k as keyof Connection],
        ))
    )
      form.value.query_enabled = false;
  },
);
defineExpose({ refresh: load, create: () => edit() });
</script>

<template>
  <section
    :class="[
      'databases',
      { panel: !embedded, 'databases--embedded': embedded },
    ]"
  >
    <div class="panel-heading">
      <h3 v-if="summary">数据库依赖</h3>
      <router-link
        v-if="summary"
        :to="{
          path: '/applications/' + applicationId,
          query: { view: 'databases' },
        }"
        >管理数据库</router-link
      >
      <div v-else class="actions">
        <el-button @click="load">刷新</el-button
        ><el-button v-if="canManage && !embedded" type="primary" @click="edit()"
          >接入数据库</el-button
        >
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-table :data="rows" row-key="id" empty-text="未接入数据库">
      <el-table-column prop="name" label="名称" min-width="150"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="edit(row)">{{
            row.name
          }}</el-button></template
        ></el-table-column
      >
      <el-table-column prop="engine" label="类型" width="120" />
      <el-table-column prop="database" label="数据库" min-width="130" />
      <el-table-column prop="environment" label="环境" width="90" />
      <el-table-column v-if="askData" label="问数" width="100">
        <template #default="{ row }">
          <el-switch
            :model-value="row.askdata_enabled"
            :disabled="busy || !auth.can('askdata:manage')"
            inline-prompt
            active-text="开"
            inactive-text="关"
            aria-label="启用问数"
            @change="
              (value: string | number | boolean) =>
                toggleAskData(row, Boolean(value))
            "
          />
        </template>
      </el-table-column>
      <el-table-column
        v-if="!summary"
        prop="purpose"
        label="用途"
        min-width="160"
      />
      <el-table-column label="允许读取的数据" width="160"
        ><template #default="{ row }"
          ><el-tag
            :type="
              !row.enabled ? 'info' : row.query_enabled ? 'success' : 'warning'
            "
            >{{
              !row.enabled
                ? "已停止使用"
                : row.query_enabled
                  ? "已选表和字段"
                  : "仅表名和字段名"
            }}</el-tag
          ></template
        ></el-table-column
      >
      <el-table-column label="最近连接测试" min-width="190"
        ><template #default="{ row }"
          >{{
            row.last_test_success === null
              ? "未测试"
              : row.last_test_success
                ? "成功"
                : "失败"
          }}<br />{{
            row.last_test_at ? date(row.last_test_at) : "—"
          }}</template
        ></el-table-column
      >
      <el-table-column
        v-if="canManage && !summary"
        label="操作"
        width="270"
        :fixed="askData ? 'right' : false"
        ><template #default="{ row }"
          ><el-button link @click="test(row)">测试连接</el-button
          ><el-button link @click="edit(row)">编辑</el-button
          ><el-button link @click="toggle(row)">{{
            row.enabled ? "停止使用" : "恢复使用"
          }}</el-button
          ><el-button link type="danger" @click="remove(row)"
            >移除</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <el-drawer
      v-model="drawer"
      :title="current ? `编辑数据库 · ${current.name}` : '接入数据库'"
      size="min(960px, 96vw)"
      append-to-body
    >
      <el-alert
        v-if="formError"
        :title="formError"
        type="error"
        :closable="false"
      />
      <el-alert
        v-if="result"
        :title="`${result.success ? '连接成功' : '连接失败'} · ${statusNames[result.permission_status] || result.permission_status}${result.error_code ? ' · ' + label(result.error_code) : ''}`"
        :type="
          result.success && result.permission_status === 'verified'
            ? 'success'
            : 'warning'
        "
        :closable="false"
      />
      <el-tabs v-model="activeTab">
        <el-tab-pane label="数据库地址与账号" name="connection">
          <el-form label-position="top" :disabled="!canManage || busy">
            <el-form-item v-if="!applicationId" label="关联服务（可选）">
              <el-select
                v-model="form.application_id"
                clearable
                :value-on-clear="null"
                filterable
                placeholder="不关联服务"
              >
                <el-option
                  v-for="service in services"
                  :key="service.id"
                  :label="service.name"
                  :value="service.id"
                />
              </el-select>
            </el-form-item>
            <div class="form-grid">
              <el-form-item label="显示名称" required
                ><el-input
                  v-model="form.name"
                  maxlength="100"
                  placeholder="如：订单数据库"
              /></el-form-item>
              <el-form-item label="数据库类型" required
                ><el-select
                  v-model="form.engine"
                  @change="
                    form.port = form.engine === 'postgresql' ? 5432 : 3306
                  "
                  ><el-option label="PostgreSQL" value="postgresql" /><el-option
                    label="MySQL"
                    value="mysql" /></el-select
              ></el-form-item>
              <el-form-item label="数据库地址" required
                ><el-input v-model="form.host" placeholder="IP地址或域名"
              /></el-form-item>
              <el-form-item label="端口" required
                ><el-input-number v-model="form.port" :min="1" :max="65535"
              /></el-form-item>
              <el-form-item label="数据库名" required
                ><el-input v-model="form.database"
              /></el-form-item>
              <el-form-item label="环境" required
                ><el-input v-model="form.environment"
              /></el-form-item>
              <el-form-item label="只读账号" required
                ><el-input
                  v-model="form.username"
                  autocomplete="off"
                  placeholder="数据库的只读用户名"
              /></el-form-item>
              <el-form-item
                v-if="canManage"
                :label="
                  current?.password_configured
                    ? '密码（已配置，留空保留）'
                    : '密码'
                "
                ><el-input
                  v-model="password"
                  type="password"
                  show-password
                  autocomplete="new-password"
              /></el-form-item>
            </div>
            <el-form-item label="连接加密（TLS）" required
              ><el-select v-model="form.tls_mode"
                ><el-option
                  label="加密连接并校验证书"
                  value="verify-full" /><el-option
                  label="不加密"
                  value="disable" /></el-select
            ></el-form-item>
            <el-form-item
              v-if="canManage && form.tls_mode === 'verify-full'"
              :label="
                current?.ca_configured
                  ? 'CA 证书（已配置，留空保留）'
                  : 'CA 证书（可选）'
              "
              ><el-input v-model="ca" type="textarea" :rows="3"
            /></el-form-item>
            <el-form-item label="服务用途"
              ><el-input v-model="form.purpose" maxlength="2000"
            /></el-form-item>
          </el-form>
        </el-tab-pane>
        <el-tab-pane label="数据表与字段" name="schema">
          <div class="panel-heading">
            <span>上次获取：{{ date(current?.schema_refreshed_at) }}</span
            ><el-button
              v-if="canManage"
              :disabled="!current || busy"
              @click="refreshSchema"
              >获取数据表与字段</el-button
            >
          </div>
          <el-form label-position="top" :disabled="!canManage || busy"
            ><el-form-item label="数据库说明"
              ><el-input
                v-model="form.description"
                type="textarea"
                :rows="4"
                maxlength="16000" /></el-form-item
          ></el-form>
          <el-empty
            v-if="!Object.keys(tables).length"
            :description="
              current
                ? '尚未获取数据表，请点击“获取数据表与字段”'
                : '请先填写数据库地址与账号并保存'
            "
            :image-size="60"
          />
          <el-collapse
            ><el-collapse-item
              v-for="(table, key) in tables"
              :key="key"
              :title="String(key)"
              :name="String(key)"
            >
              <p v-if="table.comment">{{ table.comment }}</p>
              <el-input
                v-model="form.table_notes![key]"
                type="textarea"
                :rows="3"
                :disabled="!canManage || busy"
                maxlength="8000"
                aria-label="表业务说明"
              />
              <el-table :data="table.columns"
                ><el-table-column prop="name" label="字段" /><el-table-column
                  prop="type"
                  label="类型"
                /><el-table-column
                  prop="comment"
                  label="注释"
                /><el-table-column label="可空"
                  ><template #default="{ row }">{{
                    row.nullable ? "是" : "否"
                  }}</template></el-table-column
                ><el-table-column label="主键" width="75"
                  ><template #default="{ row }">{{
                    table.primary_key
                      ? table.primary_key.includes(row.name)
                        ? "是"
                        : "否"
                      : "未获取"
                  }}</template></el-table-column
                ></el-table
              >
              <pre v-for="index in table.indexes" :key="index">{{ index }}</pre>
              <el-table
                v-if="table.foreign_keys?.length"
                :data="table.foreign_keys"
              >
                <el-table-column label="关联字段"
                  ><template #default="{ row }">{{
                    row.columns.join("、")
                  }}</template></el-table-column
                >
                <el-table-column prop="referenced_table" label="关联表" />
                <el-table-column label="目标字段"
                  ><template #default="{ row }">{{
                    row.referenced_columns.join("、")
                  }}</template></el-table-column
                >
              </el-table>
            </el-collapse-item></el-collapse
          >
        </el-tab-pane>
        <el-tab-pane label="允许读取的数据" name="access">
          <el-form :disabled="!canManage || busy" label-position="top">
            <el-form-item label="平台可以读取什么"
              ><el-radio-group v-model="form.query_enabled">
                <el-radio-button :value="false">仅表名和字段名</el-radio-button>
                <el-radio-button
                  :value="true"
                  :disabled="!Object.keys(form.allowed_tables || {}).length"
                  >所选字段中的数据</el-radio-button
                >
              </el-radio-group></el-form-item
            >
            <el-empty
              v-if="!Object.keys(tables).length"
              :description="
                current
                  ? '请先在“数据表与字段”中获取数据表'
                  : '请先填写数据库地址与账号并保存'
              "
              :image-size="60"
            />
            <el-form-item
              v-for="(table, key) in tables"
              :key="key"
              :label="`${key} · 允许读取的字段`"
              ><el-checkbox-group
                :model-value="form.allowed_tables?.[key] || []"
                @update:model-value="
                  (v: unknown) => selectColumns(String(key), v as string[])
                "
                ><el-checkbox
                  v-for="column in table.columns"
                  :key="column.name"
                  :value="column.name"
                  :disabled="
                    !canManage ||
                    busy ||
                    (/password|passphrase|secret|api.?key|authorization|token|private.?key/i.test(
                      column.name,
                    ) && !tokenCountFields.has(column.name.toLowerCase()))
                  "
                  >{{ column.name }}</el-checkbox
                ></el-checkbox-group
              ></el-form-item
            >
          </el-form>
        </el-tab-pane>
      </el-tabs>
      <template #footer
        ><el-button @click="drawer = false">关闭</el-button
        ><el-button v-if="canManage" :loading="busy" @click="test()"
          >测试连接</el-button
        ><el-button
          v-if="canManage"
          type="primary"
          :loading="busy"
          @click="save"
          >{{ current ? "保存修改" : "保存数据库" }}</el-button
        ></template
      >
    </el-drawer>
  </section>
</template>

<style scoped>
.databases {
  overflow: hidden;
}
.databases--embedded .panel-heading {
  justify-content: flex-end;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 20px;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
@media (max-width: 640px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
