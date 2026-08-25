# P2 用户文档 RAG 设计

## 目标

在现有 LangGraph 深度研究系统中增加受认证保护的个人文档知识库。用户可上传 PDF 或 Markdown，等待异步解析和索引完成后，在发起研究时选择具体文档或“全部我的文档”。系统必须保证文档、索引、检索结果和研究引用均不能跨用户访问，并使文档删除、版本归档和恢复具备明确语义。

首期面向原生文本型学术论文：PDF 可能包含双栏排版、表格和不超过约 20 张有效图片，但不是扫描件。系统以文本检索为主，通过离线视觉增强补足图片语义，而不是将全文统一交给 VLM。

## 范围与非目标

首期范围：

- 用户名和密码自注册、JWT 登录，以及后端强制的 `owner_id` 隔离；
- PDF、Markdown 上传，私有文件存储、异步转换、分块、索引、重试和状态展示；
- PDF 图片的离线 VLM 描述、检索命中后的按需 VLM 复核；
- BGE-M3 子块向量检索、SQLite FTS5 BM25、RRF 融合和父块 reranker；
- 显式上传新版本、逻辑删除、30 天回收站和物理清理；
- Vue 前端的注册/登录、上传、文档管理、删除/恢复及研究文档选择闭环。

以下内容不在首期范围：网页 URL 导入、扫描件 OCR、Markdown 图片包或 ZIP 导入、文档分享/协作、管理员后台、密码找回和第三方登录、PDF 页内预览、Redis/Kafka、分布式 Worker 与生产级多租户配额。

## 架构选择

采用单机、文本优先的渐进式多模态 RAG：FastAPI 单体服务、SQLite/FTS5、Chroma、私有本地文件目录、Docling 和独立入库 Worker。该方案复用现有 SQLite、Chroma、BGE-M3、FastAPI 和 Vue 技术栈，能够可靠处理当前的论文规模，不因低频文档入库引入 Redis 或 Kafka 的部署复杂度。

入库任务不使用 FastAPI `BackgroundTasks` 作为唯一执行机制。上传请求只负责持久化文件、文档元数据和任务；独立 Worker 从 SQLite 领取任务。因此 API 或 Worker 重启后，未完成任务仍可重试和恢复。Redis + Celery/Dramatiq 只在未来有多 Worker 并发和独立扩缩容需求时替换任务执行层；Kafka 仅在出现跨系统事件订阅需求时再评估。

```text
Vue -> FastAPI 认证/文档 API -> SQLite: 用户、版本、任务、元数据、FTS5
                                  -> 私有文件目录: 原件、Markdown、提取图片
独立 Worker -> Docling -> VLM 增强 -> 父子分块 -> Chroma + FTS5
研究图 -> 文档检索工具 -> 向量/BM25 -> RRF -> 父块 reranker -> 带定位的证据
```

## 身份、权限与文件安全

`users` 表至少包含 `user_id`、唯一 `username`、强密码哈希和创建时间。密码只保存 Argon2/bcrypt 等自适应哈希结果；JWT 的 `sub` 固定为 `user_id`，认证依赖统一的 FastAPI 依赖项。JWT 密钥、算法和有效期均通过环境变量配置；生产环境缺失强密钥时服务不得启动。

文档接口从 JWT 获取当前用户，而不接受客户端传入的 `owner_id`。读取、上传新版本、删除、恢复、重试、查询任务和检索范围校验都必须以当前用户为条件；不属于该用户的资源统一返回 `404`，避免枚举存在性。研究运行保存发起者 `owner_id` 与实际允许的版本 ID，后续恢复、重试和引用读取同样复用该边界。

上传时服务端流式计数并执行以下检查：

- 只接受 PDF 与 UTF-8 Markdown，扩展名、声明 MIME 和文件头必须一致；
- 单文件上限 `50 MB`，每用户活跃及回收站占用合计上限 `500 MB`，两者均配置化；
- 原始文件名只作为展示信息，磁盘路径由 UUID 生成；禁止路径穿越、符号链接跟随和直接以用户文件名落盘；
- 写入临时路径后原子移动至私有目录，未完成写入不得创建可索引版本；
- Markdown 首期解析文本、标题和表格；相对路径或网络图片链接仅保留原文，不下载、不渲染、不发送给 VLM。

