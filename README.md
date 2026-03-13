# OpenNews — 实时金融新闻知识图谱 & 影响评估系统

基于 LangGraph 编排的金融新闻处理流水线。自动从 NewsNow 接口抓取新闻，完成 NER 实体抽取、主题聚类、零样本分类、7 维特征提取、时序记忆聚合、DK-CoT 影响评分，并将结果写入 Neo4j 知识图谱。

## 架构概览

```
NewsNow/种子新闻 → fetch → embed → extract_entities ─┬→ topics ──────────┐
                                                  ├→ classify ────────┤
                                                  └→ extract_features ┘
                                                          ↓
                                                    build_payload → dump_output
                                                          ↓
                                                    memory_ingest → update_trends
                                                          ↓
                                                       report → write_graph → END
```

流水线中 `extract_entities` 之后三路并行（BERTopic 主题聚类 / DeBERTa 零样本分类 / 7 维特征提取），汇聚后依次完成时序记忆写入、趋势聚合、DK-CoT 影响评分，最终持久化到 Neo4j。

---

## 依赖服务

系统运行需要以下外部服务。Neo4j 和 Redis 均支持不可用时自动降级，不会导致流水线崩溃。

| 服务 | 用途 | 必需 | 默认地址 |
|------|------|------|----------|
| PostgreSQL | 批次数据 & 报告持久化 | **是** | `127.0.0.1:5432` |
| Neo4j | 知识图谱存储 | 否（不可用时跳过图谱写入） | `bolt://127.0.0.1:7687` |
| Redis | 时序记忆存储（30 天滚动窗口） | 否（不可用时 fallback 到内存） | `redis://127.0.0.1:6379/0` |

### 启动 PostgreSQL

流水线和 Web 面板均依赖 PostgreSQL 存储批次数据。需要提前创建数据库：

```bash
# 方式一：Docker
docker run -d --name opennews-pg -p 5432:5432 \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=123456 \
  -e POSTGRES_DB=opennews \
  postgres:16

# 方式二：已有 PostgreSQL 实例，手动建库
psql -U postgres -c "CREATE DATABASE opennews;"
```

默认连接参数（可通过环境变量覆盖）：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `PG_HOST` | `127.0.0.1` | PostgreSQL 主机 |
| `PG_PORT` | `5432` | PostgreSQL 端口 |
| `PG_USER` | `postgres` | 用户名 |
| `PG_PASSWORD` | `123456` | 密码 |
| `PG_DATABASE` | `opennews` | 数据库名 |

> 表结构（`batches`、`batch_records`、`reports`）在首次运行时自动创建，无需手动建表。

### 启动 Neo4j

```bash
cd docker/neo4j
docker compose up -d
```

默认账号 `neo4j`，密码 `Aa123456`。运行时需通过环境变量对齐：

```bash
export NEO4J_PASSWORD=Aa123456
```

Neo4j Browser 访问地址：http://localhost:7474

### 启动 Redis

```bash
# 方式一：Docker
docker run -d --name opennews-redis -p 6379:6379 redis:7

# 方式二：系统安装
sudo apt install redis-server && sudo systemctl start redis
```

---

## 安装

```bash
# 克隆项目
git clone <repo-url> opennews && cd opennews

# 创建虚拟环境
python3.10 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

首次运行时会自动下载以下 HuggingFace 模型（共约 1.5GB）：

| 模型 | 用途 | 大小 |
|------|------|------|
| `ProsusAI/finbert` | 金融文本嵌入 (768 维) + BERTopic | ~440MB |
| `dslim/bert-base-NER` | 命名实体识别 | ~430MB |
| `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` | 零样本分类 + 特征提取 | ~440MB |

---

## 运行

```bash
# 基本启动（每 5 分钟轮询一次）
PYTHONPATH=src NEO4J_PASSWORD=Aa123456 python -m opennews.main
```

启动后会立即执行一轮流水线，之后按间隔自动轮询。

### 环境变量

所有配置均可通过环境变量覆盖：

```bash
# 示例：调整轮询间隔、禁用报告生成
PYTHONPATH=src \
  NEO4J_PASSWORD=Aa123456 \
  NEWS_POLL_INTERVAL_MIN=10 \
  REPORT_ENABLED=false \
  python -m opennews.main
