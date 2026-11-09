<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import {
  ArrowLeft,
  ArrowRight,
  Back,
  Bell,
  Box,
  ChatDotRound,
  CircleCheck,
  Close,
  Cloudy,
  Coin,
  Collection,
  Connection,
  Cpu,
  Document,
  Files,
  FullScreen,
  Grid,
  Histogram,
  Link,
  List,
  Lock,
  Monitor,
  Refresh,
  Search,
  Share,
  Timer,
  Tools,
  TrendCharts,
  User,
  UserFilled,
  View,
  Warning,
} from "@element-plus/icons-vue";

const chapters = [
  { id: "context", title: "现状" },
  { id: "gaps", title: "痛点" },
  { id: "solution", title: "方案" },
  { id: "architecture", title: "架构" },
  { id: "capabilities", title: "能力" },
  { id: "inspection", title: "巡检Agent" },
  { id: "askdata", title: "问数Agent" },
  { id: "loop", title: "闭环" },
  { id: "evolution", title: "优化" },
  { id: "roadmap", title: "展望" },
];
const activeChapter = ref("context");
function chapterLabel(id: string) {
  return chapters.find((chapter) => chapter.id === id)?.title ?? "";
}
const gaps = [
  {
    icon: Warning,
    title: "发现晚",
    name: "感知缺口",
    tone: "orange",
    before: "用户反馈 / 服务不可用",
    beforeIcon: User,
    after: "异常已影响业务，才开始处理",
    tagIcon: Warning,
    tags: ["被动发现", "缺少预判"],
  },
  {
    icon: Search,
    title: "排查慢",
    name: "上下文缺口",
    tone: "blue",
    before: "指标 → 日志 → 版本 → 源码",
    beforeIcon: Document,
    after: "信息分散，AI 也需要人工搬运",
    tagIcon: Refresh,
    tags: ["反复切换", "证据不足"],
  },
  {
    icon: Collection,
    title: "难复用",
    name: "经验缺口",
    tone: "violet",
    before: "个人经验 / 聊天记录",
    beforeIcon: User,
    after: "相似问题再次发生，重新排查",
    tagIcon: Collection,
    tags: ["依赖个人", "沉淀不足"],
  },
];
const engines = [
  {
    icon: TrendCharts,
    title: "预测算法",
    en: "FORECAST",
    verb: "提前看见风险",
    steps: [
      "识别周期与长期趋势",
      "检测尖峰异常与指标漂移",
      "输出预测区间与风险提示",
    ],
    detail: "MSTL · AutoETS · Robust Z-Score · EWMA",
    tone: "blue",
  },
  {
    icon: Cpu,
    title: "Agent Harness",
    en: "DIAGNOSE",
    verb: "围绕证据诊断",
    steps: [
      "组织现场指标、日志和版本",
      "按需调用受约束的只读工具",
      "输出结论、依据与信息缺口",
    ],
    detail: "上下文管理 · 工具约束 · 多轮对话",
    tone: "teal",
  },
  {
    icon: Files,
    title: "知识与 Skill",
    en: "REUSE",
    verb: "复用排查经验",
    steps: [
      "检索历史案例与相关知识",
      "按需加载标准化排查步骤",
      "保留知识引用与 Skill 版本",
    ],
    detail: "知识检索 · SOP · 版本化指令",
    tone: "violet",
  },
];
const layers = [
  {
    name: "业务流程",
    en: "WORKFLOW",
    icon: Share,
    items: [
      { label: "事故中心", icon: Bell },
      { label: "诊断工作台", icon: Monitor },
      { label: "审批与执行", icon: Document },
      { label: "修复交接 / 复盘", icon: Refresh },
    ],
    tone: "blue",
  },
  {
    name: "智能能力",
    en: "INTELLIGENCE",
    icon: Cpu,
    items: [
      { label: "预测与异常检测", icon: Histogram },
      { label: "Agent Harness", icon: ChatDotRound },
      { label: "知识检索", icon: Search },
      { label: "Skill 按需加载", icon: Box },
    ],
    tone: "teal",
  },
  {
    name: "证据基础",
    en: "EVIDENCE",
    icon: Coin,
    items: [
      { label: "指标 / 告警 / 日志", icon: List },
      { label: "服务与部署关系", icon: Share },
      { label: "版本 / 源码快照", icon: Document },
      { label: "处理与验证记录", icon: Files },
    ],
    tone: "violet",
  },
  {
    name: "资源对象",
    en: "RESOURCES",
    icon: Grid,
    items: [
      { label: "服务器", icon: Monitor },
      { label: "Docker 容器", icon: Box },
      { label: "LLM 服务", icon: Cloudy },
      { label: "代码仓库", icon: Collection },
    ],
    tone: "slate",
  },
];
const governance = [
  { label: "角色权限", icon: UserFilled },
  { label: "只读诊断", icon: View },
  { label: "人工审批", icon: CircleCheck },
  { label: "审计追溯", icon: Lock },
];
const serviceTiles = [
  { label: "业务应用", icon: Monitor },
  { label: "API / 后台", icon: Document },
  { label: "数据服务", icon: Coin },
  { label: "AI / LLM", icon: Cpu, ai: true },
];
const evidenceSteps = ["事故时刻", "实际部署", "对应版本", "冻结证据"];
const forwardSteps = [
  { title: "监测预警", sub: "指标 / 探测 / 预测", icon: TrendCharts },
  { title: "事故聚合", sub: "关联告警与目标", icon: Connection },
  { title: "证据诊断", sub: "工具 / 版本 / 源码", icon: Cpu },
  { title: "人工决策", sub: "采纳结论 / 审批", icon: User },
];
const backwardSteps = [
  { title: "受控处置", sub: "容器重启 / 执行记录", icon: Box },
  { title: "恢复核验", sub: "重新观测目标状态", icon: CircleCheck },
  { title: "复盘沉淀", sub: "冻结证据 / 人工审核", icon: Document },
];
const screenshots = [
  {
    id: "prediction",
    src: "/project-intro-media/prediction.png",
    title: "提前看风险",
    subtitle: "实测趋势 · 预测区间 · 数据质量",
    note: "本机预测页面 · 历史验收截图",
    icon: TrendCharts,
    output: "输出风险趋势与数据可用性",
    tone: "blue",
  },
  {
    id: "diagnosis",
    src: "/project-intro-media/diagnosis.png",
    title: "按证据定位",
    subtitle: "工具过程 · 知识引用 · 人工采用",
    note: "隔离验收界面 · 模拟 Provider",
    icon: Cpu,
    output: "输出结论、依据与信息缺口",
    tone: "teal",
  },
  {
    id: "repair",
    src: "/project-intro-media/repair.png",
    title: "带材料交接",
    subtitle: "修复建议 · 版本关联 · 人工验证",
    note: "隔离 HTTP 故障 · 修复交接验收",
    icon: Document,
    output: "输出可交接、可核对的修复材料",
    tone: "violet",
  },
];
type Screenshot = (typeof screenshots)[number];

const inspectionFlow = [
  {
    icon: Timer,
    label: "计划与调度",
    text: "间隔 / Cron 到期，冻结计划版本、Skill 与字段授权",
  },
  {
    icon: Connection,
    label: "多源只读取证",
    text: "固定 GET 与受控 SELECT，主源加关联源按字段匹配",
  },
  {
    icon: Cpu,
    label: "规则与 Agent 检查",
    text: "确定性规则先判定，Skill 在隔离沙箱补充计算",
  },
  {
    icon: Files,
    label: "逐项结论与复核",
    text: "保存对象结论、证据快照、运行轨迹与人工复核",
  },
];
const inspectionCards = [
  {
    tone: "blue",
    en: "DATA SOURCES & PLANS",
    icon: Connection,
    title: "数据源与计划",
    points: [
      "API 固定 GET、数据库受控 SELECT，表与字段列级授权",
      "凭据加密保存且不回填，支持连接测试与数据预览",
      "试运行默认最多 3 个对象，不推进正式进度",
    ],
  },
  {
    tone: "teal",
    en: "RULES & CONSTRAINTS",
    icon: Tools,
    title: "检查与约束",
    points: [
      "必填、相等、字段比较、数值范围与数组通配，Decimal 比较",
      "水位增量读取、每轮上限与 Agent 项数／时长预算",
      "暂停、撤权、修改计划后，后续读取与保存重新校验",
    ],
  },
  {
    tone: "violet",
    en: "RESULTS & REVIEW",
    icon: Files,
    title: "结果与复核",
    points: [
      "任务、执行记录、对象覆盖三级视图与检查覆盖统计",
      "运行轨迹可展开真实输入、输出与 Python 代码",
      "证据下载、CSV 导出，人工复核只追加",
    ],
  },
];
const askdataFlow = [
  {
    icon: Collection,
    label: "语义构建",
    text: "元数据、源码口径与人工说明汇成版本化业务模型",
  },
  {
    icon: ChatDotRound,
    label: "中文问数",
    text: "检索目录与向量，生成候选 SQL 并核对业务口径",
  },
  {
    icon: Lock,
    label: "只读执行",
    text: "列级授权、SELECT 白名单、只读事务与超时限额",
  },
  {
    icon: Histogram,
    label: "答案与依据",
    text: "脱敏结果生成中文摘要、表格图表、CSV 与代码依据",
  },
];
const askdataCards = [
  {
    tone: "blue",
    en: "SEMANTIC LAYER",
    icon: Collection,
    title: "口径沉淀",
    points: [
      "适配 Auto-Ontology 固定版本，纳入平台迁移与权限",
      "元数据、Python / Java 源码规则与人工表说明合并",
      "模型版本隔离，pgvector 保存目录与语义向量",
    ],
  },
  {
    tone: "teal",
    en: "CONTROLLED EXECUTION",
    icon: Lock,
    title: "可控执行",
    points: [
      "SQL 网关拒绝非 SELECT 与未授权列，默认只读事务",
      "连接隔离、语句超时与结果限额，凭据不进入上下文",
      "中文摘要数值取自执行结果，不从 SQL 说明猜测",
    ],
  },
  {
    tone: "violet",
    en: "WORKBENCH",
    icon: Monitor,
    title: "问数工作台",
    points: [
      "问数对话、数据目录、业务模型、分析库与统计",
      "问数设置覆盖连接、业务规则、语义构建与模型",
      "SSE 展示运行进度，权限、版本与用量留痕",
    ],
  },
];

const preview = ref<Screenshot>();
const previewDialog = ref<HTMLDialogElement>();
async function openPreview(item: Screenshot) {
  preview.value = item;
  await nextTick();
  previewDialog.value?.showModal();
}
function jump(id: string) {
  document.getElementById(id)?.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
    block: "start",
  });
}

/* 演示模式：一页一屏，整页等比缩放填满视口 */
const DESIGN_WIDTH = 1360;
const MIN_SLIDE_HEIGHT = 780;
const root = ref<HTMLElement>();
const presenting = ref(false);
const slideIndex = ref(0);
const currentChapter = computed(
  () => chapters[slideIndex.value] ?? chapters[0],
);
let previousOverflow = "";

function slideSections(): HTMLElement[] {
  return Array.from(
    root.value?.querySelectorAll<HTMLElement>(".intro-section") ?? [],
  );
}

/** 以设计宽度量出所有页面的最高内容高度，统一画布高度后再算缩放比 */
function applySlideLayout() {
  const stage = root.value;
  if (!presenting.value || !stage) return;
  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;
  const width = Math.min(DESIGN_WIDTH, viewportWidth);
  stage.style.setProperty("--slide-width", `${width}px`);
  stage.classList.add("is-measuring");
  let tallest = 0;
  for (const section of slideSections()) {
    const sheet = section.querySelector<HTMLElement>(".intro-sheet");
    if (sheet) tallest = Math.max(tallest, sheet.offsetHeight);
  }
  stage.classList.remove("is-measuring");
  const height = Math.max(MIN_SLIDE_HEIGHT, Math.ceil(tallest));
  // 白色页面铺满视口；内容按统一比例缩放，底部 88px 留给工具条
  const scale = Math.min(
    (viewportWidth - 32) / width,
    (viewportHeight - 88) / height,
  );
  stage.style.setProperty("--slide-height", `${height}px`);
  stage.style.setProperty(
    "--slide-scale",
    `${Math.max(scale, 0.2).toFixed(4)}`,
  );
}

function goToSlide(index: number) {
  const next = Math.min(Math.max(index, 0), chapters.length - 1);
  if (next === slideIndex.value) return;
  slideIndex.value = next;
  activeChapter.value = chapters[next].id;
}

async function enterPresentation() {
  if (presenting.value) return;
  const index = chapters.findIndex(
    (chapter) => chapter.id === activeChapter.value,
  );
  slideIndex.value = index >= 0 ? index : 0;
  presenting.value = true;
  previousOverflow = document.documentElement.style.overflow;
  document.documentElement.style.overflow = "hidden";
  try {
    await root.value?.requestFullscreen();
  } catch {
    // 浏览器拒绝全屏时仍以覆盖层演示
  }
  await nextTick();
  applySlideLayout();
}

function exitPresentation() {
  if (!presenting.value) return;
  presenting.value = false;
  document.documentElement.style.overflow = previousOverflow;
  if (document.fullscreenElement)
    void document.exitFullscreen().catch(() => {});
  const id = chapters[slideIndex.value]?.id;
  if (id) {
    document.getElementById(id)?.scrollIntoView({ block: "start" });
  }
}

let wheelLock = 0;
function onKeydown(event: KeyboardEvent) {
  if (!presenting.value) return;
  const target = event.target as HTMLElement | null;
  const spaceLike = event.key === " " || event.key === "Enter";
  // 工具条按钮获得焦点时，空格/回车交给按钮自身，避免翻两页
  if (spaceLike && target?.closest(".slide-bar")) return;
  // 图片预览打开时保留原生 Esc 关闭行为
  if (event.key === "Escape" && document.querySelector("dialog[open]")) return;
  switch (event.key) {
    case "ArrowRight":
    case "ArrowDown":
    case "PageDown":
    case " ":
    case "Enter":
      event.preventDefault();
      goToSlide(slideIndex.value + 1);
      break;
    case "ArrowLeft":
    case "ArrowUp":
    case "PageUp":
      event.preventDefault();
      goToSlide(slideIndex.value - 1);
      break;
    case "Home":
      event.preventDefault();
      goToSlide(0);
      break;
    case "End":
      event.preventDefault();
      goToSlide(chapters.length - 1);
      break;
    case "Escape":
      exitPresentation();
      break;
  }
}
function onWheel(event: WheelEvent) {
  if (!presenting.value) return;
  event.preventDefault();
  const now = Date.now();
  if (now < wheelLock || Math.abs(event.deltaY) < 12) return;
  wheelLock = now + 520;
  goToSlide(slideIndex.value + (event.deltaY > 0 ? 1 : -1));
}
function onFullscreenChange() {
  if (presenting.value && !document.fullscreenElement) exitPresentation();
  else if (presenting.value) applySlideLayout();
}
let resizeFrame = 0;
function onResize() {
  if (!presenting.value) return;
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() =>
    requestAnimationFrame(applySlideLayout),
  );
}

watch(
  [presenting, slideIndex],
  () => {
    const current = slideIndex.value;
    slideSections().forEach((section, index) => {
      section.classList.toggle(
        "is-current",
        presenting.value && index === current,
      );
    });
  },
  { flush: "post" },
);

let observer: IntersectionObserver | undefined;
let previousTitle = "";
onMounted(() => {
  previousTitle = document.title;
  document.title = "项目介绍 · SentinelOps";
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) activeChapter.value = entry.target.id;
      }
    },
    { rootMargin: "-15% 0px -60% 0px" },
  );
  document
    .querySelectorAll(".intro-section")
    .forEach((section) => observer?.observe(section));
  window.addEventListener("keydown", onKeydown);
  window.addEventListener("resize", onResize);
  document.addEventListener("fullscreenchange", onFullscreenChange);
});
onUnmounted(() => {
  observer?.disconnect();
  document.title = previousTitle;
  window.removeEventListener("keydown", onKeydown);
  window.removeEventListener("resize", onResize);
  document.removeEventListener("fullscreenchange", onFullscreenChange);
  cancelAnimationFrame(resizeFrame);
  if (presenting.value) exitPresentation();
  document.documentElement.style.overflow = previousOverflow;
});
</script>

