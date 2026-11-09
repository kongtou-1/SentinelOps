<script setup lang="ts" generic="T">
import { onUnmounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { downloadCsv, type CsvColumn } from "@/utils/csv";
const props = defineProps<{
  filename: string;
  columns: CsvColumn<T>[];
  load: (signal: AbortSignal) => Promise<T[]>;
  disabled?: boolean;
}>();
const busy = ref(false);
let controller: AbortController | undefined;
onUnmounted(() => controller?.abort());
async function run() {
  if (busy.value || props.disabled) return;
  busy.value = true;
  const current = new AbortController();
  controller = current;
  const { filename, columns, load } = props;
  try {
    const rows = await load(current.signal);
    if (current.signal.aborted) return;
    if (!rows.length) {
      ElMessage.warning("当前筛选条件下没有可导出的记录");
      return;
    }
    downloadCsv(filename, columns, rows);
    ElMessage.success(`已导出 ${rows.length} 条记录`);
  } catch (e) {
    if (!current.signal.aborted)
      ElMessage.error(e instanceof Error ? e.message : "导出失败，请重试");
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <el-tooltip content="导出当前筛选条件下的全部记录">
    <el-button :loading="busy" :disabled="disabled" @click="run"
      >导出 CSV</el-button
    >
  </el-tooltip>
</template>
