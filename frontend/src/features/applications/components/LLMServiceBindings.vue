<script setup lang="ts">
import { computed, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { all, api, query, type Models } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import StatusTag from "@/components/common/StatusTag.vue";

const props = defineProps<{ endpointId?: string; applicationId?: string }>();
const emit = defineEmits<{ saved: [] }>();
const auth = useAuth();
type Binding = Models["LLMBindingResponse"];
const rows = ref<Binding[]>([]),
  choices = ref<{ id: string; name: string }[]>([]),
  error = ref(""),
  dialogError = ref(""),
  loading = ref(false),
  choicesLoading = ref(false),
  saving = ref(false),
  open = ref(false),
  editing = ref<Binding>(),
  target = ref(""),
  purpose = ref("");
const endpointMode = computed(() => !!props.endpointId);
const canManage = computed(() => auth.can("llm:manage"));
async function load() {
  loading.value = true;
  try {
    rows.value = await all<Binding>(
      "/llm-bindings" +
        query({
          llm_endpoint_id: props.endpointId,
          application_id: props.applicationId,
        }),
    );
    error.value = "";
  } catch (e) {
    rows.value = [];
    error.value = e instanceof Error ? e.message : "关联加载失败";
  } finally {
    loading.value = false;
  }
}
const { refresh } = usePoll(load, () => (open.value ? null : 30000));
defineExpose({ refresh });
async function edit(row?: Binding) {
  editing.value = row;
  target.value = row
    ? endpointMode.value
      ? row.application_id
      : row.llm_endpoint_id
    : "";
  purpose.value = row?.purpose || "";
  dialogError.value = "";
  choices.value = [];
  open.value = true;
  if (row) return;
  choicesLoading.value = true;
  try {
    const values = await all<{ id: string; name: string }>(
      endpointMode.value ? "/business-services" : "/llm-endpoints",
    );
    choices.value = values.filter(
      (item) =>
        !rows.value.some(
          (binding) =>
            (endpointMode.value
              ? binding.application_id
              : binding.llm_endpoint_id) === item.id,
        ),
    );
  } catch (e) {
    dialogError.value = e instanceof Error ? e.message : "选择列表加载失败";
  } finally {
    choicesLoading.value = false;
  }
}
async function save() {
  if (!target.value) {
    dialogError.value = endpointMode.value
      ? "请选择业务服务"
      : "请选择 LLM 服务";
    return;
  }
  saving.value = true;
  try {
    const endpoint = props.endpointId || target.value;
    const application = props.applicationId || target.value;
    await api(`/llm-endpoints/${endpoint}/applications/${application}`, "PUT", {
      purpose: purpose.value,
    });
    open.value = false;
    await load();
    emit("saved");
    ElMessage.success("关联与用途备注已保存");
  } catch (e) {
    dialogError.value = e instanceof Error ? e.message : "保存失败";
  } finally {
    saving.value = false;
  }
}
async function remove(row: Binding) {
  try {
    await ElMessageBox.confirm(
      "解除此业务服务与 LLM 端点的使用关系？",
      "解除关联",
    );
    await api(
      `/llm-endpoints/${row.llm_endpoint_id}/applications/${row.application_id}`,
      "DELETE",
    );
    await load();
    emit("saved");
    ElMessage.success("关联已解除");
  } catch (e) {
    if (e !== "cancel" && e !== "close")
      ElMessage.error(e instanceof Error ? e.message : "解除失败");
  }
}
</script>

<template>
  <section class="panel llm-service-bindings" v-loading="loading">
    <div v-if="endpointMode || canManage" class="panel-heading">
      <h2 v-if="endpointMode">关联业务服务</h2>
      <el-button v-if="canManage" type="primary" @click="edit()">
        {{ endpointMode ? "关联业务服务" : "关联 LLM 服务" }}
      </el-button>
    </div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      class="error-box"
    />
    <el-table :data="rows" empty-text="尚未关联" row-key="id">
      <el-table-column
        :label="endpointMode ? '业务服务' : 'LLM 服务'"
        min-width="180"
      >
        <template #default="{ row }">
          <router-link
            v-if="!endpointMode || auth.can('sources:read')"
            :to="
              endpointMode
                ? '/applications/' + row.application_id
                : '/llm-endpoints/' + row.llm_endpoint_id
            "
          >
            {{ endpointMode ? row.application_name : row.endpoint_name }}
          </router-link>
          <span v-else>{{ row.application_name }}</span>
        </template>
      </el-table-column>
      <el-table-column
        v-if="!endpointMode"
        prop="model"
        label="模型"
        min-width="150"
      />
      <el-table-column v-if="!endpointMode" label="状态" width="110">
        <template #default="{ row }"
          ><StatusTag :value="row.status"
        /></template>
      </el-table-column>
      <el-table-column label="用途备注" min-width="240">
        <template #default="{ row }"
          ><span class="purpose">{{ row.purpose || "—" }}</span></template
        >
      </el-table-column>
      <el-table-column v-if="canManage" label="操作" width="180">
        <template #default="{ row }">
          <el-button text type="primary" @click="edit(row)">编辑备注</el-button>
          <el-button text type="danger" @click="remove(row)"
            >解除关联</el-button
          >
        </template>
      </el-table-column>
    </el-table>
  </section>
  <el-dialog
    v-model="open"
    :title="
      editing ? '编辑用途备注' : endpointMode ? '关联业务服务' : '关联 LLM 服务'
    "
    width="560px"
    :close-on-click-modal="false"
    :close-on-press-escape="!saving"
    :show-close="!saving"
  >
    <el-alert
      v-if="dialogError"
      :title="dialogError"
      type="error"
      :closable="false"
      class="error-box"
    />
    <el-form label-position="top" @submit.prevent="save">
      <el-form-item :label="endpointMode ? '业务服务' : 'LLM 服务'" required>
        <el-input
          v-if="editing"
          :model-value="
            endpointMode ? editing.application_name : editing.endpoint_name
          "
          disabled
        />
        <el-select
          v-else
          v-model="target"
          filterable
          :loading="choicesLoading"
          :disabled="saving"
          :placeholder="endpointMode ? '选择业务服务' : '选择 LLM 服务'"
          style="width: 100%"
        >
          <el-option
            v-for="item in choices"
            :key="item.id"
            :value="item.id"
            :label="item.name"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="用途备注">
        <el-input
          v-model="purpose"
          type="textarea"
          :rows="4"
          maxlength="2000"
          show-word-limit
          :disabled="saving"
          placeholder="例如：问答生成、故障诊断或结果复核"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="saving" @click="open = false">取消</el-button>
      <el-button
        type="primary"
        :loading="saving"
        :disabled="choicesLoading"
        @click="save"
        >保存</el-button
      >
    </template>
  </el-dialog>
</template>

<style scoped>
.purpose {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
