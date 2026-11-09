<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { pageIntro } from "@/config/pageIntros";

const props = defineProps<{ text?: string }>();
const route = useRoute();
const intro = computed(() => props.text ?? pageIntro(route.path));
</script>

<template>
  <p v-if="intro" class="page-intro">{{ intro }}</p>
</template>
