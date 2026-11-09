<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { configs } from "@/config/lists";
import { type Row } from "@/types/records";
import { request, query } from "@/api/client";
import { effectiveApproval } from "@/utils/display";
import StatusTag from "@/components/common/StatusTag.vue";
const props = defineProps<{
    kind: string;
    incidentId: string;
    revision?: number;
  }>(),
  router = useRouter(),
  rows = ref<Row[]>([]),
  total = ref(0),
  page = ref(1),
  error = ref(""),
  loading = ref(false);
async function load() {
  loading.value = true;
  try {
    const r = await request<Row[]>(
      "/" +
        props.kind +
        query({
          incident_id: props.incidentId,
          offset: (page.value - 1) * 10,
          limit: 10,
        }),
    );
    rows.value = r.data;
    total.value = r.total ?? 0;
    error.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
}
onMounted(load);
watch(() => props.revision, load);
</script>
<template>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
  /><el-table v-loading="loading" :data="rows" empty-text="暂无记录"
    ><el-table-column
      v-for="c in configs[kind]!.columns"
      :key="c.key"
      :label="c.label"
      :min-width="c.width || 150"
      ><template #default="{ row }"
        ><StatusTag
          v-if="c.status"
          :value="kind === 'approvals' ? effectiveApproval(row) : row[c.key]"
        /><template v-else>{{
          c.format ? c.format(row) : (row[c.key] ?? "—")
        }}</template></template
      ></el-table-column
    ><el-table-column label="操作" width="100" fixed="right"
      ><template #default="{ row }"
        ><div class="table-actions">
          <el-button
            text
            type="primary"
            @click="router.push('/' + kind + '/' + row.id)"
            >详情</el-button
          >
        </div></template
      ></el-table-column
    ></el-table
  >
  <div class="pagination">
    <el-pagination
      v-model:current-page="page"
      :page-size="10"
      :total="total"
      layout="prev, pager, next"
      @current-change="load"
    />
  </div>
</template>
