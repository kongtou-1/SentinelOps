<script setup lang="ts">
import { computed } from "vue";
import "@/features/inspections/styles.css";
import { useRoute } from "vue-router";
import PlanList from "./PlanList.vue";
import PlanDetail from "./PlanDetail.vue";
import RunList from "./RunList.vue";
import RunDetail from "./RunDetail.vue";
import DataSources from "./DataSources.vue";
const route = useRoute();
const section = computed(() =>
  route.path.startsWith("/inspections/sources")
    ? "sources"
    : route.path === "/inspections" ||
        route.path === "/inspections/plans" ||
        route.path.startsWith("/inspections/services/") ||
        (route.params.planId && !route.path.endsWith("/runs"))
      ? "plans"
      : "runs",
);
const page = computed(() =>
  section.value === "sources"
    ? DataSources
    : section.value === "plans"
      ? route.params.planId
        ? PlanDetail
        : PlanList
      : route.params.id
        ? RunDetail
        : RunList,
);
</script>
<template>
  <div class="inspection-center">
    <component :is="page" :key="route.path" />
  </div>
</template>
