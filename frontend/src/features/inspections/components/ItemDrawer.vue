<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { api, all, type Models } from "@/api/client";
import { useAuth } from "@/features/auth/store";
import { usePoll } from "@/composables/usePoll";
import { date } from "@/utils/display";
import {
  errorText,
  errorNames,
  stateNames,
  tone,
  type Item,
} from "@/features/inspections/ui";
import { itemTitle, displayValue } from "@/features/inspections/display";
import InspectionChecks from "./InspectionChecks.vue";
import JsonEvidence from "@/components/common/JsonEvidence.vue";
import CheckResults from "./CheckResults.vue";
const open = defineModel<boolean>({ required: true });
const props = defineProps<{
  itemId?: string;
  active?: boolean;
  showProcess?: boolean;
}>();
const emit = defineEmits<{
  process: [check: Models["InspectionCheckResultResponse"]];
}>();
const auth = useAuth(),
  item = ref<Item>(),
  tab = ref("data"),
  error = ref(""),
  loading = ref(false),
  reviews = ref<Models["InspectionReviewResponse"][]>([]),
  verdict = ref("original_correct"),
  comment = ref(""),
  saving = ref(false);
const gaps = computed(() =>
  Array.isArray(item.value?.agent_result.data_gaps)
    ? (item.value!.agent_result.data_gaps as string[])
    : [],
);
const agentError = computed(() =>
  String(item.value?.agent_result.error_code || ""),
);
const facts = computed(() => {
  const data = item.value?.evidence.facts as
    | Record<string, unknown>
    | undefined;
  return Object.entries(
    data?.main && typeof data.main === "object"
      ? (data.main as Record<string, unknown>)
      : data || {},
  ).filter(([key]) => key !== "_inspection");
});
let revision = 0,
  selectionRevision = 0;
