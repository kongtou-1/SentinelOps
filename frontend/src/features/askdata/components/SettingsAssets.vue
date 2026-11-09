<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useAuth } from "@/features/auth/store";
import { askdata } from "@/features/askdata/api";

type Kind = "zones" | "tags" | "rules" | "sensitive" | "supplements";
type Row = Record<string, unknown>;
type FieldType = "text" | "textarea" | "select" | "switch" | "number" | "list" | "targets";

interface Field {
  key: string;
  label: string;
  type: FieldType;
  options?: { value: string; label: string }[];
  span?: number;
}

const auth = useAuth();
const canManage = computed(() => auth.can("askdata:manage"));
const props = defineProps<{ kind: Kind; connectionId: string }>();
const emit = defineEmits<{ saved: [] }>();

const config: Record<Kind, { title: string; columns: string[]; fields: Field[]; empty: string }> = {
  zones: {
    title: "业务分区",
    columns: ["name", "description", "enabled"],
    empty: "还没有业务分区，可按数据库、Schema 或数据表归类",
    fields: [
      { key: "name", label: "名称", type: "text", span: 12 },
      { key: "description", label: "说明", type: "text", span: 12 },
      { key: "targets", label: "归类对象", type: "targets", span: 24 },
      { key: "enabled", label: "启用", type: "switch" },
    ],
  },
  tags: {
    title: "标签",
    columns: ["name", "description"],
    empty: "还没有标签",
    fields: [
      { key: "name", label: "名称", type: "text", span: 8 },
      { key: "color", label: "颜色", type: "text", span: 6 },
      { key: "description", label: "说明", type: "text", span: 10 },
      { key: "targets", label: "标签对象", type: "targets", span: 24 },
    ],
  },
  rules: {
    title: "业务规则",
    columns: ["name", "kind", "target", "enabled"],
    empty: "还没有业务规则，可约束统计范围、时间语义、单位与过滤条件",
    fields: [
      { key: "name", label: "名称", type: "text", span: 12 },
      { key: "kind", label: "类型", type: "select", span: 6, options: [
        { value: "statistical_scope", label: "统计范围" },
        { value: "time_semantics", label: "时间语义" },
        { value: "unit", label: "单位" },
        { value: "filter", label: "业务过滤" },
        { value: "soft_delete", label: "软删除" },
        { value: "status", label: "状态条件" },
      ] },
      { key: "priority", label: "优先级", type: "number", span: 6 },
      { key: "target", label: "作用对象", type: "text", span: 12 },
      { key: "unit", label: "单位", type: "text", span: 6 },
      { key: "description", label: "说明", type: "text", span: 6 },
      { key: "expression", label: "规则内容", type: "textarea", span: 24 },
      { key: "enabled", label: "启用", type: "switch" },
    ],
  },
  sensitive: {
    title: "敏感数据",
    columns: ["object_key", "category", "policy", "sampling"],
    empty: "还没有敏感字段标记",
    fields: [
      { key: "object_key", label: "字段", type: "text", span: 12 },
      { key: "category", label: "分类", type: "select", span: 6, options: [
        { value: "mobile", label: "手机号" },
        { value: "id_card", label: "身份证" },
        { value: "bank_card", label: "银行卡" },
        { value: "email", label: "邮箱" },
        { value: "address", label: "地址" },
        { value: "name", label: "姓名" },
        { value: "amount", label: "金额" },
        { value: "credential", label: "凭据" },
        { value: "other", label: "其他" },
      ] },
      { key: "sampling", label: "采样", type: "select", span: 6, options: [
        { value: "exclude", label: "禁止采样" },
        { value: "allow", label: "允许采样" },
      ] },
      { key: "policy", label: "结果处理", type: "select", span: 8, options: [
        { value: "mask", label: "脱敏" },
        { value: "exclude", label: "不展示" },
      ] },
      { key: "mask_strategy", label: "脱敏方式", type: "select", span: 8, options: [
        { value: "full", label: "全部隐藏" },
        { value: "keep_prefix", label: "保留前缀" },
        { value: "keep_suffix", label: "保留后缀" },
        { value: "hash", label: "哈希" },
      ] },
      { key: "mask_argument", label: "保留位数", type: "number", span: 8 },
      { key: "note", label: "说明", type: "text", span: 24 },
      { key: "enabled", label: "启用", type: "switch" },
    ],
  },
  supplements: {
    title: "补充定义",
    columns: ["term", "kind", "definition", "enabled"],
    empty: "还没有补充定义",
    fields: [
      { key: "term", label: "术语", type: "text", span: 8 },
      { key: "kind", label: "类型", type: "select", span: 8, options: [
        { value: "abbreviation", label: "缩写" },
        { value: "alias", label: "别名" },
        { value: "context", label: "上下文说明" },
      ] },
      { key: "applies_to", label: "适用范围", type: "text", span: 8 },
      { key: "aliases", label: "其他叫法", type: "list", span: 12 },
      { key: "definition", label: "定义", type: "textarea", span: 24 },
      { key: "enabled", label: "启用", type: "switch" },
    ],
  },
};