文件存储目录固定为 `<DOCUMENT_STORAGE_ROOT>/<owner_id>/<document_id>/<version_id>/`，保存原文件、标准化 Markdown、图片和转换清单。该目录不通过静态文件服务暴露。

## 数据模型与生命周期

SQLite 新增以下业务表；已有 `runs`、`plans`、`event_logs` 保持兼容，只在需要时增加文档选择和引用快照字段。

| 表 | 核心字段 | 职责 |
| --- | --- | --- |
| `users` | `user_id`, `username`, `password_hash` | 认证主体 |
| `documents` | `document_id`, `owner_id`, `title`, `current_version_id`, `deleted_at` | 用户可见的逻辑文档 |
| `document_versions` | `version_id`, `document_id`, `version_number`, `source_sha256`, `source_path`, `markdown_path`, `converter_fingerprint`, `index_fingerprint`, `status`, `is_current`, `retrieval_enabled` | 原件、转换与版本状态 |
| `ingestion_jobs` | `job_id`, `version_id`, `status`, `attempt`, `lease_until`, `stage`, `error_code` | 可恢复的异步入库任务 |
| `document_parents` | `parent_id`, `version_id`, `logical_heading_path`, `physical_index`, `text`, `locator` | 物理父块及其定位 |
| `document_chunks` | `chunk_id`, `parent_id`, `kind`, `text`, `page_start`, `page_end`, `chroma_id`, `fts_rowid` | 子块、视觉增强块和索引映射 |
| `document_images` | `image_id`, `version_id`, `parent_id`, `page`, `path`, `caption`, `vision_status`, `vision_metadata` | 图片及其视觉增强状态 |

`document_versions.status` 使用 `queued`、`processing`、`ready`、`failed`、`archived`、`deleted`。视觉增强单独使用 `pending`、`succeeded`、`partial`、`failed`、`not_configured`，不能因个别图片失败使已完成文本索引的版本失败。

普通上传即使文件同名也创建独立 `document_id`。只有用户在某一文档详情中显式点击“上传新版本”时，才创建关联的 `version_id`。旧版本在新版本成功索引后才原子转为 `archived`、`is_current=false`、`retrieval_enabled=false`；新版本失败时，原当前版本继续可检索。首期不提供“将旧版本恢复为当前版本”的操作，历史版本仅用于审计和已完成研究的可复现引用。

删除是文档级逻辑删除：`documents.deleted_at` 写入时间，所有版本设为 `deleted` 并禁止新的检索任务使用。Chroma 没有内建回收站，块元数据因此保存 `owner_id`、`document_id`、`version_id`、`is_deleted` 和 `retrieval_enabled`；逻辑删除时批量更新为 `is_deleted=true`，SQLite/FTS5 查询同时过滤删除和归档状态。恢复在 30 天内反转这些标记，并恢复删除前的当前版本。清理任务只处理 `deleted_at` 超过 30 天的文档：先物理删除 Chroma 记录和 FTS 行，再删除私有文件与业务元数据。清理必须可重入并记录每一步，避免半清理状态泄露到检索。

## 异步入库与转换

上传成功后 API 返回文档和版本 ID，状态为 `queued`；前端轮询文档详情或任务状态，不要求 WebSocket。Worker 以短事务原子领取任务并设置租约，状态顺序为：`queued -> converting -> vision_enriching -> chunking -> indexing -> ready`。Worker 异常、租约到期或服务重启后，任务重新进入可领取状态；超过最大尝试次数进入 `failed`，用户可显式重试。删除请求会取消尚未开始的任务，并要求正在执行的阶段在写入索引前检查删除状态。

PDF 优先由 Docling 转换为 Markdown，明确关闭 OCR。转换产物必须保留页码、标题层级、表格 Markdown/HTML、图片文件与图注的关联。Docling 在转换阶段失败时才用 MarkItDown 做文本级降级；降级结果无法可靠建立图片关联时，文档以文本就绪、视觉状态 `partial` 完成。转换、图片处理与索引均有单阶段超时，错误以脱敏的错误码和摘要保存，不记录原始私有正文到普通日志或 LangSmith。

