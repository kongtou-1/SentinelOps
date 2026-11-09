<script setup lang="ts">
import type { Row } from "@/types/records";
import LLMStatus from "./LLMStatus.vue";

defineProps<{ resources: Row[] }>();
</script>

<template>
  <section class="llm-overview" aria-label="LLM 服务状态列表">
    <div v-if="resources.length" class="panel-heading">
      <span class="muted">本页 {{ resources.length }} 个服务</span>
    </div>
    <div v-if="resources.length" class="llm-status-grid">
      <LLMStatus
        v-for="resource in resources"
        :key="resource.id"
        :id="resource.id"
        :name="resource.name"
        :model="resource.model"
        :base-url="resource.base_url"
        :refresh-key="resource.updated_at"
      />
    </div>
    <div v-else class="panel empty-hint">暂无可展示的 LLM 服务</div>
  </section>
</template>

<style scoped>
.llm-overview {
  margin-bottom: 20px;
}
.llm-status-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
}
.llm-status-grid > :deep(.llm-status) {
  margin-bottom: 0;
}
@media (max-width: 1100px) {
  .llm-status-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
