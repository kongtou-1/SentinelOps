<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Search, Collection, Link } from "@element-plus/icons-vue";
import {
  getCategories,
  listEntries,
  pricingNames,
  type Category,
  type Entry,
  type MarketKind,
} from "@/features/market/api";
const props = defineProps<{ kind: MarketKind }>(),
  route = useRoute(),
  router = useRouter();
const categories = ref<Category[]>([]),
  entries = ref<Entry[]>([]),
  total = ref(0),
  error = ref(""),
  loading = ref(false);
const search = ref(String(route.query.q || ""));
const category = computed(() => String(route.query.category || ""));
const page = computed(() => Math.max(1, Number(route.query.page) || 1));
let sequence = 0;
async function load() {
  const run = ++sequence;
  loading.value = true;
  error.value = "";
  search.value = String(route.query.q || "");
  try {
    const [cats, result] = await Promise.all([
      getCategories(props.kind),
      listEntries({
        kind: props.kind,
        category_id: category.value,
        q: search.value,
        offset: (page.value - 1) * 12,
        limit: 12,
      }),
    ]);
    if (run !== sequence) return;
    categories.value = cats;
    entries.value = result.data;
    total.value = result.total || 0;
  } catch (e) {
    if (run === sequence) {
      entries.value = [];
      total.value = 0;
      error.value = e instanceof Error ? e.message : "加载失败";
    }
  } finally {
    if (run === sequence) loading.value = false;
  }
}
function filter(values: Record<string, string | undefined>) {
  void router.push({
    path: route.path,
    query: { ...route.query, page: undefined, ...values },
  });
}
watch(() => route.fullPath, load);
onMounted(load);
</script>
<template>
  <form
    class="discover-search"
    @submit.prevent="filter({ q: search.trim() || undefined })"
  >
    <el-input
      v-model="search"
      size="large"
      placeholder="搜索名称、简介或标签"
      clearable
      :prefix-icon="Search"
      aria-label="搜索内容"
    /><el-button type="primary" size="large" native-type="submit"
      >搜索</el-button
    >
  </form>
  <nav class="category-chips" aria-label="内容分类">
    <button
      :class="{ selected: !category }"
      @click="filter({ category: undefined })"
    >
      全部</button
    ><button
      v-for="c in categories.filter((c) => c.enabled || c.count > 0)"
      :key="c.id"
      :class="{ selected: category === c.id }"
      :title="c.description"
      @click="filter({ category: c.id })"
    >
      {{ c.name }} <span>{{ c.count }}</span>
    </button>
  </nav>
  <div class="catalogue-meta">
    <span>{{ total }} 个{{ kind === "skill" ? "Skill" : "实用工具" }}</span
    ><span>按发布时间排序</span>
  </div>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
    show-icon
    class="error-box"
  />
  <div v-loading="loading" class="catalogue-results">
    <div v-if="entries.length" class="catalogue-grid">
      <router-link
        v-for="entry in entries"
        :key="entry.id"
        :to="{ path: `/discover/items/${entry.id}`, query: { kind } }"
        class="discovery-card"
        ><div class="card-heading">
          <span class="card-icon"
            ><el-icon
              ><Collection v-if="kind === 'skill'" /><Link
                v-else /></el-icon></span
          ><el-tag type="info" size="small">{{ entry.category_name }}</el-tag>
        </div>
        <h2>{{ entry.revision.title }}</h2>
        <p>{{ entry.revision.summary }}</p>
        <div class="card-tags">
          <el-tag
            v-for="tag in entry.revision.tags.slice(0, 3)"
            :key="tag"
            size="small"
            effect="plain"
            >{{ tag }}</el-tag
          >
        </div>
        <div class="card-footer">
          <span>{{ entry.revision.platform || "通用场景" }}</span
          ><span>{{
            kind === "tool"
              ? pricingNames[entry.revision.pricing]
              : entry.revision.skill_version || "Skill"
          }}</span>
        </div></router-link
      >
    </div>
    <el-empty
      v-else-if="!loading"
      :description="
        error
          ? '暂时无法加载内容，请重试'
          : '还没有符合条件的内容，可以提交第一条'
      "
    />
  </div>
  <div class="catalogue-pagination">
    <el-pagination
      v-if="total > 12"
      :current-page="page"
      :page-size="12"
      :total="total"
      layout="prev, pager, next"
      @current-change="filter({ page: String($event) })"
    /><el-button v-if="error" @click="load">重新加载</el-button>
  </div>
</template>
<style scoped>
.discover-search {
  display: flex;
  gap: 12px;
  margin: 8px 0 22px;
  max-width: 760px;
}
.category-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 24px;
}
.category-chips button {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--el-text-color-regular);
  border-radius: 20px;
  padding: 8px 15px;
  cursor: pointer;
  font: inherit;
  white-space: nowrap;
}
.category-chips button.selected {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.category-chips span {
  margin-left: 4px;
  font-size: 12px;
  opacity: 0.7;
}
.catalogue-meta {
  display: flex;
  justify-content: space-between;
  color: var(--muted);
  margin-bottom: 16px;
  font-size: 13px;
}
.catalogue-results {
  min-height: 230px;
}
.catalogue-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
}
.discovery-card {
  display: flex;
  flex-direction: column;
  padding: 24px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  color: var(--el-text-color-primary);
  transition:
    border-color 0.15s,
    transform 0.15s;
  min-width: 0;
}
.discovery-card:hover {
  border-color: var(--el-color-primary);
  transform: translateY(-3px);
}
.card-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 20px;
}
.card-icon {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  border-radius: 12px;
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  font-size: 23px;
}
.discovery-card h2 {
  font-size: 18px;
  margin-bottom: 10px;
  overflow-wrap: anywhere;
}
.discovery-card p {
  color: var(--muted);
  flex: 1;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 72px;
  overflow-wrap: anywhere;
}
.card-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  min-height: 24px;
  margin-bottom: 20px;
}
.card-footer {
  border-top: 1px solid var(--border);
  padding-top: 16px;
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: var(--muted);
  font-size: 12px;
}
.card-footer span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.catalogue-pagination {
  margin-top: 28px;
  display: flex;
  justify-content: center;
}
@media (max-width: 1000px) {
  .catalogue-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 650px) {
  .catalogue-grid {
    grid-template-columns: 1fr;
  }
  .category-chips {
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-bottom: 8px;
  }
}
</style>
