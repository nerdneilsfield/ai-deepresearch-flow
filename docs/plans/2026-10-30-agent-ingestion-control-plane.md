# Agent 论文接入控制面方案

## 结论

当前项目不需要新建一套 Agent 平台。项目已经有以下可复用基础：

- `paper db api serve` 提供的 MCP 查询入口；
- `pipeline` 的 Admin HTTP API；
- 持久化任务状态、worker lease 和重启恢复；
- OCR、抽取、翻译、预览和发布步骤；
- `review_ready` 人工审核状态；
- 私有 artifact 与正式 snapshot 发布路径。

下一步应增加一个受限的 **Agent ingestion control plane**，让 Grok、Hermes 和其他 Agent 可以安全提交论文、查询长任务、重试失败任务并请求人工审核。Agent 不直接操作文件系统、数据库、`run.sh` 或 provider 配置。

推荐边界如下：

```text
Grok / Hermes / CLI / 其他 Agent
              │
       MCP 或 Agent HTTP API
              │
              ▼
       Agent Command Service
       权限、幂等、审计、限额
              │
              ▼
       共享 Pipeline command service
              │
              ▼
   PipelineState + Worker + ArtifactStore
              │
   OCR → Extract → Translate → Preview
              │
              ▼
          review_ready
              │
          人工批准发布
```

本方案是设计与实施计划，不改变现有代码，也不承诺 provider 不支持 idempotency 时的 exactly-once 或 exactly-once billing。

## 目标

第一阶段完成以下闭环：

```text
Agent 找到论文
→ 提交 PDF
→ 获得 submission/job ID
→ 查询处理状态
→ 失败时重试已有 checkpoint
→ 获得私有预览
→ 请求人工审核
→ 人工决定是否发布
```

具体目标：

1. 为 Agent 提供稳定的异步提交和状态接口。
2. 防止网络重试造成重复 batch，并尽量避免由重复提交引起的重复 OCR 或重复 LLM 请求。provider 已接收但结果未落盘的重复计费窗口仍然存在。
3. 让 Agent 只能控制自己创建的任务。
4. 保留现有 pipeline 的 checkpoint、cache、lease 和恢复语义。
5. 保证 Agent 不能绕过 `review_ready` 直接发布。
6. 控制 Agent 循环提交造成的文件、并发和模型费用。
7. 让支持 MCP 的客户端直接接入，让只支持 function calling 的客户端通过薄适配器接入。

## 非目标

本阶段不做以下工作：

- 不重写 OCR、抽取、翻译或 snapshot worker；
- 不让 Agent 直接执行 `run.sh` 或任意未受限 CLI；
- 不开放任意本地路径、目录监听或数据库写入；
- 不为 Grok、Hermes 等客户端分别实现业务流程；
- 不一开始实现自动发现论文和自动批量下载；
- 不一开始实现 Agent 自动发布；
- 不承诺 provider 侧 exactly-once 或精确费用结算；
- 不引入跨 provider 的全局分布式 quota 调度器；
- 不改变现有 snapshot 数据格式。

## 现有边界与问题

### 可复用能力

现有 `pipeline/api.py` 已经提供批次创建、任务查询、重试、取消、审核、artifact 读取和发布相关能力。`PipelineState` 已有任务状态、租约和状态转移约束。现有 MCP 面向已发布论文的搜索、元数据和有界内容读取。

这些能力应继续作为唯一执行和发布内核。Agent 入口只做适配、授权和请求编排。Agent API 只在现有 `[pipeline].enabled`、admin token、queue/work/preview/snapshot 持久卷检查均通过的同一 pipeline 部署中挂载；它不能成为绕过 Worker 或持久卷检查的第二入口。

现有 `BatchIngestor` 的落盘和 job 创建需要在实现 Phase 1 时提取为可由同一 SQLite 事务协调的 command service 边界；本方案不假设当前 multipart 落盘流程已经具备该原子性。

### 必须补齐的能力

1. **Agent 身份**：当前 Admin token 权限过宽，不能直接交给外部 Agent。
2. **请求幂等**：当前 PDF digest 主要用于单个 batch 内去重，不能防止重复 POST 创建多个 batch。
3. **任务归属**：需要记录 `principal_id` 或 `created_by`，限制 Agent 只能操作自己的任务。
4. **稳定状态契约**：Agent 不能解析日志文本判断任务状态。
5. **资源限制**：需要限制 PDF 数量、大小、并发任务和 retry 次数。
6. **审计**：需要记录 Agent 命令、任务状态变化和发布审批，但不能记录 token、provider key 或完整 prompt。

