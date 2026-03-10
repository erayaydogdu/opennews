# Step1：图谱构建（GraphRAG + 实时种子注入）

已完成能力：
- 新闻抓取（RSS）+ 去重 + checkpoint 增量处理
- Fin 模型嵌入（默认 `ProsusAI/finbert`，可换 FinRoBERTa/DeBERTa）
- 实体抽取（Transformers NER）
- BERTopic 在线更新（优先 `partial_fit`）
- Neo4j 图谱写入（News/Entity/Topic + MENTIONS/IN_TOPIC/IMPACTS）
- 每 5 分钟轮询调度（可配置）
- 实时种子注入（`seeds/realtime_seeds.jsonl`）

## 目录

- `src/opennews/workflow/langgraph_pipeline.py`：LangGraph 主流程
- `src/opennews/ingest/`：抓取、checkpoint、seed 注入
- `src/opennews/nlp/`：嵌入和实体抽取
- `src/opennews/topic/`：BERTopic 在线主题模块
- `src/opennews/graph/`：Neo4j schema 与 upsert
- `src/opennews/scheduler/polling_job.py`：5 分钟轮询

## 安装依赖

```bash
pip install -r requirements-step1.txt
```

## 环境变量

```bash
export NEWS_POLL_INTERVAL_MIN=5
export BATCH_SIZE=32
export EMBEDDING_MODEL=ProsusAI/finbert
export NER_MODEL=dslim/bert-base-NER

export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=neo4j

export NEWS_SOURCES="https://feeds.reuters.com/reuters/businessNews"
export CHECKPOINT_FILE=seeds/checkpoint.json
```

## 启动 Neo4j（先做这一步）

如果你本机没有 Neo4j，直接用 Docker：

```bash
docker compose -f docker-compose.neo4j.yml up -d
```

启动后可访问：
- Neo4j Browser: `http://localhost:7474`
- Bolt: `127.0.0.1:7687`

## 运行

```bash
PYTHONPATH=src python -m opennews.main
```

> 现在代码已做容错：即使 Neo4j 暂时没启动，服务也不会在导入阶段崩溃，会跳过该轮写图并继续轮询。

## 实时种子注入格式

向 `seeds/realtime_seeds.jsonl` 追加 JSON 行：

```json
{"news_id":"seed-001","title":"Apple expands AI investment","content":"...","source":"seed","url":"seed://seed-001","published_at":"2026-03-09T07:30:00+00:00"}
```

下一轮轮询会自动读入并入图。