const rows = ref<Row[]>([]);
const busy = ref(false);
const error = ref("");
const dialog = ref(false);
const saving = ref(false);
const editingId = ref("");
const form = reactive<Record<string, any>>({});
const current = computed(() => config[props.kind]);

const labels: Record<string, Record<string, string>> = {
  rules: {
    statistical_scope: "统计范围",
    time_semantics: "时间语义",
    unit: "单位",
    filter: "业务过滤",
    soft_delete: "软删除",
    status: "状态条件",
  },
  sensitive: {
    mobile: "手机号",
    id_card: "身份证",
    bank_card: "银行卡",
    email: "邮箱",
    address: "地址",
    name: "姓名",
    amount: "金额",
    credential: "凭据",
    other: "其他",
    mask: "脱敏",
    exclude: "不展示",
    allow: "允许采样",
    full: "全部隐藏",
    keep_prefix: "保留前缀",
    keep_suffix: "保留后缀",
    hash: "哈希",
  },
  supplements: { abbreviation: "缩写", alias: "别名", context: "上下文说明" },
};

interface AssetApi {
  list: (connectionId: string) => Promise<{ items: Row[] }>;
  create: (connectionId: string, body: any) => Promise<unknown>;
  update: (connectionId: string, id: string, body: any) => Promise<unknown>;
  remove: (connectionId: string, id: string) => Promise<void>;
}

function makeApi(): AssetApi {
  switch (props.kind) {
    case "zones":
      return { list: askdata.zones, create: askdata.createZone, update: askdata.updateZone, remove: askdata.deleteZone };
    case "tags":
      return { list: askdata.tags, create: askdata.createTag, update: askdata.updateTag, remove: askdata.deleteTag };
    case "rules":
      return { list: askdata.rules, create: askdata.createRule, update: askdata.updateRule, remove: askdata.deleteRule };
    case "sensitive":
      return {
        list: askdata.sensitiveFields,
        create: askdata.createSensitiveField,
        update: askdata.updateSensitiveField,
        remove: askdata.deleteSensitiveField,
      };
    default:
      return {
        list: askdata.supplements,
        create: askdata.createSupplement,
        update: askdata.updateSupplement,
        remove: askdata.deleteSupplement,
      };
  }
}

const api = computed<AssetApi>(makeApi);

function cell(row: Row, key: string) {
  const value = row[key];
  if (typeof value === "boolean") return value ? "启用" : "停用";
  if (key === "targets") return Array.isArray(value) ? `${value.length} 个对象` : "—";
  if (props.kind === "zones" && key === "description") return value || "—";
  return labels[props.kind]?.[String(value)] ?? (value === "" || value == null ? "—" : String(value));
}

function setList(key: string, value: string) {
  form[key] = value
    .split(/[、,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function reset() {
  editingId.value = "";
  for (const field of current.value.fields) {
    form[field.key] = field.type === "switch" ? true : field.type === "number" ? 100 : field.type === "select" ? field.options?.[0]?.value ?? "" : [];
  }
  form.targets = [];
  form.targetsText = "";
  form.aliases = [];
  if (props.kind === "sensitive") {
    form.policy = "mask";
    form.mask_strategy = "full";
    form.mask_argument = 0;
    form.sampling = "exclude";
    form.priority = 0;
  }
  if (props.kind === "zones") form.enabled = true;
}

function openCreate() {
  reset();
  dialog.value = true;
}

function openEdit(row: Row) {
  reset();
  editingId.value = String(row.id);
  for (const field of current.value.fields) {
    const value = row[field.key];
    if (field.type === "targets") form.targets = Array.isArray(value) ? value : [];
    else if (field.type === "list") form.aliases = Array.isArray(value) ? value : [];
    else if (field.type === "number") form[field.key] = Number(value ?? 0);
    else form[field.key] = value ?? (field.type === "switch" ? false : "");
  }
  dialog.value = true;
}

function parseTargets(text: string) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [kind, ...rest] = line.split(":");
      const key = rest.join(":").trim();
      return props.kind === "tags"
        ? { object_kind: kind || "table", object_key: key }
        : { kind: kind || "table", key };
    })
    .filter((item) => ("key" in item ? item.key : item.object_key));
}

