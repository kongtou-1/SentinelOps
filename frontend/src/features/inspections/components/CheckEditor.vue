<script setup lang="ts">
import { ref, watch } from "vue";
import type { Models } from "@/api/client";
import {
  newCheck,
  newCondition,
  ruleKinds,
  type CheckInput,
} from "@/features/inspections/task";
import type { DataSource } from "@/features/inspections/ui";
const checks = defineModel<CheckInput[]>({ required: true });
const props = defineProps<{
  skills: Models["SkillResponse"][];
  sources: DataSource[];
}>();
const parameters = ref<Record<string, string>>({});
watch(
  checks,
  (values) => {
    for (const check of values)
      if (!(check.check_id in parameters.value))
        parameters.value[check.check_id] = JSON.stringify(
          check.parameters || {},
          null,
          2,
        );
  },
  { immediate: true, deep: true },
);
function validate() {
  for (const [index, check] of checks.value.entries()) {
    if (!check.name.trim())
      throw new Error(`请填写第 ${index + 1} 项检查的名称`);
    if (check.kind === "agent") {
      if (!check.skill_id || !check.requirements?.trim())
        throw new Error(`请补全“${check.name}”的 Skill 和检查要求`);
      try {
        const value = JSON.parse(parameters.value[check.check_id] || "{}");
        if (!value || typeof value !== "object" || Array.isArray(value))
          throw new Error();
        check.parameters = value;
      } catch {
        throw new Error(`“${check.name}”的业务参数需要有效的 JSON 对象`);
      }
    } else {
      for (const condition of check.conditions || []) {
        if (
          !condition.title.trim() ||
          !condition.path.trim() ||
          !condition.basis.trim()
        )
          throw new Error(`请补全“${check.name}”的条件名称、字段和判断依据`);
      }
    }
  }
  if (!checks.value.some((check) => check.enabled))
    throw new Error("请至少添加并启用一项检查");
}
defineExpose({ validate });
</script>
<template>
  <div class="check-editor">
    <el-empty
      v-if="!checks.length"
      description="添加需要执行的检查"
      :image-size="70"
    />
    <section
      v-for="(check, index) in checks"
      :key="check.check_id"
      class="inspection-panel check-card"
    >
      <header class="inspection-heading">
        <h3>
          {{ index + 1 }} ·
          {{ check.kind === "rule" ? "规则检查" : "Agent 检查" }}
        </h3>
        <div>
          <el-switch v-model="check.enabled" active-text="启用" /><el-button
            link
            type="danger"
            @click="checks.splice(index, 1)"
            >移除</el-button
          >
        </div>
      </header>
      <div class="inspection-grid">
        <el-form-item label="检查名称" required
          ><el-input
            v-model="check.name"
            maxlength="200"
            :placeholder="
              check.kind === 'agent' ? '如：答复符合业务政策' : '如：金额非负'
            "
        /></el-form-item>
        <el-form-item label="检查范围"
          ><el-select v-model="check.scope"
            ><el-option label="逐对象" value="object" /><el-option
              label="本次对象集合"
              value="collection" /></el-select
        ></el-form-item>
      </div>
      <template v-if="check.kind === 'rule'">
        <el-form-item label="条件关系"
          ><el-radio-group v-model="check.operator"
            ><el-radio-button value="and">全部满足</el-radio-button
            ><el-radio-button value="or"
              >任意满足</el-radio-button
            ></el-radio-group
          ></el-form-item
        >
        <div
          v-for="(condition, i) in check.conditions"
          :key="condition.code"
          class="condition-card"
        >
          <div class="inspection-heading">
            <strong>条件 {{ i + 1 }}</strong
            ><el-button
              :disabled="check.conditions!.length === 1"
              link
              @click="check.conditions!.splice(i, 1)"
              >移除条件</el-button
            >
          </div>
          <div class="inspection-grid">
            <el-form-item label="条件名称" required
              ><el-input v-model="condition.title" maxlength="200"
            /></el-form-item>
            <el-form-item label="判断方式"
              ><el-select v-model="condition.kind"
                ><el-option
                  v-for="(label, value) in ruleKinds"
                  :key="value"
                  :label="label"
                  :value="value" /></el-select
            ></el-form-item>
            <el-form-item label="检查字段" required
              ><el-input
                v-model="condition.path"
                placeholder="如 amount 或 _http.status_code"
            /></el-form-item>
            <el-form-item
              v-if="condition.kind === 'field_equals'"
              label="比较字段"
              required
              ><el-input v-model="condition.compare_path"
            /></el-form-item>
            <template
              v-if="['numeric_range', 'count_range'].includes(condition.kind)"
            >
              <el-form-item label="最小值"
                ><el-input
                  v-model="condition.minimum"
                  placeholder="不限制时留空"
              /></el-form-item>
              <el-form-item label="最大值"
                ><el-input
                  v-model="condition.maximum"
                  placeholder="不限制时留空"
              /></el-form-item>
            </template>
            <el-form-item
              v-if="
                ['equals', 'contains', 'starts_with', 'ends_with'].includes(
                  condition.kind,
                )
              "
              label="期望值"
              required
            >
              <div class="expected-value">
                <el-select
                  v-if="condition.kind === 'equals'"
                  :model-value="typeof condition.expected"
                  @change="
                    condition.expected =
                      $event === 'number' ? 0 : $event === 'boolean' ? true : ''
                  "
                  ><el-option label="文本" value="string" /><el-option
                    label="数值"
                    value="number" /><el-option label="布尔值" value="boolean"
                /></el-select>
                <el-input-number
                  v-if="typeof condition.expected === 'number'"
                  v-model="condition.expected"
                  :controls="false"
                /><el-switch
                  v-else-if="typeof condition.expected === 'boolean'"
                  v-model="condition.expected"
                /><el-input v-else v-model="condition.expected" />
              </div>
            </el-form-item>
          </div>
          <el-form-item label="判断依据" required
            ><el-input
              v-model="condition.basis"
              placeholder="填写业务口径或明确的判断标准"
              maxlength="2000"
          /></el-form-item>
        </div>
        <el-button
          :disabled="check.conditions!.length >= 30"
          @click="check.conditions!.push(newCondition())"
          >添加条件</el-button
        >
      </template>
      <template v-else>
        <el-form-item label="要检查什么" required
          ><el-input
            v-model="check.requirements"
            type="textarea"
            :rows="3"
            maxlength="8000"
            placeholder="如：结合业务政策与用户上下文，判断答复是否恰当，并引用原文依据"
        /></el-form-item>
        <el-form-item label="使用哪份方法（Skill）" required
          ><el-select v-model="check.skill_id" filterable
            ><el-option
              v-for="skill in props.skills"
              :key="skill.id"
              :label="`${skill.title} · v${skill.version}`"
              :value="skill.id"
              :disabled="!skill.enabled" /></el-select
        ></el-form-item>
        <el-form-item label="Agent可调用的API与数据库查询"
          ><el-select v-model="check.source_ids" multiple filterable
            ><el-option
              v-for="source in props.sources.filter(
                (s) =>
                  s.source_kind === 'database' ||
                  s.source_config.agent_api_enabled,
              )"
              :key="source.id"
              :label="`${source.name} · ${source.source_kind === 'api' ? 'API' : '数据库查询'}`"
              :value="source.id" /></el-select
        ></el-form-item>
        <el-collapse
          ><el-collapse-item title="辅助方法与业务参数" :name="check.check_id">
            <el-form-item label="辅助 Skill（最多 3 份）"
              ><el-select
                v-model="check.auxiliary_skill_ids"
                multiple
                :multiple-limit="3"
                ><el-option
                  v-for="skill in props.skills.filter(
                    (s) => s.id !== check.skill_id,
                  )"
                  :key="skill.id"
                  :label="`${skill.title} · v${skill.version}`"
                  :value="skill.id"
                  :disabled="!skill.enabled" /></el-select
            ></el-form-item>
            <el-form-item label="业务参数（JSON）"
              ><el-input
                v-model="parameters[check.check_id]"
                type="textarea"
                :rows="4"
            /></el-form-item> </el-collapse-item
        ></el-collapse>
      </template>
      <el-form-item label="异常级别" class="severity-field"
        ><el-select v-model="check.severity"
          ><el-option label="提示" value="info" /><el-option
            label="警告"
            value="warning" /><el-option
            label="严重"
            value="critical" /></el-select
      ></el-form-item>
    </section>
    <div class="inspection-actions">
      <div>
        <el-button
          :disabled="checks.length >= 30"
          @click="checks.push(newCheck('rule'))"
          >添加规则检查</el-button
        ><el-button
          :disabled="checks.length >= 30"
          @click="checks.push(newCheck('agent'))"
          >添加 Agent 检查</el-button
        >
      </div>
    </div>
  </div>
</template>
<style scoped>
.check-editor {
  display: grid;
  gap: 20px;
}
.check-card > header {
  margin-bottom: 24px;
}
.check-card > header h3 {
  margin: 0;
}
.condition-card {
  background: var(--el-fill-color-extra-light);
  padding: 18px;
  border-radius: 8px;
  margin-bottom: 16px;
}
.condition-card > .inspection-heading {
  margin-bottom: 16px;
}
.expected-value {
  display: flex;
  gap: 10px;
  width: 100%;
}
.severity-field {
  margin-top: 24px;
  max-width: 220px;
}
</style>
