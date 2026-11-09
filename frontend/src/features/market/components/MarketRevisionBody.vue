<script setup lang="ts">
import { computed } from "vue";
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
import {
  pricingNames,
  type MarketKind,
  type Revision,
} from "@/features/market/api";
const props = defineProps<{
  revision: Revision;
  kind: MarketKind;
  categoryName?: string;
}>();
const md = new MarkdownIt({ html: false, linkify: false });
const description = computed(() =>
  DOMPurify.sanitize(md.render(props.revision.description)),
);
const usage = computed(() =>
  DOMPurify.sanitize(md.render(props.revision.usage)),
);
</script>
<template>
  <div class="market-revision-body">
    <h2>{{ revision.title }}</h2>
    <p class="revision-summary">{{ revision.summary }}</p>
    <div class="badge-line">
      <el-tag v-if="categoryName" type="info">{{ categoryName }}</el-tag
      ><el-tag v-for="tag in revision.tags" :key="tag" effect="plain">{{
        tag
      }}</el-tag>
    </div>
    <dl class="market-facts">
      <div>
        <dt>适用平台</dt>
        <dd>{{ revision.platform || "未提供" }}</dd>
      </div>
      <div>
        <dt>作者／来源</dt>
        <dd>{{ revision.author || "未提供" }}</dd>
      </div>
      <template v-if="kind === 'skill'"
        ><div>
          <dt>版本</dt>
          <dd>{{ revision.skill_version || "未提供" }}</dd>
        </div>
        <div>
          <dt>许可证</dt>
          <dd>{{ revision.license || "未提供" }}</dd>
        </div></template
      >
      <div v-else>
        <dt>收费方式</dt>
        <dd>{{ pricingNames[revision.pricing] }}</dd>
      </div>
    </dl>
    <h3>详细介绍</h3>
    <article
      v-if="revision.description"
      class="markdown"
      v-html="description"
    ></article>
    <p v-else class="muted">尚未填写</p>
    <h3>使用说明</h3>
    <article v-if="revision.usage" class="markdown" v-html="usage"></article>
    <p v-else class="muted">尚未填写</p>
    <template v-if="kind === 'skill' && revision.requirements"
      ><h3>依赖与所需权限</h3>
      <p class="plain-text">{{ revision.requirements }}</p></template
    >
    <template v-if="kind === 'tool' && revision.reason"
      ><h3>推荐理由</h3>
      <p class="plain-text">{{ revision.reason }}</p></template
    >
    <div class="revision-links">
      <a
        v-if="revision.target_url"
        :href="revision.target_url"
        target="_blank"
        rel="noopener noreferrer"
        >{{ kind === "skill" ? "访问仓库／下载页面 ↗" : "访问工具官网 ↗" }}</a
      ><a
        v-if="revision.source_url"
        :href="revision.source_url"
        target="_blank"
        rel="noopener noreferrer"
        >查看来源 ↗</a
      >
    </div>
  </div>
</template>
<style scoped>
.market-revision-body {
  overflow-wrap: anywhere;
}
.market-revision-body > h2 {
  font-size: 24px;
}
.revision-summary {
  color: var(--muted);
}
.market-facts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px 24px;
  border-block: 1px solid var(--border);
  padding: 20px 0;
  margin: 24px 0;
}
.market-facts dt {
  color: var(--muted);
  font-size: 13px;
  margin-bottom: 6px;
}
.market-facts dd {
  margin: 0;
}
.market-revision-body h3 {
  margin-top: 28px;
}
.plain-text {
  white-space: pre-wrap;
}
.revision-links {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  margin-top: 28px;
  padding-top: 20px;
  border-top: 1px solid var(--border);
}
</style>