论文中提取的所有图片均提交 VLM，不设置自动张数上限。VLM 输出固定为结构化视觉描述、图注补全、关键实体、趋势和不确定性说明，另存为 `kind=vision` 的增强子块，并以 `image_id`、页码和父块关联。视觉描述永远标记为“视觉增强，非原文”，不能作为伪造的 PDF 原文或页码事实；最终引用仍指向原 PDF 页码、图号和标题路径。若视觉块成为主要命中或问题明显询问图表细节，检索阶段只对 Top-K 原图按需调用 VLM 复核。

## 父子分块与索引

逻辑父级采用 Markdown 二级标题，并保留一级标题至当前标题的完整路径。没有二级标题的文档或导言内容，以文档标题建立稳定的根逻辑父级。二级章节过长时，优先按三级标题拆分，否则按自然段边界拆为约 `400--600 tokens` 的物理父块。这一尺度既能容纳论文中方法或实验小节的完整论证，又避免将过大的章节整体送入 reranker 和生成模型。

每个物理父块内进行语义子分块，连续块保留 `10%--15%` token 重叠。子块继承 `owner_id`、文档/版本 ID、逻辑标题路径、物理父块 ID、页码范围和定位信息。文本子块与视觉增强子块分别索引；视觉块保留其关联父块和图片路径。父块文本用于精排时，拼接父块正文及明确标记的关联视觉描述，避免图表相关查询因父块正文缺失而被误排。

Chroma 新建独立的用户文档集合，不复用 `research_memories`。BGE-M3 是默认嵌入模型，沿用现有 `EMBEDDINGS_*` 配置；文档检索与对话长期记忆使用不同集合、不同 metadata 契约。FTS5 保存与 Chroma 对应的可检索子块文本，查询结果必须回表验证版本、所有者、当前状态和删除状态，不能仅信任 FTS 命中。

## 检索、精排与研究图集成

用户发起研究时明确选择允许的 `document_ids`，前端提供“全部我的文档”这一显式选项。服务端把它解析为当前用户所有 `ready + is_current + retrieval_enabled` 文档版本；空选择不执行用户文档检索。客户端提交的每个 ID 必须先验证归属，最终查询始终叠加 `owner_id`、版本状态、`is_deleted=false` 和允许版本范围。

检索固定按以下顺序执行：

```text
查询
  -> 子块 BGE-M3 向量 Top-N + 子块 FTS5 BM25 Top-N
  -> RRF 融合
  -> 按物理父块聚合、去重，保留最佳命中子块
  -> 物理父块 reranker
  -> Top 父块 + 命中子块邻域交给 Summarizer/Reporter
```

向量、BM25、RRF、候选父块、reranker Top-K 和邻域窗口均配置化。RRF、聚合和 reranker 的分数与命中子块 ID 写入本地运行事件，供诊断和离线评测使用。Reranker 默认使用开源 `BAAI/bge-reranker-v2-m3`，对物理父块进行精排；加载或推理失败时，保留 RRF 父块排序，并在事件、任务产物和最终报告范围说明中明确标记“未执行精排”，不静默改用其他模型。

文档检索以独立工具接入既有研究图，输出已存在的来源/结论契约可接受的 `SourceItem` 与证据片段。每个文档来源保存 `document_id`、`version_id`、文档标题、页码范围、标题路径、父块/子块 ID 和内容类型。报告引用格式为“文档标题 + 页码 + 标题路径”；Markdown 无页码时显示标题路径。首期点击引用只进入授权后的文档详情和定位信息，不实现 PDF 在线页内预览。

## 模型与配置

VLM 与 `OPENAI_MODEL` 及研究图的角色模型独立。它支持 OpenAI-compatible/GPT 或本地 HuggingFace provider；如果 VLM 未配置，图片视觉状态为 `not_configured`，文本文档仍可就绪，绝不把普通文本模型当作 VLM 回退。

