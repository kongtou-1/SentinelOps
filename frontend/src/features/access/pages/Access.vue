<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { confirmOperation } from "@/utils/confirmOperation";
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, all, query, type Models } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import {
  bundleState,
  ownsBundle,
  missingPermissionItems,
  durations,
  resourceKinds,
  type Catalogue,
} from "@/features/access/model";
import ResourcePolicies from "@/features/access/components/ResourcePolicies.vue";
type AccessRequest = Models["AccessRequestResponse"];
type Grant = Models["AccessGrantResponse"];
const auth = useAuth(),
  route = useRoute();
const administrator = computed(() => auth.user?.role === "admin");
const catalogue = ref<Catalogue>();
const requests = ref<AccessRequest[]>([]),
  grants = ref<Grant[]>([]);
const users = ref<Models["UserResponse"][]>([]);
const tab = ref(administrator.value ? "pending" : "grants"),
  error = ref(""),
  busy = ref(false);
const formOpen = ref(false),
  kind = ref("application"),
  target = ref(""),
  selected = ref<string[]>([]);
const selectedTargets = ref<string[]>([]);
const requestPermissions = ref<Record<string, string[]>>({}),
  checkingPermissions = ref(false),
  requestError = ref("");
let permissionCheck = 0;
const targets = ref<{ id: string; name: string; kind: string }[]>([]);
const days = ref(7),
  reason = ref(""),
  idempotencyKey = ref(crypto.randomUUID());
const decisionOpen = ref(false),
  decision = ref<AccessRequest>(),
  decisionDays = ref(7),
  comment = ref("");
const statusLabels: Record<string, string> = {
  pending: "待审批",
  approved: "已批准",
  rejected: "已拒绝",
  withdrawn: "已撤回",
  invalidated: "已失效",
};
const permissionNames = computed(() =>
  Object.fromEntries(
    catalogue.value?.permissions.map((p) => [p.code, p.name]) || [],
  ),
);
const modules = computed(() =>
  Object.entries(catalogue.value?.modules || {})
    .map(([id, name]) => ({
      id,
      name,
      bundles:
        catalogue.value?.bundles.filter(
          (b) => b.module === id && b.scopes?.includes(kind.value),
        ) || [],
    }))
    .filter((m) => m.bundles.length),
);
const owns = (targetId: string, bundleId: string) => {
  const bundle = catalogue.value?.bundles.find((item) => item.id === bundleId);
  return !!bundle && ownsBundle(requestPermissions.value[targetId], bundle);
};
const ownedOnAllTargets = (bundleId: string) =>
  !!selectedTargets.value.length &&
  selectedTargets.value.every((id) => owns(id, bundleId));
const ownedOnSomeTargets = (bundleId: string) =>
  selectedTargets.value.some((id) => owns(id, bundleId));

