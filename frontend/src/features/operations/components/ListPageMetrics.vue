<script setup lang="ts">
import { onUnmounted, ref, watch } from "vue";
import PageMetrics, {
  type PageMetric,
} from "@/components/common/PageMetrics.vue";
import { usePoll } from "@/composables/usePoll";
import {
  metricNames,
  metricTones,
  readPageMetrics,
} from "@/features/operations/pageMetrics";
const props = defineProps<{
  kind: string;
  refreshKey?: unknown;
  scope?: Record<string, unknown>;
}>();
const items = ref<PageMetric[]>([]),
  loading = ref(false),
  error = ref(false);
let controller: AbortController | undefined;
async function load() {
  controller?.abort();
  controller = new AbortController();
  const signal = controller.signal;
  items.value = (metricNames[props.kind] || []).map((label, i) => ({
    label,
    value: null,
    tone: metricTones[props.kind]?.[i],
  }));
  loading.value = true;
  error.value = false;
  try {
    const result = await readPageMetrics(props.kind, props.scope, signal);
    if (!signal.aborted) items.value = result;
  } catch {
    if (!signal.aborted) error.value = true;
  } finally {
    if (!signal.aborted) loading.value = false;
  }
}
usePoll(load, () => null);
watch(() => [props.kind, props.refreshKey, JSON.stringify(props.scope)], load);
onUnmounted(() => controller?.abort());
</script>
<template>
  <PageMetrics :items="items" :loading="loading" :error="error" />
</template>
