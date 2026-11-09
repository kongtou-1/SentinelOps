<script setup lang="ts">
import PageIntro from "@/components/common/PageIntro.vue";
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import { confirmOperation } from "@/utils/confirmOperation";
import { api, type Models } from "@/api/client";
import { date, metricUnit, metricValue, number, short } from "@/utils/display";
import { metricLabels } from "@/config/metrics";
import { useAuth } from "@/features/auth/store";
import StatusTag from "@/components/common/StatusTag.vue";
import RecordForm from "@/components/records/RecordForm.vue";
import { usePoll } from "@/composables/usePoll";
const id = String(useRoute().params.id),
  router = useRouter(),
  auth = useAuth(),
  row = ref<Models["TaskResponse"]>(),
  runs = ref<Models["ResultResponse"][]>([]),
  error = ref(""),
  busy = ref(false),
  editOpen = ref(false);
async function load() {
  try {
    row.value = await api("/prediction-tasks/" + id);
    runs.value = await api("/predictions?task_id=" + id + "&limit=10");
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  }
}
const { refresh } = usePoll(load, () => (editOpen.value ? null : 30000));
async function run() {
  if (busy.value) return;
  busy.value = true;
  try {
    if (
      !(await confirmOperation(
        `手动运行预测任务“${row.value?.name || "当前任务"}”？`,
        "运行预测",
      ))
    )
      return;
    const r = await api<Models["ResultResponse"]>(
      `/prediction-tasks/${id}/run`,
      "POST",
    );
    ElMessage.success("预测任务已提交，可查看运行进度");
    await router.push("/predictions/" + r.id);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "运行失败");
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="page-heading">
    <div>
      <router-link to="/prediction-tasks" class="subtle-link"
        >← 返回任务列表</router-link
      >
      <div class="heading-copy">
        <h1>{{ row?.name || "预测任务" }}</h1>
        <PageIntro />
        <p>{{ id }}</p>
      </div>
    </div>

    <div class="actions">
      <el-button @click="refresh">刷新</el-button
      ><el-button v-if="auth.can('predictions:manage')" @click="editOpen = true"
        >编辑任务</el-button
      ><el-button
        v-if="auth.can('predictions:run')"
        type="primary"
        :loading="busy"
        @click="run"
        >手动运行</el-button
      >
    </div>
  </div>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
    class="error-box"
  />
  <section v-if="row" class="panel">
    <h2>任务配置</h2>
    <dl class="detail-grid">
      <div>
        <dt>指标</dt>
        <dd>{{ metricLabels[row.metric] }}</dd>
      </div>
      <div>
        <dt>风险阈值</dt>
        <dd>{{ row.threshold }}（原始单位）</dd>
      </div>
      <div>
        <dt>自动调度</dt>
        <dd>{{ row.enabled ? "已启用" : "已停用" }}</dd>
      </div>
      <div>
        <dt>运行间隔</dt>
        <dd>{{ row.interval_seconds }} 秒</dd>
      </div>
      <div>
        <dt>下次运行</dt>
        <dd>{{ row.enabled ? date(row.next_run_at) : "已停用" }}</dd>
      </div>
      <div>
        <dt>目标资源</dt>
        <dd>
          <router-link
            :to="
              '/' +
              (row.container_id
                ? 'containers'
                : row.llm_endpoint_id
                  ? 'llm-endpoints'
                  : 'servers') +
              '/' +
              (row.container_id || row.llm_endpoint_id || row.server_id)
            "
            >{{
              short(row.container_id || row.llm_endpoint_id || row.server_id)
            }}</router-link
          >
        </dd>
      </div>
      <div>
        <dt>序列条件</dt>
        <dd>
          {{
            Object.entries(row.selector)
              .map(([k, v]) => k + " = " + v)
              .join("；") || "单序列"
          }}
        </dd>
      </div>
    </dl>
  </section>
  <section class="panel">
    <h2>最近运行记录</h2>
    <el-table :data="runs" empty-text="暂无运行记录"
      ><el-table-column label="编号"
        ><template #default="{ row: r }"
          ><router-link :to="'/predictions/' + r.id">{{
            short(r.id)
          }}</router-link></template
        ></el-table-column
      ><el-table-column label="状态"
        ><template #default="{ row: r }"
          ><StatusTag :value="r.status" /></template></el-table-column
      ><el-table-column label="时效"
        ><template #default="{ row: r }">{{
          r.fresh ? "有效" : "已过期"
        }}</template></el-table-column
      ><el-table-column prop="error_code" label="结果说明" /><el-table-column
        label="时间"
        min-width="180"
        ><template #default="{ row: r }">{{
          date(r.created_at)
        }}</template></el-table-column
      ></el-table
    ><router-link :to="'/predictions?task_id=' + id" class="subtle-link"
      >全部结果 →</router-link
    >
  </section>
  <RecordForm
    v-model="editOpen"
    kind="prediction-tasks"
    :row="row"
    @saved="refresh"
  />
</template>
