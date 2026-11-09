<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { api, type Models } from "@/api/client";
import type { Plan, DataSource } from "@/features/inspections/ui";
import {
  configuredChecks,
  conditionRequirement,
  inputRows,
} from "@/features/inspections/configuration";

const props = defineProps<{ plan: Plan }>();
const checks = computed(() => configuredChecks(props.plan));
const skills = ref<Record<string, Models["SkillResponse"]>>({});
const sources = ref<Record<string, DataSource>>({});
const loading = ref(false),
  error = ref("");
let revision = 0;
watch(
  () => [props.plan.id, props.plan.version],
  async () => {
    const current = ++revision;
    loading.value = true;
    error.value = "";
    skills.value = {};
    sources.value = {};
    const skillIds = [
      ...new Set(
        checks.value
          .flatMap((check) => [
            check.skill_id,
            ...(check.auxiliary_skill_ids || []),
          ])
          .filter((id): id is string => Boolean(id)),
      ),
    ];
    const sourceIds = [
      ...new Set(
        [
          props.plan.data_source_id,
          ...checks.value.flatMap((check) => check.source_ids || []),
        ].filter((id): id is string => Boolean(id)),
      ),
    ];
    const results = await Promise.allSettled([
      ...skillIds.map(async (id) => {
        const value = await api<Models["SkillResponse"]>(`/skills/${id}`);
        if (current === revision) skills.value[id] = value;
      }),
      ...sourceIds.map(async (id) => {
        const value = await api<DataSource>(`/inspection-data-sources/${id}`);
        if (current === revision) sources.value[id] = value;
      }),
    ]);
    if (current !== revision) return;
    if (results.some((result) => result.status === "rejected"))
      error.value =
        "部分检查方法或数据来源无法读取，请检查权限或配置是否仍存在";
    loading.value = false;
  },
  { immediate: true },
);
function skillName(id?: string | null) {
  return id
    ? skills.value[id]?.title ||
        (loading.value ? "正在读取检查方法…" : "检查方法无法读取")
    : "未选择检查方法";
}
function sourceName(id: string) {
  const source = sources.value[id];
  return source
    ? `${source.name} · ${source.source_kind === "api" ? "API" : "数据库查询"}`
    : loading.value
      ? "正在读取数据来源…"
      : "数据来源无法读取";
}
</script>

<template>
  <div class="configuration">
    <el-alert v-if="error" :title="error" type="warning" :closable="false" />
    <section class="inspection-panel">
      <h3>检查哪些数据</h3>
      <template v-if="plan.object_mode === 'fixed'">
        <el-table :data="plan.fixed_objects" empty-text="未添加检查对象">
          <el-table-column prop="id" label="对象编号" min-width="150" />
          <el-table-column label="提供的数据" min-width="280">
            <template #default="{ row }">
              <dl class="input-values">
                <template
                  v-for="field in inputRows(row.facts)"
                  :key="field.name"
                  ><dt>{{ field.name }}</dt>
                  <dd>{{ field.value }}</dd></template
                >
              </dl>
            </template>
          </el-table-column>
        </el-table>
      </template>
      <p v-else>
        {{
          plan.data_source_id
            ? sourceName(plan.data_source_id)
            : plan.source_kind === "api"
              ? "任务中配置的API"
              : "任务中配置的数据库查询"
        }}
      </p>
    </section>
    <el-empty
      v-if="!checks.length"
      description="尚未配置检查内容"
      :image-size="70"
    />
    <section
      v-for="(check, index) in checks"
      :key="check.check_id"
      class="inspection-panel"
    >
      <header class="inspection-heading">
        <h3>{{ index + 1 }}. {{ check.name }}</h3>
        <div class="check-labels">
          <el-tag>{{
            check.kind === "agent" ? "智能检查" : "规则检查"
          }}</el-tag>
          <el-tag type="info">{{
            check.scope === "collection" ? "检查整批数据" : "逐个对象检查"
          }}</el-tag>
          <el-tag v-if="!check.enabled" type="info">不参与执行</el-tag>
        </div>
      </header>
      <template v-if="check.kind === 'agent'">
        <h4>检查要求</h4>
        <p class="requirements">
          {{ check.requirements || "按下方检查方法中的要求进行检查" }}
        </p>
        <dl class="check-details">
          <dt>检查方法</dt>
          <dd>{{ skillName(check.skill_id) }}</dd>
          <template v-if="check.auxiliary_skill_ids?.length"
            ><dt>辅助方法</dt>
            <dd>
              <div v-for="id in check.auxiliary_skill_ids" :key="id">
                {{ skillName(id) }}
              </div>
            </dd></template
          >
          <dt>允许调用的API与数据库查询</dt>
          <dd>
            <div v-for="id in check.source_ids" :key="id">
              {{ sourceName(id) }}
            </div>
            <span v-if="!check.source_ids?.length">未额外授权</span>
          </dd>
        </dl>
        <el-collapse v-if="check.skill_id && skills[check.skill_id]">
          <el-collapse-item title="查看检查方法说明" :name="check.check_id"
            ><p class="requirements">
              {{ skills[check.skill_id]!.content }}
            </p></el-collapse-item
          >
        </el-collapse>
        <template v-if="inputRows(check.parameters || {}).length"
          ><h4>补充输入</h4>
          <dl class="input-values">
            <template
              v-for="field in inputRows(check.parameters)"
              :key="field.name"
              ><dt>{{ field.name }}</dt>
              <dd>{{ field.value }}</dd></template
            >
          </dl></template
        >
      </template>
      <template v-else>
        <p>
          {{
            check.operator === "or"
              ? "以下条件满足任意一项即通过"
              : "以下条件全部满足才通过"
          }}
        </p>
        <el-table :data="check.conditions">
          <el-table-column prop="title" label="检查条件" min-width="160" />
          <el-table-column prop="path" label="检查字段" min-width="130" />
          <el-table-column label="通过要求" min-width="240"
            ><template #default="{ row }">{{
              conditionRequirement(row)
            }}</template></el-table-column
          >
          <el-table-column prop="basis" label="判断依据" min-width="240" />
        </el-table>
      </template>
    </section>
  </div>
</template>

<style scoped>
.configuration {
  display: grid;
  gap: 18px;
}
.inspection-heading {
  margin-bottom: 20px;
}
.check-labels {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
h4 {
  margin: 20px 0 10px;
  font-size: 14px;
  font-weight: 600;
}
p {
  margin: 0;
  line-height: 1.8;
}
.requirements {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.check-details,
.input-values {
  display: grid;
  grid-template-columns: minmax(100px, 220px) minmax(0, 1fr);
  gap: 12px 24px;
  margin: 20px 0;
  line-height: 1.7;
}
dt {
  color: var(--el-text-color-secondary);
  overflow-wrap: anywhere;
}
dd {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
@media (max-width: 650px) {
  .check-details,
  .input-values {
    grid-template-columns: 1fr;
    gap: 8px;
  }
  dd {
    margin-bottom: 12px;
  }
}
</style>