async function checkRequestPermissions() {
  const generation = ++permissionCheck;
  requestPermissions.value = {};
  requestError.value = "";
  if (!formOpen.value || !selectedTargets.value.length || !catalogue.value) {
    checkingPermissions.value = false;
    return;
  }
  checkingPermissions.value = true;
  const targetIds = [...selectedTargets.value];
  const permissions = [
    ...new Set(
      modules.value.flatMap((module) =>
        module.bundles.flatMap((bundle) => bundle.permissions),
      ),
    ),
  ];
  const items = targetIds.flatMap((resource_id) =>
    permissions.map((permission) => ({
      permission,
      resource_kind: kind.value,
      resource_id,
      include_descendants: true,
    })),
  );
  try {
    const batches = [];
    for (let index = 0; index < items.length; index += 100)
      batches.push(
        api<{ resource_id: string; permission: string; allowed: boolean }[]>(
          "/access/check",
          "POST",
          { items: items.slice(index, index + 100) },
        ),
      );
    const results = (await Promise.all(batches)).flat();
    if (generation !== permissionCheck) return;
    requestPermissions.value = Object.fromEntries(
      targetIds.map((id) => [
        id,
        results
          .filter((item) => item.resource_id === id && item.allowed)
          .map((item) => item.permission),
      ]),
    );
    selected.value = selected.value.filter(
      (bundleId) => !ownedOnAllTargets(bundleId),
    );
  } catch (e) {
    if (generation === permissionCheck)
      requestError.value = e instanceof Error ? e.message : "权限状态读取失败";
  } finally {
    if (generation === permissionCheck) checkingPermissions.value = false;
  }
}
watch(
  [
    kind,
    selectedTargets,
    formOpen,
    () => catalogue.value,
    () => auth.user?.permissions_version,
  ],
  checkRequestPermissions,
  { deep: true },
);
const defaultModules = computed(() =>
  Object.entries(catalogue.value?.modules || {})
    .map(([id, name]) => ({
      id,
      name,
      bundles:
        catalogue.value?.bundles.filter(
          (b) =>
            b.module === id &&
            (b.primary_permissions || b.permissions).some((p) =>
              auth.user?.default_permissions?.includes(p),
            ),
        ) || [],
    }))
    .filter((m) => m.bundles.length),
);
const visibleRequests = computed(() =>
  requests.value.filter(
    (r) => tab.value !== "pending" || r.status === "pending",
  ),
);
const userNames = computed(() =>
  Object.fromEntries(users.value.map((u) => [u.id, u.username])),
);
const groupedGrants = computed(() => {
  const groups = new Map<
    string,
    {
      id: string;
      request: string;
      user: string;
      resource: string;
      permissions: string[];
      expires: string | null;
      revoked: boolean;
    }
  >();
  for (const grant of grants.value) {
    const key = `${grant.request_id}:${grant.resource_kind}:${grant.resource_id}`;
    let row = groups.get(key);
    if (!row) {
      const request = requests.value.find((r) => r.id === grant.request_id);
      const item = request?.items.find(
        (i) =>
          i.resource_kind === grant.resource_kind &&
          i.resource_id === grant.resource_id,
      );
      row = {
        id: grant.id,
        request: grant.request_id,
        user: grant.user_id,
        resource: String(item?.resource_name || grant.resource_id),
        permissions: item ? itemName(item).split("、") : [],
        expires: grant.expires_at ?? null,
        revoked: !!grant.revoked_at,
      };
      groups.set(key, row);
    }
    if (!requests.value.find((r) => r.id === grant.request_id))
      row.permissions = [
        ...new Set([
          ...row.permissions,
          permissionNames.value[grant.permission] || "业务权限",
        ]),
      ];
    row.revoked = row.revoked && !!grant.revoked_at;
  }
  return [...groups.values()];
});
async function load() {
  try {
    const [catalogueResult, requestResult, grantResult, userResult] =
      await Promise.all([
        api<Catalogue>("/permissions"),
        all<AccessRequest>("/access-requests"),
        all<Grant>("/access-grants"),
        administrator.value
          ? all<Models["UserResponse"]>("/users")
          : Promise.resolve([]),
      ]);
    users.value = userResult;
    catalogue.value = catalogueResult;
    requests.value = requestResult;
    grants.value = grantResult;
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () => 30000);
async function loadTargets(search = "") {
  try {
    targets.value = await api(
      "/access-targets" + query({ kind: kind.value, q: search, limit: 100 }),
    );
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
watch(
  kind,
  () => {
    target.value = "";
    selectedTargets.value = [];
    selected.value = [];
    void loadTargets();
  },
  { flush: "sync" },
);
function openRequest() {
  if (administrator.value) return;
  kind.value = String(route.query.resource_kind || "application");
  target.value = String(route.query.resource_id || "");
  selectedTargets.value = target.value ? [target.value] : [];
  selected.value = kind.value === "dashboard" ? ["dashboards.view"] : [];
  reason.value = "";
  days.value = 7;
  idempotencyKey.value = crypto.randomUUID();
  formOpen.value = true;
  void loadTargets();
}
onMounted(() => {
  if (route.query.resource_kind) openRequest();
});
async function submit() {
  if (busy.value || checkingPermissions.value) return;
  if (
    !selectedTargets.value.length ||
    !selected.value.length ||
    !reason.value.trim()
  ) {
    ElMessage.warning("请选择资源、权限并填写理由");
    return;
  }
  busy.value = true;
  try {
    await checkRequestPermissions();
    if (requestError.value || checkingPermissions.value) return;
    const items = missingPermissionItems(
      kind.value,
      selectedTargets.value,
      selected.value,
      catalogue.value?.bundles || [],
      requestPermissions.value,
    );
    if (!items.length) {
      ElMessage.warning("所选权限已拥有，无需申请");
      return;
    }
    await api("/access-requests", "POST", {
      idempotency_key: idempotencyKey.value,
      reason: reason.value,
      duration_days: days.value || null,
      items,
    });
    formOpen.value = false;
    tab.value = "requests";
    await refresh();
    ElMessage.success("申请已提交");
  } catch (e) {
    requestError.value = e instanceof Error ? e.message : "提交失败";
  } finally {
    busy.value = false;
  }
}
function openDecision(row: AccessRequest) {
  decision.value = row;
  decisionDays.value = row.duration_days || 0;
  comment.value = "";
  decisionOpen.value = true;
}
async function decide(approved: boolean) {
  if (!decision.value) return;
  busy.value = true;
  try {
    await api(
      `/access-requests/${decision.value.id}/${approved ? "approve" : "reject"}`,
      "POST",
      { duration_days: decisionDays.value || null, comment: comment.value },
    );
    decisionOpen.value = false;
    await refresh();
    await auth.load();
    ElMessage.success(approved ? "已批准" : "已拒绝");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "审批失败";
  } finally {
    busy.value = false;
  }
}
async function withdraw(row: AccessRequest) {
  if (busy.value) return;
  busy.value = true;
  try {
    if (
      !(await confirmOperation(
        "撤回这条权限申请？撤回后将停止审批。",
        "撤回申请",
      ))
    )
      return;
    await api(`/access-requests/${row.id}/withdraw`, "POST");
    ElMessage.success("权限申请已撤回");
    await refresh();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "撤回失败";
  } finally {
    busy.value = false;
  }
}
async function revoke(id: string) {
  try {
    await ElMessageBox.confirm("撤销该资源上的本次补充授权？", "撤销授权");
    await api(`/access-grants/${id}/revoke`, "POST");
    ElMessage.success("补充授权已撤销");
    await refresh();
    await auth.load();
  } catch (e) {
    if (e instanceof Error) error.value = e.message;
  }
}
function itemName(item: Record<string, unknown>) {
  return (item.bundle_ids as string[])
    .map((id) => {
      const b = catalogue.value?.bundles.find((b) => b.id === id);
      return b ? `${catalogue.value?.modules[b.module]}·${b.name}` : "业务权限";
    })
    .join("、");
}
</script>
<template>
  <section class="page-header">
    <div class="page-heading">
      <h1>{{ administrator ? "权限申请与授权" : "我的权限" }}</h1>
      <PageIntro />

      <div class="actions">
        <el-button @click="refresh">刷新</el-button
        ><el-button v-if="!administrator" type="primary" @click="openRequest"
          >申请权限</el-button
        >
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
    <el-tabs class="workspace-tabs" v-model="tab"
      ><el-tab-pane v-if="administrator" label="待审批" name="pending" />
      <el-tab-pane label="角色默认" name="defaults" /><el-tab-pane
        label="补充授权"
        name="grants" /><el-tab-pane
        label="申请记录"
        name="requests" /><el-tab-pane
        v-if="administrator"
        label="资源策略"
        name="policies"
    /></el-tabs>
    <div v-if="tab === 'defaults'" class="permission-groups">
      <section
        v-for="module in defaultModules"
        :key="module.id"
        class="permission-module"
      >
        <strong>{{ module.name }}</strong
        ><el-tag
          v-for="bundle in module.bundles"
          :key="bundle.id"
          class="scope-tag"
          :type="
            bundleState(auth.user?.default_permissions || [], bundle).partial
              ? 'info'
              : 'success'
          "
          >{{ bundle.name
          }}{{
            bundleState(auth.user?.default_permissions || [], bundle).partial
              ? "（部分）"
              : ""
          }}</el-tag
        >
      </section>
    </div>
    <ResourcePolicies v-else-if="tab === 'policies'" :users="users" />
    <el-table
      v-else-if="tab === 'grants'"
      :data="groupedGrants"
      empty-text="暂无补充授权"
    >
      <el-table-column v-if="administrator" label="用户" min-width="130"
        ><template #default="s">{{
          userNames[s.row.user] || "已删除用户"
        }}</template></el-table-column
      >
      <el-table-column prop="resource" label="资源" min-width="170" />
      <el-table-column label="权限" min-width="230"
        ><template #default="s"
          ><el-popover trigger="click" width="300"
            ><template #reference
              ><el-button link
                >{{ s.row.permissions.length }}项权限</el-button
              ></template
            >
            <div v-for="name in s.row.permissions" :key="name">
              {{ name }}
            </div></el-popover
          ></template
        ></el-table-column
      >
      <el-table-column label="有效期" min-width="170"
        ><template #default="s">{{
          s.row.expires ? date(s.row.expires) : "长期"
        }}</template></el-table-column
      >
      <el-table-column label="状态" width="100"
        ><template #default="s"
          ><el-tag
            :type="
              s.row.revoked ||
              (s.row.expires && Date.parse(s.row.expires) <= Date.now())
                ? 'info'
                : 'success'
            "
            >{{
              s.row.revoked
                ? "已撤销"
                : s.row.expires && Date.parse(s.row.expires) <= Date.now()
                  ? "已到期"
                  : "有效"
            }}</el-tag
          ></template
        ></el-table-column
      >
      <el-table-column v-if="administrator" label="操作" width="100"
        ><template #default="s"
          ><el-button
            v-if="!s.row.revoked"
            link
            type="danger"
            @click="revoke(s.row.id)"
            >撤销</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <el-table v-else :data="visibleRequests" empty-text="暂无申请">
      <el-table-column v-if="administrator" label="申请人" min-width="130"
        ><template #default="s">{{
          userNames[s.row.user_id] || "已删除用户"
        }}</template></el-table-column
      >
      <el-table-column label="资源与权限" min-width="300"
        ><template #default="s"
          ><div v-for="(item, i) in s.row.items" :key="i">
            <strong>{{ item.resource_name }}</strong> · {{ itemName(item)
            }}<el-tag
              v-if="
                item.include_descendants &&
                ['application', 'server'].includes(item.resource_kind)
              "
              size="small"
              class="scope-tag"
              >包含下属</el-tag
            >
          </div></template
        ></el-table-column
      >
      <el-table-column prop="reason" label="申请理由" min-width="180" />
      <el-table-column label="期限" width="90"
        ><template #default="s">{{
          s.row.duration_days ? `${s.row.duration_days}天` : "长期"
        }}</template></el-table-column
      >
      <el-table-column label="状态" width="100"
        ><template #default="s"
          ><el-tag
            :type="
              s.row.status === 'approved'
                ? 'success'
                : s.row.status === 'pending'
                  ? 'warning'
                  : 'info'
            "
            >{{ statusLabels[s.row.status] }}</el-tag
          ></template
        ></el-table-column
      >
      <el-table-column label="申请时间" min-width="170"
        ><template #default="s">{{
          date(s.row.created_at)
        }}</template></el-table-column
      >
      <el-table-column
        prop="decision_comment"
        label="审批意见"
        min-width="140"
      />
      <el-table-column label="操作" width="110"
        ><template #default="s"
          ><el-button
            v-if="
              administrator &&
              s.row.status === 'pending' &&
              s.row.user_id !== auth.user?.id
            "
            link
            type="primary"
            @click="openDecision(s.row)"
            >审批</el-button
          ><el-button
            v-if="s.row.status === 'pending' && s.row.user_id === auth.user?.id"
            link
            @click="withdraw(s.row)"
            >撤回</el-button
          ></template
        ></el-table-column
      >
    </el-table>
  </section>
  <el-dialog v-model="formOpen" title="申请权限" width="min(760px, 94vw)"
    ><el-form label-width="90px" :disabled="busy" @submit.prevent="submit">
      <el-alert
        v-if="requestError"
        :title="requestError"
        type="error"
        :closable="false"
      />
      <el-form-item label="资源类型" required
        ><el-select v-model="kind"
          ><el-option
            v-for="k in resourceKinds"
            :key="k.value"
            :value="k.value"
            :label="k.label" /></el-select
      ></el-form-item>
      <el-form-item label="资源" required
        ><el-select
          v-model="selectedTargets"
          multiple
          :multiple-limit="30"
          filterable
          remote
          :remote-method="loadTargets"
          placeholder="选择或搜索资源"
          ><el-option
            v-for="r in targets"
            :key="r.id"
            :value="r.id"
            :label="r.name" /></el-select
        ><el-tag
          v-if="['application', 'server'].includes(kind)"
          class="scope-tag"
          >包含当前及后续下属资源</el-tag
        ></el-form-item
      >
      <el-form-item label="权限" required
        ><el-checkbox-group
          v-model="selected"
          class="permission-groups"
          v-loading="checkingPermissions"
          ><div
            v-for="module in modules"
            :key="module.id"
            class="permission-module"
          >
            <strong>{{ module.name }}</strong>
            <div>
              <el-checkbox
                v-for="bundle in module.bundles"
                :key="bundle.id"
                :value="bundle.id"
                :disabled="
                  !selectedTargets.length ||
                  checkingPermissions ||
                  !!requestError ||
                  ownedOnAllTargets(bundle.id)
                "
                >{{ bundle.name
                }}{{
                  ownedOnAllTargets(bundle.id)
                    ? "（已拥有）"
                    : ownedOnSomeTargets(bundle.id)
                      ? "（部分资源已拥有）"
                      : ""
                }}</el-checkbox
              >
            </div>
          </div></el-checkbox-group
        ></el-form-item
      >
      <el-form-item label="有效期" required
        ><el-radio-group v-model="days"
          ><el-radio-button
            v-for="d in durations"
            :key="d.value"
            :value="d.value"
            >{{ d.label }}</el-radio-button
          ></el-radio-group
        ></el-form-item
      >
      <el-form-item label="申请理由" required
        ><el-input v-model="reason" type="textarea" :rows="3" maxlength="2000"
      /></el-form-item> </el-form
    ><template #footer
      ><el-button @click="formOpen = false">取消</el-button
      ><el-button
        type="primary"
        :loading="busy"
        :disabled="checkingPermissions || !!requestError || !selected.length"
        @click="submit"
        >提交申请</el-button
      ></template
    ></el-dialog
  >
  <el-dialog
    v-model="decisionOpen"
    title="审批权限申请"
    width="min(560px, 94vw)"
    ><el-form label-width="90px"
      ><el-form-item label="申请人">{{
        userNames[decision?.user_id || ""] || ""
      }}</el-form-item>
      <el-form-item label="资源范围"
        ><div v-for="(item, index) in decision?.items" :key="index">
          {{ item.resource_name }} · {{ itemName(item)
          }}<el-tag
            v-if="
              item.include_descendants &&
              ['application', 'server'].includes(String(item.resource_kind))
            "
            size="small"
            >包含下属</el-tag
          >
        </div></el-form-item
      >
      <el-form-item label="申请理由">{{ decision?.reason }}</el-form-item
      ><el-form-item label="批准期限" required
        ><el-select v-model="decisionDays"
          ><el-option
            v-for="d in durations.filter(
              (d) =>
                !decision?.duration_days ||
                (d.value > 0 && d.value <= decision.duration_days),
            )"
            :key="d.value"
            :label="d.label"
            :value="d.value" /></el-select></el-form-item
      ><el-form-item label="审批意见"
        ><el-input
          v-model="comment"
          type="textarea"
          maxlength="2000" /></el-form-item></el-form
    ><template #footer
      ><el-button :loading="busy" @click="decide(false)">拒绝</el-button
      ><el-button type="primary" :loading="busy" @click="decide(true)"
        >批准</el-button
      ></template
    ></el-dialog
  >
</template>
<style scoped>
.permission-groups {
  width: 100%;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.permission-module {
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 10px 14px;
}
.permission-module strong {
  display: block;
  font-size: 14px;
  line-height: 22px;
  margin-bottom: 6px;
}
.scope-tag {
  margin-left: 8px;
}
@media (max-width: 640px) {
  .permission-groups {
    grid-template-columns: 1fr;
  }
}
</style>