## 目标数据模型

第一阶段增加的持久数据应尽量小，并与现有 `PipelineState` 关联：

### Agent principal

```text
principal_id
display_name
token_digest
scopes
allowed_project
created_at
expires_at
revoked_at
```

服务端只保存 token digest，不保存明文 token。MVP 可以先使用静态 Agent token 配置，但必须在请求上下文中形成稳定的 principal。静态 token 的撤销和轮换通过配置变更与重启完成；实现时应允许短暂保留旧、新两个 token。`expires_at` 和 `revoked_at` 在静态方案中暂不落库，后续 token registry 再实现。

### Submission

```text
submission_id
principal_id
idempotency_key
request_digest
source_type
source_ref
input_digest
 requested_actions  # Phase 1 固定为 full_pipeline
created_at
```

`(principal_id, idempotency_key)` 必须唯一。Agent API 应先在与 pipeline state 相同的 SQLite 事务中插入 `pending` 幂等占位记录，再创建 submission 与 batch/job，并在同一事务中关联它们。相同 key 和相同请求返回原 submission；相同 key 和不同请求返回 `409 Conflict`。

两个并发的相同 key 请求由唯一约束和事务锁串行化。若记录仍为 `pending`，后一个请求返回 `409` 或带 `Retry-After` 的冲突响应，不得再次创建 batch。`request_digest` 由 PDF/BibTeX 内容 digest 和规范化 action/model 参数组成，不包含 multipart boundary、临时文件名或客户端显示文件名。幂等记录至少保留到任务进入终态后 30 天。

### Agent audit event

```text
event_id
event_type
principal_id
command_id
submission_id
job_id
timestamp
input_digest
output_digest
result_code
```

建议事件类型：

```text
submission.created
input.resolved
job.queued
step.started
step.succeeded
step.failed
approval.requested
approval.granted
approval.rejected
publication.queued
publication.completed
```

## Agent HTTP API

新增独立前缀，不让 Agent 直接使用完整 Admin API：

```text
/api/v1/agent
```

### Agent API 与现有 Admin API 的边界

Phase 1 优先在同一服务进程内实现 Agent command service，并直接调用受控的 `PipelineState`、`BatchIngestor` 和现有 worker enqueue service；不通过外部 HTTP 再调用 Admin API。这样可以让幂等占位、submission 归属和 batch/job 创建使用同一事务边界。

如果部署需要拆成独立进程，Agent service 只能通过内网受保护接口调用一个不对外暴露的内部 command endpoint。Admin token 不得放在外部 Agent 配置或请求中。Agent API 与 Admin API 是两个外部 adapter，共享内核；Admin API 的 publish、publish-ready、reject 和任意管理路由不对 Agent token 开放。

submission、batch 和 job 的归属由 Agent command service 在创建事务中写入并维护。所有 Agent 查询和控制先按 `principal_id` 过滤，再调用底层状态操作。

### 提交

```http
POST /api/v1/agent/submissions
Authorization: Bearer <agent-token>
Idempotency-Key: <stable-command-key>
```

Phase 1 只支持直接上传 PDF。服务端从受限 HTTPS URL 下载 PDF，以及已上传对象引用，放到后续阶段；这样不会在最小提交接口中同时引入远程下载和 SSRF 防护边界。

服务端负责生成内部落盘路径。客户端文件名仅作为元数据。

返回异步任务：

```json
{
  "submission_id": "sub_123",
  "job_ids": ["job_456"],
  "status": "queued",
  "deduplicated": false,
  "next_action": "poll",
  "retry_after": 10
}
```

### 查询

```http
GET /api/v1/agent/submissions/{submission_id}
GET /api/v1/agent/jobs/{job_id}
```

响应至少包含：

```text
status
current_step
attempts
retryable
error_code
needs_human
approval_state
artifact_digests
next_actions
retry_after
```

状态必须使用稳定枚举，不把人类日志作为 API 契约。

### 控制命令

```http
POST /api/v1/agent/jobs/{job_id}/retry
POST /api/v1/agent/jobs/{job_id}/cancel
POST /api/v1/agent/jobs/{job_id}/review
```

所有控制命令都要求 command-level idempotency key。Agent `retry` 只允许作用于 `failed` 和 `needs_attention` 状态，不能携带 `model`、`selected_models` 或 `retry_indexing` 参数，并继续使用已有 checkpoint；Agent 不得重试已发布任务。`cancel` 表示请求在安全边界停止；若实现需要过渡状态，应向 Agent 返回明确的 `cancelling`，直到状态进入 `cancelled`。`review` 只触发通知或标记人工关注。

