---
name: api-project-inspection
description: 从授权 API 获取列表，筛选特殊项目，逐项取得详情并校验业务数据。
---

# API 项目巡检

使用前由业务负责人补充实际标记字段、详情字段及判定标准。本文件是接入示例，不代表已完成实际业务校验。

1. 读取 Skill 返回的 parameters、max_records、扫描时间范围；通过 list_inspection_apis 识别列表和详情数据源。
2. 使用 request_inspection_api 获取列表。根据实际返回的分页信息继续查询，不能把第一页当作全部记录。
3. 使用 execute_inspection_python 筛选 parameters 指定的特殊标记；把列表工具的 id 放入 input_ids。Python 的 data.evidence 是证据 ID 到返回 JSON 的映射，计算结果赋给 result。
4. 逐项提取项目 ID，使用详情数据源允许的 path_params、query 或 body 请求详情。禁止根据接口数据改用新地址。
5. 对确定的字段、数值及一致性要求用 Python 校验；按 Skill 的业务标准分析语义。缺少标准或详情时 status=unknown，说明缺口。
6. 每个项目输出 record_id、status、summary、evidence_ids。引用实际成功的详情或计算证据；不得把 Skill 内容当作业务证据。
7. 只有完整遍历列表、完成全部目标的检查时 complete=true。没有特殊项目时 no_matches=true，引用列表证据。未完成分页、超过上限、请求或代码失败时 complete=false。

## Python 输入与输出

```python
rows = next(iter(data["evidence"].values()))
flag = data["inputs"]["flag_field"]
result = [row["id"] for row in rows if row.get(flag) is True]
```

可将可复用代码保存到 Skill 的 scripts 字段，例如 `scripts/select.py`。执行时指定 script，临时程序则指定 code，两者不能同时提供。Python 没有网络、平台文件或凭据，仅支持标准库和临时文件。
