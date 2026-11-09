<script setup lang="ts">
import { computed } from "vue";
import { date, label } from "@/utils/display";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
const props = defineProps<{
  context?: Record<string, unknown>;
  evidence?: unknown;
}>();
interface Connection {
  connection_id: string;
  application_id: string;
  name: string;
  engine: string;
  database: string;
  environment: string;
  purpose: string;
  description: string;
  query_enabled: boolean;
  allowed_tables: Record<string, string[]>;
  table_notes: Record<string, string>;
  schema_refreshed_at?: string;
  schema?: Record<
    string,
    { columns: { name: string; type?: string }[]; comment?: string }
  >;
}
const connections = computed(
  () => (props.context?.connections || []) as Connection[],
);
const gaps = computed(() => (props.context?.data_gaps || []) as string[]);
const record = computed(
  () =>
    props.evidence as
      | {
          source: string;
          observed_at?: string;
          data: {
            connection_id?: string;
            columns?: string[];
            rows?: unknown[][];
            sql?: string;
            error_code?: string;
            observed_at?: string;
            truncated?: boolean;
            limit_reached?: boolean;
            schema_hash?: string;
            content_hash?: string;
            application_id?: string;
          };
        }
      | undefined,
);
const databaseEvidence = computed(
  () =>
    record.value &&
    [
      "list_service_databases",
      "get_database_schema",
      "query_database_readonly",
    ].includes(record.value.source),
);
</script>
<template>
  <section v-if="context" class="database-context">
    <h4>本轮数据库</h4>
    <el-tag v-if="!context.version" type="info">未记录数据库上下文</el-tag>
    <el-empty
      v-else-if="!connections.length"
      description="无可用数据库"
      :image-size="40"
    />
    <el-collapse
      ><el-collapse-item
        v-for="c in connections"
        :key="c.connection_id"
        :name="c.connection_id"
        :title="`${c.name} · ${c.database} · ${c.environment}`"
      >
        <el-tag>{{ c.query_enabled ? "业务只读" : "仅结构" }}</el-tag>
        <p>{{ c.purpose }}</p>
        <p>{{ c.description }}</p>
        <p>结构刷新：{{ date(c.schema_refreshed_at) }}</p>
        <el-collapse>
          <el-collapse-item
            v-for="(table, name) in c.schema"
            :key="name"
            :title="String(name)"
            :name="String(name)"
          >
            <p v-if="table.comment">{{ table.comment }}</p>
            <p v-if="c.table_notes[name]">{{ c.table_notes[name] }}</p>
            <el-table :data="table.columns">
              <el-table-column prop="name" label="字段" />
              <el-table-column prop="type" label="类型" />
            </el-table>
          </el-collapse-item>
        </el-collapse> </el-collapse-item
    ></el-collapse>
    <el-tag v-for="gap in gaps" :key="gap" type="warning">{{
      label(gap)
    }}</el-tag>
  </section>
  <section v-if="databaseEvidence && record" class="database-evidence">
    <el-tag type="info">数据库只读证据</el-tag>
    <p v-if="record.data.observed_at">
      查询时状态 · {{ date(record.data.observed_at) }}
    </p>
    <el-alert
      v-if="record.data.error_code"
      :title="label(record.data.error_code)"
      type="warning"
      :closable="false"
    />
    <pre v-if="record.data.sql">{{ record.data.sql }}</pre>
    <el-table
      v-if="record.data.rows"
      :data="record.data.rows.map((cells) => ({ cells }))"
      max-height="360"
    >
      <el-table-column
        v-for="(column, i) in record.data.columns"
        :key="i"
        :label="column"
        min-width="120"
        ><template #default="{ row }">{{
          row.cells[i] ?? "NULL"
        }}</template></el-table-column
      >
    </el-table>
    <el-tag v-if="record.data.truncated" type="warning">结果已截断</el-tag>
    <el-tag v-if="record.data.limit_reached" type="warning"
      >已达 200 行上限</el-tag
    >
    <el-collapse
      ><el-collapse-item title="证据详情" name="details"
        ><JsonEvidence :value="record" /></el-collapse-item
    ></el-collapse>
  </section>
</template>
<style scoped>
.database-context,
.database-evidence {
  padding: 12px 0;
  overflow-wrap: anywhere;
}
p,
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
