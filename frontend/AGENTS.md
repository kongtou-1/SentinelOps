# 前端协作指南

本文件补充仓库根目录 `AGENTS.md`，适用于 `frontend/`。目录说明见 `../docs/frontend/代码组织.md`。

## 代码放置

- `src/main.ts` 负责创建应用及安装插件；`src/App.vue` 负责选择布局和全局会话失效处理。
- `src/router/routes.ts` 声明路由，`guards.ts` 负责认证、权限及分块加载恢复，`index.ts` 创建 Router。
- `src/layouts/` 放控制台与资源市场布局，不放具体业务操作。
- `src/features/<业务>/` 聚合该业务的 `pages/`、`components/`、`model/`、`api.ts`、`store.ts`、`list.config.ts`。只创建实际需要的文件和目录。
- `src/views/` 放跨业务页面编排；`components/records/` 放列表、表单和关联记录编排；`config/lists.ts` 只汇总业务配置。
- `components/common/`、`components/charts/` 放跨业务组件；`api/` 放 HTTP、SSE 及生成类型；`composables/` 放通用 Vue 状态与生命周期逻辑；`utils/` 放共享函数；`types/` 放共享类型。
- 业务组件与模型留在业务目录。公共组件、`api/`、`composables/`、`utils/`、`types/` 不反向导入 `features/`、页面、路由或列表配置汇总。
- 跨业务组合显式导入具体文件，不增加大范围转导出入口或隐藏依赖；不重建平铺的 `pages/`、`lib/`。
- `styles/index.css` 是全局样式入口，保留当前导入顺序；局部样式优先放组件的 `scoped` 样式块。`public/` 仅放需要固定 URL 的静态资源。
- 跨目录导入统一使用 `@/`，同目录可使用相对路径；别名由 Vite 和 TypeScript 同步配置。
- 测试与代码同目录放置为 `*.test.ts`；`router/__fixtures__/routes.json` 保存重构前的路由契约。业务确实改变时才更新契约。

## 修改与验证

```powershell
pnpm install --frozen-lockfile
pnpm dev
pnpm typecheck
pnpm test
pnpm build
```

- `pnpm build` 包含类型检查。交付目录为 `dist/`；不手改 `dist/`、`node_modules/` 或历史导出包。
- 后端契约变更后运行 `pnpm api:types`，不手改 `src/api/schema.d.ts`。
- 目录重构保持 URL、权限、接口、轮询、SSE、表单字段、状态与导出行为；检查懒加载页面能实际导入。
- 沿用 sessionStorage 会话、同源 `/api/` 代理及既有权限处理。前端按钮权限不替代后端授权。
- 不自动重试写请求，不把令牌放入 URL、静态文件、配置或下载链接；Markdown 展示继续执行 HTML 清理。
- Docker 继续使用冻结的 pnpm 锁文件构建，通过既有 Nginx 提供页面及 API 代理；目录整理不附带部署、更新依赖或更改生产配置。
