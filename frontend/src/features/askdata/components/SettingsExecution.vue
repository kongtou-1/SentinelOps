<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import {
  askdata,
  type AskDataExecutionSettings,
} from "@/features/askdata/api";

const props = defineProps<{ connectionId: string }>();
const settings = ref<AskDataExecutionSettings | null>(null);
const busy = ref(false);
const saving = ref(false);
const testing = ref(false);
const error = ref("");
const form = reactive({
  budget_seconds: 180,
  reasoning_channel_id: "",
  light_channel_id: "",
  vector_model: "",
  vector_dimension: 512,
  reranker_enabled: false,
  reranker_endpoint: "",
  reranker_model: "",
  reranker_api_key: "",
});

const vectorChanged = computed(
  () =>
    settings.value !== null &&
    (form.vector_model !== settings.value.vector_model ||
      form.vector_dimension !== settings.value.vector_dimension),
);

async function load() {
  if (!props.connectionId) {
    settings.value = null;
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const value = await askdata.executionSettings(props.connectionId);
    settings.value = value;
    form.budget_seconds = value.budget_seconds;
    form.reasoning_channel_id = value.reasoning_channel_id ?? "";
    form.light_channel_id = value.light_channel_id ?? "";
    form.vector_model = value.vector_model;
    form.vector_dimension = value.vector_dimension;
    form.reranker_enabled = value.reranker_enabled;
    form.reranker_endpoint = value.reranker_endpoint;
    form.reranker_model = value.reranker_model;
    form.reranker_api_key = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "设置加载失败";
  } finally {
    busy.value = false;
  }
}

async function save() {
  if (!settings.value) return;
  saving.value = true;
  error.value = "";
  try {
    const value = await askdata.updateExecutionSettings(props.connectionId, {
      expected_version: settings.value.version,
      budget_seconds: form.budget_seconds,
      reasoning_channel_id: form.reasoning_channel_id || null,
      light_channel_id: form.light_channel_id || null,
      vector_model: form.vector_model,
      vector_dimension: form.vector_dimension,
      reranker_enabled: form.reranker_enabled,
      reranker_endpoint: form.reranker_endpoint,
      reranker_model: form.reranker_model,
      reranker_api_key: form.reranker_api_key || null,
    });
    settings.value = value;
    form.reranker_api_key = "";
    ElMessage.success("问数设置已保存");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "保存失败";
  } finally {
    saving.value = false;
  }
}

async function test() {
  testing.value = true;
  error.value = "";
  try {
    const result = await askdata.testExecution(props.connectionId);
    ElMessage.success(`${result.message}${result.latency_ms ? ` · ${result.latency_ms}ms` : ""}`);
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "连接测试失败";
  } finally {
    testing.value = false;
  }
}

watch(() => props.connectionId, () => void load(), { immediate: true });
</script>

<template>
  <section v-loading="busy" class="execution">
    <div class="heading">
      <h2>模型与执行</h2>
      <el-button type="primary" :loading="saving" :disabled="!settings" @click="save">保存</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="settings">
      <el-form label-width="120px" class="form">
        <el-form-item label="推理通道">
          <el-select v-model="form.reasoning_channel_id" clearable placeholder="默认使用首个可用通道" style="width: 100%">
            <el-option
              v-for="channel in settings.channels"
              :key="channel.id"
              :label="`${channel.model} · ${channel.base_url}`"
              :value="channel.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="轻量处理通道">
          <el-select v-model="form.light_channel_id" clearable placeholder="与推理通道相同" style="width: 100%">
            <el-option
              v-for="channel in settings.channels"
              :key="channel.id"
              :label="`${channel.model} · ${channel.base_url}`"
              :value="channel.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="问数预算">
          <el-input-number v-model="form.budget_seconds" :min="30" :max="900" :step="30" />
          <span class="hint">秒；单条 SQL 仍沿用 5 秒、200 行、128 KiB 限制</span>
        </el-form-item>
        <el-form-item label="向量模型">
          <el-input v-model="form.vector_model" class="model-input" />
          <span class="hint">维度</span>
          <el-input-number v-model="form.vector_dimension" :min="1" :max="8192" />
          <span class="hint">当前签名 {{ settings.vector_signature }}</span>
        </el-form-item>
        <el-alert
          v-if="vectorChanged"
          title="变更向量模型或维度后，已有模型版本需要重新构建；旧向量不会被混用。"
          type="warning"
          :closable="false"
        />
        <el-form-item label="重排">
          <el-switch v-model="form.reranker_enabled" :disabled="!settings.reranker_verified_at" />
          <span class="hint">
            {{ settings.reranker_verified_at ? "已通过连接测试" : "需先保存地址并通过连接测试" }}
          </span>
        </el-form-item>
        <el-form-item label="重排地址">
          <el-input v-model="form.reranker_endpoint" placeholder="https://host/v1/rerank" />
        </el-form-item>
        <el-form-item label="重排模型">
          <el-input v-model="form.reranker_model" />
        </el-form-item>
        <el-form-item label="重排密钥">
          <el-input
            v-model="form.reranker_api_key"
            type="password"
            show-password
            :placeholder="settings?.reranker_configured ? '已配置，留空表示不修改' : '可选'"
          />
        </el-form-item>
        <el-form-item label="连接测试">
          <el-button :loading="testing" @click="test">测试重排服务</el-button>
          <span v-if="settings?.reranker_verified_at" class="hint">
            最近通过 {{ String(settings?.reranker_verified_at).slice(0, 16).replace("T", " ") }}
          </span>
        </el-form-item>
      </el-form>
    </template>
    <el-empty v-else-if="!busy" description="请选择数据库连接" />
  </section>
</template>

<style scoped>
.execution { display: grid; gap: 12px; min-width: 0; }
.heading { display: flex; align-items: center; justify-content: space-between; }
h2 { margin: 0; font-size: 18px; }
.form { max-width: 780px; }
.model-input { width: 280px; margin-right: 8px; }
.hint { margin-left: 8px; color: var(--muted, #8993a4); font-size: 12px; }
</style>
