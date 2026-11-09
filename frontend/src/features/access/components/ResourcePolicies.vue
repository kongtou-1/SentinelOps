<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api, query, request, type Models } from "@/api/client";
import { confirmOperation } from "@/utils/confirmOperation";
import { resourceKinds } from "@/features/access/model";
import { policyPath, updatePolicies } from "@/features/access/api";
import ResourceAccess from "./ResourceAccess.vue";

type Resource = { id: string; name: string; kind: string };
type Row = Resource & {
  key: string;
  userId?: string;
  userName?: string;
  policy?: Models["PolicyResponse"];
  error?: string;
};
const props = defineProps<{ users: Models["UserResponse"][] }>();
const view = ref("resources"),
  kind = ref("application"),
  search = ref(""),
  scope = ref("all"),
  userId = ref(""),
  role = ref(""),
  page = ref(1),
  pageSize = ref(20),
  total = ref(0),
  loading = ref(false),
  busy = ref(false),
  error = ref("");
const rows = ref<Row[]>([]),
  selected = ref<Row[]>([]),
  editing = ref<Row>();
const drawerOpen = ref(false);
const resource = ref<Resource>(),
  targets = ref<Resource[]>([]),
  targetLoading = ref(false);
const roleView = computed(() => view.value === "roles");
const personal = computed(() => roleView.value || scope.value === "user");
const roleName = computed(() =>
  role.value === "operator" ? "运维人员" : "查看者",
);
const unit = computed(() => (roleView.value ? "位用户" : "项资源"));
const users = computed(() =>
  props.users.filter(
    (u) =>
      u.role !== "admin" &&
      u.is_active &&
      (!role.value || u.role === role.value),
  ),
);
const userName = computed(
  () => users.value.find((u) => u.id === userId.value)?.username,
);
const subject = computed(() =>
  roleView.value
    ? `${roleName.value} · ${resource.value?.name || "未选择资源"}`
    : scope.value === "all"
      ? "所有非管理员"
      : userName.value,
);
const ready = computed(() =>
  roleView.value ? !!resource.value : scope.value === "all" || !!userName.value,
);
let targetGeneration = 0;
async function loadTargets(q = "") {
  const current = ++targetGeneration;
  targetLoading.value = true;
  try {
    const result = await api<Resource[]>(
      "/access-targets" + query({ kind: kind.value, q, limit: 100 }),
    );
    if (current === targetGeneration) targets.value = result;
  } catch (e) {
    if (current === targetGeneration)
      error.value = e instanceof Error ? e.message : "资源加载失败";
  } finally {
    if (current === targetGeneration) targetLoading.value = false;
  }
}
let generation = 0;
async function load() {
  const current = ++generation;
  selected.value = [];
  rows.value = [];
  total.value = 0;
  error.value = "";
  if (!ready.value) {
    loading.value = false;
    return;
  }
  loading.value = true;
  try {
    let items: Row[];
    if (roleView.value) {
      const matches = users.value.filter((u) =>
        u.username.toLowerCase().includes(search.value.trim().toLowerCase()),
      );
      total.value = matches.length;
      items = matches
        .slice((page.value - 1) * pageSize.value, page.value * pageSize.value)
        .map((u) => ({
          ...resource.value!,
          key: u.id,
          userId: u.id,
          userName: u.username,
        }));
    } else {
      const result = await request<Resource[]>(
        "/access-targets" +
          query({
            kind: kind.value,
            q: search.value.trim(),
            offset: (page.value - 1) * pageSize.value,
            limit: pageSize.value,
          }),
      );
      if (current !== generation) return;
      total.value = result.total ?? result.data.length;
      items = result.data.map((item) => ({
        ...item,
        key: item.id,
        userId: scope.value === "user" ? userId.value : undefined,
        userName: scope.value === "user" ? userName.value : undefined,
      }));
    }
    const paths = items.map(policyPath);
    const loaded = await Promise.all(
      items.map(async (resource, index): Promise<Row> => {
        try {
          return {
            ...resource,
            policy: await api<Models["PolicyResponse"]>(paths[index]!),
          };
        } catch (e) {
          return {
            ...resource,
            error: e instanceof Error ? e.message : "读取失败",
          };
        }
      }),
    );
    if (current === generation) rows.value = loaded;
  } catch (e) {
    if (current === generation)
      error.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    if (current === generation) loading.value = false;
  }
}
function resetPage() {
  if (page.value !== 1) page.value = 1;
  else void load();
}
watch(role, () => {
  userId.value = "";
  if (roleView.value) resetPage();
});
watch(view, () => {
  search.value = "";
  if (roleView.value && !role.value) role.value = "operator";
  resetPage();
});
watch(kind, () => {
  resource.value = undefined;
  targets.value = [];
  ++targetGeneration;
  targetLoading.value = false;
  resetPage();
});
watch([scope, userId, pageSize, resource], resetPage);
watch(page, load);
void load();
function edit(row: Row) {
  editing.value = row;
  drawerOpen.value = true;
}
async function apply(restricted: boolean) {
  const changed = selected.value
    .filter((row) => row.policy && row.policy.restricted !== restricted)
    .map((row) => ({
      kind: row.kind,
      id: row.id,
      userId: row.userId,
      name: roleView.value ? row.userName! : row.name,
      version: row.policy!.version,
    }));
  if (busy.value || loading.value || !ready.value) return;
  if (!changed.length) {
    ElMessage.info("所选项已是该设置，无需修改");
    return;
  }
  const action = personal.value
    ? restricted
      ? "禁止访问"
      : "取消单独禁止"
    : restricted
      ? "改为需单独授权"
      : "恢复角色与补充授权";
  const confirmation = roleView.value
    ? `将“${resource.value!.name}”对选中的 ${changed.length} 位用户${restricted ? "设为禁止访问" : "取消单独禁止"}`
    : `为“${subject.value}”将选中的 ${changed.length} 项资源${action}`;
  busy.value = true;
  try {
    if (
      !(await confirmOperation(
        `${confirmation}？${["application", "server"].includes(kind.value) ? "设置同时作用于下属资源。" : ""}`,
        `批量${action}`,
      ))
    )
      return;
    const { succeeded, failed } = await updatePolicies(changed, restricted);
    await load();
    if (failed.length)
      error.value = `已完成 ${succeeded} 项，失败 ${failed.length} 项。${failed.join("；")}`;
    else ElMessage.success(`已更新 ${succeeded} ${unit.value}`);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="resource-policies">
    <el-radio-group
      v-model="view"
      :disabled="busy || drawerOpen"
      aria-label="资源策略查看方式"
    >
      <el-radio-button value="resources">按资源查看</el-radio-button>
      <el-radio-button value="roles">按角色查看</el-radio-button>
    </el-radio-group>
    <el-form
      class="policy-filters"
      label-position="top"
      :disabled="busy || drawerOpen"
    >
      <el-form-item v-if="!roleView" label="管理对象">
        <el-select v-model="scope" aria-label="管理对象">
          <el-option label="所有非管理员" value="all" />
          <el-option label="指定用户" value="user" />
        </el-select>
      </el-form-item>
      <template v-if="roleView || scope === 'user'">
        <el-form-item :label="roleView ? '用户角色' : '筛选用户角色'">
          <el-select
            v-model="role"
            :clearable="!roleView"
            placeholder="全部非管理员角色"
            aria-label="筛选用户角色"
          >
            <el-option label="运维人员" value="operator" />
            <el-option label="查看者" value="viewer" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="!roleView" label="用户">
          <el-select
            v-model="userId"
            filterable
            placeholder="选择用户"
            aria-label="用户"
          >
            <el-option
              v-for="user in users"
              :key="user.id"
              :value="user.id"
              :label="user.username"
            />
          </el-select>
        </el-form-item>
      </template>
      <el-form-item label="资源类型">
        <el-select v-model="kind" aria-label="资源类型">
          <el-option
            v-for="item in resourceKinds.filter((r) => r.value !== 'global')"
            :key="item.value"
            :value="item.value"
            :label="item.label"
          />
        </el-select>
      </el-form-item>
      <el-form-item v-if="roleView" label="资源">
        <el-select
          :model-value="resource?.id"
          filterable
          remote
          :remote-method="loadTargets"
          :loading="targetLoading"
          placeholder="搜索并选择资源"
          aria-label="角色视图资源"
          @visible-change="$event && loadTargets()"
          @change="resource = targets.find((item) => item.id === $event)"
        >
          <el-option
            v-if="resource && !targets.some((item) => item.id === resource?.id)"
            :value="resource.id"
            :label="resource.name"
          />
          <el-option
            v-for="item in targets"
            :key="item.id"
            :value="item.id"
            :label="item.name"
          />
        </el-select>
      </el-form-item>
      <el-form-item :label="roleView ? '用户名' : '资源名称'">
        <el-input
          v-model="search"
          clearable
          :placeholder="roleView ? '搜索当前角色的用户' : '搜索资源'"
          @keyup.enter="resetPage"
          @clear="resetPage"
        />
      </el-form-item>
      <el-form-item class="filter-actions">
        <el-button type="primary" @click="resetPage">查询</el-button>
        <el-button @click="load">刷新列表</el-button>
      </el-form-item>
    </el-form>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <template v-if="ready">
      <div class="policy-toolbar">
        <div class="selection-state">
          <strong>{{ subject }}</strong
          ><span>已选 {{ selected.length }} {{ unit }}</span>
        </div>
        <div class="actions">
          <el-button
            :disabled="!selected.length || busy || loading || drawerOpen"
            @click="apply(false)"
          >
            {{ personal ? "批量取消禁止" : "批量恢复角色与补充授权" }}
          </el-button>
          <el-button
            type="warning"
            :loading="busy"
            :disabled="!selected.length || loading || drawerOpen"
            @click="apply(true)"
          >
            {{ personal ? "批量禁止访问" : "批量设为需单独授权" }}
          </el-button>
        </div>
      </div>
      <el-table
        v-loading="loading"
        :data="rows"
        row-key="key"
        :empty-text="roleView ? '该角色下暂无匹配的启用用户' : '暂无匹配资源'"
        @selection-change="selected = $event"
      >
        <el-table-column
          type="selection"
          width="48"
          :selectable="(row: Row) => !!row.policy && !busy && !drawerOpen"
        />
        <el-table-column
          v-if="roleView"
          prop="userName"
          label="用户"
          min-width="150"
        />
        <el-table-column v-if="roleView" label="角色" min-width="110"
          ><template #default>{{ roleName }}</template></el-table-column
        >
        <el-table-column prop="name" label="资源名称" min-width="180" />
        <el-table-column label="生效范围" min-width="150">
          <template #default="s">{{
            ["application", "server"].includes(s.row.kind)
              ? "本资源及下属资源"
              : "仅本资源"
          }}</template>
        </el-table-column>
        <el-table-column label="已保存设置" min-width="190">
          <template #default="s">
            <el-tag
              v-if="s.row.policy"
              :type="s.row.policy.restricted ? 'warning' : 'info'"
            >
              {{
                personal
                  ? s.row.policy.restricted
                    ? "禁止访问"
                    : "未单独禁止"
                  : s.row.policy.restricted
                    ? "需单独授权"
                    : "按角色与补充授权"
              }}
            </el-tag>
            <el-text v-else type="danger">{{ s.row.error }}</el-text>
          </template>
        </el-table-column>
        <el-table-column v-if="personal" label="当前查看权限" min-width="130">
          <template #default="s"
            ><el-tag
              v-if="s.row.policy"
              :type="s.row.policy.allowed ? 'success' : 'danger'"
              >{{ s.row.policy.allowed ? "可查看" : "不可查看" }}</el-tag
            ></template
          >
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="s"
            ><el-button
              link
              type="primary"
              :disabled="busy || !s.row.policy"
              @click="edit(s.row)"
              >设置</el-button
            ></template
          >
        </el-table-column>
      </el-table>
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[20, 50, 100]"
        :total="total"
        layout="sizes, prev, pager, next"
        :disabled="busy || loading || drawerOpen"
      />
    </template>
    <el-empty
      v-else
      :description="
        roleView ? '选择资源后查看该角色下的用户' : '选择用户后查看资源限制'
      "
    />
    <el-drawer
      v-model="drawerOpen"
      title="设置资源访问"
      size="min(620px, 96vw)"
      :close-on-click-modal="!busy"
      :close-on-press-escape="!busy"
      :show-close="!busy"
      @closed="load"
    >
      <ResourceAccess
        v-if="drawerOpen && editing"
        :kind="editing.kind"
        :id="editing.id"
        :resource-name="editing.name"
        :user-id="editing.userId"
        :user-name="editing.userName"
        @saving="busy = $event"
      />
    </el-drawer>
  </div>
</template>
<style scoped>
.resource-policies {
  display: grid;
  gap: 20px;
}
.policy-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0 16px;
  align-items: end;
}
.policy-filters .el-form-item {
  width: 210px;
  margin-bottom: 0;
}
.policy-filters .filter-actions {
  width: auto;
  margin-top: 20px;
}
.policy-toolbar,
.selection-state {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
}
.policy-toolbar {
  justify-content: space-between;
}
.selection-state span {
  color: var(--el-text-color-regular);
}
.el-pagination {
  justify-content: flex-end;
  max-width: 100%;
  overflow-x: auto;
}
@media (max-width: 600px) {
  .policy-filters .el-form-item {
    width: 100%;
    margin-bottom: 16px;
  }
}
</style>
