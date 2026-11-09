<script setup lang="ts">
import { computed } from "vue";
import { label } from "@/utils/display";
const props = defineProps<{ value: unknown; text?: string }>();
const type = computed(() =>
  [
    "critical",
    "firing",
    "failed",
    "invalidated",
    "terminated",
    "unavailable",
  ].includes(String(props.value))
    ? "danger"
    : [
          "warning",
          "pending",
          "expired",
          "stale",
          "possible",
          "expected",
          "unknown",
          "provisional",
          "insufficient_data",
          "interval_low_confidence",
        ].includes(String(props.value))
      ? "warning"
      : [
            "healthy",
            "verified",
            "succeeded",
            "sent",
            "ready",
            "approved",
            "completed",
            "validated",
            "normal",
            "recovered",
          ].includes(String(props.value))
        ? "success"
        : [
              "open",
              "in_progress",
              "running",
              "queued",
              "executing",
              "verifying",
              "prechecking",
              "sending",
            ].includes(String(props.value))
          ? "primary"
          : "info",
);
</script>
<template>
  <el-tag :type="type" effect="light">{{ text ?? label(value) }}</el-tag>
</template>