<template>
  <div
    ref="root"
    class="project-intro"
    :class="{ 'is-presenting': presenting }"
  >
    <header class="intro-nav">
      <router-link
        to="/"
        class="intro-brand"
        aria-label="返回 SentinelOps 工作台"
      >
        <span class="intro-brand-mark"><Monitor /></span>
        <strong>SentinelOps</strong
        ><span class="intro-brand-label">项目介绍</span>
      </router-link>
      <nav class="intro-chapters" aria-label="项目介绍章节">
        <a
          v-for="(chapter, index) in chapters"
          :key="chapter.id"
          :href="'#' + chapter.id"
          :class="{ active: activeChapter === chapter.id }"
          :aria-current="activeChapter === chapter.id ? 'location' : undefined"
          @click.prevent="jump(chapter.id)"
          ><span>{{ String(index + 1).padStart(2, "0") }}</span
          >{{ chapter.title }}</a
        >
      </nav>
      <button
        class="intro-present"
        type="button"
        title="全屏演示，一屏一页"
        @click="enterPresentation"
      >
        <FullScreen /><span>演示</span>
      </button>
      <router-link to="/" class="intro-return"
        ><Back /><span>工作台</span></router-link
      >
    </header>

    <main class="intro-pages" @wheel="onWheel">
      <section
        id="context"
        class="intro-section"
        aria-labelledby="context-title"
      >
        <div class="intro-sheet hero-sheet">
          <header class="intro-heading">
            <h1 id="context-title">
              {{ chapterLabel("context") }}：团队运维，需要跟上项目增长
            </h1>
          </header>
          <div class="context-panels">
            <section class="context-panel panel-infrastructure">
              <header class="panel-caption">
                <span class="panel-eyebrow"
                  ><b>01</b>INFRASTRUCTURE · 基建现状</span
                >
                <h2>少量资源，承载多种服务</h2>
                <p>业务、API、数据与 AI 服务共用有限的运维资源</p>
              </header>
              <div class="panel-map">
                <div class="server-stack">
                  <div class="rack">
                    <span class="rack-icon"><Monitor /></span>
                    <span class="rack-lines"><i></i><i></i></span>
                    <span class="rack-led"></span>
                  </div>
                  <div class="rack">
                    <span class="rack-icon"><Monitor /></span>
                    <span class="rack-lines"><i></i><i></i></span>
                    <span class="rack-led"></span>
                  </div>
                  <span class="rack-label">有限服务器资源</span>
                </div>
                <svg
                  class="panel-topology"
                  viewBox="0 0 112 158"
                  preserveAspectRatio="none"
                  aria-hidden="true"
                >
                  <g
                    fill="none"
                    stroke="#8eafe0"
                    stroke-width="2.3"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M0 26H26V82H110" />
                    <path d="M0 94H26V82" />
                    <path d="M58 82V26H110" />
                    <path d="M58 82V137H110" />
                    <path d="M83 82H110" />
                  </g>
                  <circle cx="26" cy="82" r="5" fill="#478ee4" />
                  <circle cx="58" cy="82" r="4" fill="#9bbbe7" />
                  <path
                    d="m103 76 7 6-7 6"
                    fill="none"
                    stroke="#417ac7"
                    stroke-width="2.2"
                  />
                </svg>
                <div class="service-grid">
                  <span
                    v-for="tile in serviceTiles"
                    :key="tile.label"
                    class="service-tile"
                    :class="{ ai: tile.ai }"
                  >
                    <span class="tile-icon"><component :is="tile.icon" /></span
                    >{{ tile.label }}
                  </span>
                </div>
              </div>
              <div class="panel-detail">
                <span><i></i>监控能力待完善</span>
                <span><i></i>服务关系分散</span>
                <span><i></i>维护依赖人工</span>
              </div>
            </section>

            <section class="context-panel panel-delivery">
              <header class="panel-caption">
                <span class="panel-eyebrow"><b>02</b>DELIVERY · 研发现状</span>
                <h2>交付越来越快，维护压力持续累积</h2>
                <p>项目、版本与依赖增长，人工运维能力难以同步扩张</p>
              </header>
              <span class="trend-type">概念趋势示意 · 非实测</span>
              <div class="trend-chart">
                <svg
                  viewBox="0 0 690 226"
                  preserveAspectRatio="none"
                  role="img"
                  aria-label="概念趋势：项目与依赖的增长速度逐渐快于人工维护能力，不代表实测数据"
                >
                  <defs>
                    <linearGradient
                      id="context-gap-fill"
                      x1="0"
                      x2="0"
                      y1="0"
                      y2="1"
                    >
                      <stop stop-color="#f6ad69" stop-opacity=".42" />
                      <stop
                        offset="1"
                        stop-color="#fff1e3"
                        stop-opacity=".18"
                      />
                    </linearGradient>
                    <linearGradient id="context-gap-stroke" x1="0" x2="1">
                      <stop stop-color="#ea9c5b" />
                      <stop offset="1" stop-color="#e3782a" />
                    </linearGradient>
                  </defs>
                  <g stroke="#e7edf5" stroke-width="1" stroke-dasharray="5 7">
                    <path d="M38 58H661M38 109H661M38 160H661" />
                  </g>
                  <path
                    d="M38 15V196H661"
                    fill="none"
                    stroke="#aebfd6"
                    stroke-width="1.5"
                  />
                  <path
                    d="M45 190C159 180 267 161 360 135S553 77 644 20L644 146C551 148 443 157 360 167S156 182 45 190Z"
                    fill="url(#context-gap-fill)"
                  />
                  <path
                    d="M45 190C155 183 265 177 365 166S558 150 644 146"
                    fill="none"
                    stroke="#4288d7"
                    stroke-width="4"
                    stroke-linecap="round"
                  />
                  <path
                    d="M45 190C159 180 267 161 360 135S553 77 644 20"
                    fill="none"
                    stroke="url(#context-gap-stroke)"
                    stroke-width="4.6"
                    stroke-linecap="round"
                  />
                  <circle
                    cx="644"
                    cy="20"
                    r="6"
                    fill="#e57b30"
                    stroke="#fff"
                    stroke-width="3"
                  />
                  <circle
                    cx="644"
                    cy="146"
                    r="5"
                    fill="#4288d7"
                    stroke="#fff"
                    stroke-width="2"
                  />
                </svg>
                <div class="trend-legend">
                  <span><i></i>项目 / 版本 / 依赖增长</span>
                  <span><i class="blue"></i>人工维护能力</span>
                </div>
                <span class="trend-gap">压力差扩大</span>
                <div class="trend-axis" aria-hidden="true">
                  <span>现在</span><span>持续迭代 →</span>
                </div>
              </div>
              <div class="trend-caption">
                <span class="mini-metric"
                  ><TrendCharts />AI Coding 加快交付</span
                >
                <span class="mini-metric blue"><User />运维人力增长有限</span>
              </div>
            </section>
          </div>
          <footer class="intro-takeaway contradiction-band">
            <div class="callout-copy">
              <strong
                >项目交付越来越快，发现问题与处理事故仍主要依靠人工。</strong
              >
            </div>
            <small>01 / 10</small>
          </footer>
        </div>
      </section>

      <section id="gaps" class="intro-section" aria-labelledby="gaps-title">
        <div class="intro-sheet gap-sheet">
          <header class="intro-heading">
            <h2 id="gaps-title">
              {{ chapterLabel("gaps") }}：发现晚、排查慢，经验难复用
            </h2>
          </header>
          <div class="gap-columns">
            <article
              v-for="(gap, index) in gaps"
              :key="gap.name"
              class="gap-column"
              :class="'tone-' + gap.tone"
            >
              <div class="gap-top">
                <span class="gap-number">0{{ index + 1 }}</span>
                <h3>
                  {{ gap.title }}<span>{{ gap.name }}</span>
                </h3>
                <span class="gap-icon"><component :is="gap.icon" /></span>
              </div>
              <div class="gap-before">
                <span class="gap-before-icon"
                  ><component :is="gap.beforeIcon"
                /></span>
                {{ gap.before }}
              </div>
              <div class="gap-break"><i></i><span>GAP</span><i></i></div>
              <p>{{ gap.after }}</p>
              <div class="gap-tags">
                <span class="gap-tag-icon"
                  ><component :is="gap.tagIcon"
                /></span>
                <span v-for="(tag, i) in gap.tags" :key="tag"
                  ><i v-if="i">·</i>{{ tag }}</span
                >
              </div>
            </article>
          </div>
          <div class="manual-process">
            <span class="process-caption">传统事故处理</span>
            <ol>
              <li
                v-for="step in [
                  { label: '用户反馈', icon: ChatDotRound },
                  { label: '人工确认', icon: User },
                  { label: '收集信息', icon: Document },
                  { label: '尝试修复', icon: Tools },
                  { label: '验证恢复', icon: CircleCheck },
                ]"
                :key="step.label"
              >
                <span class="process-icon"><component :is="step.icon" /></span>
                <span>{{ step.label }}</span>
              </li>
            </ol>
            <p><Timer />大量时间消耗在信息收集、上下文拼接与反复确认。</p>
          </div>
          <footer class="intro-takeaway gap-callout">
            <svg class="callout-stack" viewBox="0 0 120 105" aria-hidden="true">
              <defs>
                <linearGradient id="gap-stack" x1="0" y1="0" x2="1" y2="1">
                  <stop stop-color="#b5d7ff" />
                  <stop offset="1" stop-color="#5aa4fd" />
                </linearGradient>
              </defs>
              <path d="m17 66 42-24 44 24-44 25z" fill="#b5d7fa" />
              <path d="m17 51 42-24 44 24-44 25z" fill="#74b1ff" />
              <path d="m17 35 42-24 44 24-44 25z" fill="url(#gap-stack)" />
              <path
                d="m17 35 42 25 44-25"
                fill="none"
                stroke="#d7eaff"
                stroke-width="2"
              />
              <path
                d="m17 51 42 25 44-25M17 66l42 25 44-25"
                fill="none"
                stroke="#eef7ff"
                stroke-width="2"
              />
            </svg>
            <div class="callout-copy">
              <strong
                >让 AI 获得足够的现场证据，并进入可验证的处理流程。</strong
              >
            </div>
            <small>02 / 10</small>
          </footer>
        </div>
      </section>

      <section
        id="solution"
        class="intro-section"
        aria-labelledby="solution-title"
      >
        <div class="intro-sheet solution-sheet">
          <header class="intro-heading">
            <h2 id="solution-title">
              {{ chapterLabel("solution") }}：算法预测 · Harness 诊断 · Skill
              复用
            </h2>
          </header>
          <div class="engine-columns">
            <article
              v-for="engine in engines"
              :key="engine.title"
              class="engine-column"
              :class="'tone-' + engine.tone"
            >
              <div class="engine-symbol">
                <span class="engine-icon"><component :is="engine.icon" /></span
                ><span class="engine-en">{{ engine.en }}</span>
              </div>
              <h3>{{ engine.title }}</h3>
              <p class="engine-verb">{{ engine.verb }}</p>
              <div v-if="engine.tone === 'blue'" class="engine-visual">
                <span class="engine-visual-tag">趋势变化与风险区间</span>
                <svg
                  viewBox="0 0 400 128"
                  xmlns="http://www.w3.org/2000/svg"
                  aria-label="历史趋势与未来预测区间示意图"
                >
                  <g stroke="#e3ebf7" stroke-width="1">
                    <path d="M20 46H382M20 76H382M20 104H382" />
                  </g>
                  <path
                    d="M241 61C267 55 289 42 312 42C341 42 350 27 380 21L380 92C351 85 340 103 311 94C287 86 268 102 241 90Z"
                    fill="#8db8ed"
                    opacity=".24"
                  />
                  <path
                    d="M20 93C48 89 56 93 83 78S135 92 162 69 200 83 241 61"
                    stroke="#4584d7"
                    stroke-width="3.2"
                    fill="none"
                    stroke-linecap="round"
                  />
                  <path
                    d="M241 61C272 67 297 58 317 54S356 57 380 43"
                    stroke="#4584d7"
                    stroke-dasharray="6 6"
                    stroke-width="3"
                    fill="none"
                    stroke-linecap="round"
                  />
                  <circle
                    cx="241"
                    cy="61"
                    r="5.8"
                    fill="#4584d7"
                    stroke="white"
                    stroke-width="2.5"
                  />
                  <path
                    d="M242 41V107"
                    stroke="#a4c7ef"
                    stroke-width="1.3"
                    stroke-dasharray="3 4"
                  />
                  <text x="21" y="120" font-size="11" fill="#7a91ae">
                    历史观测
                  </text>
                  <text x="309" y="120" font-size="11" fill="#6d89ae">
                    未来预测
                  </text>
                </svg>
              </div>
              <div v-else-if="engine.tone === 'teal'" class="engine-visual">
                <span class="engine-visual-tag"
                  >现场证据 → 工具分析 → 可核验结论</span
                >
                <svg
                  viewBox="0 0 400 128"
                  xmlns="http://www.w3.org/2000/svg"
                  aria-label="证据诊断工作过程示意图"
                >
                  <g font-family="Microsoft Yahei,Arial,sans-serif">
                    <rect
                      x="18"
                      y="40"
                      width="101"
                      height="59"
                      rx="8"
                      fill="#e8f7f3"
                      stroke="#bbded4"
                    />
                    <rect
                      x="33"
                      y="51"
                      width="11"
                      height="11"
                      rx="3"
                      fill="#2ea996"
                    />
                    <text
                      x="51"
                      y="60"
                      fill="#246655"
                      font-size="12"
                      font-weight="bold"
                    >
                      运行证据
                    </text>
                    <path
                      d="M34 76h65M34 84h42"
                      stroke="#abd5c8"
                      stroke-width="5"
                      stroke-linecap="round"
                    />
                    <path d="M124 69H145" stroke="#63b7a8" stroke-width="2" />
                    <path
                      d="m138 64 7 5-7 5"
                      stroke="#63b7a8"
                      stroke-width="2"
                      fill="none"
                    />
                    <rect
                      x="150"
                      y="40"
                      width="101"
                      height="59"
                      rx="8"
                      fill="#e8f7f3"
                      stroke="#bbded4"
                    />
                    <rect
                      x="165"
                      y="51"
                      width="11"
                      height="11"
                      rx="3"
                      fill="#2ea996"
                    />
                    <text
                      x="183"
                      y="60"
                      fill="#246655"
                      font-size="12"
                      font-weight="bold"
                    >
                      工具取证
                    </text>
                    <path
                      d="M165 76h65M165 84h45"
                      stroke="#abd5c8"
                      stroke-width="5"
                      stroke-linecap="round"
                    />
                    <path d="M256 69H277" stroke="#63b7a8" stroke-width="2" />
                    <path
                      d="m270 64 7 5-7 5"
                      stroke="#63b7a8"
                      stroke-width="2"
                      fill="none"
                    />
                    <rect
                      x="282"
                      y="40"
                      width="100"
                      height="59"
                      rx="8"
                      fill="#fff"
                      stroke="#8ccdbb"
                      stroke-width="1.3"
                    />
                    <circle cx="298" cy="56" r="6" fill="#2ea996" />
                    <path
                      d="m295 56 2 2 4-4"
                      stroke="white"
                      stroke-width="1.6"
                      fill="none"
                    />
                    <text
                      x="311"
                      y="60"
                      fill="#246655"
                      font-size="12"
                      font-weight="bold"
                    >
                      诊断结论
                    </text>
                    <path
                      d="M297 76h64M297 84h42"
                      stroke="#abd5c8"
                      stroke-width="5"
                      stroke-linecap="round"
                    />
                  </g>
                </svg>
              </div>
              <div v-else class="engine-visual">
                <span class="engine-visual-tag">知识检索与版本化 Skill</span>
                <svg
                  viewBox="0 0 400 128"
                  xmlns="http://www.w3.org/2000/svg"
                  aria-label="知识与技能检索版本示意图"
                >
                  <rect
                    x="22"
                    y="40"
                    width="134"
                    height="67"
                    rx="9"
                    fill="#f4effd"
                    stroke="#d9ccef"
                  />
                  <g stroke="#b7a1df" stroke-width="5" stroke-linecap="round">
                    <path d="M38 56h91M38 69h73M38 82h83" />
                  </g>
                  <rect
                    x="169"
                    y="54"
                    width="61"
                    height="29"
                    rx="14"
                    fill="#ebe2fa"
                  />
                  <text
                    x="199"
                    y="73"
                    font-size="12"
                    text-anchor="middle"
                    fill="#7555a6"
                    font-weight="bold"
                  >
                    检索
                  </text>
                  <path
                    d="M155 69H168M230 69H245"
                    stroke="#9678c2"
                    stroke-width="2"
                  />
                  <path
                    d="m239 64 6 5-6 5"
                    stroke="#9678c2"
                    stroke-width="2"
                    fill="none"
                  />
                  <rect
                    x="250"
                    y="38"
                    width="125"
                    height="71"
                    rx="9"
                    fill="#fff"
                    stroke="#c8b5e8"
                    stroke-width="1.3"
                  />
                  <rect
                    x="263"
                    y="50"
                    width="53"
                    height="16"
                    rx="5"
                    fill="#ebe2fa"
                  />
                  <text
                    x="289"
                    y="61.5"
                    text-anchor="middle"
                    font-size="11"
                    fill="#7555a6"
                    font-weight="bold"
                  >
                    Skill
                  </text>
                  <rect
                    x="322"
                    y="50"
                    width="43"
                    height="16"
                    rx="5"
                    fill="#f3effb"
                  />
                  <text
                    x="343.5"
                    y="61.5"
                    font-size="10"
                    text-anchor="middle"
                    fill="#8969b7"
                  >
                    v1.2
                  </text>
                  <g stroke="#cbbbe9" stroke-width="5" stroke-linecap="round">
                    <path d="M264 80h95M264 93h70" />
                  </g>
                </svg>
              </div>
              <ol>
                <li v-for="(step, i) in engine.steps" :key="step">
                  <span>{{ i + 1 }}</span
                  >{{ step }}
                </li>
              </ol>
              <p class="engine-detail">{{ engine.detail }}</p>
            </article>
          </div>
          <svg
            class="engine-convergence"
            viewBox="0 0 1476 29"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M232 0V12H738V28M738 0V28M1244 0V12H738"
              stroke="#b5cbe3"
              stroke-width="1.5"
            />
            <circle cx="232" cy="0" r="3" fill="#5890d5" />
            <circle cx="738" cy="0" r="3" fill="#37a995" />
            <circle cx="1244" cy="0" r="3" fill="#9781bf" />
            <path d="m732 21 6 7 6-7" stroke="#a1bbda" stroke-width="1.5" />
          </svg>
          <div class="collaboration-bar">
            <span class="collaboration-name"><UserFilled />人机协同</span>
            <div class="collaboration-steps">
              <span><User /><b>人工采用结论</b></span
              ><ArrowRight /><span><Lock /><b>审批受控动作</b></span
              ><ArrowRight /><span><CircleCheck /><b>核对恢复结果</b></span>
            </div>
          </div>
          <footer class="intro-takeaway">
            <strong
              >把监控、预测、诊断、处置与复盘放进同一条处理链路，形成统一的运维工作方式。</strong
            ><small>03 / 10</small>
          </footer>
        </div>
      </section>

      <section
        id="architecture"
        class="intro-section"
        aria-labelledby="architecture-title"
      >
        <div class="intro-sheet architecture-sheet">
          <header class="intro-heading">
            <h2 id="architecture-title">
              {{
                chapterLabel("architecture")
              }}：连接运行现场、开发资料与处理流程
            </h2>
          </header>
          <div class="architecture-visual">
            <div class="architecture-main">
              <div class="architecture-stack">
                <div
                  v-for="layer in layers"
                  :key="layer.name"
                  class="architecture-layer"
                  :class="'tone-' + layer.tone"
                >
                  <div class="layer-name">
                    <span class="layer-icon"
                      ><component :is="layer.icon"
                    /></span>
                    <span class="layer-text"
                      ><strong>{{ layer.name }}</strong
                      ><span>{{ layer.en }}</span></span
                    >
                  </div>
                  <div class="layer-items">
                    <span v-for="item in layer.items" :key="item.label"
                      ><component :is="item.icon" /><span
                        class="layer-item-text"
                        >{{ item.label }}</span
                      ></span
                    >
                  </div>
                </div>
              </div>
              <div class="evidence-path">
                <span class="evidence-name"><Link />关键关联</span>
                <div>
                  <template v-for="(step, i) in evidenceSteps" :key="step">
                    <b>{{ step }}</b
                    ><ArrowRight v-if="i < evidenceSteps.length - 1" />
                  </template>
                </div>
                <p>保留来源与缺口，历史证据随诊断保存。</p>
              </div>
              <footer class="intro-takeaway architecture-summary">
                <strong
                  >统一证据与关联关系，为预测、诊断、处置和复盘提供共同基础。</strong
                ><small>04 / 10</small>
              </footer>
            </div>
            <aside class="governance-rail">
              <div class="governance-heading">
                <span class="governance-icon"><Lock /></span>
                <strong>贯穿全程</strong>
              </div>
              <span v-for="item in governance" :key="item.label"
                ><component :is="item.icon" />{{ item.label }}</span
              >
            </aside>
          </div>
        </div>
      </section>

      <section
        id="capabilities"
        class="intro-section"
        aria-labelledby="capabilities-title"
      >
        <div class="intro-sheet capability-sheet">
          <header class="intro-heading">
            <h2 id="capabilities-title">
              {{
                chapterLabel("capabilities")
              }}：提前看风险，按证据定位，带材料交接
            </h2>
          </header>
          <div class="capability-columns">
            <article
              v-for="(item, index) in screenshots"
              :key="item.id"
              class="capability-column"
              :class="'tone-' + item.tone"
            >
              <div class="capability-head">
                <span class="capability-number">0{{ index + 1 }}</span>
                <span class="capability-icon"
                  ><component :is="item.icon"
                /></span>
                <div class="capability-heading">
                  <h3>{{ item.title }}</h3>
                  <p class="capability-subtitle">{{ item.subtitle }}</p>
                </div>
              </div>
              <button
                class="screenshot-button"
                :class="'screen-' + item.id"
                :aria-label="'放大查看' + item.title + '界面'"
                @click="openPreview(item)"
              >
                <span class="screen-chrome" aria-hidden="true"
                  ><i></i><i></i><i></i><span>SentinelOps</span></span
                >
                <span class="screen-body">
                  <img
                    :src="item.src"
                    :alt="item.title + '的实际平台验收界面，' + item.note"
                  />
                </span>
                <span class="screenshot-enlarge"><Search />点击放大</span>
              </button>
              <p class="screenshot-note">{{ item.note }}</p>
              <div class="capability-output">
                <CircleCheck /><span>{{ item.output }}</span>
              </div>
            </article>
          </div>
          <div class="capability-scope">
            <span class="scope-icon"><Document /></span>
            <p>
              界面与链路已实现，截图来自本机及隔离验收；真实业务中的预测效果与诊断质量，持续通过场景验证。
            </p>
          </div>
          <footer class="intro-takeaway">
            <strong
              >减少重复收集信息，让人工判断有依据，让后续修复有材料。</strong
            ><small>05 / 10</small>
          </footer>
        </div>
      </section>

      <section
        id="inspection"
        class="intro-section"
        aria-labelledby="inspection-title"
      >
        <div class="intro-sheet module-sheet">
          <header class="intro-heading">
            <h2 id="inspection-title">
              {{
                chapterLabel("inspection")
              }}：按计划自动取证，把人工核对变成可复核的记录
            </h2>
          </header>
          <div class="module-problem">
            <span class="module-problem-icon"><Warning /></span>
            <div class="module-problem-copy">
              <span class="module-tag">针对的问题</span>
              <p>
                业务数据分散在 API
                与数据库，靠人工逐条核对；检查口径靠人记，结论不留证据。
              </p>
            </div>
          </div>
          <div class="module-flow">
            <div class="module-section-title">
              <Connection /><span>技术与处理链路</span>
            </div>
            <ol>
              <li v-for="(step, index) in inspectionFlow" :key="step.label">
                <span class="module-flow-top">
                  <b>{{ String(index + 1).padStart(2, "0") }}</b>
                  <span class="module-flow-icon"
                    ><component :is="step.icon"
                  /></span>
                </span>
                <strong>{{ step.label }}</strong>
                <small>{{ step.text }}</small>
              </li>
            </ol>
          </div>
          <div class="module-grid">
            <article
              v-for="card in inspectionCards"
              :key="card.title"
              class="module-card"
              :class="'tone-' + card.tone"
            >
              <div class="module-card-head">
                <span class="module-card-icon"
                  ><component :is="card.icon"
                /></span>
                <span>{{ card.en }}</span>
              </div>
              <h3>{{ card.title }}</h3>
              <ul>
                <li v-for="point in card.points" :key="point">{{ point }}</li>
              </ul>
            </article>
          </div>
          <div class="module-note">
            <span class="module-note-icon"><Lock /></span>
            <span class="module-tag">实现边界</span>
            <p>
              已在本机部署。只读针对被检查的业务系统：不写入业务数据、不自动改判；任意
              PDF OCR 与完整计量规程自动编码未实现。
            </p>
          </div>
          <footer class="intro-takeaway">
            <strong
              >把逐条人工核对，变成按计划运行、逐项留痕、人工可复核的检查记录。</strong
            ><small>06 / 10</small>
          </footer>
        </div>
      </section>

      <section
        id="askdata"
        class="intro-section"
        aria-labelledby="askdata-title"
      >
        <div class="intro-sheet module-sheet">
          <header class="intro-heading">
            <h2 id="askdata-title">
              {{
                chapterLabel("askdata")
              }}：用中文直接问自有数据库，让口径有据可查
            </h2>
          </header>
          <div class="module-problem">
            <span class="module-problem-icon"><Search /></span>
            <div class="module-problem-copy">
              <span class="module-tag">针对的问题</span>
              <p>
                查一次数据要先找表、写
                SQL、核口径；口径散落在源码与少数人经验里，答案难以复核。
              </p>
            </div>
          </div>
          <div class="module-flow">
            <div class="module-section-title">
              <ChatDotRound /><span>技术与处理链路</span>
            </div>
            <ol>
              <li v-for="(step, index) in askdataFlow" :key="step.label">
                <span class="module-flow-top">
                  <b>{{ String(index + 1).padStart(2, "0") }}</b>
                  <span class="module-flow-icon"
                    ><component :is="step.icon"
                  /></span>
                </span>
                <strong>{{ step.label }}</strong>
                <small>{{ step.text }}</small>
              </li>
            </ol>
          </div>
          <div class="module-grid">
            <article
              v-for="card in askdataCards"
              :key="card.title"
              class="module-card"
              :class="'tone-' + card.tone"
            >
              <div class="module-card-head">
                <span class="module-card-icon"
                  ><component :is="card.icon"
                /></span>
                <span>{{ card.en }}</span>
              </div>
              <h3>{{ card.title }}</h3>
              <ul>
                <li v-for="point in card.points" :key="point">{{ point }}</li>
              </ul>
            </article>
          </div>
          <div class="module-note">
            <span class="module-note-icon"><Lock /></span>
            <span class="module-tag">实测与边界</span>
            <p>
              本机自有库五表 49 字段、六题只读实测
              6/6；百表规模与三模式逐题模型对比仍待验证。
            </p>
          </div>
          <footer class="intro-takeaway">
            <strong>不用先懂表结构，也能按授权口径拿到可追溯的数据答案。</strong
            ><small>07 / 10</small>
          </footer>
        </div>
      </section>

      <section id="loop" class="intro-section" aria-labelledby="loop-title">
        <div class="intro-sheet loop-sheet">
          <header class="intro-heading loop-heading">
            <h2 id="loop-title">
              {{ chapterLabel("loop") }}：诊断、决策与恢复验证连续衔接
            </h2>
            <div class="loop-legend">
              <span><i></i>平台 / AI</span><span><i></i>人工决策</span>
            </div>
          </header>
          <div class="loop-visual">
            <div class="loop-row loop-forward">
              <template v-for="(step, i) in forwardSteps" :key="step.title">
                <article :class="{ 'human-step': i === 3 }">
                  <span class="loop-step-number">0{{ i + 1 }}</span>
                  <span class="loop-step-icon"
                    ><component :is="step.icon"
                  /></span>
                  <h3>{{ step.title }}</h3>
                  <p>{{ step.sub }}</p>
                </article>
                <span
                  v-if="i < forwardSteps.length - 1"
                  class="loop-arrow"
                  aria-hidden="true"
                  ><ArrowRight
                /></span>
              </template>
            </div>
            <div class="loop-bridge" aria-hidden="true">
              <ArrowRight />
            </div>
            <div class="loop-row loop-backward">
              <div class="loop-human-note">
                <span class="loop-note-icon"><Lock /></span>
                <strong>审批绑定</strong>
                <p>目标 · 动作 · 参数</p>
              </div>
              <template v-for="(step, i) in backwardSteps" :key="step.title">
                <span class="loop-arrow" aria-hidden="true"><ArrowLeft /></span>
                <article>
                  <span class="loop-step-number">0{{ 5 + i }}</span>
                  <span class="loop-step-icon"
                    ><component :is="step.icon"
                  /></span>
                  <h3>{{ step.title }}</h3>
                  <p>{{ step.sub }}</p>
                </article>
              </template>
            </div>
            <div class="loop-feedback">
              <i></i><span>保留时间线、证据引用与审计记录</span><i></i>
            </div>
          </div>
          <div class="repair-branch">
            <span class="repair-icon"><Files /></span>
            <strong>涉及代码修复</strong>
            <div class="repair-steps">
              <span>交接开发流程</span><ArrowRight /><span
                >修改 / 测试 / 发布</span
              ><ArrowRight /><span>回传版本与人工验证</span>
            </div>
          </div>
          <footer class="intro-takeaway">
            <strong
              >AI 提供诊断与建议，人工掌握决策，恢复结果由独立观测核验。</strong
            ><small>08 / 10</small>
          </footer>
        </div>
      </section>

      <section
        id="evolution"
        class="intro-section"
        aria-labelledby="evolution-title"
      >
        <div class="intro-sheet evolution-sheet">
          <header class="intro-heading">
            <h2 id="evolution-title">
              {{ chapterLabel("evolution") }}：把一次事故，变成下一次可用的经验
            </h2>
          </header>
          <div class="evolution-visual">
            <div class="evolution-input">
              <span class="evolution-card-title"><i></i>已有基础</span>
              <span class="evolution-card-icon"><Document /></span>
              <h3>事故证据与复盘</h3>
              <p>诊断引用 · 处理记录<br />部署关联 · 恢复结果</p>
              <span class="solid-status"><i></i>证据可追溯</span>
            </div>
            <div class="evolution-center">
              <div class="evolution-pipeline">
                <div class="pipeline-head">
                  <span class="pipeline-icon"><Files /></span>
                  <strong>经验沉淀与能力构建</strong>
                  <p>从事故到能力，让每一次处理都成为可复用的知识</p>
                </div>
                <div class="evolution-stages">
                  <article>
                    <span class="stage-number">01</span>
                    <span class="stage-icon"><Files /></span>
                    <h3>提炼候选能力</h3>
                    <p>知识条目 / Skill 草案</p>
                  </article>
                  <span class="stage-arrow" aria-hidden="true"
                    ><ArrowRight
                  /></span>
                  <article>
                    <span class="stage-number">02</span>
                    <span class="stage-icon"><Search /></span>
                    <h3>故障回放评测</h3>
                    <p>覆盖场景 / 核对结果</p>
                  </article>
                  <span class="stage-arrow" aria-hidden="true"
                    ><ArrowRight
                  /></span>
                  <article class="human-review">
                    <span class="stage-number">03</span>
                    <span class="stage-icon"><User /></span>
                    <h3>人工审核发布</h3>
                    <p>版本记录 / 适用范围</p>
                  </article>
                </div>
              </div>
              <div class="evolution-feedback">
                <span class="feedback-line" aria-hidden="true"></span>
                <span class="feedback-pill">应用反馈与新证据回流</span>
                <span class="feedback-line" aria-hidden="true"></span>
              </div>
            </div>
            <div class="evolution-output">
              <span class="evolution-card-title"><i></i>新事故应用</span>
              <span class="evolution-card-icon"><Collection /></span>
              <h3>按需检索知识</h3>
              <p>加载已发布 Skill</p>
              <span class="solid-status"><i></i>已加载能力</span>
            </div>
          </div>
          <div class="improvement-tracks">
            <div>
              <span class="track-icon"><TrendCharts /></span>
              <span class="track-text"
                ><strong>算法优化</strong
                ><span>历史回测 · 误差与区间覆盖</span></span
              >
            </div>
            <div>
              <span class="track-icon"><Cpu /></span>
              <span class="track-text"
                ><strong>诊断优化</strong
                ><span>证据完整性 · 结论有效性</span></span
              >
            </div>
            <div>
              <span class="track-icon"><Files /></span>
              <span class="track-text"
                ><strong>Skill 优化</strong
                ><span>场景覆盖 · 版本评测</span></span
              >
            </div>
          </div>
          <footer class="intro-takeaway">
            <strong
              >让处理经验经评测与审核后复用，逐步降低对个人经验的依赖。</strong
            ><small>09 / 10</small>
          </footer>
        </div>
      </section>

      <section
        id="roadmap"
        class="intro-section"
        aria-labelledby="roadmap-title"
      >
        <div class="intro-sheet roadmap-sheet">
          <header class="intro-heading">
            <h2 id="roadmap-title">
              {{ chapterLabel("roadmap") }}：闭环可用 → 真实有效 → 持续进化
            </h2>
          </header>
          <div class="roadmap-visual">
            <article class="roadmap-stage stage-one">
              <div class="roadmap-stage-top">
                <span class="roadmap-number">01</span
                ><span class="roadmap-status">已有底座</span>
                <span class="roadmap-watermark"><Histogram /></span>
              </div>
              <h3>闭环可用</h3>
              <p class="roadmap-focus">打通端到端处理链路</p>
              <ul>
                <li>资源监控与风险预测</li>
                <li>证据诊断与修复交接</li>
                <li>审批、验证与复盘</li>
              </ul>
              <div class="roadmap-deliverable">
                <CircleCheck /><span>本机与隔离验收已跑通</span>
              </div>
            </article>
            <span class="roadmap-arrow" aria-hidden="true"><ArrowRight /></span>
            <article class="roadmap-stage stage-two">
              <div class="roadmap-stage-top">
                <span class="roadmap-number">02</span
                ><span class="roadmap-status">下一阶段</span>
                <span class="roadmap-watermark"><Histogram /></span>
              </div>
              <h3>真实有效</h3>
              <p class="roadmap-focus">用真实场景验证效果</p>
              <ul>
                <li>积累真实业务监控历史</li>
                <li>对照事故诊断与处理结果</li>
                <li>优化预测与诊断质量</li>
              </ul>
              <div class="roadmap-deliverable">
                <Timer /><span>关注发现 / 定位 / 恢复时间</span>
              </div>
            </article>
            <span class="roadmap-arrow" aria-hidden="true"><ArrowRight /></span>
            <article class="roadmap-stage stage-three">
              <div class="roadmap-stage-top">
                <span class="roadmap-number">03</span
                ><span class="roadmap-status">中长期规划</span>
                <span class="roadmap-watermark"><TrendCharts /></span>
              </div>
              <h3>持续进化</h3>
              <p class="roadmap-focus">让能力随处理经验积累</p>
              <ul>
                <li>提炼知识与 Skill 候选</li>
                <li>建立故障回放与版本评测</li>
                <li>经人工审核后持续复用</li>
                <li>连通需求、开发、测试、发布、运维与复盘</li>
              </ul>
              <div class="roadmap-deliverable">
                <Collection /><span>可评测、可追溯的能力资产</span>
              </div>
            </article>
          </div>
          <div class="roadmap-foundation">
            <div class="foundation-heading">
              <span class="foundation-icon"><Lock /></span>
              <strong>共同底座</strong>
            </div>
            <span class="foundation-item"><Document />统一证据</span>
            <span class="foundation-item"><CircleCheck />权限约束</span>
            <span class="foundation-item"><UserFilled />人工决策</span>
            <span class="foundation-item"><Files />审计记录</span>
            <span class="foundation-item foundation-planned"
              ><Refresh />生命周期连通<b>规划</b></span
            >
          </div>
          <footer class="intro-takeaway">
            <strong
              >更早发现问题、更有依据地处理事故，并把经验留在平台里。</strong
            ><small>10 / 10</small>
          </footer>
        </div>
      </section>
    </main>
    <footer class="intro-page-footer">
      <strong>SentinelOps</strong><span>人机协同智能运维平台</span
      ><a href="#context" @click.prevent="jump('context')">回到开篇 ↑</a>
    </footer>
    <div v-if="presenting" class="slide-chrome">
      <div class="slide-progress" aria-hidden="true">
        <i
          :style="{
            width: ((slideIndex + 1) / chapters.length) * 100 + '%',
          }"
        ></i>
      </div>
      <div class="slide-bar" role="group" aria-label="演示翻页">
        <span class="slide-chapter">{{ currentChapter.title }}</span>
        <div class="slide-nav">
          <button
            type="button"
            aria-label="上一页"
            :disabled="slideIndex === 0"
            @click="goToSlide(slideIndex - 1)"
          >
            <ArrowLeft />
          </button>
          <span class="slide-counter">
            <b>{{ String(slideIndex + 1).padStart(2, "0") }}</b>
            / {{ String(chapters.length).padStart(2, "0") }}
          </span>
          <button
            type="button"
            aria-label="下一页"
            :disabled="slideIndex === chapters.length - 1"
            @click="goToSlide(slideIndex + 1)"
          >
            <ArrowRight />
          </button>
        </div>
        <div class="slide-dots">
          <button
            v-for="(chapter, index) in chapters"
            :key="chapter.id"
            type="button"
            :class="{ active: index === slideIndex }"
            :aria-label="'第 ' + (index + 1) + ' 页：' + chapter.title"
            :aria-current="index === slideIndex ? 'true' : undefined"
            @click="goToSlide(index)"
          ></button>
        </div>
        <span class="slide-hint">← → 翻页 · Esc 退出</span>
        <button
          type="button"
          class="slide-exit"
          aria-label="退出演示"
          @click="exitPresentation"
        >
          <Close /><span>退出</span>
        </button>
      </div>
    </div>
    <dialog
      ref="previewDialog"
      class="intro-preview"
      aria-labelledby="preview-title"
      @click="$event.target === previewDialog && previewDialog?.close()"
    >
      <header>
        <div>
          <h2 id="preview-title">{{ preview?.title }}</h2>
          <p>{{ preview?.note }}</p>
        </div>
        <button aria-label="关闭界面预览" @click="previewDialog?.close()">
          <Close />
        </button>
      </header>
      <img
        v-if="preview"
        :src="preview.src"
        :alt="preview.title + '完整平台界面'"
      />
    </dialog>
  </div>
