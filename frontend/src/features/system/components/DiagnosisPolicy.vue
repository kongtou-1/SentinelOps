<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { confirmOperation } from "@/utils/confirmOperation";
import { api, type Models } from "@/api/client";
import { useAuth } from "@/features/auth/store";

const auth = useAuth();
const policy = ref<Models["DiagnosisPolicyResponse"]>();
const enabled = ref(false),
  busy = ref(false),
  error = ref("");
onMounted(async () => {
  try {
    policy.value = await api("/system/diagnosis-policy");
    enabled.value = policy.value!.enabled;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "配置读取失败";
  }
});
async function save() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    if (
      enabled.value !== policy.value?.enabled &&
      !(await confirmOperation(
        enabled.value
          ? "启用新事故自动诊断？符合条件的新事故将自动调用模型。"
          : "停用新事故自动诊断？之后的新事故需人工发起诊断。",
        enabled.value ? "启用自动诊断" : "停用自动诊断",
      ))
    )
      return;
    policy.value = await api("/system/diagnosis-policy", "PUT", {
      enabled: enabled.value,
    });
    ElMessage.success("自动诊断配置已保存");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "配置保存失败";
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <el-tag v-if="policy" :type="policy.ai_available ? 'success' : 'warning'">
        {{ policy.ai_available ? "模型配置就绪" : "模型配置未就绪" }}
      </el-tag>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-form v-if="policy" label-width="150px">
      <el-form-item label="新事故自动诊断">
        <el-switch
          v-model="enabled"
          :disabled="!auth.can('rules:manage') || busy"
        />
      </el-form-item>
      <el-form-item label="触发次数">每个事故一次</el-form-item>
      <el-form-item label="规则覆盖">跟随全局 / 自动诊断 / 仅手动</el-form-item>
      <el-form-item label="处置执行">人工审批</el-form-item>
      <el-form-item v-if="auth.can('rules:manage')">
        <el-button type="primary" :loading="busy" @click="save">保存</el-button>
      </el-form-item>
    </el-form>
  </section>
</template>
