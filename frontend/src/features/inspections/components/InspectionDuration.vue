<script setup lang="ts">
import { computed, ref } from "vue";

const props = defineProps<{
  max: number;
  presets: { label: string; value: number }[];
}>();
const seconds = defineModel<number>({ required: true });
const units = [
  { label: "天", value: 86400 },
  { label: "小时", value: 3600 },
  { label: "分钟", value: 60 },
  { label: "秒", value: 1 },
];
const unit = ref(units.find((u) => seconds.value % u.value === 0)?.value || 1);
const custom = ref(!props.presets.some((p) => p.value === seconds.value));
const selection = computed(() => (custom.value ? "custom" : seconds.value));
const amount = computed(() => seconds.value / unit.value);
function select(value: number | string) {
  custom.value = value === "custom";
  if (!custom.value) seconds.value = Number(value);
  unit.value = units.find((u) => seconds.value % u.value === 0)?.value || 1;
}
function changeUnit(value: number) {
  const previousAmount = amount.value;
  unit.value = value;
  seconds.value = previousAmount * value;
}
</script>

<template>
  <div class="duration-control">
    <el-select :model-value="selection" @update:model-value="select">
      <el-option
        v-for="preset in presets"
        :key="preset.value"
        :label="preset.label"
        :value="preset.value"
      />
      <el-option label="自定义" value="custom" />
    </el-select>
    <div v-if="custom" class="custom-duration">
      <el-input-number
        :model-value="amount"
        :min="Math.ceil(60 / unit)"
        :max="Math.floor(max / unit)"
        :precision="0"
        :controls="false"
        aria-label="自定义时长数值"
        @update:model-value="seconds = ($event || 0) * unit"
      />
      <el-select
        :model-value="unit"
        aria-label="时间单位"
        @update:model-value="changeUnit"
      >
        <el-option
          v-for="item in units"
          :key="item.value"
          :label="item.label"
          :value="item.value"
        />
      </el-select>
    </div>
  </div>
</template>

<style scoped>
.duration-control {
  display: grid;
  gap: 8px;
  width: 100%;
}
.custom-duration {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 90px;
  gap: 8px;
}
.el-input-number {
  width: 100%;
}
</style>
