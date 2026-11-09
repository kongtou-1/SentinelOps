<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, query, type Models } from "@/api/client";

const props = defineProps<{
  kind: string;
  id: string;
  resourceName: string;
  userId?: string;
  userName?: string;
}>();
const emit = defineEmits<{ saving: [value: boolean] }>();
const policy = ref<Models["PolicyResponse"]>(),
  restricted = ref(false),
  loading = ref(false),
  busy = ref(false),
  error = ref("");
const endpoint = computed(
  () =>
    `/access-policies/${props.kind}/${props.id}` +
    query({ user_id: props.userId }),
);
let generation = 0;
async function load() {
  const current = ++generation;
  policy.value = undefined;
  loading.value = true;
  error.value = "";
  try {
    const result = await api<Models["PolicyResponse"]>(endpoint.value);
    if (current !== generation) return;
    policy.value = result;
    restricted.value = result.restricted;
  } catch (e) {
    if (current === generation)
      error.value = e instanceof Error ? e.message : "访问策略加载失败";
  } finally {
    if (current === generation) loading.value = false;
  }
}
watch(endpoint, load, { immediate: true });
async function save() {
  if (!policy.value || loading.value || busy.value) return;
  const current = generation,
    path = endpoint.value,
    value = restricted.value,
    version = policy.value.version;
  busy.value = true;
  emit("saving", true);
  try {
    const scope = props.userId ? `用户“${props.userName}”` : "所有非管理员用户";
    await ElMessageBox.confirm(
      props.userId
        ? value
          ? `禁止${scope}访问“${props.resourceName}”及其下属资源？已有授权也不能绕过禁止。`
          : `解除${scope}对“${props.resourceName}”的单独禁止，恢复按角色、补充授权和全员策略判断？`
        : value
          ? `将“${props.resourceName}”对${scope}改为需单独授权？角色默认权限和全局补充授权将不再适用于此资源，具体资源的补充授权仍有效。`
          : `将“${props.resourceName}”对${scope}恢复按角色与补充授权判断？用户的单独禁止仍然生效。`,
      "保存资源访问策略",
      {
        confirmButtonText: "确认保存",
        cancelButtonText: "取消",
        type: value ? "warning" : "info",
      },
    );
    const result = await api<Models["PolicyResponse"]>(path, "PUT", {
      restricted: value,
      expected_version: version,
    });
    if (current !== generation) return;
    policy.value = result;
    restricted.value = result.restricted;
    error.value = "";
    ElMessage.success("访问策略已更新");
  } catch (e) {
    if (e instanceof Error && current === generation) error.value = e.message;
  } finally {
    busy.value = false;
    emit("saving", false);
  }
}
</script>
<template>
  <div v-loading="loading" class="resource-policy">
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-descriptions :column="1" border>
      <el-descriptions-item label="管理对象">{{
        userId ? userName : "所有非管理员"
      }}</el-descriptions-item>
      <el-descriptions-item label="资源">{{
        resourceName
      }}</el-descriptions-item>
      <el-descriptions-item label="生效范围">{{
        ["application", "server"].includes(kind)
          ? "本资源及下属资源"
          : "仅本资源"
      }}</el-descriptions-item>
    </el-descriptions>
    <template v-if="policy && !loading">
      <div class="policy-state">
        <span>已保存策略</span>
        <el-tag :type="policy.restricted ? 'warning' : 'info'">{{
          userId
            ? policy.restricted
              ? "禁止访问"
              : "未单独禁止"
            : policy.restricted
              ? "需单独授权"
              : "按角色与补充授权"
        }}</el-tag>
        <template v-if="userId">
          <span>当前查看权限</span>
          <el-tag :type="policy.allowed ? 'success' : 'danger'">{{
            policy.allowed ? "可查看" : "不可查看"
          }}</el-tag>
        </template>
      </div>
      <strong>访问设置</strong>
      <el-radio-group
        v-model="restricted"
        :disabled="busy"
        aria-label="访问策略"
      >
        <el-radio :value="false" border>{{
          userId ? "不单独禁止" : "按角色与补充授权"
        }}</el-radio>
        <el-radio :value="true" border>{{
          userId
            ? "禁止访问（已有授权也不可访问）"
            : "需单独授权（角色默认权限不生效）"
        }}</el-radio>
      </el-radio-group>
      <el-tag v-if="restricted !== policy.restricted" type="warning"
        >有未保存的修改</el-tag
      >
      <div class="actions">
        <el-button :disabled="busy" @click="load">重新读取</el-button>
        <el-button
          type="primary"
          :loading="busy"
          :disabled="loading || restricted === policy.restricted"
          @click="save"
          >保存策略</el-button
        >
      </div>
    </template>
    <el-button v-else-if="!loading" @click="load">重新加载</el-button>
  </div>
</template>
<style scoped>
.resource-policy {
  display: grid;
  gap: 20px;
  min-height: 100px;
}
.policy-state {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.el-radio-group {
  display: grid;
  gap: 12px;
}
.el-radio {
  margin-right: 0;
  min-height: 44px;
  height: auto;
  padding: 12px;
}
.el-radio :deep(.el-radio__label) {
  white-space: normal;
  line-height: 1.5;
}
</style>
