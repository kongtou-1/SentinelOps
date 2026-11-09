<script setup lang="ts">
import { ref, watch } from "vue";
import { api } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { date } from "@/utils/display";
import type { Row } from "@/types/records";
const props = defineProps<{ analysisId: string }>();
const auth = useAuth(),
  rows = ref<Row[]>([]),
  error = ref(""),
  busy = ref(false);
const form = ref({
  verdict: "inconclusive",
  confirmed_cause: "",
  evidence: "",
});
const labels: Record<string, string> = {
  correct: "正确",
  partial: "部分正确",
  incorrect: "错误",
  inconclusive: "无法判断",
};
async function load() {
  try {
    rows.value = await api(`/analyses/${props.analysisId}/reviews`);
    error.value = "";
  } catch (e) {
    error.value = String(e);
  }
}
watch(() => props.analysisId, load, { immediate: true });
async function save() {
  busy.value = true;
  try {
    await api(`/analyses/${props.analysisId}/reviews`, "POST", form.value);
    await load();
  } catch (e) {
    error.value = String(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <h2>人工评价</h2>
      <router-link
        v-if="auth.can('knowledge:manage')"
        :to="{ path: '/evaluations', query: { analysis: analysisId } }"
        ><el-button>收录故障案例</el-button></router-link
      >
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-form
      v-if="auth.can('analyses:run')"
      label-width="100px"
      @submit.prevent="save"
    >
      <el-form-item label="结论" required
        ><el-select v-model="form.verdict"
          ><el-option
            v-for="(label, key) in labels"
            :key="key"
            :label="label"
            :value="key" /></el-select
      ></el-form-item>
      <el-form-item label="确认根因"
        ><el-input
          v-model="form.confirmed_cause"
          type="textarea"
          :maxlength="4000"
      /></el-form-item>
      <el-form-item label="依据" required
        ><el-input v-model="form.evidence" type="textarea" :maxlength="8000"
      /></el-form-item>
      <el-button
        type="primary"
        :loading="busy"
        :disabled="!form.evidence.trim()"
        @click="save"
        >提交评价</el-button
      >
    </el-form>
    <el-table :data="rows"
      ><el-table-column label="结论"
        ><template #default="{ row }">{{
          labels[row.verdict]
        }}</template></el-table-column
      ><el-table-column
        prop="confirmed_cause"
        label="确认根因" /><el-table-column
        prop="evidence"
        label="依据" /><el-table-column label="评价时间"
        ><template #default="{ row }">{{
          date(row.created_at)
        }}</template></el-table-column
      ><el-table-column prop="reviewer_id" label="评价人"
    /></el-table>
  </section>
</template>
