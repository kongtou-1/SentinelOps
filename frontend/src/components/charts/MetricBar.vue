<script setup lang="ts">
import { computed } from "vue";
const props = defineProps<{ value?: number | null }>();
const valid = computed(
  () =>
    props.value !== null &&
    props.value !== undefined &&
    Number.isFinite(props.value),
);
</script>
<template>
  <div class="metric-bar">
    <span>{{ valid ? value!.toFixed(0) + "%" : "—" }}</span>
    <div v-if="valid" class="metric-bar-track">
      <i
        :class="{ warning: value! >= 60, danger: value! >= 85 }"
        :style="{ width: Math.min(100, Math.max(0, value!)) + '%' }"
      />
    </div>
  </div>
</template>