</template>

<style scoped>
.project-intro {
  --intro-ink: #153252;
  --intro-muted: #63758b;
  --intro-line: #dce5f1;
  --intro-blue: #3978ce;
  --intro-orange: #c87a31;
  color: var(--intro-ink);
  background: #eff3f9;
  color-scheme: light;
  font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.project-intro svg {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
}
.project-intro a {
  color: inherit;
}
.project-intro p {
  margin-bottom: 0;
}
.project-intro button {
  font-family: inherit;
}
.intro-nav {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 30px;
  min-height: 72px;
  padding: 12px 32px;
  border-bottom: 1px solid #dce5f1;
  background: #ffffffef;
  backdrop-filter: blur(16px);
}
.intro-brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  white-space: nowrap;
}
.intro-brand strong {
  font-size: 18px;
  letter-spacing: -0.6px;
}
.intro-brand-mark {
  width: 30px;
  height: 34px;
  border: 1.5px solid #4879b7;
  border-radius: 8px 8px 13px 13px;
  display: grid;
  place-items: center;
  color: #4879b7;
}
.intro-brand-mark svg {
  width: 18px;
}
.intro-brand-label {
  font-size: 12px;
  color: var(--intro-muted);
  border-left: 1px solid var(--intro-line);
  padding-left: 12px;
  margin-left: 2px;
}
.intro-chapters {
  display: flex;
  align-items: center;
  gap: 3px;
  margin: auto;
}
.intro-chapters a {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 13px;
  padding: 9px 10px;
  white-space: nowrap;
  border-radius: 5px;
}
.intro-chapters a > span {
  font-size: 10px;
  color: #8192a7;
  font-variant-numeric: tabular-nums;
}
.intro-chapters a:hover,
.intro-chapters a.active {
  color: #326ec0;
  background: #edf3fc;
}
.intro-chapters a.active > span {
  color: #326ec0;
}
.intro-return {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  white-space: nowrap;
}
.intro-return svg {
  width: 16px;
}
.intro-pages {
  padding: 24px;
}
.intro-section {
  max-width: 1360px;
  margin: 0 auto 24px;
  scroll-margin-top: 94px;
}
.intro-section:last-child {
  margin-bottom: 0;
}
.intro-sheet {
  min-height: min(780px, calc(100svh - 120px));
  padding: 42px 54px 26px;
  background: #fff;
  border-radius: 4px;
  box-shadow: 0 12px 30px #29476c14;
  display: flex;
  flex-direction: column;
  gap: 22px;
  position: relative;
}
.intro-heading h1,
.intro-heading h2 {
  font-size: clamp(20px, 1.9vw, 26px);
  line-height: 1.4;
  letter-spacing: -0.6px;
  font-weight: 700;
  margin: 0;
  padding-left: 18px;
  position: relative;
  color: var(--intro-ink);
}
.intro-heading h1::before,
.intro-heading h2::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0.2em;
  bottom: 0.2em;
  width: 5px;
  border-radius: 2px;
  background: #4b82ca;
}
.intro-takeaway {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  margin-top: 6px;
  padding: 22px 96px 20px;
  border: 1px solid #dbe5f2;
  border-radius: 12px;
  background: #f7fafd;
  text-align: center;
}
.intro-takeaway > strong {
  font-weight: 700;
  font-size: clamp(21px, 1.75vw, 27px);
  line-height: 1.5;
  color: #22364f;
  max-width: 1000px;
}
.intro-takeaway > small {
  position: absolute;
  right: 22px;
  bottom: 16px;
  font-size: 12px;
  color: #a3b2c4;
  white-space: nowrap;
  letter-spacing: 1px;
}
.contradiction-band {
  align-items: flex-start;
  justify-content: center;
  min-height: 108px;
  padding: 20px 74px 24px 30px;
  border: 1px solid #bed7ff;
  border-left: 7px solid #3485ff;
  border-radius: 16px;
  background: linear-gradient(105deg, #eef6ff, #e4f0ff);
  text-align: left;
}
.contradiction-band > small {
  color: #8097c1;
}
.context-panels {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
  margin: 0;
}
.context-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 24px 24px 20px;
  border: 1px solid #e1eaf5;
  border-radius: 18px;
  box-shadow: 0 13px 29px #163c6f13;
}
.context-panel::before {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  height: 4px;
  background: var(--panel-accent);
}
.panel-infrastructure {
  --panel-accent: linear-gradient(90deg, #2c7af0, #6fb0ff);
  background: linear-gradient(152deg, #fff 50%, #f6faff 100%);
}
.panel-delivery {
  --panel-accent: linear-gradient(90deg, #f3a15e, #e57923);
  background: linear-gradient(151deg, #fff 48%, #fffaf4 100%);
}
.panel-eyebrow {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 1.5px;
  color: #3976c7;
}
.panel-eyebrow b {
  font-size: 15px;
  letter-spacing: 1px;
  color: #94b3dc;
}
.panel-delivery .panel-eyebrow {
  color: #bb7139;
}
.panel-delivery .panel-eyebrow b {
  color: #eab38b;
}
.panel-caption h2 {
  margin: 10px 0 0;
  font-size: 26px;
  font-weight: 800;
  line-height: 1.3;
  color: #122f58;
}
.panel-caption p {
  margin: 7px 0 0;
  font-size: 14px;
  color: #7288a5;
}
.panel-map {
  display: grid;
  grid-template-columns: auto auto minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  padding: 15px 16px;
  border: 1px solid #e5efff;
  border-radius: 14px;
  background: #f4f8ff;
}
.server-stack {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rack {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 46px;
  padding: 0 13px;
  border: 1px solid #b9d0ee;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 4px 10px #2a69b50f;
}
.rack-icon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 29px;
  flex-shrink: 0;
  border-radius: 7px;
  background: #e8f2ff;
  color: #2575d6;
}
.rack-icon svg {
  width: 19px;
  height: 19px;
}
.rack-lines {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
}
.rack-lines i {
  height: 6px;
  border-radius: 5px;
  background: #c8d9f0;
}
.rack-lines i:last-child {
  width: 66%;
}
.rack-led {
  width: 6px;
  height: 6px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #30b69a;
  box-shadow: 0 0 0 3px #e1f8f2;
}
.rack-label {
  text-align: center;
  font-size: 14px;
  font-weight: 600;
  color: #647f9f;
}
.panel-map .panel-topology {
  width: 68px;
  height: 132px;
}
.service-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.service-tile {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 62px;
  padding: 0 10px;
  border: 1px solid #d6e5f8;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 4px 10px #28549a0e;
  color: #163965;
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
}
.tile-icon {
  display: grid;
  place-items: center;
  width: 31px;
  height: 31px;
  flex-shrink: 0;
  border-radius: 8px;
  background: #eaf2ff;
  color: #3b82df;
}
.tile-icon svg {
  width: 18px;
  height: 18px;
}
.service-tile.ai {
  border-color: #c1e8df;
  background: #f6fffc;
  color: #167566;
}
.service-tile.ai .tile-icon {
  background: #ddf7ef;
  color: #16a088;
}
.panel-detail {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 41px;
  margin-top: 16px;
  padding: 8px 14px;
  border-radius: 10px;
  background: #edf4ff;
  color: #41668f;
  font-size: 13px;
  font-weight: 600;
}
.panel-detail span {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.panel-detail i {
  width: 5px;
  height: 5px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #5a94dc;
}
.trend-type {
  position: absolute;
  right: 22px;
  top: 22px;
  padding: 5px 10px;
  border: 1px solid #f5e5d4;
  border-radius: 11px;
  background: #fff5eb;
  color: #ac9078;
  font-size: 11px;
}
.trend-chart {
  position: relative;
  height: 178px;
  margin-top: 16px;
  overflow: hidden;
  border: 1px solid #f5eadc;
  border-radius: 13px;
  background: linear-gradient(180deg, #fffbf44d, #fff);
}
.trend-chart svg {
  display: block;
  width: 100%;
  height: 100%;
}
.trend-axis {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 7px;
  display: flex;
  justify-content: space-between;
  color: #8799ad;
  font-size: 12px;
}
.trend-legend {
  position: absolute;
  left: 14px;
  top: 11px;
  display: flex;
  gap: 16px;
  color: #7185a3;
  font-size: 12px;
  font-weight: 600;
}
.trend-legend span {
  display: flex;
  align-items: center;
  gap: 7px;
}
.trend-legend i {
  width: 18px;
  height: 3px;
  border-radius: 3px;
  background: #e88736;
}
.trend-legend i.blue {
  background: #4184d3;
}
.trend-gap {
  position: absolute;
  right: 14px;
  top: 52%;
  padding: 5px 11px;
  border: 1px solid #f2cfad;
  border-radius: 9px;
  background: #fff8ee;
  box-shadow: 0 3px 9px #b86a2812;
  color: #c56a29;
  font-size: 14px;
  font-weight: 800;
}
.trend-caption {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}
.mini-metric {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 38px;
  padding: 0 12px;
  border: 1px solid #f6e4d6;
  border-radius: 10px;
  background: #fff3e9;
  color: #a6622c;
  font-size: 13px;
  font-weight: 700;
  white-space: nowrap;
}
.mini-metric.blue {
  border-color: #dce9fa;
  background: #eef5ff;
  color: #3271bc;
}
.mini-metric svg {
  width: 17px;
  height: 17px;
  flex-shrink: 0;
}
.gap-sheet {
  background: #f7f9fc;
}
.gap-columns {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
  margin: 2px 0 0;
}
.gap-column {
  --gap-accent: #ff934c;
  --gap-tint: #fff1e8;
  --gap-ink: #f56c28;
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #e5edfa;
  border-radius: 16px;
  box-shadow: 0 11px 25px #24518b0f;
  padding: 20px 24px 18px 26px;
}
.gap-column.tone-blue {
  --gap-accent: #43affd;
  --gap-tint: #e7f5ff;
  --gap-ink: #1761bd;
}
.gap-column.tone-violet {
  --gap-accent: #9459f5;
  --gap-tint: #f1e9ff;
  --gap-ink: #7045d6;
}
.gap-column::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  width: 7px;
  height: 94px;
  border-radius: 16px 0 6px 0;
  background: var(--gap-accent);
}
.gap-top {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  column-gap: 16px;
}
.gap-number {
  grid-column: 1;
  grid-row: 1;
  font-size: 47px;
  font-weight: 800;
  font-style: italic;
  line-height: 1;
  letter-spacing: 1px;
  color: var(--gap-accent);
  opacity: 0.54;
  font-variant-numeric: tabular-nums;
}
.gap-icon {
  grid-column: 2;
  grid-row: 1 / span 2;
  display: grid;
  place-items: center;
  width: 66px;
  height: 66px;
  border-radius: 50%;
  background: var(--gap-tint);
  color: var(--gap-ink);
}
.gap-icon svg {
  width: 34px;
  height: 34px;
}
.gap-column h3 {
  grid-column: 1;
  grid-row: 2;
  display: flex;
  align-items: baseline;
  gap: 19px;
  font-size: 27px;
  font-weight: 800;
  letter-spacing: -0.2px;
  color: #102957;
  margin: 4px 0 0;
}
.gap-column h3 > span {
  font-size: 15px;
  font-weight: 400;
  color: #5d7291;
}
.gap-before {
  display: flex;
  align-items: center;
  gap: 15px;
  min-height: 51px;
  margin-top: 16px;
  padding: 6px 16px;
  font-size: 17px;
  border-radius: 10px;
  background: linear-gradient(100deg, #eef5ff, #f0f4fc);
  color: #27446b;
}
.gap-before-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #fff;
  color: #197aff;
  box-shadow: 0 3px 8px #5089c414;
}
.gap-before-icon svg {
  width: 20px;
  height: 20px;
}
.gap-break {
  display: flex;
  gap: 14px;
  align-items: center;
  justify-content: center;
  margin: 15px 0;
  color: #f07732;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 2.2px;
}
.gap-break i {
  height: 1px;
  flex: 1;
  background: #fbd4bd;
}
.gap-column p {
  color: #627798;
  font-size: 17px;
  line-height: 1.5;
  margin-bottom: 14px;
}
.gap-tags {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 46px;
  margin-top: auto;
  padding: 6px 18px;
  border-radius: 10px;
  background: linear-gradient(95deg, #fff0e6, #fff2e8);
  color: #ed651c;
  font-size: 18px;
  font-weight: 700;
}
.gap-tags > span {
  display: flex;
  align-items: center;
  gap: 6px;
}
.gap-tags i {
  font-style: normal;
  color: #f0a97e;
}
.gap-tag-icon {
  display: flex;
}
.gap-tag-icon svg {
  width: 23px;
  height: 23px;
}
.manual-process {
  position: relative;
  background: linear-gradient(106deg, #fcfdff 5%, #f1f7ff 100%);
  border: 1px solid #d6e7ff;
  border-radius: 16px;
  box-shadow: 0 9px 18px #2764a90f;
  padding: 18px 24px 16px;
}
.process-caption {
  display: flex;
  align-items: center;
  gap: 17px;
  font-size: 23px;
  font-weight: 800;
  color: #102957;
  margin-bottom: 16px;
}
.process-caption::before {
  content: "";
  width: 6px;
  height: 31px;
  border-radius: 4px;
  background: linear-gradient(#5ca7ff, #2a7aff);
}
.manual-process ol {
  display: flex;
  list-style: none;
  margin: 0;
  padding: 0;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.manual-process li {
  display: flex;
  align-items: center;
  gap: 16px;
  flex: 1 1 auto;
  color: #213b61;
  font-size: 18px;
  white-space: nowrap;
}
.manual-process li:last-child {
  flex: 0 1 auto;
}
.process-icon {
  display: grid;
  place-items: center;
  width: 52px;
  height: 52px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #fff;
  color: #0860cb;
  box-shadow: 0 3px 11px #4c76b821;
}
.process-icon svg {
  width: 26px;
  height: 26px;
}
.manual-process li:not(:last-child)::after {
  content: "→";
  flex: 1;
  text-align: center;
  color: #7eacff;
  font-size: 20px;
}
.manual-process > p {
  display: flex;
  gap: 14px;
  align-items: center;
  min-height: 34px;
  margin-top: 14px;
  padding: 5px 22px;
  border-radius: 19px;
  background: #e6f0ff;
  color: #7588aa;
  font-size: 16px;
}
.manual-process > p svg {
  width: 22px;
  height: 22px;
  color: #5f82be;
}
.gap-callout {
  flex-direction: row;
  align-items: center;
  justify-content: flex-start;
  gap: 24px;
  min-height: 88px;
  padding: 14px 34px 14px 20px;
  overflow: hidden;
  border: 1px solid #bed7ff;
  border-radius: 16px;
  background: linear-gradient(105deg, #eef6ff 0%, #e4f0ff 70%, #dceaff 100%);
  text-align: left;
}
.gap-callout::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 9px;
  background: #3485ff;
}
.gap-callout::after {
  content: "";
  position: absolute;
  right: -42px;
  top: -36px;
  width: 436px;
  height: 184px;
  border-radius: 40%;
  background: linear-gradient(125deg, #0000 10%, #7ab5fc26 47%, #2c82f921 75%);
  transform: skewX(-29deg);
  pointer-events: none;
}
.gap-callout .callout-stack {
  position: relative;
  width: 97px;
  height: 84px;
  flex-shrink: 0;
  color: #65a7ff;
  filter: drop-shadow(0 6px 5px #448ce724);
}
.callout-copy {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  min-width: 0;
}
.callout-copy strong {
  font-size: clamp(21px, 1.75vw, 26px);
  font-weight: 700;
  line-height: 1.4;
  color: #0c3171;
}
.gap-callout > small {
  color: #8097c1;
}
.tone-orange {
  --tone: #e08b4c;
  --tone-soft: #fdf1e7;
  --tone-border: #f0dcc6;
}
.tone-blue {
  --tone: #3978ce;
  --tone-soft: #f0f5fd;
  --tone-border: #cbdcf4;
}
.tone-teal {
  --tone: #288e80;
  --tone-soft: #eff8f5;
  --tone-border: #c2e2d9;
}
.tone-violet {
  --tone: #7b6ba5;
  --tone-soft: #f5f2fa;
  --tone-border: #ddd5ed;
}
.tone-slate {
  --tone: #6a7e97;
  --tone-soft: #f4f6f9;
  --tone-border: #dce3eb;
}
.engine-columns {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 24px;
  margin-top: 6px;
}
.engine-column {
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  padding: 26px 28px 20px;
  border: 1px solid var(--tone-border);
  border-radius: 14px;
  background: var(--tone-soft);
}
.engine-column::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 4px;
  background: var(--tone);
}
.engine-visual {
  position: relative;
  flex-shrink: 0;
  margin-top: 16px;
  border: 1px solid var(--tone-border);
  border-radius: 12px;
  overflow: hidden;
  background: #ffffffa6;
}
.engine-visual-tag {
  position: absolute;
  top: 10px;
  left: 12px;
  color: var(--intro-muted);
  font-size: 11px;
  font-weight: 600;
}
.engine-visual > svg {
  display: block;
  width: 100%;
  height: auto;
  margin-top: 7px;
}
.engine-symbol {
  display: flex;
  gap: 14px;
  align-items: center;
  color: var(--tone);
  margin-bottom: 20px;
}
.engine-icon {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  flex-shrink: 0;
  border-radius: 11px;
  background: var(--tone);
  color: #fff;
}
.engine-icon svg {
  width: 22px;
  height: 22px;
}
.engine-en {
  font-size: 13px;
  letter-spacing: 2px;
  font-weight: 600;
}
.engine-column h3 {
  font-size: 26px;
  margin-bottom: 10px;
  color: var(--intro-ink);
  letter-spacing: -0.6px;
}
.engine-verb {
  color: var(--tone);
  font-size: 17px;
  font-weight: 600;
}
.engine-column ol {
  margin: 18px 0 20px;
  list-style: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  position: relative;
}
.engine-column li {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 15px;
  color: #263d59;
}
.engine-column li > span {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  font-size: 13px;
  font-weight: 600;
  color: var(--tone);
  background: #fff;
  border: 1px solid var(--tone-border);
  border-radius: 50%;
}
.engine-detail {
  margin-top: auto;
  position: relative;
  padding-top: 15px;
  border-top: 1px solid var(--tone-border);
  font-size: 13px;
  color: #5f7590;
  line-height: 1.8;
}
.project-intro .engine-convergence {
  display: block;
  width: 100%;
  height: 24px;
  flex-shrink: 0;
  margin: -22px 0;
  overflow: visible;
}
.collaboration-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 30px;
  padding: 22px 26px;
  border: 1px solid #f0dcc4;
  border-radius: 14px;
  background: #fdf4e9;
}
.collaboration-name {
  display: flex;
  gap: 12px;
  align-items: center;
  color: #b96a1f;
  font-size: 26px;
  font-weight: 700;
  white-space: nowrap;
}
.collaboration-name svg {
  width: 30px;
  height: 30px;
}
.collaboration-steps {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
}
.collaboration-steps > span {
  display: flex;
  align-items: center;
  gap: 10px;
  justify-content: center;
  flex: 1;
  padding: 14px 12px;
  border: 1px solid #f0e1d0;
  border-radius: 999px;
  background: #fff;
  color: #8a6134;
  font-size: 17px;
}
.collaboration-steps > span svg {
  width: 21px;
  height: 21px;
  flex-shrink: 0;
  color: #b9762f;
}
.collaboration-steps b {
  font-weight: 600;
  white-space: nowrap;
}
.collaboration-steps > svg {
  width: 17px;
  height: 17px;
  color: #d3ab7e;
}
.architecture-visual {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 176px;
  gap: 18px;
  margin-top: 4px;
}
.architecture-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 18px;
}
.architecture-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  border: 1px solid #cdd9e9;
  border-radius: 22px;
  background: linear-gradient(180deg, #f1f5fa, #f8fafd);
  box-shadow: 0 8px 22px #224c8110;
}
.architecture-layer {
  display: grid;
  grid-template-columns: 178px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  background: linear-gradient(110deg, var(--layer-fill), var(--layer-light));
  border: 1px solid var(--layer-border);
  box-shadow:
    0 5px 12px #153b6b14,
    inset 0 1px 0 #ffffffb3;
  border-radius: 18px;
  min-height: 86px;
  position: relative;
}
.architecture-layer.tone-blue {
  --layer-fill: #dbe9fc;
  --layer-light: #edf4ff;
  --layer-border: #adc7eb;
}
.architecture-layer.tone-teal {
  --layer-fill: #d8eee6;
  --layer-light: #eaf7f2;
  --layer-border: #9fcfc0;
}
.architecture-layer.tone-violet {
  --layer-fill: #e6def4;
  --layer-light: #f2edfa;
  --layer-border: #c5b3e1;
}
.architecture-layer.tone-slate {
  --layer-fill: #e0e7f0;
  --layer-light: #eff3f8;
  --layer-border: #b7c5d6;
}
.architecture-layer:not(:first-child)::before {
  content: "↑";
  position: absolute;
  top: -20px;
  left: 56%;
  font-size: 18px;
  font-weight: 700;
  color: #5c83b8;
}
.layer-name {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--tone);
}
.layer-icon {
  display: grid;
  place-items: center;
  width: 54px;
  height: 54px;
  flex-shrink: 0;
  border-radius: 15px;
  background: linear-gradient(
    180deg,
    var(--tone),
    color-mix(in srgb, var(--tone), #11386a 18%)
  );
  box-shadow: 0 6px 16px #163b6b14;
  color: #fff;
}
.layer-icon svg {
  width: 26px;
  height: 26px;
}
.layer-text {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.layer-name strong {
  font-size: 19px;
  font-weight: 800;
  color: #173b6b;
  white-space: nowrap;
}
.layer-text > span {
  font-size: 11px;
  letter-spacing: 1px;
  color: #7b92b1;
}
.layer-items {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.layer-items > span {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 58px;
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--layer-border);
  border-radius: 14px;
  background: #fff;
  box-shadow:
    0 2px 3px #173b6b0a,
    0 6px 12px #173b6b14;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
  color: #274870;
}
.layer-item-text {
  min-width: 0;
}
.layer-items > span svg {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  color: var(--tone);
}
.governance-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0;
  background: linear-gradient(180deg, #f9edda, #fff5e6);
  border: 1px solid #dfbf8c;
  border-radius: 20px;
  padding: 26px 20px;
  box-shadow: 0 8px 22px #224c8110;
  color: #916321;
}
.governance-heading {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.governance-heading > .governance-icon {
  display: grid;
  place-items: center;
  width: 62px;
  height: 62px;
  padding: 0;
  margin-bottom: 18px;
  border: 0;
  border-radius: 50%;
  background: #f7e9cf;
  color: #c48a33;
}
.governance-heading > .governance-icon svg {
  width: 28px;
  height: 28px;
}
.governance-rail strong {
  font-size: 21px;
  font-weight: 800;
  color: #b97718;
  margin-bottom: 16px;
}
.governance-rail > span:not(.governance-icon) {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 14px 0;
  border-bottom: 1px dashed #c48a3366;
  font-size: 15px;
  font-weight: 600;
}
.governance-rail > span:last-child {
  border-bottom: 0;
}
.governance-rail > span svg {
  width: 20px;
  height: 20px;
  color: #c48a33;
}
.evidence-path {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr) 170px;
  align-items: center;
  gap: 14px;
  padding: 16px 18px;
  border: 1px solid #b9cfea;
  border-radius: 17px;
  background: linear-gradient(90deg, #e2edfc, #edf4ff);
  box-shadow: 0 5px 14px #163b6b14;
}
.evidence-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 800;
  color: #2f5f9e;
}
.evidence-name svg {
  width: 20px;
  height: 20px;
  color: #4b82ca;
}
.evidence-path > div {
  display: flex;
  justify-content: space-between;
  gap: 6px;
  align-items: center;
}
.evidence-path b {
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
  color: #35567d;
  padding: 9px 12px;
  border: 1px solid #dbe6f4;
  border-radius: 999px;
  background: #fff;
  box-shadow: 0 3px 8px #1432640a;
}
.evidence-path > div svg {
  width: 14px;
  height: 14px;
  color: #8ea8ca;
}
.evidence-path > p {
  font-size: 13px;
  line-height: 1.55;
  color: #7188aa;
  border-left: 1px solid #d9e4f2;
  padding-left: 14px;
}
.architecture-summary {
  min-height: 122px;
  margin-top: 2px;
  padding: 22px 54px 40px;
  border-color: #c3d5ec;
  border-radius: 20px;
  background: linear-gradient(180deg, #f8fbff, #f3f8ff);
  box-shadow: 0 8px 22px #224c8110;
}
.architecture-summary > strong {
  font-size: clamp(21px, 1.65vw, 25px);
  color: #173b6b;
}
.capability-columns {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 24px;
  margin-top: 4px;
}
.capability-column {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: #fff;
  border: 1px solid #e5ebf4;
  border-radius: 14px;
  box-shadow: 0 8px 22px #29476c0d;
  padding: 24px 26px 22px;
}
.capability-head {
  display: flex;
  align-items: center;
  gap: 16px;
}
.capability-number {
  display: grid;
  place-items: center;
  width: 54px;
  height: 54px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--tone-soft);
  color: var(--tone);
  font-size: 21px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.capability-icon {
  display: grid;
  place-items: center;
  width: 54px;
  height: 54px;
  flex-shrink: 0;
  border-radius: 15px;
  background: var(--tone);
  color: #fff;
}
.capability-icon svg {
  width: 26px;
  height: 26px;
}
.capability-heading {
  min-width: 0;
}
.capability-heading h3 {
  font-size: 25px;
  margin: 0 0 7px;
  font-weight: 700;
  color: var(--intro-ink);
}
.capability-subtitle {
  font-size: 14px;
  color: #7d8ea3;
}
.screenshot-button {
  width: 100%;
  height: 286px;
  display: block;
  overflow: hidden;
  padding: 27px 0 0;
  background: #eef2f8;
  border: 1px solid #dfe6f0;
  border-radius: 10px;
  margin-top: 20px;
  position: relative;
  cursor: zoom-in;
  text-align: left;
}
.screen-body {
  display: block;
  height: calc(100% - 27px);
  overflow: hidden;
  padding: 9px 10px 0;
  box-sizing: border-box;
  -webkit-mask-image: linear-gradient(180deg, #000 72%, #0000 100%);
  mask-image: linear-gradient(180deg, #000 72%, #0000 100%);
}
.screen-body img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: top center;
  display: block;
  border-radius: 6px;
}
.screen-chrome {
  display: flex;
  gap: 5px;
  align-items: center;
  height: 27px;
  padding: 0 11px;
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  background: #e6ecf5;
}
.screen-chrome i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #d8dee8;
}
.screen-chrome i:first-child {
  background: #f0867f;
}
.screen-chrome i:nth-child(2) {
  background: #f0c26b;
}
.screen-chrome i:nth-child(3) {
  background: #7ec89a;
}
.screen-chrome > span {
  color: #8596ad;
  font-size: 10px;
  margin: auto;
  transform: translateX(-10px);
}
.screenshot-enlarge {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  color: #e5edf7;
  background: #1b2c43da;
  padding: 8px;
  font-size: 12px;
}
.screenshot-enlarge svg {
  width: 13px;
  height: 13px;
}
.screenshot-note {
  font-size: 13px;
  color: #8997a9;
  line-height: 1.7;
  margin-top: 12px;
}
.capability-output {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 16px;
  font-weight: 600;
  color: #2f7d6c;
  background: #ecf7f2;
  border-radius: 10px;
  padding: 14px 18px;
  margin-top: auto;
}
.capability-output svg {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  color: #47a58e;
}
.capability-scope {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 18px 24px;
  border-radius: 12px;
  background: #eef4fc;
}
.scope-icon {
  display: grid;
  place-items: center;
  width: 46px;
  height: 46px;
  flex-shrink: 0;
  border-radius: 12px;
  background: #fff;
  color: #4b82ca;
}
.scope-icon svg {
  width: 24px;
  height: 24px;
}
.capability-scope p {
  color: #4f6a89;
  font-size: 16px;
  line-height: 1.6;
}
.loop-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  flex-wrap: wrap;
}
.loop-legend {
  display: flex;
  gap: 22px;
  font-size: 14px;
  color: #6f88ab;
  white-space: nowrap;
}
.loop-legend > span {
  display: flex;
  align-items: center;
  gap: 8px;
}
.loop-legend i {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #4d86df;
}
.loop-legend > span:last-child i {
  background: #d08a35;
}
.loop-visual {
  margin-top: 4px;
}
.loop-row,
.loop-bridge {
  display: grid;
  grid-template-columns:
    minmax(0, 1fr) 36px minmax(0, 1fr) 36px minmax(0, 1fr)
    36px minmax(0, 1fr);
  gap: 10px;
  align-items: stretch;
}
.loop-row article {
  position: relative;
  overflow: hidden;
  min-width: 0;
  min-height: 184px;
  padding: 24px 16px 22px;
  text-align: center;
  background: linear-gradient(180deg, #f6faff, #eaf2fc);
  border: 1px solid #c8d8ec;
  border-radius: 20px;
  box-shadow: 0 8px 18px #113b6b12;
}
.loop-arrow {
  display: grid;
  place-items: center;
  color: #4d86df;
}
.loop-arrow svg {
  width: 30px;
  height: 30px;
}
.loop-row article::after {
  content: "";
  position: absolute;
  right: -20px;
  bottom: -30px;
  width: 110px;
  height: 110px;
  border-radius: 50%;
  background: radial-gradient(circle at 30% 35%, #86b2f41f, #86b2f400 70%);
  pointer-events: none;
}
.loop-row .human-step {
  background: linear-gradient(180deg, #fff9ef, #fcefdc);
  border-color: #e7c995;
}
.loop-row .human-step h3,
.loop-row .human-step p {
  color: #9a661f;
}
.loop-row .human-step .loop-step-number {
  color: #c6944d;
}
.loop-step-icon {
  display: grid;
  place-items: center;
  width: 60px;
  height: 60px;
  margin: 0 auto 16px;
  border-radius: 17px;
  background: linear-gradient(180deg, #deebff, #edf4ff);
  color: #4d86df;
}
.loop-step-icon svg {
  width: 28px;
  height: 28px;
}
.loop-row .human-step .loop-step-icon {
  background: linear-gradient(180deg, #fbefdb, #f5e0bc);
  color: #d08a35;
}
.loop-row h3 {
  font-size: 24px;
  margin: 0 0 10px;
  font-weight: 800;
  color: #163b6a;
}
.loop-row p {
  font-size: 14px;
  line-height: 1.6;
  color: #6f85a5;
}
.loop-step-number {
  position: absolute;
  left: 16px;
  top: 12px;
  color: #8caad5;
  font-size: 24px;
  font-weight: 300;
  letter-spacing: 1px;
}
.loop-backward {
  direction: rtl;
}
.loop-backward > * {
  direction: ltr;
}
.loop-backward article:last-of-type {
  background: linear-gradient(180deg, #effaf5, #e3f3eb);
  border-color: #bcdccb;
}
.loop-backward article:last-of-type .loop-step-icon {
  background: linear-gradient(180deg, #d9efe7, #e9f7f1);
  color: #2f9a88;
}
.loop-bridge {
  height: 38px;
  align-items: center;
}
.loop-bridge svg {
  grid-column: 7;
  justify-self: center;
  width: 28px;
  height: 28px;
  color: #4d86df;
  transform: rotate(90deg);
}
.loop-human-note {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-width: 0;
  min-height: 184px;
  padding: 20px 14px;
  text-align: center;
  border: 1px dashed #dcb474;
  border-radius: 20px;
  background: linear-gradient(180deg, #fffaf3, #fcf0de);
  box-shadow: 0 8px 18px #9a661f0d;
}
.loop-note-icon {
  display: grid;
  place-items: center;
  width: 60px;
  height: 60px;
  margin-bottom: 16px;
  border-radius: 17px;
  background: linear-gradient(180deg, #fff6e7, #f8e5bd);
  color: #d08a35;
}
.loop-note-icon svg {
  width: 28px;
  height: 28px;
}
.loop-human-note strong {
  font-size: 23px;
  font-weight: 800;
  color: #b37520;
  margin-bottom: 8px;
}
.loop-human-note p {
  font-size: 14px;
  color: #a77a3d;
}
.loop-feedback {
  display: flex;
  gap: 12px;
  align-items: center;
  margin: 16px 0 0;
  color: #6e89ab;
  font-size: 14px;
  font-weight: 600;
}
.loop-feedback i:first-child {
  flex: 0 0 9px;
  height: 9px;
  border-radius: 50%;
  background: #83afe8;
}
.loop-feedback i:last-child {
  flex: 1;
  height: 2px;
  background: linear-gradient(90deg, #9dc0ee, #dce6f2);
}
.repair-branch {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 18px 22px;
  border: 1px solid #d5c6ec;
  border-radius: 18px;
  background: linear-gradient(90deg, #eee6fa, #f6f1fb);
  box-shadow: 0 6px 16px #3826700d;
  flex-wrap: wrap;
}
.repair-icon {
  display: grid;
  place-items: center;
  width: 46px;
  height: 46px;
  flex-shrink: 0;
  border-radius: 14px;
  background: #fff;
  color: #8b72c8;
  box-shadow: 0 5px 12px #503f8a14;
}
.repair-icon svg {
  width: 24px;
  height: 24px;
}
.repair-branch strong {
  font-size: 20px;
  font-weight: 800;
  color: #6a4fae;
}
.repair-steps {
  display: flex;
  align-items: center;
  gap: 18px;
  margin-left: auto;
  flex-wrap: wrap;
}
.repair-steps > span {
  font-size: 14px;
  color: #7c6d9d;
}
.repair-steps > span:first-child {
  padding: 10px 18px;
  border-radius: 999px;
  background: #e6d9f7;
  color: #7b5eb1;
  font-weight: 700;
}
.repair-steps svg {
  width: 16px;
  height: 16px;
  color: #a896ce;
}
.evolution-visual {
  display: grid;
  grid-template-columns: 216px minmax(0, 1fr) 216px;
  align-items: start;
  gap: 18px;
  margin: 4px 0 0;
}
.evolution-input,
.evolution-output {
  display: flex;
  flex-direction: column;
  min-height: 326px;
  min-width: 0;
  text-align: center;
  padding: 22px 18px;
  border: 1px solid #bcdccb;
  border-radius: 20px;
  background: linear-gradient(180deg, #effaf5, #e3f3eb);
  box-shadow: 0 8px 20px #113b6b12;
}
.evolution-card-title {
  display: flex;
  align-items: center;
  gap: 9px;
  text-align: left;
  font-size: 17px;
  font-weight: 800;
  color: #2f9a88;
  margin-bottom: 22px;
}
.evolution-card-title i {
  width: 4px;
  height: 22px;
  border-radius: 4px;
  background: #38b090;
}
.evolution-card-icon {
  display: grid;
  place-items: center;
  width: 84px;
  height: 84px;
  margin: 0 auto 18px;
  border-radius: 23px;
  background: #d9efe7;
  color: #2f9a88;
}
.evolution-card-icon svg {
  width: 38px;
  height: 38px;
}
.evolution-input h3,
.evolution-output h3 {
  font-size: 22px;
  font-weight: 800;
  color: #163b6a;
  margin-bottom: 10px;
}
.evolution-input p,
.evolution-output p {
  font-size: 14px;
  color: #6f85a5;
  line-height: 1.7;
  margin-bottom: 18px;
}
.solid-status {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 700;
  color: #3a9b85;
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px solid #badbcd;
}
.solid-status i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #35b38d;
}
.evolution-center {
  min-width: 0;
}
.evolution-pipeline {
  border: 1px dashed #a8c2e7;
  border-radius: 22px;
  padding: 20px;
  background: linear-gradient(180deg, #f4f9ff, #edf4fc);
  box-shadow: 0 8px 20px #113b6b0d;
}
.pipeline-head {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  align-items: center;
  gap: 8px 12px;
  margin-bottom: 18px;
}
.pipeline-icon {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 13px;
  background: #deebff;
  color: #4d86df;
}
.pipeline-icon svg {
  width: 24px;
  height: 24px;
}
.pipeline-head strong {
  font-size: 21px;
  font-weight: 800;
  color: #20446f;
}
.pipeline-head p {
  grid-column: 1 / -1;
  font-size: 13px;
  line-height: 1.6;
  color: #6f85a5;
}
.evolution-stages {
  display: flex;
  align-items: stretch;
  gap: 10px;
}
.evolution-stages article {
  position: relative;
  flex: 1 1 0;
  min-width: 0;
  min-height: 220px;
  padding: 40px 12px 18px;
  text-align: center;
  border: 1px solid #c8d8ec;
  border-radius: 18px;
  background: linear-gradient(180deg, #fbfdff, #eaf2fc);
  box-shadow: 0 6px 16px #113b6b0d;
}
.stage-number {
  position: absolute;
  left: 16px;
  top: 12px;
  font-size: 23px;
  font-weight: 300;
  color: #8caad5;
}
.stage-icon {
  display: grid;
  place-items: center;
  width: 56px;
  height: 56px;
  margin: 0 auto 14px;
  border-radius: 16px;
  background: linear-gradient(180deg, #deebff, #edf4ff);
  color: #4d86df;
}
.stage-icon svg {
  width: 26px;
  height: 26px;
}
.evolution-stages h3 {
  font-size: 20px;
  font-weight: 800;
  color: #173b6b;
  margin-bottom: 10px;
}
.evolution-stages p {
  font-size: 13px;
  line-height: 1.6;
  color: #6f85a5;
}
.evolution-stages .human-review {
  background: linear-gradient(180deg, #fffaf3, #fcf0de);
  border-color: #e7c995;
}
.evolution-stages .human-review h3 {
  color: #9a661f;
}
.evolution-stages .human-review .stage-icon {
  background: linear-gradient(180deg, #fbefdb, #f5e0bc);
  color: #d08a35;
}
.stage-arrow {
  display: grid;
  place-items: center;
  width: 26px;
  flex-shrink: 0;
  color: #4d86df;
}
.stage-arrow svg {
  width: 22px;
  height: 22px;
}
.evolution-feedback {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 18px;
}
.feedback-line {
  flex: 1;
  height: 2px;
  border-radius: 2px;
  background: linear-gradient(90deg, #b7dfd3, #8cd0be);
}
.feedback-pill {
  padding: 10px 20px;
  border-radius: 999px;
  background: #e1f2e9;
  color: #278e79;
  font-size: 16px;
  font-weight: 800;
  text-align: center;
  box-shadow: 0 5px 12px #278e7914;
}
.improvement-tracks {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  overflow: hidden;
  border: 1px solid #c8d8ec;
  border-radius: 20px;
  background: linear-gradient(180deg, #f7fbff, #edf3fc);
  box-shadow: 0 8px 20px #113b6b0d;
}
.improvement-tracks > div {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 16px;
  padding: 20px 24px;
}
.improvement-tracks > div + div {
  border-left: 1px solid #c8d8ec;
}
.track-icon {
  display: grid;
  place-items: center;
  width: 50px;
  height: 50px;
  flex-shrink: 0;
  border-radius: 14px;
  background: #fff;
  color: #4d86df;
  box-shadow: 0 6px 14px #14386614;
}
.track-icon svg {
  width: 25px;
  height: 25px;
}
.track-text {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.improvement-tracks strong {
  font-size: 20px;
  font-weight: 800;
  color: #163b6a;
}
.improvement-tracks .track-text > span {
  font-size: 14px;
  line-height: 1.6;
  color: #6f85a5;
}
.loop-sheet > .intro-takeaway,
.evolution-sheet > .intro-takeaway {
  min-height: 122px;
  padding-bottom: 38px;
  border-color: #c3d5ec;
  border-radius: 20px;
  background: linear-gradient(180deg, #f8fbff, #f3f8ff);
  box-shadow: 0 8px 20px #113b6b0d;
}
.roadmap-visual {
  display: flex;
  align-items: stretch;
  gap: 14px;
  margin: 4px 0 0;
}
.roadmap-stage {
  --stage: #3978ce;
  --stage-soft: #dce9fc;
  --stage-light: #edf4ff;
  --stage-line: #a9c5ed;
  --stage-number: #729bd8;
  position: relative;
  overflow: hidden;
  flex: 1;
  min-width: 0;
  min-height: 448px;
  display: flex;
  flex-direction: column;
  padding: 24px;
  border: 1px solid var(--stage-line);
  border-top: 5px solid var(--stage);
  border-radius: 22px;
  background: linear-gradient(160deg, var(--stage-light), var(--stage-soft));
  box-shadow: 0 9px 22px #113b6b18;
}
.roadmap-stage.stage-two {
  --stage: #278e7b;
  --stage-soft: #d9eee4;
  --stage-light: #edf8f3;
  --stage-line: #a4d0bd;
  --stage-number: #5da697;
}
.roadmap-stage.stage-three {
  --stage: #bd7b2b;
  --stage-soft: #f6e4c8;
  --stage-light: #fff7e9;
  --stage-line: #ddbd87;
  --stage-number: #ca9a55;
}
.roadmap-stage-top {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 22px;
  color: var(--stage);
}
.roadmap-number {
  font-size: 50px;
  font-weight: 300;
  line-height: 1;
  color: var(--stage-number);
}
.roadmap-status {
  padding: 7px 14px;
  border: 1px solid var(--stage);
  border-radius: 999px;
  background: #ffffffde;
  font-size: 13px;
  font-weight: 700;
}
.roadmap-watermark {
  position: absolute;
  right: 0;
  top: 0;
  display: grid;
  place-items: center;
  width: 76px;
  height: 76px;
  border: 1px solid #ffffffd9;
  border-radius: 22px;
  background: #ffffffe6;
  box-shadow: 0 8px 20px #123a681a;
  color: var(--stage);
}
.roadmap-watermark svg {
  width: 34px;
  height: 34px;
}
.roadmap-stage h3 {
  font-size: 42px;
  line-height: 1.2;
  font-weight: 800;
  color: var(--stage);
  margin: 0 0 10px;
}
.roadmap-focus {
  font-size: 15px;
  line-height: 1.6;
  color: #60799a;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--stage-line);
}
.roadmap-stage ul {
  display: flex;
  flex-direction: column;
  gap: 10px;
  list-style: none;
  padding: 0;
  margin: 18px 0 22px;
  font-size: 15px;
  line-height: 1.65;
  color: #28486e;
}
.roadmap-stage li {
  position: relative;
  padding-left: 18px;
}
.roadmap-stage li::before {
  content: "";
  position: absolute;
  left: 1px;
  top: 0.6em;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--stage);
}
.roadmap-deliverable {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: auto;
  padding: 14px;
  border: 1px solid #ffffffd9;
  border-radius: 15px;
  background: #ffffffde;
  box-shadow: 0 4px 12px #113b6b0d;
  color: var(--stage);
  font-size: 14px;
  line-height: 1.5;
  font-weight: 700;
}
.roadmap-deliverable svg {
  width: 22px;
  height: 22px;
}
.roadmap-arrow {
  display: grid;
  place-items: center;
  width: 30px;
  color: #4d86df;
  flex-shrink: 0;
}
.roadmap-arrow svg {
  width: 30px;
  height: 30px;
}
.roadmap-foundation {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 16px;
  padding: 18px 24px;
  border: 1px solid #bdd0eb;
  border-radius: 20px;
  background: linear-gradient(180deg, #edf4ff, #dfebfa);
  box-shadow: 0 7px 18px #113b6b12;
}
.foundation-heading {
  display: flex;
  align-items: center;
  gap: 14px;
  padding-right: 20px;
  border-right: 1px solid #bdcfe8;
}
.foundation-icon {
  display: grid;
  place-items: center;
  width: 50px;
  height: 50px;
  flex-shrink: 0;
  border-radius: 15px;
  background: #fff;
  color: #4d86df;
  box-shadow: 0 6px 14px #113b6b14;
}
.foundation-icon svg {
  width: 25px;
  height: 25px;
}
.roadmap-foundation strong {
  font-size: 21px;
  font-weight: 800;
  color: #183b6b;
  white-space: nowrap;
}
.foundation-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 700;
  color: #55769e;
  white-space: nowrap;
}
.foundation-item svg {
  width: 19px;
  height: 19px;
  color: #4f7cb7;
}
.foundation-planned b {
  padding: 2px 6px;
  border: 1px solid #d9b77c;
  border-radius: 5px;
  background: #fff4df;
  color: #a47026;
  font-size: 11px;
  font-weight: 600;
}
.roadmap-sheet > .intro-takeaway {
  min-height: 136px;
  padding-bottom: 38px;
  border-color: #bdd0eb;
  border-radius: 20px;
  background: linear-gradient(180deg, #edf4ff, #e3edfb);
  box-shadow: 0 7px 18px #113b6b12;
}
.intro-page-footer {
  display: flex;
  align-items: center;
  gap: 15px;
  max-width: 1360px;
  margin: auto;
  padding: 10px 24px 32px;
  color: #8497ad;
  font-size: 11px;
}
.intro-page-footer strong {
  font-size: 13px;
  font-weight: 600;
}
.intro-page-footer a {
  margin-left: auto;
}
.intro-preview {
  width: min(1400px, calc(100vw - 40px));
  max-height: calc(100svh - 40px);
  padding: 18px;
  border: 1px solid #dce5f1;
  border-radius: 8px;
  background: #fff;
  color: #153252;
}
.intro-preview::backdrop {
  background: #142b46bb;
  backdrop-filter: blur(4px);
}
.intro-preview > header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 15px;
  padding-bottom: 15px;
}
.intro-preview h2 {
  margin: 0 0 5px;
  font-size: 19px;
}
.intro-preview p {
  color: #7f90a5;
  font-size: 12px;
}
.intro-preview button {
  background: #f2f5fa;
  border: 0;
  color: #537293;
  width: 32px;
  height: 32px;
  border-radius: 4px;
  display: grid;
  place-items: center;
  cursor: pointer;
}
.intro-preview > img {
  display: block;
  width: 100%;
  height: auto;
}
@media (min-width: 1600px) {
  .intro-nav {
    padding-left: max(32px, calc((100vw - 1360px) / 2));
    padding-right: max(32px, calc((100vw - 1360px) / 2));
  }
}
@media (max-width: 1150px) {
  .intro-nav {
    gap: 16px;
    padding: 12px 22px;
  }
  .intro-brand-label {
    display: none;
  }
  .intro-chapters a {
    padding: 9px 7px;
  }
  .intro-sheet {
    padding: 34px 35px 24px;
    gap: 25px;
  }
  .gap-column h3 {
    font-size: 24px;
    gap: 14px;
  }
  .gap-before {
    font-size: 15px;
    gap: 12px;
  }
  .gap-before-icon {
    width: 30px;
    height: 30px;
  }
  .gap-before-icon svg {
    width: 17px;
    height: 17px;
  }
  .gap-column p {
    font-size: 16px;
  }
  .gap-tags {
    font-size: 16px;
  }
  .gap-tag-icon svg {
    width: 21px;
    height: 21px;
  }
  .context-panels {
    grid-template-columns: 1fr;
  }
  .panel-caption h2 {
    font-size: 22px;
  }
  .engine-column {
    padding: 22px 18px 16px;
  }
  .engine-column h3 {
    font-size: 22px;
  }
  .architecture-layer {
    grid-template-columns: 164px minmax(0, 1fr);
  }
  .layer-items {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .evidence-path {
    grid-template-columns: 110px minmax(0, 1fr);
  }
  .evidence-path > p {
    grid-column: 1 / -1;
    border-left: 0;
    padding-left: 0;
  }
  .evolution-visual {
    grid-template-columns: 160px minmax(0, 1fr) 160px;
    gap: 16px;
  }
  .evolution-input,
  .evolution-output {
    padding: 20px 14px;
  }
  .evolution-input h3,
  .evolution-output h3 {
    font-size: 18px;
  }
  .evolution-pipeline {
    padding: 18px 16px;
  }
  .pipeline-head strong {
    font-size: 18px;
  }
  .evolution-stages {
    gap: 8px;
  }
  .evolution-stages article {
    padding-right: 10px;
    padding-left: 10px;
  }
  .evolution-stages h3 {
    font-size: 17px;
  }
  .evolution-stages p {
    font-size: 12px;
  }
  .stage-arrow {
    width: 20px;
  }
  .improvement-tracks > div {
    gap: 12px;
    padding: 18px;
  }
  .improvement-tracks strong {
    font-size: 18px;
  }
  .roadmap-stage {
    padding: 22px 18px;
  }
  .roadmap-watermark {
    width: 66px;
    height: 66px;
  }
  .roadmap-watermark svg {
    width: 30px;
    height: 30px;
  }
  .roadmap-number {
    font-size: 46px;
  }
  .roadmap-stage h3 {
    font-size: 36px;
  }
  .roadmap-stage ul {
    font-size: 14px;
  }
  .roadmap-foundation {
    gap: 12px;
    padding: 16px 18px;
  }
  .foundation-heading {
    gap: 10px;
    padding-right: 14px;
  }
  .roadmap-foundation strong {
    font-size: 19px;
  }
  .foundation-item {
    font-size: 13px;
    gap: 7px;
  }
}
@media (max-width: 900px) {
  .intro-nav {
    flex-wrap: wrap;
    gap: 10px;
    padding: 12px 24px 8px;
  }
  .intro-return {
    margin-left: auto;
  }
  .intro-chapters {
    width: 100%;
    order: 3;
    justify-content: space-between;
    margin: 0;
    overflow-x: auto;
  }
  .intro-chapters a {
    font-size: 12px;
    padding: 7px 5px;
  }
  .intro-pages {
    padding: 18px;
  }
  .intro-section {
    scroll-margin-top: 118px;
    margin-bottom: 18px;
  }
  .intro-sheet {
    min-height: auto;
    padding: 32px 26px 24px;
    gap: 25px;
  }
  .intro-heading h1,
  .intro-heading h2 {
    font-size: 23px;
  }
  .panel-map {
    grid-template-columns: auto minmax(0, 1fr);
  }
  .panel-map .panel-topology {
    display: none;
  }
  .intro-takeaway > strong {
    font-size: 18px;
  }
  .gap-columns {
    gap: 14px;
  }
  .gap-column {
    padding: 16px 16px 14px 20px;
  }
  .gap-column::before {
    width: 6px;
    height: 76px;
  }
  .gap-number {
    font-size: 36px;
  }
  .gap-icon {
    width: 50px;
    height: 50px;
  }
  .gap-icon svg {
    width: 26px;
    height: 26px;
  }
  .gap-column h3 {
    font-size: 22px;
    gap: 12px;
  }
  .gap-before {
    font-size: 13px;
    min-height: 44px;
    gap: 11px;
  }
  .gap-before-icon {
    width: 28px;
    height: 28px;
  }
  .gap-before-icon svg {
    width: 16px;
    height: 16px;
  }
  .gap-column p {
    font-size: 15px;
  }
  .gap-tags {
    font-size: 15px;
    min-height: 40px;
    padding: 6px 14px;
  }
  .gap-tag-icon svg {
    width: 19px;
    height: 19px;
  }
  .gap-callout {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
    padding: 16px 22px 30px;
  }
  .gap-callout .callout-stack {
    width: 72px;
    height: 62px;
  }
  .process-caption {
    font-size: 20px;
    gap: 14px;
  }
  .process-icon {
    width: 44px;
    height: 44px;
  }
  .process-icon svg {
    width: 22px;
    height: 22px;
  }
  .manual-process li {
    font-size: 16px;
    gap: 12px;
  }
  .manual-process > p {
    font-size: 14px;
  }
  .gap-callout > strong {
    font-size: 18px;
  }
  .engine-columns {
    grid-template-columns: 1fr;
    gap: 20px;
  }
  .engine-visual {
    max-width: 400px;
  }
  .project-intro .engine-convergence {
    display: none;
  }
  .engine-column {
    padding: 18px 16px 16px;
  }
  .engine-column h3 {
    font-size: 20px;
  }
  .engine-column li {
    font-size: 14px;
    gap: 9px;
  }
  .engine-icon {
    width: 36px;
    height: 36px;
  }
  .engine-en {
    font-size: 11px;
  }
  .engine-verb {
    font-size: 15px;
  }
  .engine-detail {
    font-size: 11px;
  }
  .collaboration-bar {
    gap: 24px;
    padding: 18px 20px;
  }
  .collaboration-steps > span {
    padding: 11px 16px;
    font-size: 14px;
  }
  .collaboration-name {
    font-size: 20px;
  }
  .architecture-visual {
    grid-template-columns: minmax(0, 1fr) 150px;
    gap: 13px;
  }
  .architecture-layer {
    grid-template-columns: 1fr;
    gap: 14px;
  }
  .architecture-stack {
    padding: 12px;
  }
  .layer-name {
    gap: 10px;
  }
  .evidence-path {
    grid-template-columns: 1fr;
  }
  .architecture-summary {
    padding: 22px 24px 36px;
  }
  .architecture-summary > strong {
    font-size: 20px;
  }
  .layer-icon {
    width: 42px;
    height: 42px;
  }
  .layer-name strong {
    font-size: 16px;
  }
  .layer-text > span {
    font-size: 9px;
  }
  .layer-items {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
  .layer-items > span {
    font-size: 13px;
    min-height: 50px;
  }
  .governance-rail {
    padding: 22px 10px;
    gap: 16px;
  }
  .governance-rail > span {
    font-size: 13px;
  }
  .evidence-path > p {
    margin-left: 0;
    border-left: 0;
    padding-left: 0;
  }
  .capability-columns {
    gap: 16px;
  }
  .capability-column {
    padding: 18px 16px 16px;
  }
  .capability-head {
    gap: 12px;
  }
  .capability-number,
  .capability-icon {
    width: 44px;
    height: 44px;
  }
  .capability-heading h3 {
    font-size: 20px;
  }
  .capability-subtitle {
    font-size: 13px;
  }
  .screenshot-button {
    height: 210px;
  }
  .screenshot-note {
    font-size: 12px;
  }
  .capability-output {
    font-size: 14px;
    padding: 12px 14px;
    line-height: 1.6;
  }
  .capability-scope {
    padding: 16px 18px;
    gap: 14px;
  }
  .capability-scope p {
    font-size: 14px;
  }
  .loop-row,
  .loop-bridge {
    grid-template-columns:
      minmax(0, 1fr) 24px minmax(0, 1fr) 24px minmax(0, 1fr)
      24px minmax(0, 1fr);
    gap: 6px;
  }
  .loop-row article {
    padding: 38px 10px 20px;
  }
  .loop-row h3 {
    font-size: 19px;
  }
  .loop-row p,
  .loop-human-note p {
    font-size: 12px;
  }
  .loop-step-icon {
    width: 48px;
    height: 48px;
    margin-bottom: 12px;
  }
  .loop-human-note strong {
    font-size: 18px;
  }
  .loop-arrow svg {
    width: 22px;
    height: 22px;
  }
  .repair-branch {
    padding: 16px 18px;
  }
  .repair-branch strong {
    font-size: 18px;
  }
  .repair-steps {
    margin-left: 0;
  }
  .evolution-visual {
    grid-template-columns: 1fr;
    gap: 20px;
  }
  .evolution-input,
  .evolution-output {
    min-height: auto;
    padding: 22px;
  }
  .evolution-card-icon {
    width: 64px;
    height: 64px;
  }
  .evolution-feedback {
    margin-top: 16px;
  }
  .improvement-tracks {
    grid-template-columns: 1fr;
  }
  .improvement-tracks > div + div {
    border-left: 0;
    border-top: 1px solid #c8d8ec;
  }
  .roadmap-visual {
    flex-direction: column;
  }
  .roadmap-arrow {
    width: 100%;
    height: 26px;
  }
  .roadmap-arrow svg {
    transform: rotate(90deg);
  }
  .roadmap-stage {
    min-height: auto;
    padding: 24px;
  }
  .roadmap-stage h3 {
    font-size: 36px;
  }
  .roadmap-stage ul {
    font-size: 15px;
  }
  .roadmap-foundation {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    justify-items: start;
    gap: 16px;
    padding: 20px;
  }
  .foundation-heading {
    grid-column: 1 / -1;
    padding-right: 0;
    border-right: 0;
  }
  .foundation-item {
    font-size: 14px;
    white-space: normal;
  }
  .foundation-planned {
    grid-column: 1 / -1;
  }
}
@media (max-width: 600px) {
  .intro-nav {
    padding: 12px 16px 7px;
  }
  .intro-brand strong {
    font-size: 17px;
  }
  .intro-chapters {
    gap: 2px;
    scrollbar-width: none;
  }
  .intro-chapters::-webkit-scrollbar {
    display: none;
  }
  .intro-chapters a {
    flex-direction: column;
    gap: 2px;
    font-size: 11px;
    padding: 6px 5px;
  }
  .intro-pages {
    padding: 12px;
  }
  .intro-section {
    scroll-margin-top: 125px;
    margin-bottom: 12px;
  }
  .intro-sheet {
    padding: 28px 20px 22px;
    gap: 26px;
  }
  .intro-heading h1,
  .intro-heading h2 {
    font-size: 20px;
    letter-spacing: -0.5px;
    line-height: 1.45;
  }
  .context-panel {
    padding: 20px 18px 16px;
  }
  .panel-caption h2 {
    font-size: 20px;
  }
  .panel-caption p {
    font-size: 13px;
  }
  .panel-map {
    grid-template-columns: 1fr;
  }
  .service-grid {
    gap: 8px;
  }
  .service-tile {
    min-height: 54px;
    font-size: 13px;
  }
  .panel-detail {
    flex-wrap: wrap;
    gap: 6px 14px;
    padding: 10px 13px;
  }
  .trend-type {
    right: 16px;
    top: 18px;
    font-size: 10px;
  }
  .trend-chart {
    height: 190px;
  }
  .trend-legend {
    gap: 12px;
    font-size: 11px;
  }
  .trend-caption {
    grid-template-columns: 1fr;
  }
  .mini-metric {
    white-space: normal;
  }
  .intro-takeaway {
    gap: 12px;
    padding: 18px 72px 16px;
  }
  .intro-takeaway > strong {
    font-size: 17px;
  }
  .intro-takeaway > small {
    font-size: 11px;
    right: 16px;
    bottom: 12px;
  }
  .gap-columns {
    grid-template-columns: 1fr;
    gap: 14px;
  }
  .gap-number {
    font-size: 34px;
  }
  .gap-icon {
    width: 46px;
    height: 46px;
  }
  .gap-icon svg {
    width: 24px;
    height: 24px;
  }
  .gap-column h3 {
    font-size: 21px;
    gap: 10px;
  }
  .gap-column h3 > span {
    font-size: 13px;
  }
  .gap-before {
    font-size: 14px;
  }
  .gap-column p {
    font-size: 15px;
    margin-bottom: 14px;
  }
  .gap-break {
    margin: 14px 0;
  }
  .gap-tags {
    font-size: 15px;
    min-height: 40px;
    flex-wrap: wrap;
  }
  .callout-copy strong {
    font-size: 18px;
  }
  .contradiction-band {
    padding: 16px 18px 34px;
  }
  .gap-callout {
    padding: 16px 18px 32px;
  }
  .gap-callout > strong {
    font-size: 17px;
  }
  .manual-process {
    padding: 14px 16px;
  }
  .process-caption {
    font-size: 17px;
    gap: 12px;
    margin-bottom: 12px;
  }
  .process-caption::before {
    height: 24px;
  }
  .process-icon {
    width: 38px;
    height: 38px;
  }
  .process-icon svg {
    width: 20px;
    height: 20px;
  }
  .manual-process ol {
    flex-wrap: wrap;
    gap: 10px;
  }
  .manual-process li {
    flex: 0 0 calc(50% - 10px);
    font-size: 14px;
    gap: 9px;
  }
  .manual-process li:not(:last-child)::after {
    margin-left: 0;
  }
  .manual-process > p {
    font-size: 13px;
    line-height: 1.7;
    align-items: flex-start;
    padding: 9px 14px;
  }
  .engine-columns {
    grid-template-columns: 1fr;
    gap: 20px;
  }
  .engine-column {
    padding: 22px 24px;
  }
  .engine-symbol {
    margin-bottom: 14px;
  }
  .engine-column h3 {
    font-size: 24px;
  }
  .engine-column ol {
    margin: 18px 0;
    gap: 12px;
  }
  .engine-column li {
    font-size: 15px;
  }
  .engine-detail {
    font-size: 12px;
  }
  .project-intro .engine-convergence {
    display: none;
  }
  .collaboration-bar {
    padding: 18px 14px;
    flex-direction: column;
    gap: 14px;
  }
  .collaboration-name {
    font-size: 22px;
  }
  .collaboration-steps {
    flex-wrap: wrap;
    justify-content: center;
    gap: 10px;
  }
  .collaboration-steps > span {
    padding: 10px 14px;
    font-size: 13px;
  }
  .collaboration-steps > span svg {
    width: 17px;
    height: 17px;
  }
  .architecture-visual {
    grid-template-columns: 1fr;
    gap: 18px;
  }
  .architecture-layer {
    grid-template-columns: 1fr;
    padding: 14px;
  }
  .layer-name {
    flex-direction: row;
    justify-content: flex-start;
    width: 100%;
    padding: 0 0 12px;
    border-bottom: 1px solid #e2e9f3;
  }
  .layer-items {
    grid-template-columns: 1fr;
    gap: 10px;
  }
  .layer-items > span {
    justify-content: flex-start;
    min-height: 48px;
    font-size: 14px;
  }
  .governance-rail {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px 14px;
    padding: 18px;
  }
  .governance-heading {
    grid-column: 1 / -1;
    flex-direction: row;
    justify-content: center;
    gap: 12px;
  }
  .governance-heading > .governance-icon {
    width: 40px;
    height: 40px;
    margin-bottom: 0;
  }
  .governance-rail strong {
    font-size: 18px;
    margin-bottom: 0;
  }
  .governance-rail > span:not(.governance-icon) {
    width: 100%;
    padding: 10px 0;
    font-size: 13px;
  }
  .governance-rail > span {
    font-size: 13px;
  }
  .evidence-path {
    gap: 12px;
    padding: 14px 16px;
  }
  .evidence-name {
    font-size: 15px;
  }
  .evidence-path > div {
    gap: 8px;
    flex-wrap: wrap;
  }
  .evidence-path b {
    font-size: 13px;
    padding: 7px 14px;
  }
  .evidence-path > div svg {
    width: 12px;
  }
  .architecture-summary {
    padding: 20px 18px 36px;
  }
  .architecture-summary > strong {
    font-size: 18px;
  }
  .evidence-path > p {
    font-size: 12px;
  }
  .capability-columns {
    grid-template-columns: 1fr;
    gap: 20px;
  }
  .capability-heading h3 {
    font-size: 22px;
  }
  .capability-subtitle {
    font-size: 13px;
  }
  .screenshot-button {
    height: 250px;
  }
  .screenshot-note {
    font-size: 12px;
  }
  .capability-output {
    font-size: 15px;
    padding: 13px 15px;
  }
  .capability-scope {
    flex-direction: row;
    align-items: center;
    gap: 12px;
    padding: 16px;
  }
  .scope-icon {
    width: 40px;
    height: 40px;
  }
  .capability-scope p {
    font-size: 13px;
  }
  .loop-row {
    display: flex;
    flex-direction: column;
    gap: 26px;
  }
  .loop-row article {
    display: grid;
    grid-template-columns: 44px 1fr;
    column-gap: 10px;
    min-height: auto;
    padding: 20px 14px 20px 42px;
    align-items: center;
    text-align: left;
  }
  .loop-arrow {
    width: 100%;
    height: 22px;
    margin: -14px 0;
  }
  .loop-arrow svg {
    width: 22px;
    height: 22px;
    transform: rotate(90deg);
  }
  .loop-backward .loop-arrow svg {
    transform: rotate(-90deg);
  }
  .loop-step-icon {
    grid-row: span 2;
    width: 42px;
    height: 42px;
    margin: 0;
  }
  .loop-step-icon svg {
    width: 22px;
    height: 22px;
  }
  .loop-row h3 {
    margin: 0 0 5px;
    font-size: 19px;
  }
  .loop-step-number {
    top: 20px;
    left: 10px;
    font-size: 18px;
  }
  .loop-bridge {
    display: flex;
    height: 38px;
    justify-content: center;
  }
  .loop-backward {
    direction: ltr;
  }
  .loop-human-note {
    flex-direction: row;
    flex-wrap: wrap;
    gap: 10px;
    min-height: auto;
    padding: 16px;
  }
  .loop-note-icon {
    width: 38px;
    height: 38px;
    margin: 0;
  }
  .loop-human-note strong {
    font-size: 18px;
    margin: 0;
  }
  .loop-feedback {
    font-size: 12px;
  }
  .repair-branch {
    gap: 10px;
    padding: 16px;
  }
  .repair-icon {
    width: 38px;
    height: 38px;
  }
  .repair-branch strong {
    font-size: 17px;
  }
  .repair-steps {
    gap: 10px;
    flex-basis: 100%;
  }
  .repair-steps > span {
    font-size: 13px;
  }
  .evolution-input,
  .evolution-output {
    padding: 20px 16px;
  }
  .evolution-pipeline {
    padding: 18px 16px;
  }
  .pipeline-head strong {
    font-size: 18px;
  }
  .evolution-stages {
    flex-direction: column;
    gap: 12px;
  }
  .evolution-stages article {
    flex: 0 0 auto;
    min-height: 210px;
  }
  .stage-arrow {
    width: 100%;
    height: 20px;
  }
  .stage-arrow svg {
    transform: rotate(90deg);
    width: 20px;
    height: 20px;
  }
  .evolution-stages h3 {
    font-size: 20px;
  }
  .feedback-pill {
    padding: 9px 14px;
    font-size: 14px;
  }
  .improvement-tracks > div {
    padding: 18px;
  }
  .loop-sheet > .intro-takeaway,
  .evolution-sheet > .intro-takeaway {
    padding: 20px 18px 36px;
  }
  .roadmap-visual {
    margin: 0;
  }
  .roadmap-stage {
    padding: 22px 18px;
  }
  .roadmap-stage-top {
    margin-bottom: 20px;
  }
  .roadmap-watermark {
    width: 64px;
    height: 64px;
  }
  .roadmap-stage h3 {
    font-size: 34px;
  }
  .roadmap-focus,
  .roadmap-stage ul {
    font-size: 14px;
  }
  .roadmap-foundation {
    gap: 16px 12px;
    padding: 18px;
  }
  .roadmap-foundation strong {
    font-size: 20px;
  }
  .foundation-item {
    font-size: 13px;
  }
  .roadmap-sheet > .intro-takeaway {
    padding: 22px 18px 36px;
  }
  .intro-page-footer {
    flex-wrap: wrap;
    font-size: 9px;
    padding: 10px 22px 25px;
    gap: 8px;
  }
  .intro-page-footer strong {
    font-size: 11px;
  }
  .intro-preview {
    width: calc(100vw - 20px);
    max-height: calc(100svh - 20px);
    padding: 12px;
  }
  .intro-preview h2 {
    font-size: 17px;
  }
  .intro-preview p {
    font-size: 10px;
  }
}

/* 巡检Agent / 问数Agent两页：问题 → 技术链路 → 已实现功能 → 边界 → 价值 */
.module-sheet {
  gap: 18px;
}
.module-problem {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 13px 20px;
  border: 1px solid #d6e7ff;
  border-radius: 14px;
  background: linear-gradient(100deg, #f1f8ff, #fafcff);
}
.module-problem-icon {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  width: 46px;
  height: 46px;
  border-radius: 12px;
  background: #ffe9da;
  color: #e17a2f;
}
.module-problem-icon svg {
  width: 24px;
  height: 24px;
}
.module-problem-copy {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}
.module-tag {
  flex: 0 0 auto;
  padding: 4px 10px;
  border-radius: 6px;
  background: #dceaff;
  color: #1d6be1;
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
}
.module-problem p {
  font-size: 18px;
  font-weight: 600;
  line-height: 1.5;
  color: #20395a;
}
.module-flow {
  padding: 16px 20px 18px;
  border: 1px solid #dce8f7;
  border-radius: 14px;
  background: #f7fbff;
}
.module-section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  font-size: 19px;
  font-weight: 800;
  color: #153252;
}
.module-section-title svg {
  width: 20px;
  height: 20px;
  color: #3978ce;
}
.module-flow ol {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 22px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.module-flow li {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 118px;
  padding: 13px 14px;
  border: 1px solid #dbe8f8;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 4px 12px #2f5f9610;
}
.module-flow li:not(:last-child)::after {
  content: "→";
  position: absolute;
  right: -18px;
  top: 50%;
  transform: translateY(-50%);
  color: #7eacff;
  font-size: 20px;
  font-weight: 700;
}
.module-flow-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.module-flow-top b {
  font-size: 18px;
  font-style: italic;
  color: #a9c2e3;
}
.module-flow-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: #eaf3ff;
  color: #2f7ad6;
}
.module-flow-icon svg {
  width: 19px;
  height: 19px;
}
.module-flow li strong {
  font-size: 18px;
  color: #153252;
}
.module-flow li small {
  font-size: 13px;
  line-height: 1.45;
  color: #63758b;
}
.module-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}
.module-card {
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  padding: 18px 20px 20px;
  border: 1px solid var(--tone-border);
  border-radius: 14px;
  background: var(--tone-soft);
}
.module-card::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 4px;
  background: var(--tone);
}
.module-card-head {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--tone);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 1.2px;
}
.module-card-icon {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 11px;
  background: var(--tone);
  color: #fff;
}
.module-card-icon svg {
  width: 20px;
  height: 20px;
}
.module-card h3 {
  margin: 14px 0 12px;
  font-size: 22px;
  font-weight: 800;
  color: #153252;
}
.module-card ul {
  display: flex;
  flex-direction: column;
  gap: 9px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.module-card li {
  position: relative;
  padding-left: 16px;
  font-size: 15px;
  line-height: 1.5;
  color: #3d5573;
}
.module-card li::before {
  content: "";
  position: absolute;
  left: 2px;
  top: 9px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--tone);
}
.module-note {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 18px;
  border: 1px solid #d6e7ff;
  border-radius: 12px;
  background: #f4f9ff;
}
.module-note-icon {
  display: grid;
  place-items: center;
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: #e3f0ff;
  color: #2f7ad6;
}
.module-note-icon svg {
  width: 18px;
  height: 18px;
}
.module-note .module-tag {
  background: #e3f0ff;
  color: #2f7ad6;
}
.module-note p {
  font-size: 14px;
  line-height: 1.5;
  color: #4a6387;
}
@media (max-width: 1150px) {
  .module-flow ol {
    gap: 16px;
  }
  .module-card h3 {
    font-size: 20px;
  }
  .module-card li {
    font-size: 14px;
  }
}
@media (max-width: 900px) {
  .module-problem-copy {
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
  }
  .module-flow ol {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .module-flow li:not(:last-child)::after {
    display: none;
  }
  .module-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 600px) {
  .module-flow ol {
    grid-template-columns: 1fr;
  }
  .module-note {
    flex-wrap: wrap;
  }
  .module-problem p,
  .module-flow li strong {
    font-size: 16px;
  }
}

/* 演示模式：一页一屏，按视口等比缩放 */
.intro-present {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border: 1px solid #cfdcee;
  border-radius: 7px;
  background: #f4f9ff;
  color: #326ec0;
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
}
.intro-present:hover {
  border-color: #a9c7ec;
  background: #e9f2ff;
}
.intro-present svg {
  width: 15px;
  height: 15px;
}
.intro-nav .intro-return {
  margin-left: 0;
}
.is-presenting .intro-nav,
.is-presenting .intro-page-footer {
  display: none;
}
.is-presenting .intro-pages {
  position: fixed;
  inset: 0;
  z-index: 90;
  overflow: hidden;
  padding: 0;
  background: radial-gradient(
    circle at 50% 0%,
    #17314f 0%,
    #0a1524 60%,
    #07101c 100%
  );
}
/* 白色页面铺满整屏，内容按统一比例居中缩放，短页把余量交给主图区 */
.is-presenting .intro-section {
  display: none;
  position: fixed;
  left: 0;
  top: 0;
  width: 100vw;
  height: 100vh;
  max-width: none;
  margin: 0;
  overflow: hidden;
  background: #fff;
}
.is-presenting .intro-section.is-current {
  display: block;
  animation: slide-enter 0.34s cubic-bezier(0.22, 0.61, 0.36, 1);
}
.is-presenting .intro-sheet {
  position: absolute;
  left: 50%;
  top: calc(50% - 34px);
  width: var(--slide-width, 1360px);
  max-width: none;
  min-height: 0;
  height: var(--slide-height, auto);
  border-radius: 0;
  box-shadow: none;
  transform: translate(-50%, -50%) scale(var(--slide-scale, 1));
  transform-origin: center center;
  justify-content: space-between;
}
.is-presenting .gap-sheet {
  background: #fff;
}
.is-presenting .intro-sheet > *:nth-child(2) {
  flex: 1 1 auto;
  min-height: 0;
}
/* 架构页右侧「贯穿全程」栏需与左侧内容等高，余量改由间隙吸收 */
.is-presenting #architecture .intro-sheet > *:nth-child(2) {
  flex: 0 1 auto;
}
/* 新增两页：问题条保持原高，余量交给三张功能卡 */
.is-presenting .module-sheet > *:nth-child(2) {
  flex: 0 1 auto;
}
.is-presenting .module-sheet .module-grid {
  flex: 1 1 auto;
  min-height: 0;
}
.is-presenting .context-panel .panel-map,
.is-presenting .context-panel .trend-chart {
  flex: 1 1 auto;
  min-height: 178px;
}
/* 量高阶段：所有页面按设计宽度排布但不可见，只取内容高度 */
.is-presenting.is-measuring .intro-section {
  display: block;
  position: absolute;
  top: 0;
  left: -20000px;
  width: var(--slide-width, 1360px);
  height: auto;
  overflow: visible;
  animation: none;
}
.is-presenting.is-measuring .intro-sheet {
  position: static;
  width: var(--slide-width, 1360px);
  height: auto;
  min-height: 0;
  transform: none;
  justify-content: flex-start;
}
.is-presenting.is-measuring .intro-sheet > *:nth-child(2),
.is-presenting.is-measuring .module-sheet .module-grid,
.is-presenting.is-measuring .context-panel .panel-map,
.is-presenting.is-measuring .context-panel .trend-chart {
  flex: 0 1 auto;
}
@keyframes slide-enter {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
.slide-progress {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: #e6edf7;
}
.slide-progress i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #4c9bff, #7fd7c4);
  transition: width 0.3s ease;
}
.slide-bar {
  position: fixed;
  left: 50%;
  bottom: 16px;
  transform: translateX(-50%);
  z-index: 95;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 8px 10px 8px 14px;
  border: 1px solid #ffffff26;
  border-radius: 12px;
  background: #0c1a2bf2;
  box-shadow: 0 16px 34px #00000073;
  color: #cfe0f2;
  backdrop-filter: blur(12px);
}
.slide-chapter {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.5px;
  color: #a8c4e0;
  white-space: nowrap;
}
.slide-nav {
  display: flex;
  align-items: center;
  gap: 6px;
}
.slide-nav button,
.slide-exit {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: 1px solid #ffffff24;
  border-radius: 8px;
  background: #ffffff14;
  color: #dcebf9;
  cursor: pointer;
}
.slide-nav button:hover:not(:disabled),
.slide-exit:hover {
  border-color: #ffffff52;
  background: #ffffff26;
}
.slide-nav button:disabled {
  opacity: 0.32;
  cursor: default;
}
.slide-nav button svg {
  width: 16px;
  height: 16px;
}
.slide-counter {
  min-width: 58px;
  text-align: center;
  font-size: 12px;
  color: #8ea9c2;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.5px;
}
.slide-counter b {
  color: #ffffff;
  font-size: 14px;
}
.slide-dots {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 2px;
}
.slide-dots button {
  width: 7px;
  height: 7px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: #ffffff38;
  cursor: pointer;
  transition:
    width 0.2s ease,
    background 0.2s ease;
}
.slide-dots button:hover {
  background: #ffffff6b;
}
.slide-dots button.active {
  width: 18px;
  background: #5ca7ff;
}
.slide-hint {
  font-size: 11px;
  color: #7e97b3;
  white-space: nowrap;
}
.slide-exit {
  display: inline-flex;
  gap: 5px;
  width: auto;
  padding: 0 10px;
  font-size: 12px;
  color: #c9dcf1;
}
.slide-exit svg {
  width: 14px;
  height: 14px;
}
@media (max-width: 900px) {
  .slide-chapter,
  .slide-hint,
  .slide-dots {
    display: none;
  }
  .slide-bar {
    gap: 10px;
    padding: 7px 8px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .is-presenting .intro-section.is-current {
    animation: none;
  }
  .slide-progress i {
    transition: none;
  }
}
</style>