```text
DOCUMENT_VLM_PROVIDER
DOCUMENT_VLM_API_KEY
DOCUMENT_VLM_BASE_URL
DOCUMENT_VLM_MODEL
DOCUMENT_VLM_MAX_TOKENS
DOCUMENT_VLM_TIMEOUT_SECONDS

RERANKER_PROVIDER
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_DEVICE
RERANKER_BATCH_SIZE
RERANKER_MAX_LENGTH
RERANKER_TOP_K

DOCUMENT_STORAGE_ROOT
DOCUMENT_MAX_FILE_BYTES=52428800
DOCUMENT_USER_QUOTA_BYTES=524288000
DOCUMENT_JOB_LEASE_SECONDS
DOCUMENT_JOB_MAX_ATTEMPTS
DOCUMENT_PURGE_RETENTION_DAYS=30
DOCUMENT_CONVERTER=docling
DOCUMENT_DOCLING_OCR_ENABLED=false
DOCUMENT_MARKITDOWN_FALLBACK_ENABLED=true
DOCUMENT_PARENT_TARGET_TOKENS
DOCUMENT_CHILD_OVERLAP_RATIO
DOCUMENT_VECTOR_TOP_K
DOCUMENT_BM25_TOP_K
DOCUMENT_RRF_K
```

认证还需 `AUTH_JWT_SECRET`、`AUTH_JWT_ALGORITHM` 和 `AUTH_ACCESS_TOKEN_MINUTES`。所有密钥只从环境变量读取。LangSmith 遵循 P1.2 的默认脱敏策略：不得上传完整用户文档、转换 Markdown、图片、VLM 响应、认证头或文件路径；仅允许记录已脱敏的运行 ID、阶段、模型名、时延、计数、状态和错误码。

## API 与前端范围

认证 API 包含注册、登录、当前用户信息；文档 API 包含上传、分页列表、详情/状态、显式上传新版本、失败重试、逻辑删除、恢复、历史版本只读列表和授权后的引用定位信息。所有响应返回稳定 ID、显示名、当前版本、转换/视觉/索引状态、容量使用和可读错误摘要。

研究计划或执行 API 在兼容现有语义的前提下接收 `document_ids` 或显式 `use_all_my_documents`。后端将解析后的允许版本快照固化进运行记录，而非在运行中动态读取“当前文档”，从而使已完成研究能解释实际检索范围。

Vue 首期增加登录状态、注册/登录页、文档管理页和研究选择器。文档管理支持上传、处理中阶段、失败原因/重试、当前版本标识、归档版本只读展示、删除与 30 天内恢复。研究选择器只展示当前用户的 `ready` 文档，提供勾选、全选我的文档以及不可用状态提示。

## 错误处理与验收

关键错误必须显式化：不支持类型、文件过大、配额不足、身份失效、跨用户访问、转换失败、VLM 部分失败、向量/FTS 写入失败、reranker 降级、任务租约超时、文档已删除和版本未就绪。任何失败不得返回看似成功的检索或无证据结论。

自动化测试至少覆盖：

- 密码哈希、JWT、跨用户文档/API/检索拒绝，以及研究运行范围快照；
- 文件类型、大小、配额和路径安全校验；
- Docling 成功、Docling 到 MarkItDown 降级、转换失败、VLM 未配置和单图片失败；
- 二级标题逻辑父级、长章节物理父块、语义子块重叠、表格/页码/标题路径继承；
- Chroma 与 FTS5 双路召回、RRF、父块聚合、reranker 输入和 reranker 降级；
- 同名独立上传、显式新版本原子切换、旧版本归档、逻辑删除/恢复和 30 天物理清理；
- 删除后 Chroma、FTS5、数据库查询及研究图均不再返回该文档；
- API 与前端主流程：注册、上传、等待就绪、选择文档发起研究、查看带页码/标题路径的引用。

手工验收使用至少一篇双栏、含图片和表格的原生论文，确认页码与标题路径可追溯、图片描述不被表述为原文、删除后不可检索、恢复后可检索，并检查 LangSmith 与普通日志不含用户私有内容。

## 完成定义

只有在上述代码、测试、真实论文手工验收和隐私边界均完成后，项目才可在简历中表述为“支持用户隔离、文档生命周期管理和混合检索精排的多模态论文 RAG 深度研究系统”。在此之前，设计中的能力不得作为已实现事实对外描述。
