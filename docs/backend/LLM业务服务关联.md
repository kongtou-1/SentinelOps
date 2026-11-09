# LLM 与业务服务的使用关系

一个业务服务可使用多个 LLM 端点，一个端点可被多个业务服务共用。每条使用关系单独保存用途备注，和源码、发布版本、部署记录相互独立。

- LLM 服务列表显示关联业务服务及用途，可通过“关联服务”管理；端点详情提供相同入口。
- 应用与服务详情的“使用的 LLM 服务”展示端点、模型、探测状态和用途，可从该处关联或解除。
- Viewer、Operator 可查看；Admin 按既有 `llm:manage` 权限管理，读取同时要求 `resources:read` 与 `llm:read`。
- 修改备注或解除关联不会修改密钥、探测配置、探测结果，也不会停止端点或创建部署记录。新建功能不会替已有端点猜测归属。

接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/llm-bindings` | 列表，可按 `application_id`、`llm_endpoint_id` 筛选，支持分页 |
| PUT | `/api/v1/llm-endpoints/{endpoint_id}/applications/{application_id}` | 新建、更新或恢复关联，正文为 `{"purpose":"问答生成"}` |
| DELETE | 同上 | 软删除关联 |

迁移 `0020_llm_service_bindings` 新增关系表，具有 UTC 带时区的创建、修改和删除时间，应用与端点组合唯一。保存、解除均写入既有源码审计日志；普通查询排除已删除的关联、应用和端点。
