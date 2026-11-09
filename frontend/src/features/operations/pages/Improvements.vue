<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { ref } from "vue";
import { useRoute } from "vue-router";
import { api, all, request, query } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import type { Row } from "@/types/records";
const auth = useAuth(),
  route = useRoute(),
  rows = ref<Row[]>([]),
  assignees = ref<Row[]>([]),
  handoffs = ref<Row[]>([]),
  error = ref(""),
  status = ref(""),
  page = ref(1),
  total = ref(0),
  busy = ref(false),
  selected = ref<Row>(),
  mode = ref(""),
  form = ref<Row>({});
const labels: Record<string, string> = {
  unassigned: "待分配",
  pending: "待处理",
  in_progress: "进行中",
  awaiting_review: "待验收",
  completed: "已完成",
  cancelled: "已取消",
};
async function load() {
  try {
    const result = await request<Row[]>(
      "/improvement-tasks" +
        query({
          report_id: route.query.report,
          status: status.value,
          offset: (page.value - 1) * 20,
          limit: 20,
        }),
    );
    rows.value = result.data;
    total.value = result.total || 0;
    if (auth.can("incidents:handle"))
      assignees.value = await all("/incidents/assignees");
    error.value = "";
  } catch (e) {
    error.value = String(e);
  }
}
const { refresh } = usePoll(load, () => (selected.value ? null : 30000));
function canEdit(row: Row) {
  return (
    auth.can("incidents:handle") &&
    (auth.can("reports:manage") ||
      !row.assignee_id ||
      row.assignee_id === auth.user?.id) &&
    !["completed", "cancelled"].includes(row.status)
  );
}
async function open(row: Row, action: string) {
  try {
    selected.value = row;
    mode.value = action;
    form.value =
      action === "edit"
        ? {
            expected_version: row.version,
            assignee_id: row.assignee_id,
            due_at: row.due_at,
            acceptance: row.acceptance,
            status: row.status,
          }
        : action === "submit"
          ? { expected_version: row.version, result: "", handoff_ids: [] }
          : { expected_version: row.version, accepted: true, evidence: "" };
    if (action === "submit")
      handoffs.value = await all(
        `/incidents/${row.incident_id}/repair-handoffs`,
      );
  } catch (e) {
    error.value = String(e);
  }
}
async function save() {
  busy.value = true;
  try {
    await api(
      `/improvement-tasks/${selected.value!.id}` +
        (mode.value === "edit"
          ? ""
          : mode.value === "submit"
            ? "/submit"
            : "/decision"),
      mode.value === "edit" ? "PUT" : "POST",
      form.value,
    );
    selected.value = undefined;
    await refresh();
  } catch (e) {
    error.value = String(e);
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="page-heading">
    <h1>复盘整改</h1>
    <PageIntro />

    <div class="actions">
      <el-select
        v-model="status"
        clearable
        placeholder="全部状态"
        @change="
          page = 1;
          refresh();
        "
        ><el-option
          v-for="(v, k) in labels"
          :key="k"
          :label="v"
          :value="k" /></el-select
      ><el-button @click="refresh">刷新</el-button>
    </div>
  </div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" />
  <section class="panel">
    <el-table :data="rows"
      ><el-table-column
        prop="title"
        label="整改任务"
        min-width="220"
      /><el-table-column label="来源"
        ><template #default="{ row }"
          ><router-link :to="'/reports/' + row.report_id"
            >报告 v{{ row.report_revision }} · 第
            {{ row.item_index + 1 }} 项</router-link
          ></template
        ></el-table-column
      ><el-table-column label="状态"
        ><template #default="{ row }">{{
          labels[row.status]
        }}</template></el-table-column
      ><el-table-column label="负责人"
        ><template #default="{ row }">{{
          assignees.find((a) => a.id === row.assignee_id)?.username ||
          row.assignee_id?.slice(0, 8) ||
          "待分配"
        }}</template></el-table-column
      ><el-table-column label="期限"
        ><template #default="{ row }">{{
          date(row.due_at)
        }}</template></el-table-column
      ><el-table-column prop="acceptance" label="验收条件" /><el-table-column
        label="操作"
        min-width="200"
        ><template #default="{ row }"
          ><el-button
            text
            @click="
              selected = row;
              mode = 'detail';
            "
            >证据</el-button
          ><el-button
            v-if="canEdit(row) && row.status !== 'awaiting_review'"
            text
            @click="open(row, 'edit')"
            >编辑</el-button
          ><el-button
            v-if="
              canEdit(row) && ['pending', 'in_progress'].includes(row.status)
            "
            text
            @click="open(row, 'submit')"
            >提交结果</el-button
          ><el-button
            v-if="
              auth.can('reports:manage') && row.status === 'awaiting_review'
            "
            text
            @click="open(row, 'decision')"
            >验收</el-button
          ></template
        ></el-table-column
      ></el-table
    ><el-pagination
      v-model:current-page="page"
      :total="total"
      :page-size="20"
      layout="prev, pager, next"
      @current-change="refresh"
    />
  </section>
  <el-dialog
    :model-value="!!selected"
    :title="
      mode === 'edit'
        ? '编辑整改'
        : mode === 'submit'
          ? '提交整改结果'
          : mode === 'decision'
            ? 'Admin 验收'
            : '整改证据'
    "
    width="720px"
    @close="selected = undefined"
    ><JsonEvidence
      v-if="mode === 'detail'"
      :value="{
        item: selected?.item,
        submission: selected?.submission,
        decision: selected?.decision,
      }"
    /><el-form v-else label-width="100px"
      ><template v-if="mode === 'edit'"
        ><el-form-item label="负责人"
          ><el-select v-model="form.assignee_id" clearable
            ><el-option
              v-for="a in assignees"
              :key="a.id"
              :value="a.id"
              :label="a.username" /></el-select></el-form-item
        ><el-form-item label="期限"
          ><el-date-picker
            v-model="form.due_at"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ssZ" /></el-form-item
        ><el-form-item label="验收条件"
          ><el-input
            v-model="form.acceptance"
            type="textarea"
            :maxlength="8000" /></el-form-item
        ><el-form-item label="状态" required
          ><el-select v-model="form.status"
            ><el-option
              v-for="k in [
                'unassigned',
                'pending',
                'in_progress',
                ...(auth.can('reports:manage') ? ['cancelled'] : []),
              ]"
              :key="k"
              :value="k"
              :label="labels[k]" /></el-select></el-form-item></template
      ><template v-else-if="mode === 'submit'"
        ><el-form-item label="结果与证据" required
          ><el-input
            v-model="form.result"
            type="textarea"
            :rows="6"
            :maxlength="16000" /></el-form-item
        ><el-form-item label="修复交接"
          ><el-select v-model="form.handoff_ids" multiple
            ><el-option
              v-for="h in handoffs"
              :key="h.id"
              :value="h.id"
              :label="
                h.id.slice(0, 8) + ' · ' + date(h.created_at)
              " /></el-select></el-form-item></template
      ><template v-else
        ><el-form-item label="验收结果"
          ><el-switch
            v-model="form.accepted"
            active-text="通过"
            inactive-text="退回" /></el-form-item
        ><el-form-item label="依据" required
          ><el-input
            v-model="form.evidence"
            type="textarea"
            :rows="6"
            :maxlength="8000" /></el-form-item
        ><JsonEvidence
          :value="selected?.submission || {}" /></template></el-form
    ><template v-if="mode !== 'detail'" #footer
      ><el-button type="primary" :loading="busy" @click="save">{{
        mode === "decision" ? "提交验收" : "保存"
      }}</el-button></template
    ></el-dialog
  >
</template>