MVP 不提供 Agent publish endpoint。Agent API 路由表中不存在 publish、publish-ready、reject 和 BibTeX 最终绑定路由。正式发布仍由已有人工审核入口完成，并使用现有 revision/乐观并发控制；服务端也必须拒绝 Agent token 对这些 Admin 路由的访问。

## 权限模型

推荐 scope：

| Scope | 权限 |
|---|---|
| `papers:read` | 查询自己归属的 submission、job 和私有预览 artifact |
| `papers:submit` | 创建 submission |
| `papers:retry` | 重试自己创建的任务 |
| `papers:cancel` | 取消自己创建的任务 |
| `papers:review` | 请求人工审核 |
| `papers:publish` | 默认不授予 Agent |
| `papers:admin` | 仅管理员使用 |

服务端必须检查 scope 和资源归属。跨 principal 查询只提供给明确授权的 reviewer/admin。不能只因为 MCP 没暴露 publish 工具，就认为 Agent 无法 publish。

PDF、BibTeX、摘要和翻译都是不可信数据。Agent 返回这些内容时，应将其视为数据而不是系统指令，避免 prompt injection 影响权限决策。

## 输入来源与安全边界

### URL 输入（后续阶段）

URL 输入不属于 Phase 1。未来加入时，只接受明确域名 allowlist 中的 HTTPS URL，且服务端下载器必须：

- 禁止 localhost、内网、link-local 和云 metadata 地址；
- 只允许端口 443，禁用代理环境变量；
- 在建立每次连接时校验解析后的 IP，并对每次重定向重新校验，防止 DNS rebinding；
- 限制总下载时间、重定向次数，并流式限制响应大小；
- 校验 PDF 签名和内容类型；
- 记录规范化 URL、下载时间、digest 和可用响应元数据；
- 不接受服务端本地路径。

URL 内容 digest 变化时，创建新版本，不静默覆盖旧任务。

### 本地 Agent 输入

Hermes 等本机 Agent 后续可以使用 local adapter，但不能直接获得仓库目录权限。adapter 只允许配置的 inbox 目录，必须规范化路径、拒绝符号链接、等待文件写入完成、复制到私有目录并计算 digest。

目录投递只能是 adapter，不能成为主要 API。目录本身无法表达身份、幂等 key、审批和可靠回执。

## MCP 与客户端适配

### MCP 客户端

现有 MCP 继续负责已发布论文的搜索和有界读取。Phase 2 只有在 MCP transport 能解析为与 Agent API 相同的 principal，并能强制 submission/job ownership 时，才新增只读任务工具：

```text
get_pipeline_job(job_id)
search_pipeline_jobs(status?, source_ref?, limit?)
```

如果无法建立这个身份映射，Phase 2 只保留 Agent HTTP API 和 CLI 的状态查询，不添加 pipeline MCP tools。身份映射验证后，再新增高层写工具：

```text
submit_paper
request_review
```

不要把 Admin API 的每个底层 endpoint 逐个映射为 MCP tool。

### Grok、Hermes 和其他 function-calling 客户端

如果客户端不支持 MCP，提供薄的 function-calling adapter。adapter 只转换 JSON Schema、认证、错误和 `retry_after`，所有业务语义仍调用同一个 Agent HTTP API。

如果 Hermes 运行在本机，可以优先提供 localhost MCP 或 `deepresearch-flow agent` CLI。CLI 也必须调用 Agent API，不得直接串联 OCR、extract、translate 子命令。

不根据某个客户端当前版本假设其一定支持 Streamable HTTP、SSE 或 OAuth。应维护一份客户端兼容性表，以实际连接验证为准。

## 资源、费用与循环控制

写入能力上线前至少需要：

- 单次最大 PDF 数；
- 单个 PDF 最大字节数；
- 每个 principal 的并发 job 数；
- 每日 PDF 数或字节数上限，按 principal 和 UTC 日期计算；
- 模型 allowlist；
- 每个 job 的最大 retry 次数，并将计数保存在 job/submission 记录中；
- polling 最小间隔和 `retry_after`；
- 单次处理允许的 action 范围。

并发 job 上限默认包含 `queued`、`running`、`batch_waiting` 和 `needs_attention`，直到任务进入终态。在没有可靠 token cost 统计前，只提供保守的资源 admission control，不宣称精确金额预算。provider 不支持 idempotency 时，仍存在请求已被接收但结果未落盘的重复计费窗口。

## 分阶段实施

### Phase 0：契约整理

