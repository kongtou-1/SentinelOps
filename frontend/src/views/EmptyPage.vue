<script setup lang="ts">
import { useRoute } from "vue-router";
const route = useRoute();
defineProps<{ title: string; description: string }>();
</script>
<template>
  <el-result icon="info" :title="title" :sub-title="description"
    ><template #extra
      ><router-link to="/"
        ><el-button type="primary">返回总览</el-button></router-link
      ><router-link :to="{ path: '/access', query: route.query }"
        ><el-button>申请权限</el-button></router-link
      ></template
    ></el-result
  >
</template>
