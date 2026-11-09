<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { api, type Models } from "@/api/client";
import DiagnosisWorkbench from "@/features/diagnosis/components/DiagnosisWorkbench.vue";
const id = String(useRoute().params.id),
  incident = ref<Models["IncidentDetail"]>(),
  error = ref("");
onMounted(async () => {
  try {
    incident.value = await api(`/incidents/${id}`);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
});
</script>
<template>
  <div class="page-heading">
    <div>
      <router-link :to="`/incidents/${id}`" class="subtle-link"
        >← 返回事故详情</router-link
      >
      <div class="heading-copy">
        <h1>{{ incident?.title || "AI 诊断" }}</h1>
        <PageIntro />
        <p>围绕事故证据排查问题，确认结论后再发起审批。</p>
      </div>
    </div>

  </div>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
  /><DiagnosisWorkbench
    v-if="incident"
    :incident-id="id"
    :writable="!['closed', 'merged'].includes(incident.status)"
    fullscreen
  />
</template>
