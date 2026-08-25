# P2 用户文档 RAG 实施计划

## 前提与执行方式

本计划落实已确认的 [P2 用户文档 RAG 设计](../specs/2026-08-25-user-document-rag-design.md)。先完成身份、数据库迁移和文档生命周期，再接入解析、混合检索、研究图与前端。任何阶段未通过其自动化测试，不进入下一阶段。

P2 是一个横跨安全、持久化、检索和 UI 的新子系统，主线负责接口契约、数据库迁移、授权边界、研究图集成和最终验收。契约冻结后的纯函数分块测试、VLM provider 适配和前端组件可拆给子代理；子代理不得修改状态/权限契约，合并前由主线做全量回归。

## P2.0：依赖、配置与可序列化契约

**方式：直接实现。** 这一步定义后续所有模块共享的稳定边界。

1. 修改 `pyproject.toml`，显式加入：`python-multipart`（上传）、`PyJWT`、`pwdlib[argon2]`、`docling`、`markitdown`、`Pillow`、`FlagEmbedding` 和直接使用的 `chromadb`。锁定与当前 Python/LangChain 兼容的最低版本，不将可选 VLM 本地模型权重打进仓库。
2. 扩展 `src/config.py`：新增 `AuthConfig`、`DocumentConfig`、`DocumentVLMConfig`、`RerankerConfig` 和 `DocumentRetrievalConfig`，并在 `Config.from_env()` 中解析设计文档列出的环境变量。
3. 默认值固定为：文件 `50 MB`、用户总量 `500 MB`、回收站 `30` 天、Docling OCR 关闭、BGE-M3 嵌入、`BAAI/bge-reranker-v2-m3`。文档 Chroma 目录/集合独立于 `memory.long_term_persist_dir` 与 `research_memories`。
4. 在 `src/state.py` 将 `SCHEMA_VERSION` 升至 `3`；为 `ResearchRun` 增加 `owner_id` 与不可变 `document_scope`（允许的 `version_id`、是否来自“全部我的文档”、创建时解析时间），并为 `ResearchState` 增加同一范围。新增 Pydantic 模型只保存 ID、状态、计数和定位，不保存全文。
5. 新增 `tests/test_document_config.py` 和 `tests/test_document_state.py`，覆盖默认值、环境变量覆盖、缺失 JWT 密钥的生产配置、VLM 与 reranker 的独立配置及范围 JSON round-trip。

**验收：** 当前 BGE-M3 测试不回归；新契约不允许客户端伪造 `owner_id`；`pytest -q tests/test_embeddings.py tests/test_document_config.py tests/test_document_state.py` 通过。

## P2.1：认证、所有者迁移与受限运行接口

**方式：直接实现。** 所有后续文档工作以该权限边界为前置条件。

1. 新增 `src/auth.py`，实现密码哈希/校验、JWT 签发与验证、`CurrentUser`、Bearer 认证依赖和统一的 `401` 语义。仅在测试中允许注入假认证依赖；生产环境不得存在匿名绕过开关。
2. 扩展 `src/repository.py` 的建表与迁移逻辑：创建 `users`；为 `plans`、`runs`、`task_runs`、`report_versions` 和 `event_logs` 记录/关联 `owner_id`；对现有 SQLite 使用 `PRAGMA table_info` + 幂等 `ALTER TABLE` 迁移。旧无归属记录不分配给新用户，也不通过已认证 API 返回。
3. 将 repository 的计划、运行、恢复、取消、重试、事件读取方法改为接受 `owner_id` 并在 SQL `WHERE` 中强制过滤。`ResearchRun.owner_id` 与持久化列交叉校验，防止 JSON payload 和关系字段不一致。
4. 修改 `src/main.py`：增加 `/auth/register`、`/auth/login`、`/auth/me`；为计划、运行、流式研究、会话和历史接口注入当前用户。删除基于全局 `research_history/*.json` 的读写路径，历史面板改由当前用户的 `runs/report_versions` 提供数据。
5. 修改 `src/session.py`，为内存会话增加 `owner_id`；所有 `get/add/delete` 都校验所有者。保留内存会话只作为短期对话状态，不能作为文档授权来源。
6. 更新 `ResearchRequest`、`RunCreateRequest` 和内部 `_execute_persisted_run()` 的参数传播，让新运行在创建时获得当前用户及空的文档范围快照。
7. 新增 `tests/test_auth.py` 与 `tests/test_owned_runs.py`。覆盖重复用户名、错误密码、过期/伪造 token、跨用户的计划/运行/会话/历史返回 `404`、旧无归属数据不可见，以及同用户原有 P1.1 恢复和重试语义。

