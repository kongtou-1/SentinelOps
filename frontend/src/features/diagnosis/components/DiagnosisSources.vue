<script setup lang="ts">
import { computed } from "vue";
import { date, label } from "@/utils/display";
import DatabaseContext from "./DatabaseContext.vue";
const props = defineProps<{
  context?: Record<string, unknown>;
  databaseContext?: Record<string, unknown>;
}>();
interface Binding {
  deployment_id: string;
  release_version: string;
  occurred_at: string;
  environment: string;
  config_version: string;
  identity: { service_code?: string; entrypoint?: string; status?: string };
  change_summary: string;
  test_result: string;
}
interface Snapshot {
  snapshot_id: string;
  git_commit?: string;
  svn_revision?: string;
  content_hash: string;
  source: string;
  role: string;
  release_version: string;
}
const bindings = computed(() => (props.context?.bindings || []) as Binding[]);
const snapshots = computed(
  () => (props.context?.snapshots || []) as Snapshot[],
);
const gaps = computed(() => (props.context?.data_gaps || []) as string[]);
const candidates = computed(
  () =>
    (props.context?.candidates || []) as {
      service_code: string;
      deployment_id: string;
    }[],
);
const roles: Record<string, string> = {
  release: "关联发布源码",
  reference: "参考源码 · 版本未核验",
  previous_release: "上次部署源码 · 未认定正常",
};
</script>

<template>
  <DatabaseContext :context="databaseContext" />
  <section class="diagnosis-sources">
    <h4>本轮开发资料</h4>
    <p v-if="!snapshots.length" class="muted">没有源码，继续使用运行证据。</p>
    <p v-else class="muted">版本已随本轮冻结；同步新资料后，下一轮使用。</p>
    <div
      v-for="b in bindings"
      :key="b.deployment_id + b.occurred_at"
      class="source-binding"
    >
      <strong
        >{{ b.identity.service_code || "服务" }} ·
        {{ b.release_version }}</strong
      >
      <p>{{ b.environment }} · 告警时间 {{ date(b.occurred_at) }}</p>
      <p v-if="b.identity.entrypoint">入口：{{ b.identity.entrypoint }}</p>
      <p v-if="b.config_version">配置版本：{{ b.config_version }}</p>
      <p>
        镜像关联：{{
          b.identity.status === "verified" ? "登记时身份已核对" : "未核验"
        }}
      </p>
      <p v-if="b.change_summary">变更：{{ b.change_summary }}</p>
      <p v-if="b.test_result">登记的测试结果：{{ b.test_result }}</p>
    </div>
    <div
      v-for="s in snapshots"
      :key="s.snapshot_id + s.role"
      class="source-binding"
    >
      <el-tag
        size="small"
        :type="
          s.source === 'local' || s.role === 'reference' ? 'warning' : 'info'
        "
        >{{ roles[s.role] }}</el-tag
      >
      <p>
        {{ s.release_version }} ·
        {{
          s.source === "local"
            ? "本地目录 · 部署版本未核验"
            : s.source === "svn"
              ? "固定 SVN revision"
              : "固定 Git commit"
        }}
      </p>
      <p v-if="s.git_commit">
        commit：<code>{{ s.git_commit }}</code>
      </p>
      <p v-if="s.svn_revision">
        revision：<code>{{ s.svn_revision }}</code>
      </p>
      <p>
        内容哈希：<code>{{ s.content_hash }}</code>
      </p>
    </div>
    <p v-if="candidates.length">
      主机上的候选服务：{{
        candidates.map((c) => c.service_code || c.deployment_id).join("、")
      }}。尚未确认故障归属，不自动读取源码。
    </p>
    <ul v-if="gaps.length">
      <li v-for="gap in gaps" :key="gap">{{ label(gap) }}</li>
    </ul>
    <p v-if="!context?.version" class="muted">历史诊断未记录源码上下文。</p>
  </section>
</template>

<style scoped>
.diagnosis-sources {
  padding: 12px 0;
  overflow-wrap: anywhere;
}
h4 {
  margin: 0 0 8px;
}
p {
  margin: 6px 0;
  white-space: pre-wrap;
}
.source-binding {
  border-bottom: 1px solid var(--border);
  padding: 10px 0;
}
code {
  font-size: 12px;
}
ul {
  padding-left: 20px;
}
</style>
