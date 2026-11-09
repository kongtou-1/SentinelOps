<script setup lang="ts">
import { ref, watch } from "vue";
import type { Row } from "@/types/records";

const form = defineModel<Row>({ required: true });
defineProps<{ hasGitToken?: boolean; hasSvnPassword?: boolean }>();
const repositoryKind = ref("git");
watch(
  () => form.value,
  (value) => {
    repositoryKind.value = value.svn_url
      ? "svn"
      : value.git_url
        ? "git"
        : value.local_path
          ? "local"
          : "git";
  },
  { immediate: true },
);
function changeRepositoryKind(value: string) {
  if (value !== "git") {
    form.value.git_url = null;
    form.value.git_ref = "HEAD";
    form.value.git_username = "";
    form.value.git_token = "";
    form.value.clear_git_token = false;
  }
  if (value !== "svn") {
    form.value.svn_url = null;
    form.value.svn_revision = "HEAD";
    form.value.svn_username = "";
    form.value.svn_password = "";
    form.value.clear_svn_password = false;
  }
}
const fields = [
  ["source_subdir", "主要源码目录", "仓库内相对目录，例如 backend；默认 ."],
  ["entrypoint", "服务入口", "例如 app.main:app 或 app.ai_worker"],
];
</script>

<template>
  <el-form-item label="源码来源">
    <el-select v-model="repositoryKind" @change="changeRepositoryKind">
      <el-option label="Git" value="git" />
      <el-option label="SVN" value="svn" />
      <el-option label="本地目录" value="local" />
    </el-select>
  </el-form-item>
  <template v-if="repositoryKind === 'git'">
    <el-form-item label="Git HTTPS 地址">
      <el-input v-model="form.git_url" placeholder="https://…" />
    </el-form-item>
    <el-form-item label="Git 分支/标签">
      <el-input v-model="form.git_ref" placeholder="HEAD" />
    </el-form-item>
    <el-form-item label="Git 用户名" :required="!!form.git_token">
      <el-input v-model="form.git_username" />
    </el-form-item>
    <el-form-item
      :label="hasGitToken ? 'Git 令牌（已保存，留空保留）' : 'Git 只读令牌'"
    >
      <el-input
        v-model="form.git_token"
        type="password"
        show-password
        autocomplete="new-password"
      />
    </el-form-item>
    <el-checkbox v-if="hasGitToken" v-model="form.clear_git_token"
      >移除已保存的令牌</el-checkbox
    >
  </template>
  <template v-else-if="repositoryKind === 'svn'">
    <el-form-item label="SVN HTTPS 地址（仓库或分支目录）">
      <el-input v-model="form.svn_url" placeholder="https://…/trunk" />
    </el-form-item>
    <el-form-item label="SVN revision">
      <el-input
        v-model="form.svn_revision"
        placeholder="HEAD 或版本号，例如 123"
      />
    </el-form-item>
    <el-form-item label="SVN 用户名" :required="!!form.svn_password">
      <el-input v-model="form.svn_username" />
    </el-form-item>
    <el-form-item
      :label="
        hasSvnPassword ? 'SVN 密码（已保存，留空保留）' : 'SVN 只读账号密码'
      "
    >
      <el-input
        v-model="form.svn_password"
        type="password"
        show-password
        autocomplete="new-password"
      />
    </el-form-item>
    <el-checkbox v-if="hasSvnPassword" v-model="form.clear_svn_password"
      >移除已保存的密码</el-checkbox
    >
  </template>
  <el-form-item v-for="field in fields" :key="field[0]" :label="field[1]">
    <el-input v-model="form[field[0]!]" :placeholder="field[2]" />
  </el-form-item>
  <el-form-item
    :label="repositoryKind === 'local' ? '本地目录' : '本地后备目录'"
  >
    <el-input
      v-model="form.local_path"
      placeholder="只读挂载入口内的相对目录；. 代表入口本身"
    />
  </el-form-item>
  <el-form-item
    label="容器运行路径 → 仓库路径"
    :required="!!form.path_mappings.length"
  >
    <div class="mapping-list">
      <div v-for="(m, i) in form.path_mappings" :key="i" class="actions">
        <el-input v-model="m.runtime_prefix" placeholder="/app/app" /><span
          >→</span
        >
        <el-input v-model="m.repository_prefix" placeholder="backend/app" />
        <el-button @click="form.path_mappings.splice(i, 1)">移除</el-button>
      </div>
      <el-button
        @click="
          form.path_mappings.push({ runtime_prefix: '', repository_prefix: '' })
        "
        >添加映射</el-button
      >
    </div>
  </el-form-item>
  <el-switch v-model="form.enabled" active-text="启用源码连接" />
</template>

<style scoped>
.mapping-list {
  width: 100%;
}
.mapping-list .el-input {
  flex: 1;
  min-width: 120px;
}
</style>