**验收：** 所有研究相关 HTTP API 均需 Bearer token；测试数据库中用户 A 无法读取、恢复、取消或重试用户 B 的任何运行。

## P2.2：文档领域、私有存储与可恢复入库队列

**方式：直接实现。** 任务状态、版本切换和删除必须由主线统一控制。

1. 新建 `src/documents/` 包：`models.py` 定义文档、版本、图片、父块、子块、任务、状态机和 API DTO；`repository.py` 使用同一个 SQLite 文件维护文档业务表与 FTS5 虚表，不直接操作 LangGraph checkpoint 表。
2. 在 `documents`、`document_versions`、`ingestion_jobs`、`document_parents`、`document_chunks`、`document_images` 建立设计文档中的字段、外键和索引。为 `owner_id + status`、`version_id + retrieval_enabled`、`deleted_at`、job 租约及 FTS 回表路径建立索引。
3. 新增 `src/documents/storage.py`：流式上传计数、扩展名/MIME/PDF 文件头/UTF-8 验证、配额计算、UUID 路径、临时文件原子移动和安全删除。磁盘路径永不由展示名拼接。
4. 实现 repository 事务：普通上传创建独立文档；显式新版本只关联指定文档；新版本成功时再原子归档旧当前版本；失败时保留旧当前版本。删除标记文档、所有版本和任务，恢复仅允许 `deleted_at + 30 天` 以内。
5. 新增 `src/documents/jobs.py`：以 `BEGIN IMMEDIATE` 或等价短事务领取任务、写入租约/尝试次数、回收过期租约、取消待运行任务和记录脱敏错误码。新增 `src/documents/worker.py`，提供单次领取和循环 CLI，供独立进程运行。
6. 在 `src/main.py` 增加认证保护的 multipart 上传、文档分页/详情、显式新版本、重试、删除、恢复、版本只读列表和容量使用 API。API 只入队，不同步解析文件。
7. 新增 `tests/test_document_repository.py`、`tests/test_document_storage.py`、`tests/test_ingestion_jobs.py` 与 API 测试。使用临时目录和 SQLite，验证配额、类型、路径安全、租约恢复、并发领取、版本原子切换和删除/恢复状态。

**验收：** API 或 Worker 重启后任务仍在 SQLite 中；相同文件名的普通上传彼此独立；删除前后 30 天内的恢复行为可重复测试。

## P2.3：PDF/Markdown 转换、视觉增强与父子分块

**方式：解析和分块单元测试可交给子代理；主线保留任务编排与失败语义。**

1. 新增 `src/documents/conversion.py`，定义统一的 `ConvertedDocument`、`BlockLocator`、`ExtractedImage`。PDF 优先调用 Docling 且显式禁用 OCR，保留页码、标题路径、表格和图片/图注关联；仅在 Docling 转换失败时调用 MarkItDown，降级产物标记其定位/图片能力受限。Markdown 直接规范化编码、标题与表格，不下载图片链接。
2. 新增 `src/documents/vision.py`，定义 VLM provider 协议与 `OpenAICompatibleVisionProvider`、`HuggingFaceVisionProvider`。输出按 schema 校验为描述、图注补全、实体、趋势和不确定性；每次调用记录模型和 prompt 指纹。未配置、超时或单图失败返回明确视觉状态，绝不调用 `OPENAI_MODEL` 作为替代。
3. 新增 `src/documents/chunking.py`。以 H2 作为逻辑父级；没有 H2 时使用文档标题根父级。过长内容按 H3 再按段落分为 `400--600` token 物理父块；在每个父块内按语义边界生成子块，重叠比例为 `10%--15%`。所有子块继承页码、标题路径、父/逻辑父 ID；视觉描述生成单独 `kind=vision` 子块并带图片关联。
4. 在 Worker 中按 `converting -> vision_enriching -> chunking` 编排以上模块。VLM 未配置或局部失败时允许文本完成，写入 `partial`/`not_configured`；转换或文本分块失败才标记版本失败。
5. 新增 `tests/test_conversion.py`、`tests/test_vision.py`、`tests/test_chunking.py`。Docling/MarkItDown/VLM 均通过 fake adapter 测试；fixture 覆盖双栏论文的页码块、表格、H2/H3、无 H2、长段落、相邻语义重叠、图片失败和视觉描述标识。