async function load() {
  const current = ++revision;
  if (!open.value || !props.itemId) return;
  try {
    const value = await api<Item>(`/inspection-items/${props.itemId}`);
    if (current === revision && open.value) {
      item.value = value;
      error.value = "";
    }
  } catch (e) {
    if (current === revision && open.value) error.value = errorText(e);
  }
}
usePoll(load, () => (open.value && props.active ? 3000 : null));
watch(
  () => props.active,
  (active) => {
    if (!active) void load();
  },
);
watch(
  () => [open.value, props.itemId],
  async () => {
    const selection = ++selectionRevision;
    revision++;
    if (!open.value || !props.itemId) return;
    loading.value = true;
    item.value = undefined;
    error.value = "";
    tab.value = "data";
    comment.value = "";
    try {
      const [, history] = await Promise.all([
        load(),
        all<Models["InspectionReviewResponse"]>(
          `/inspection-items/${props.itemId}/reviews`,
        ),
      ]);
      if (selection !== selectionRevision || !open.value) return;
      reviews.value = history;
    } catch (e) {
      if (selection === selectionRevision && open.value)
        error.value = errorText(e);
    } finally {
      if (selection === selectionRevision && open.value) loading.value = false;
    }
  },
);
async function review() {
  saving.value = true;
  try {
    await api(`/inspection-items/${item.value!.id}/reviews`, "POST", {
      verdict: verdict.value,
      comment: comment.value,
    });
    reviews.value = await all<Models["InspectionReviewResponse"]>(
      `/inspection-items/${item.value!.id}/reviews`,
    );
    comment.value = "";
  } catch (e) {
    error.value = errorText(e);
  } finally {
    saving.value = false;
  }
}
</script>
<template>
  <el-drawer
    v-model="open"
    :title="item ? itemTitle(item, 1) : '对象详情'"
    size="min(780px,100vw)"
    class="inspection-drawer"
    destroy-on-close
  >
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div v-loading="loading" class="inspection-details" v-if="item">
      <div class="inspection-heading">
        <el-tag :type="tone(item.final_status)">{{
          stateNames[item.final_status]
        }}</el-tag
        ><span>{{ date(item.observed_at) }}</span>
      </div>
      <el-alert
        :title="item.explanation"
        :type="
          item.final_status === 'fail'
            ? 'error'
            : item.agent_status === 'failed'
              ? 'warning'
              : 'info'
        "
        :closable="false"
      />
      <el-tabs v-model="tab">
        <el-tab-pane label="实际数据" name="data"
          ><div class="fact-row" v-for="[key, value] in facts" :key="key">
            <span>{{ key }}</span
            ><span>{{ displayValue(value) }}</span>
          </div>
          <el-collapse style="margin-top: 24px"
            ><el-collapse-item title="完整查询证据" name="raw"
              ><JsonEvidence :value="item.evidence" /></el-collapse-item
            ><el-collapse-item title="对象标识" name="id"
              ><div class="fact-row">
                <span>业务编号</span><span>{{ item.record_id }}</span>
              </div>
              <div class="fact-row">
                <span>数据版本</span><span>{{ item.record_version }}</span>
              </div></el-collapse-item
            ></el-collapse
          ></el-tab-pane
        >
        <el-tab-pane
          :label="item.check_status != null ? '逐项检查结果' : '规则判断依据'"
          name="rules"
          ><CheckResults
            v-if="item.check_status != null"
            :key="item.id"
            :run-id="item.run_id"
            :item-id="item.id"
            :active="open && !!active"
            :show-process="showProcess"
            @process="emit('process', $event)" />
          <InspectionChecks
            v-else-if="item.checks.length"
            :checks="item.checks" /><el-empty
            v-else
            description="本次使用 Agent Skill 检查"
            :image-size="70"
        /></el-tab-pane>
        <el-tab-pane label="Agent 分析" name="agent"
          ><el-tag
            :type="
              item.agent_status === 'failed'
                ? 'warning'
                : tone(item.agent_status)
            "
            >{{
              item.agent_status === "failed"
                ? "智能分析失败"
                : stateNames[item.agent_status] || item.agent_status
            }}</el-tag
          >
          <p
            v-if="item.agent_result.summary"
            style="white-space: pre-wrap; line-height: 1.8"
          >
            {{ item.agent_result.summary }}
          </p>
          <template v-if="gaps.length"
            ><h3>缺失资料</h3>
            <ul>
              <li v-for="(gap, i) in gaps" :key="i">{{ gap }}</li>
            </ul></template
          ><el-alert
            v-if="item.agent_result.error_code"
            :title="errorNames[agentError] || '智能分析未完成，请查看错误详情'"
            type="warning"
            :closable="false" /><el-collapse
            v-if="Object.keys(item.agent_result).length"
            style="margin-top: 24px"
            ><el-collapse-item title="结构化分析与错误详情" name="result"
              ><JsonEvidence
                :value="item.agent_result" /></el-collapse-item></el-collapse
        ></el-tab-pane>
        <el-tab-pane label="人工复核" name="review"
          ><el-timeline v-if="reviews.length"
            ><el-timeline-item
              v-for="r in reviews"
              :key="r.id"
              :timestamp="date(r.created_at)"
              >{{ r.comment }}</el-timeline-item
            ></el-timeline
          ><el-empty
            v-else
            description="暂无复核记录"
            :image-size="60"
          /><el-form v-if="auth.can('inspections:review')" label-position="top"
            ><el-form-item label="复核结论"
              ><el-select v-model="verdict"
                ><el-option
                  label="原判定正确"
                  value="original_correct" /><el-option
                  label="原判定误拒"
                  value="false_reject" /><el-option
                  label="原判定误放"
                  value="false_accept" /><el-option
                  label="证据不足"
                  value="insufficient_evidence" /></el-select></el-form-item
            ><el-form-item label="复核意见" required
              ><el-input
                v-model="comment"
                type="textarea"
                :rows="3"
                maxlength="4000" /></el-form-item
            ><el-button
              type="primary"
              :loading="saving"
              :disabled="!comment.trim()"
              @click="review"
              >提交复核</el-button
            ></el-form
          ></el-tab-pane
        >
      </el-tabs>
    </div>
  </el-drawer>
</template>
