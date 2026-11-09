<script setup lang="ts">
import { onMounted, ref } from "vue";
import { all, type Models } from "@/api/client";

const owner = defineModel<string>({ required: true });
const users = ref<Models["UserResponse"][]>([]),
  loading = ref(false),
  error = ref("");

async function load() {
  loading.value = true;
  error.value = "";
  try {
    users.value = (await all<Models["UserResponse"]>("/users")).filter(
      (user) => user.is_active,
    );
  } catch (e) {
    error.value = e instanceof Error ? e.message : "负责人列表加载失败";
  } finally {
    loading.value = false;
  }
}
onMounted(load);
</script>

<template>
  <el-select
    v-model="owner"
    filterable
    clearable
    :loading="loading"
    aria-label="负责人"
    placeholder="搜索并选择系统用户"
    no-match-text="没有匹配的用户"
    no-data-text="暂无可选择的启用用户"
    style="width: 100%"
  >
    <el-option
      v-for="user in users"
      :key="user.id"
      :value="user.username"
      :label="user.username"
    />
    <el-option
      v-if="owner && !users.some((user) => user.username === owner)"
      :value="owner"
      :label="owner + '（原记录）'"
      disabled
    />
  </el-select>
  <el-alert v-if="error" :title="error" type="error" :closable="false">
    <el-button link type="primary" @click="load">重新加载</el-button>
  </el-alert>
</template>
