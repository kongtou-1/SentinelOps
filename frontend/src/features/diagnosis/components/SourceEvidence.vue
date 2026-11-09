<script setup lang="ts">
import { computed, ref } from "vue";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import { label } from "@/utils/display";
import DatabaseContext from "./DatabaseContext.vue";
const props = defineProps<{ evidence: unknown }>();
const open = ref(false);
interface Data {
  path?: string;
  content?: string;
  diff?: string;
  start_line?: number;
  end_line?: number;
  reason?: string;
  availability?: string;
  snippet_hash?: string;
  source_refs?: {
    snapshot_id: string;
    git_commit?: string;
    svn_revision?: string;
    content_hash: string;
  }[];
}
const record = computed(
  () => props.evidence as { id: string; source: string; data?: Data },
);
const data = computed(() => record.value.data || {});
const isSource = computed(() =>
  [
    "read_source_file",
    "search_source",
    "list_source_tree",
    "compare_source_snapshots",
  ].includes(record.value.source),
);
const lines = computed(
  () =>
    data.value.content
      ?.split("\n")
      .map((text, i) => ({ number: (data.value.start_line || 1) + i, text })) ||
    [],
);
</script>

<template>
  <DatabaseContext :evidence="evidence" />
  <div v-if="isSource" class="source-evidence">
    <p v-if="data.availability === 'unavailable'">
      源码证据缺口：{{ label(data.reason) }}
    </p>
    <el-button v-else link type="primary" @click="open = true">
      {{ data.path || "查看源码工具证据"
      }}<span v-if="data.start_line"
        >：{{ data.start_line }}–{{ data.end_line }}</span
      >
    </el-button>
    <p class="muted">{{ record.id }}</p>
    <el-drawer
      v-model="open"
      :title="data.path || '本轮源码证据'"
      size="min(880px, 95vw)"
      append-to-body
    >
      <p>诊断时保存的只读片段</p>
      <p v-for="s in data.source_refs" :key="s.snapshot_id" class="identity">
        快照 {{ s.snapshot_id }}<br />{{
          s.git_commit
            ? `commit ${s.git_commit}`
            : s.svn_revision
              ? `SVN r${s.svn_revision}`
              : "本地参考源码"
        }}<br />内容哈希 {{ s.content_hash }}
      </p>
      <p v-if="data.snippet_hash" class="identity">
        片段哈希 {{ data.snippet_hash }}
      </p>
      <pre
        v-if="data.content !== undefined"
        class="source-code"
      ><span v-for="line in lines" :key="line.number"><span class="line-number">{{ line.number }}</span>{{ line.text }}{{ '\n' }}</span></pre>
      <pre v-else-if="data.diff !== undefined" class="source-code">{{
        data.diff
      }}</pre>
      <JsonEvidence v-else :value="record" />
    </el-drawer>
  </div>
</template>

<style scoped>
.source-evidence {
  margin: 8px 0;
  overflow-wrap: anywhere;
}
.identity {
  font-size: 12px;
  overflow-wrap: anywhere;
}
.source-code {
  overflow: auto;
  font-size: 13px;
  line-height: 1.6;
  padding: 12px;
  background: var(--surface-soft);
}
.line-number {
  display: inline-block;
  min-width: 4em;
  user-select: none;
  color: var(--text-muted);
}
</style>
