# BGE-M3 本地嵌入部署

## 默认配置

长期记忆默认使用 Hugging Face 本地模型 `BAAI/bge-m3`，通过
`sentence-transformers` 加载，向量会进行 L2 归一化。默认最大长度为 1024
tokens、批大小为 8，适配 6 GB 本地 GPU；长研究报告应在写入长期记忆前按业务边界分块，不能依赖
模型截断保留完整语义。

```dotenv
EMBEDDINGS_PROVIDER=huggingface
EMBEDDINGS_MODEL=BAAI/bge-m3
EMBEDDINGS_DEVICE=cuda:0
EMBEDDINGS_BATCH_SIZE=16
EMBEDDINGS_MAX_LENGTH=1024
EMBEDDINGS_NORMALIZE=true
CHROMA_PERSIST_DIR=/data/deep-research/chroma_bge_m3
```

`auto` 会在 CUDA 可用时选择第一张卡，否则使用 CPU。部署环境应显式写为
`cuda:0` 或 `cuda:1`，避免设备选择随宿主机变化。

## 双 12 GB GPU 部署

单个 BGE-M3 推理实例只需一张 12 GB GPU，模型不会自动将一次编码拆到两张卡。
12 GB 部署环境可将 `EMBEDDINGS_BATCH_SIZE` 提升至 16，并在目标负载下测量峰值显存。
若需要提升并发吞吐，可以运行两个服务副本，各自绑定一张卡和独立的 Chroma
持久化目录：

```bash
EMBEDDINGS_DEVICE=cuda:0 CHROMA_PERSIST_DIR=/data/chroma_0 uvicorn src.main:app --host 0.0.0.0 --port 8000
EMBEDDINGS_DEVICE=cuda:1 CHROMA_PERSIST_DIR=/data/chroma_1 uvicorn src.main:app --host 0.0.0.0 --port 8001
```

嵌入式 Chroma 不应由多个服务副本并发写同一持久化目录。需要共享长期记忆时，
应先将向量库改为集中式服务，例如 Chroma Server 或 Qdrant，再通过负载均衡将
请求分发到两个副本。

## 索引迁移

不同嵌入模型的向量空间不能混用。保留旧的 `chroma_data` 仅用于回滚；使用
`chroma_data_bge_m3` 或新的部署目录重建所有长期记忆，确认检索结果后再清理旧
索引。