**验收：** 不依赖真实模型或 PDF 网络下载即可覆盖所有失败分支；视觉描述不能在输出中丢失“非原文”标记。

## P2.4：Chroma + FTS5 混合检索与 reranker

**方式：索引删除语义由主线实现；reranker adapter 与其 fake 测试可交给子代理。**

1. 新增 `src/documents/index.py`。为用户文档使用独立 Chroma collection，构造 BGE-M3 embedding adapter；写入/更新/删除均保存 `owner_id`、`document_id`、`version_id`、`parent_id`、`kind`、`is_deleted`、`retrieval_enabled` 和定位 metadata。SQLite 中保存 Chroma ID 与 FTS5 rowid，任何索引阶段失败都执行可重试的补偿清理。
2. 实现逻辑删除的双存储操作：Chroma `update` 批量设 `is_deleted=true`，FTS 查询回表过滤；恢复反向更新；30 天清理以可重入顺序物理删除 Chroma、FTS、元数据和私有目录。没有 Chroma “软删除” API 的假设。
3. 新增 `src/documents/retrieval.py` 与 `src/documents/reranker.py`。在授权范围内分别取向量/FTS 子块候选，使用可配置 RRF 融合，按物理父块聚合和去重，构造“父块正文 + 明确标签的视觉描述”输入，再用 `BAAI/bge-reranker-v2-m3` 精排。最终返回命中子块及相邻子块窗口。
4. reranker 以 provider、设备、batch、最大长度和 Top-K 配置为唯一来源。加载/推理失败时返回 RRF 顺序及 `reranker_status=degraded`，调用方必须把该状态写入运行事件和范围说明。
5. 新增 `tests/test_document_index.py`、`tests/test_document_retrieval.py`、`tests/test_reranker.py`。用假的 embedding、Chroma 和 reranker 覆盖 owner/version/deletion 过滤、FTS 回表、RRF 打分、父块聚合、视觉块命中、删除/恢复/清理和精排降级。

**验收：** 查询永远同时受所有者、当前可检索版本和显式允许版本限制；删除后的块不能由向量、BM25 或父块邻域路径返回。

## P2.5：研究图、来源引用和可观测性集成

**方式：直接实现。** 这是文档权限与 Agent 证据链的最终汇合点。

1. 新增 `src/tools/documents.py`，提供按运行范围绑定的文档检索工具 factory。工具不接受可绕过范围的 `owner_id` 或版本 ID 参数；从闭包中的已验证 `DocumentScope` 检索，并将 `DocumentSourceItem` 转换为现有 `SourceItem` JSON 格式。
2. 修改 `src/agents/summarizer.py` 与 `src/graph/research.py`：仅当运行范围非空时将文档检索工具加入 Summarizer 工具集；Prompt 明确区分私有文档、网页来源与视觉增强内容。并行任务共享不可变 scope，不共享可变召回结果。
3. 修改 `src/state.py`、`src/repository.py` 和 `src/main.py`：创建运行时先校验 `document_ids`，将解析后的 `version_id` 快照放入 `ResearchRun`/图 state；恢复和单任务重试复用同一快照，不重新读取当前文档列表。
4. 更新 `SourceItem.locator` 与 reporter 来源索引，生成“标题 + 页码 + 标题路径”的文档引用；Markdown 去掉页码但保留标题路径。视觉增强引用仍落回原文页码/图号，不把 VLM 文字作为原文摘录。
5. 在 `src/events.py`、`src/tracing.py` 与图节点写入可观测字段：文档数量、候选数量、RRF/reranker 状态、模型名、阶段时延和错误码。LangSmith 与本地日志不得包含完整文档、图片、Markdown、文件路径或 VLM 输出。
6. 新增 `tests/test_document_tool.py`、`tests/test_document_graph_integration.py` 和扩展 `tests/test_citations.py`。覆盖选定文档、全部我的文档、空范围、跨用户 ID、已归档/删除版本、运行快照、reranker 降级和带页码的 `SourceItem`/报告引用。