function payload() {
  const body: Record<string, any> = {};
  for (const field of current.value.fields) {
    if (field.type === "targets") body.targets = parseTargets(form.targetsText || "");
    else body[field.key] = form[field.key];
  }
  return body;
}

async function load() {
  if (!props.connectionId) {
    rows.value = [];
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    rows.value = (await api.value.list(props.connectionId)).items;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    busy.value = false;
  }
}

async function save() {
  saving.value = true;
  error.value = "";
  try {
    if (editingId.value) await api.value.update(props.connectionId, editingId.value, payload());
    else await api.value.create(props.connectionId, payload());
    dialog.value = false;
    ElMessage.success("已保存");
    emit("saved");
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "保存失败";
  } finally {
    saving.value = false;
  }
}

async function remove(row: Row) {
  try {
    await ElMessageBox.confirm(`删除「${row.name ?? row.term ?? row.object_key}」？`, "确认删除", {
      confirmButtonText: "删除",
      cancelButtonText: "取消",
      type: "warning",
    });
  } catch {
    return;
  }
  try {
    await api.value.remove(props.connectionId, String(row.id));
    ElMessage.success("已删除");
    emit("saved");
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "删除失败";
  }
}

watch(() => [props.kind, props.connectionId], () => void load(), { immediate: true });
defineExpose({ load });
</script>

<template>
  <section class="assets">
    <div class="heading">
      <h2>{{ current.title }}</h2>
      <el-button v-if="canManage" type="primary" :disabled="!connectionId" @click="openCreate">新增</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-table v-loading="busy" :data="rows" :empty-text="current.empty" border>
      <el-table-column
        v-for="key in current.columns"
        :key="key"
        :label="key === 'object_key' ? '字段' : key === 'name' ? '名称' : key === 'term' ? '术语' : key === 'kind' ? '类型' : key === 'target' ? '作用对象' : key === 'category' ? '分类' : key === 'policy' ? '结果处理' : key === 'sampling' ? '采样' : key === 'enabled' ? '状态' : '说明'"
        :min-width="key === 'description' || key === 'definition' ? 220 : 120"
        show-overflow-tooltip
      >
        <template #default="{ row }">
          <el-tag v-if="key === 'enabled'" :type="row.enabled ? 'success' : 'info'" size="small" effect="plain">
            {{ row.enabled ? "启用" : "停用" }}
          </el-tag>
          <span v-else>{{ cell(row, key) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="{ row }">
          <el-button v-if="canManage" link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button v-if="canManage" link type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" :title="editingId ? `编辑${current.title}` : `新增${current.title}`" width="620px">
      <el-form label-width="88px">
        <el-row :gutter="12">
          <el-col v-for="field in current.fields" :key="field.key" :span="field.span ?? 12">
            <el-form-item :label="field.label">
              <el-switch v-if="field.type === 'switch'" v-model="form[field.key]" />
              <el-input-number
                v-else-if="field.type === 'number'"
                v-model="form[field.key]"
                :min="0"
                :max="field.key === 'mask_argument' ? 32 : 1000"
              />
              <el-select v-else-if="field.type === 'select'" v-model="form[field.key]" style="width: 100%">
                <el-option v-for="option in field.options" :key="option.value" :label="option.label" :value="option.value" />
              </el-select>
              <el-input
                v-else-if="field.type === 'textarea'"
                v-model="form[field.key]"
                type="textarea"
                :rows="3"
              />
              <el-input
                v-else-if="field.type === 'list'"
                :model-value="(form[field.key] || []).join('、')"
                placeholder="用、或逗号分隔"
                @update:model-value="(value: string) => (form[field.key] = value.split(/[、,，\\n]/).map((item) => item.trim()).filter(Boolean))"
              />
              <el-input v-else-if="field.type === 'targets'" v-model="form.targetsText" placeholder="kind:key，每行一个，如 table:public.orders" type="textarea" :rows="3" />
              <el-input v-else v-model="form[field.key]" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-alert
          v-if="kind === 'sensitive'"
          title="标记为禁止采样的字段不会进入业务模型提示词与向量；脱敏在查询结果写入前生效。"
          type="info"
          :closable="false"
        />
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.assets { display: grid; gap: 12px; min-width: 0; }
.heading { display: flex; align-items: center; justify-content: space-between; }
h2 { margin: 0; font-size: 18px; }
</style>