```

完整配置项：

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `NEWS_POLL_INTERVAL_MIN` | 轮询间隔（分钟） | `5` |
| `BATCH_SIZE` | 每轮最大抓取条数 | `32` |
| `EMBEDDING_MODEL` | 嵌入模型 | `ProsusAI/finbert` |
| `NER_MODEL` | NER 模型 | `dslim/bert-base-NER` |
| `NEO4J_URI` | Neo4j 连接地址 | `bolt://127.0.0.1:7687` |
| `NEO4J_USER` | Neo4j 用户名 | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j 密码 | `neo4j` |
| `CLASSIFIER_MODEL` | 零样本分类模型 | `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` |
| `CLASSIFIER_LABELS` | 分类标签（逗号分隔） | `financial_market,policy_regulation,company_event,macro_economy,industry_trend` |
| `REDIS_URL` | Redis 连接地址 | `redis://127.0.0.1:6379/0` |
| `MEMORY_WINDOW_DAYS` | 时序记忆窗口（天） | `30` |
| `REPORT_ENABLED` | 是否生成影响评估报告 | `true` |
| `REPORT_WEIGHT_STOCK` | 股价相关性权重 | `0.40` |
| `REPORT_WEIGHT_SENTIMENT` | 市场情绪权重 | `0.20` |
| `REPORT_WEIGHT_POLICY` | 政策风险权重 | `0.20` |
| `REPORT_WEIGHT_SPREAD` | 传播广度权重 | `0.20` |
| `CHECKPOINT_FILE` | 增量检查点文件路径 | `seeds/checkpoint.json` |
| `NEWSNOW_API_URL` | NewsNow 数据接口地址 | `https://newsnow.busiyi.world/api/s/entire` |
| `NEWSNOW_SOURCES` | NewsNow 数据源 ID（逗号分隔） | `wallstreetcn-news` |

---

## 输入新闻数据

系统支持两种新闻输入方式。

### 方式一：NewsNow 接口（自动抓取）