- 固定 Agent 可见的状态枚举、错误码、artifact 类型和下一动作；
- 明确 Admin API、Agent API、MCP 和 CLI 的边界；
- 增加设计级兼容性检查清单；
- 不开放 Agent 写入。

### Phase 1：Agent submission MVP

- principal、scope 和资源归属；
- `POST /agent/submissions`；
- submission/job 查询；
- retry、cancel、request-review；
- `Idempotency-Key` 和 request digest；
- 模型 allowlist、文件限制、并发限制；Phase 1 只支持 multipart PDF，不支持 URL 下载；
- Agent 禁止 publish；
- 追加式审计事件。Agent command service 和 worker 必须写入同一审计存储；step 事件由实际 worker 产生，不能由 Agent API 根据当前状态推测。

### Phase 2：MCP 与本地 Agent

- 增加 `deepresearch-flow agent` CLI 薄封装；
- 在身份映射成立后增加 `get_pipeline_job` 和 `search_pipeline_jobs`；
- 再增加 `submit_paper` 和 `request_review`；
- 受限 local inbox adapter 后置为独立子阶段；
- 验证 Grok、Hermes 和其他客户端的实际连接能力。

### Phase 3：通知和成本观测

- 出站 webhook，仅用于通知；
- HMAC 签名、时间戳和 `event_id` 去重；
- principal 级资源统计；
- provider token 使用量和保守成本估计；
- 失败、重试和重复提交的运营视图。

### Phase 4：候选论文发现

增加候选队列，而不是让 Agent 搜索结果直接触发处理：

```text
candidate → user accepts → submission → pipeline
```

### Phase 5：可选自动发布

仅在明确配置下允许，并要求 review-ready、无未处理 warning、存在有效 BibTeX 匹配或由有权 reviewer 显式确认无 BibTeX、principal 在发布 allowlist 中。默认仍关闭。

## 验收标准

### 功能

1. Agent 可以用 multipart 上传内容创建 submission，并立即获得稳定 ID。URL 下载不属于 Phase 1。
2. 重放相同 `Idempotency-Key` 不创建第二个 submission。
3. 相同 key 的不同请求返回冲突，而不是静默覆盖。
4. Agent 可以查询任务状态、当前步骤、失败原因和可执行下一动作。
5. Agent 可以重试已有 checkpoint，不需要重跑已成功的步骤。
6. Agent 可以请求人工审核，但不能直接发布。
7. 同一 Agent 不能读取或控制其他 principal 的任务。
8. Agent 写入受到文件、并发、数量和 retry 限制。

### 安全

1. 若后续启用 URL 下载，连接时和每次重定向都会拒绝内网、link-local 和 metadata 地址；Phase 1 不提供 URL 下载。
2. 服务端不接受客户端提供的内部落盘路径。
3. MCP 未暴露的 publish 能力在服务端仍被拒绝。
4. 审计事件不包含 token、provider key 或完整 prompt。
5. MCP 返回的论文内容放在明确标记为不可信数据的字段中，并限制返回长度；论文内容中的指令不会改变权限和发布状态。

### 恢复与费用

1. worker 重启后，Agent 仍可通过 submission/job ID 查询任务。
2. retry 复用现有 checkpoint 和翻译 cache。
3. 断开连接或 Agent 重启不会导致自动重复提交。
4. provider 不支持 idempotency 时的重复计费窗口在 API 文档中明确说明。
5. 超过 admission limit 的请求在创建付费任务前被拒绝。

## 暂不确定但不阻塞 Phase 0 的决定

以下选择在实现 Phase 1 前确定：

1. URL 下载是否在 Phase 3 作为独立安全设计加入；Phase 1 只支持 multipart 上传；
2. principal token 是先采用配置文件静态 token，还是直接引入持久化 token registry；
3. `Submission` 和幂等记录是扩展现有 SQLite state，还是使用同一 state 下的独立表；
4. Grok/Hermes 实际支持 Streamable HTTP、SSE、OAuth 或 function calling 的组合；
5. 首批 admission limit 的具体数值。

推荐默认值是：Phase 1 先支持 multipart + 静态 Agent token + 同一 SQLite state 的独立表；URL 下载和 OAuth 适配放到验证后加入。

## 明确不采用的方案

- Agent 直接调用 `run.sh`；
- Agent 直接写 `simple.json`、`deep_read.json`、snapshot 或队列数据库；
- 裸目录监听作为主接口；
- 为 Grok、Hermes 分别实现状态机；
- 把 publish 权限隐藏在 MCP 工具列表中而不在服务端校验；
- 在第一阶段建设跨 provider 全局 quota 调度器；
- 在没有可靠证据时宣称 exactly-once 或精确成本。
