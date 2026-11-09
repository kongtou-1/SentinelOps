<script setup lang="ts">
import {
  actualValue,
  checkReasons,
  checkTitle,
  expectation,
} from "@/features/inspections/display";

defineProps<{ checks: Record<string, unknown>[] }>();
</script>

<template>
  <div class="checks">
    <section
      v-for="(check, index) in checks"
      :key="index"
      class="check"
      :class="`check-${check.status}`"
    >
      <div class="check-heading">
        <h4>{{ checkTitle(check) }}</h4>
        <span>{{
          check.status === "pass"
            ? "通过"
            : check.status === "fail"
              ? "未通过"
              : "资料不足"
        }}</span>
      </div>
      <p v-if="check.reason" class="check-reason">
        {{ checkReasons[String(check.reason)] || "资料不足，无法评估" }}
      </p>
      <div v-else class="check-values">
        <span
          >实际 <strong>{{ actualValue(check) }}</strong></span
        >
        <span
          >要求 <strong>{{ expectation(check) }}</strong></span
        >
      </div>
      <p v-if="check.basis" class="check-basis">依据：{{ check.basis }}</p>
    </section>
  </div>
</template>

<style scoped>
.checks {
  display: grid;
  gap: 14px;
}
.check {
  padding-left: 14px;
  border-left: 2px solid var(--el-border-color);
  min-width: 0;
}
.check-fail {
  border-left-color: var(--el-color-danger);
}
.check-unknown {
  border-left-color: var(--el-color-warning);
}
.check-heading {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
}
h4,
p {
  margin: 0;
}
h4 {
  font-size: 14px;
  font-weight: 600;
}
.check-heading > span {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.check-fail .check-heading > span {
  color: var(--el-color-danger);
}
.check-values {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 28px;
  margin-top: 9px;
  color: var(--el-text-color-secondary);
}
.check-values strong {
  margin-left: 6px;
  color: var(--el-text-color-primary);
  font-weight: 500;
}
.check-reason {
  margin-top: 9px;
}
.check-basis {
  margin-top: 9px;
  color: var(--el-text-color-regular);
  line-height: 1.6;
}
.check p,
.check strong,
h4 {
  overflow-wrap: anywhere;
}
</style>
