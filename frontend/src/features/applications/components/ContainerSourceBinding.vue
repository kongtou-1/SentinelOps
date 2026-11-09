<script setup lang="ts">
import { ref } from "vue";
import { api } from "@/api/client";
import type { Row } from "@/types/records";
import { date, short } from "@/utils/display";
import { usePoll } from "@/composables/usePoll";
import {
  sourceLabel,
  sourceReason,
} from "@/features/applications/model/sources";
const props = defineProps<{
  containerId: string;
  imageId?: string;
  observedAt?: string;
}>();
const rows = ref<Row[]>([]),
  error = ref("");
usePoll(
  async () => {
    try {
      rows.value = await api<Row[]>(
        `/containers/${props.containerId}/deployments`,
      );
      error.value = "";
    } catch (e) {
      error.value = e instanceof Error ? e.message : "读取服务关联失败";
    }
  },
  () => 30000,
);
</script>
<template>
  <section class="source-binding">
    <h3>服务与源码对应关系</h3>
    <el-alert v-if="error" :title="error" type="warning" :closable="false" />
    <p v-else-if="!rows.length" class="muted">尚未登记发布与部署。</p>
    <div v-else>
      <div v-for="row in rows" :key="row.id" class="source-record">
        <router-link :to="`/applications/${row.application_id}`">{{
          row.application_name
        }}</router-link>
        <p class="muted">
          {{ row.service_code }} · {{ row.binding.entrypoint || "未登记入口" }}
        </p>
        <dl class="detail-grid">
          <div>
            <dt>发布 / 源码</dt>
            <dd>
              {{ row.version }} ·
              {{
                row.binding.svn_revision || row.binding.expected_revision
                  ? `r${row.binding.svn_revision || row.binding.expected_revision}`
                  : short(
                      row.binding.expected_commit || row.binding.snapshot_id,
                    )
              }}
            </dd>
          </div>
          <div>
            <dt>部署期间</dt>
            <dd>
              {{ date(row.started_at) }}<br />至
              {{ row.ended_at ? date(row.ended_at) : "现在" }}
            </dd>
          </div>
          <div>
            <dt>镜像核验</dt>
            <dd>
              {{ sourceLabel(row.binding.status) }}
              <div class="muted">{{ sourceReason(row.binding.reason) }}</div>
              <div v-if="!row.ended_at" class="muted">
                当前：{{ sourceLabel(row.current_identity.status) }}
              </div>
            </dd>
          </div>
        </dl>
      </div>
    </div>
    <dl class="detail-grid image-identity">
      <div>
        <dt>运行镜像</dt>
        <dd>{{ imageId || "尚未取得" }}</dd>
      </div>
      <div>
        <dt>镜像观测时间</dt>
        <dd>{{ date(observedAt) }}</dd>
      </div>
    </dl>
  </section>
</template>
<style scoped>
.source-binding {
  overflow-wrap: anywhere;
}
.detail-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.detail-grid > div {
  min-width: 0;
}
.source-record + .source-record {
  border-top: 1px solid var(--border);
  margin-top: 16px;
  padding-top: 16px;
}
.source-record p {
  margin: 6px 0 14px;
}
.image-identity {
  margin-bottom: 0;
}
@media (max-width: 600px) {
  .detail-grid {
    grid-template-columns: 1fr;
  }
}
</style>