系统默认从 [NewsNow](https://github.com/x-developer/newsnow) 格式接口抓取新闻。通过环境变量配置接口地址和数据源：

```bash
# 自定义接口地址（默认为公共实例）
export NEWSNOW_API_URL="https://newsnow.busiyi.world/api/s/entire"

# 配置数据源 ID，多个用逗号分隔
export NEWSNOW_SOURCES="wallstreetcn-news,cls-telegraph"
```

默认已配置华尔街见闻（`wallstreetcn-news`）。可自行部署 NewsNow 实例并接入更多数据源。

### 方式二：种子文件注入（手动 / 批量）

将新闻写入 `seeds/realtime_seeds.jsonl`，每行一条 JSON，系统每轮会自动读取并处理。

**格式要求**（JSONL，每行一个 JSON 对象）：

```jsonl
{"news_id":"seed-001","title":"Fed hints at slower rate cuts","content":"Officials signal a cautious approach amid sticky inflation.","source":"seed","url":"seed://seed-001","published_at":"2026-03-09T07:30:00+00:00"}
{"news_id":"seed-002","title":"NVIDIA signs major cloud AI chip deal","content":"Large hyperscaler partnership may boost semiconductor supply chain.","source":"seed","url":"seed://seed-002","published_at":"2026-03-09T07:35:00+00:00"}
```

**字段说明**：

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `news_id` | string | 是 | 唯一标识，建议用 `seed-` 前缀 |
| `title` | string | 是 | 新闻标题 |
| `content` | string | 否 | 新闻正文，缺省时使用 title |
| `source` | string | 否 | 来源标识，默认 `"seed"` |
| `url` | string | 否 | 原文链接，默认 `seed://{news_id}` |
| `published_at` | string | 否 | ISO 8601 时间戳，默认当前 UTC 时间 |

注意事项：
- 文件编码必须为 UTF-8
- 每行必须是合法的 JSON 对象，不支持多行 JSON
- `published_at` 必须晚于上次处理的检查点时间，否则会被去重跳过
- 如需重新处理所有种子，删除 `seeds/checkpoint.json` 即可

---

## 输出数据

系统产生三类输出：本地 JSON 文件、Markdown 报告、Neo4j 图谱。

### 1. 批次结果文件

每轮处理完成后输出到 `output/batch_{timestamp}.json`。

```
output/
├── batch_20260309_073500.json
├── batch_20260309_074000.json
└── ...
```

单条记录结构：

```json
{
  "news": {
    "news_id": "seed-001",
    "title": "Fed hints at slower rate cuts",
    "content": "Officials signal a cautious approach...",
    "source": "seed",
    "url": "seed://seed-001",
    "published_at": "2026-03-09T07:30:00+00:00",
    "embedding_preview": [0.012, -0.034, ...]
  },
  "entities": [
    {"entity_id": "a1b2c3", "name": "Fed", "type": "ORG", "confidence": 0.98}
  ],
  "topic": {
    "topic_id": 0,
    "probability": 0.85,
    "label": "inflation, rates, fed, economy, monetary"
  },
  "impacts": [
    {"src": "a1b2c3", "dst": "d4e5f6", "weight": 0.92}
  ],
  "classification": {
    "category": "financial_market",
    "confidence": 0.83,
    "all_scores": {"financial_market": 0.83, "policy_regulation": 0.10, "...": "..."}
  },
  "features": {
    "market_impact": 2.71,
    "price_signal": 4.55,
    "regulatory_risk": 3.19,
    "timeliness": 2.85,
    "impact": 3.17,
    "controversy": 3.24,
    "generalizability": 4.74,
    "impact_score": 3.65
  },
  "report": {
    "final_score": 62.5,
    "impact_level": "中"
  }
}
```

### 2. 影响评估报告

当 `REPORT_ENABLED=true` 时，输出到 `output/reports/`：

```
output/reports/
├── report_20260309_073500_0_中.md      # 单条 Markdown 报告
├── report_20260309_073500_1_高.md
├── summary_20260309_073500.json        # 本轮汇总
└── ...
```

Markdown 报告包含：DK-CoT 四维评分表、推理过程、趋势上下文、可视化建议。

汇总 JSON 结构：

```json
[
  {
    "news_id": "seed-001",
    "final_score": 62.5,
    "impact_level": "中",
    "dk_cot_scores": {
      "stock_relevance": 80.3,
      "market_sentiment": 57.6,
      "policy_risk": 43.4,
      "spread_breadth": 51.0
    },
    "viz_suggestions": ["雷达图: 四维评分对比...", "时序折线图: ..."]
  }
]
```

### 3. Neo4j 知识图谱

Neo4j 可用时，所有数据写入图谱。通过 Neo4j Browser (http://localhost:7474) 或 Cypher 查询。

**节点类型**：

| 标签 | 关键属性 |
|------|----------|
| `:News` | `news_id`, `title`, `category`, `impact_score`, `final_impact_score`, `impact_level` |
| `:Entity` | `entity_id`, `name`, `type` |
| `:Topic` | `topic_id`, `label`, `trend_direction`, `avg_impact` |

**关系类型**：

| 关系 | 说明 |
|------|------|
| `(News)-[:MENTIONS]->(Entity)` | 新闻提及实体 |
| `(News)-[:IN_TOPIC]->(Topic)` | 新闻所属主题 |
| `(Entity)-[:IMPACTS]->(Entity)` | 实体间影响关系 |

**常用查询示例**：

```cypher
-- 查看所有高影响新闻
MATCH (n:News) WHERE n.impact_level = '高'
RETURN n.title, n.final_impact_score, n.category
ORDER BY n.final_impact_score DESC

-- 按得分筛选新闻（≥60 分）
MATCH (n:News) WHERE n.final_impact_score >= 60
RETURN n.title, n.final_impact_score, n.impact_level, n.category

-- 查看某主题下的所有新闻和实体
MATCH (n:News)-[:IN_TOPIC]->(t:Topic {topic_id: 0})
OPTIONAL MATCH (n)-[:MENTIONS]->(e:Entity)
RETURN n.title, n.final_impact_score, collect(e.name) AS entities

-- 查看主题趋势
MATCH (t:Topic) WHERE t.trend_direction IS NOT NULL
RETURN t.topic_id, t.label, t.trend_direction, t.avg_impact, t.total_news_count
ORDER BY t.avg_impact DESC

-- 查看实体间影响网络
MATCH (e1:Entity)-[r:IMPACTS]->(e2:Entity)
RETURN e1.name, e2.name, r.weight
ORDER BY r.weight DESC LIMIT 20
```

### 4. Redis 时序记忆

通过 `redis-cli` 查看：

```bash
# 查看所有主题的记忆键
redis-cli KEYS "opennews:memory:topic:*"

# 查看某主题最近 10 条记忆
redis-cli ZREVRANGE "opennews:memory:topic:0" 0 9
```

---

## 项目结构

```
opennews/
├── src/opennews/
│   ├── main.py                        # 入口
│   ├── config.py                      # 全局配置
│   ├── agents/
│   │   ├── classifier_agent.py        # 零样本分类 (DeBERTa)
│   │   ├── feature_agent.py           # 7 维特征提取
│   │   ├── memory_agent.py            # 时序聚合
│   │   └── report_agent.py            # DK-CoT 影响评分 + 报告
│   ├── graph/
│   │   ├── neo4j_client.py            # Neo4j 连接 & 写入
│   │   ├── upsert.py                  # GraphPayload 构建
│   │   └── subgraph_query.py          # 子图查询 & 社区检测
│   ├── ingest/
│   │   ├── news_fetcher.py            # NewsNow API 抓取
│   │   ├── checkpoint.py              # 增量检查点
│   │   └── seed_injector.py           # JSONL 种子注入
│   ├── memory/
│   │   └── __init__.py                # Redis 时序存储
│   ├── nlp/
│   │   ├── embedder.py                # FinBERT 嵌入
│   │   └── entity_extractor.py        # NER 实体抽取
│   ├── topic/
│   │   └── online_topic_model.py      # BERTopic 在线聚类
│   ├── scheduler/
│   │   └── polling_job.py             # APScheduler 定时任务
│   └── workflow/
│       └── langgraph_pipeline.py      # LangGraph DAG 主流程
├── seeds/
│   └── realtime_seeds.jsonl           # 种子新闻
├── output/                            # 批次结果 & 报告输出
├── docker/neo4j/docker-compose.yml    # Neo4j 容器配置
└── requirements.txt                    # Python 依赖
```

---

## Web 可视化面板

项目内置了一个前端面板，用于浏览按主题分组的新闻及其影响评分。

### 启动

```bash
python web/server.py --port 8080
```

浏览器打开 http://localhost:8080 即可。

> 内置服务器同时提供前端静态文件和 `/api/*` 数据接口，前端通过 API 从 PostgreSQL 实时读取生产数据。

### 功能

- 顶部折线图展示 0-100 分区间的新闻数量分布
- 双端滑块筛选分数范围，图表和列表实时联动
- 新闻按主题（Topic）分组为可折叠卡片，点击展开查看子新闻列表
- 点击任意新闻条目，右侧滑出详情面板，展示：
  - DK-CoT 四维评分条形图（股价相关性 / 市场情绪 / 政策风险 / 传播广度）
  - 7 维特征网格
  - 分类置信度分布
  - 识别实体列表
  - 推理过程原文
- 自动加载最新批次数据，每 30 秒轮询刷新
- 下拉菜单可切换历史批次
- 无数据时提示用户运行后端流水线
- 支持导入本地 JSON 文件

### 数据源

面板启动后自动从 PostgreSQL 加载最新批次数据，每 30 秒轮询刷新。如果数据库中暂无数据，页面会提示「暂无数据 — 请先运行后端流水线产出批次数据」。

也可通过底部下拉菜单手动切换历史批次，或点击「导入 JSON」按钮加载本地文件。

### API 接口

内置服务器提供以下 API（供前端或外部工具调用）：

| 接口 | 说明 |
|------|------|
| `GET /api/batches` | 列出所有批次（按时间倒序），返回 `[{batch_id, batch_ts, record_count, created_at}]` |
| `GET /api/batches/latest` | 返回最新批次的全部记录（JSON 数组） |
| `GET /api/batches/<batch_id>` | 返回指定批次的全部记录 |

---

## 数据输入与格式要求

### 输入方式

系统支持两种新闻输入方式：

1. **NewsNow 自动抓取** — 配置 `NEWSNOW_API_URL` 和 `NEWSNOW_SOURCES`，流水线每 5 分钟自动拉取
2. **JSONL 种子注入** — 将新闻写入 `seeds/realtime_seeds.jsonl`，流水线启动时自动加载

### 种子文件格式（JSONL）

文件路径：`seeds/realtime_seeds.jsonl`，每行一条 JSON：

```jsonl
{"title": "新闻标题", "content": "新闻正文内容", "source": "来源标识", "url": "https://原文链接", "published_at": "2026-03-09T07:30:00+00:00"}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | ✅ | 新闻标题 |
| `content` | string | ✅ | 新闻正文（建议 50-2000 字） |
| `source` | string | ✅ | 来源标识（如 NewsNow source ID 或自定义标签） |
| `url` | string | 可选 | 原文链接 |
| `published_at` | string | 可选 | ISO 8601 时间戳，缺省取当前时间 |

### 输出格式（PostgreSQL）

流水线每轮将分析结果写入 PostgreSQL，存储在三张表中：

- `batches` — 批次元信息（batch_id, batch_ts, record_count, created_at）
- `batch_records` — 每条新闻的完整分析结果（JSONB 格式）
- `reports` — Markdown 报告 & 摘要

每条 `batch_records.payload` 的 JSON 结构：

```json
{
  "news": {
    "news_id": "seed-001",
    "title": "新闻标题",
    "content": "正文",
    "source": "来源",
    "url": "https://...",
    "published_at": "2026-03-09T07:30:00+00:00",
    "embedding_preview": [0.012, -0.034, ...]
  },
  "entities": [
    {"entity_id": "...", "name": "实体名", "type": "ORG|LOC|MISC", "confidence": 0.98}
  ],
  "topic": {
    "topic_id": 0,
    "probability": 0.88,
    "label": "主题关键词"
  },
  "impacts": [
    {"src": "entity_id_1", "dst": "entity_id_2", "weight": 0.92}
  ],
  "classification": {
    "category": "financial_market|policy_regulation|company_event|macro_economy|industry_trend",
    "confidence": 0.83,
    "all_scores": {"financial_market": 0.83, "policy_regulation": 0.09, "...": "..."}
  },
  "features": {
    "market_impact": 2.71,
    "price_signal": 4.55,
    "regulatory_risk": 3.19,
    "timeliness": 2.85,
    "impact": 3.17,
    "controversy": 3.24,
    "generalizability": 4.74,
    "impact_score": 3.65
  },
  "report": {
    "news_id": "seed-001",
    "final_score": 64.27,
    "impact_level": "高|中|低",
    "dk_cot_scores": {
      "stock_relevance": 80.35,
      "market_sentiment": 63.64,
      "policy_risk": 40.99,
      "spread_breadth": 56.0
    },
    "reasoning": "DK-CoT 推理链文本...",
    "markdown": "完整 Markdown 报告...",
    "viz_suggestions": ["可视化建议..."]
  }
}
```

> 如需手动导入数据到前端面板，只需确保 JSON 文件符合上述格式（至少包含 `news` 和 `report` 字段），通过「导入 JSON」按钮加载即可。

---

## 许可证

MIT
