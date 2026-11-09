<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { confirmOperation } from "@/utils/confirmOperation";
import { api, type Models } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import {
  bundleState,
  toggleBundle,
  type Catalogue,
  type Bundle,
} from "@/features/access/model";
const auth = useAuth(),
  rows = ref<Models["RoleResponse"][]>([]),
  catalogue = ref<Catalogue>(),
  error = ref(""),
  loading = ref(false),
  editing = ref(false),
  busy = ref(false);
const administrator = computed(() => auth.user?.role === "admin");
const modules = computed(() =>
  Object.entries(catalogue.value?.modules || {})
    .map(([id, name]) => ({
      id,
      name,
      bundles: catalogue.value?.bundles.filter((b) => b.module === id) || [],
    }))
    .filter((m) => m.bundles.length),
);
const drafts = ref<Record<string, string[]>>({});
function resetDrafts() {
  drafts.value = Object.fromEntries(
    rows.value.map((r) => [r.code, [...r.permissions]]),
  );
}
function cancel() {
  resetDrafts();
  editing.value = false;
  error.value = "";
}
async function load() {
  loading.value = true;
  error.value = "";
  try {
    [rows.value, catalogue.value] = await Promise.all([
      api<Models["RoleResponse"][]>("/roles"),
      api<Catalogue>("/permissions"),
    ]);
    resetDrafts();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
}
onMounted(load);
function toggle(code: string, bundle: Bundle, checked: boolean) {
  if (!editing.value || !administrator.value || busy.value || loading.value)
    return;
  drafts.value[code] = toggleBundle(drafts.value[code] || [], bundle, checked);
}
async function save() {
  if (!editing.value || !administrator.value || busy.value || loading.value)
    return;
  busy.value = true;
  try {
    if (
      !(await confirmOperation(
        "保存角色默认权限？变更将影响这些角色下所有账号的访问与操作权限。",
        "保存默认权限",
      ))
    )
      return;
    for (const row of rows.value.filter((r) => r.code !== "admin")) {
      if (
        JSON.stringify([...row.permissions].sort()) !==
        JSON.stringify([...(drafts.value[row.code] || [])].sort())
      )
        await api(`/roles/${row.code}/permissions`, "PUT", {
          expected_version: row.permissions_version,
          permissions: drafts.value[row.code],
        });
    }
    await load();
    await auth.load();
    editing.value = false;
    ElMessage.success("默认权限已保存");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "保存失败";
    await load();
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <section class="page-header">
    <div class="page-heading">
      <h1>角色与权限</h1>
      <PageIntro />

      <div class="actions">
        <template v-if="editing && administrator">
          <el-button :disabled="busy" @click="cancel">取消</el-button>
          <el-button type="primary" :loading="busy" @click="save"
            >保存默认权限</el-button
          >
        </template>
        <template v-else>
          <el-button :loading="loading" @click="load">刷新</el-button>
          <el-button
            v-if="administrator"
            type="primary"
            :disabled="loading || !!error || !rows.length"
            @click="editing = true"
            >编辑默认权限</el-button
          >
        </template>
      </div>
    </div>
  </section>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
    class="error-box"
  />
  <section class="panel">
    <el-table v-loading="loading" :data="modules"
      ><el-table-column prop="name" label="业务模块" width="170" />
      <el-table-column
        v-for="role in rows"
        :key="role.code"
        :label="
          role.code === 'viewer'
            ? '查看者'
            : role.code === 'operator'
              ? '运维人员'
              : '管理员'
        "
        min-width="230"
        ><template #default="s"
          ><el-tag v-if="role.code === 'admin'" type="success">完整权限</el-tag
          ><template v-else
            ><el-checkbox
              v-for="bundle in s.row.bundles"
              :key="bundle.id"
              :disabled="!editing || !administrator || busy || loading"
              :model-value="
                bundleState(drafts[role.code] || [], bundle).checked
              "
              :indeterminate="
                bundleState(drafts[role.code] || [], bundle).partial
              "
              @change="toggle(role.code, bundle, !!$event)"
              >{{ bundle.name
              }}<el-tag
                v-if="bundleState(drafts[role.code] || [], bundle).partial"
                size="small"
                type="info"
                >部分选中</el-tag
              ></el-checkbox
            ></template
          ></template
        ></el-table-column
      >
    </el-table>
  </section>
</template>