**验收：** 一个研究运行能同时引用网页和用户文档，但任何私有文档来源都能追溯到固定版本、页码/标题路径与当前用户。

## P2.6：Vue 登录、文档管理与研究选择器

**方式：前端组件可由子代理实现，主线以冻结的 OpenAPI/HTTP 契约做集成。**

1. 重构 `frontend/src/api/research.js`：增加 Axios Bearer interceptor、认证 API、multipart 上传、文档/版本/任务 API；`streamResearch()` 的 `fetch` 加入 Bearer header、文档范围和统一的 `401` 处理。
2. 新增登录/注册视图、用户状态存储、退出登录和过期 token 清理。后端恢复 `401` 后前端清空本地 token 与会话状态，不伪装为网络错误。
3. 新增文档管理与上传组件：展示容量、当前版本、处理阶段、视觉部分失败、失败重试、归档版本只读、逻辑删除和恢复剩余时间。上传新版本必须从某个文档详情入口触发，普通上传不按文件名合并。
4. 在研究输入区域加入文档选择器：勾选当前用户 `ready` 文档，或显式启用“全部我的文档”；处理/归档/删除文档不可选择。将选择结果随 `/research`、`/research/stream`、`/plans` 和 `/runs` 请求传递。
5. 更新 `frontend/src/App.vue`、`HistoryPanel.vue` 与相关组件，使历史只展示当前用户的持久化运行/报告；删除对无归属文件历史接口的依赖。保持现有 SSE 任务进度显示，并补充文档检索/精排降级状态。
6. 执行 `npm run build`，手工验证桌面和移动布局中的长文档名、错误文本、选择器和上传状态不会溢出或遮挡。

**验收：** 新用户可注册、登录、上传论文、等待完成、选择论文进行研究、查看带定位的引用、删除并在 30 天内恢复；退出后不能继续从前端读取受保护数据。

## P2.7：回归、真实论文验收与提交策略

**方式：直接实现。** 仅在端到端边界通过后更新项目能力说明。

1. 将所有新的自动化测试纳入 `pytest`；外部 Docling、VLM、Chroma 和 reranker 在单元测试中使用 fake，在受控集成测试中使用临时目录/集合。运行全量 `pytest -q`，修复 P0/P1 回归。
2. 使用一篇本地双栏、含表格和图片的原生论文做手工验收：确认 Docling 产物页码/标题路径、全部图片的 VLM 状态、文本与图表问题的召回、reranker 正常和降级路径、引用格式、删除/恢复/清理以及跨账号隔离。
3. 审查 SQLite、Chroma、私有目录、普通日志和 LangSmith Trace：不应出现未授权访问、完整私有正文、图片、认证头、API Key 或可直接暴露的文件路径。
4. 按原子能力提交：配置/契约，认证与迁移，文档生命周期，解析分块，索引检索，研究图集成，前端闭环，最终验收。每次提交前仅暂存本阶段文件，不包含既有 `frontend/dist`、本地 Chroma、历史记录、评测输出或其他无关脏文件。

**完成标准：** 全量自动化测试通过，真实论文手工验收留有可复现记录，所有私有文档检索均经服务端授权过滤，逻辑删除和 30 天物理清理均经过回归测试。届时方可按设计规格中的简历表述对外描述该能力。
