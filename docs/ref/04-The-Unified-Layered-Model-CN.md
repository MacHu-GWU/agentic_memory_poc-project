# 综合分层模型：一个成熟的 AI Memory 系统应该怎么设计

[Extension: com.atlassian.confluence.macro.core/toc]
> 调研了 MemGPT/Letta、LangMem、Claude-Mem、Mem0 等方案后，我整理出一个"理想的 AI Memory 系统"应该长什么样。不是某一家的方案，而是综合了各家优点的分层模型。

---

## 先说结论：四层 + 两条路径

一个成熟的 AI Memory 系统需要 **4 个存储层** 和 **2 条写入路径**。

```
                写入路径
        ┌──────────┴──────────┐
    Hot Path               Background
  (对话中即时提炼)       (对话后批量提炼)
        │                      │
        ▼                      ▼
┌─────────────────────────────────────────────┐
│                                             │
│   Layer 1: Identity & Profile               │  ← 始终可见，精确查找
│   "我是谁，用户是谁"                          │
│                                             │
├─────────────────────────────────────────────┤
│                                             │
│   Layer 2: Evolving Knowledge               │  ← 按需检索，语义搜索
│   "我知道什么，世界是什么样"                   │
│                                             │
├─────────────────────────────────────────────┤
│                                             │
│   Layer 3: Session Context                  │  ← 自动管理，滚动压缩
│   "当前正在发生什么"                          │
│                                             │
├─────────────────────────────────────────────┤
│                                             │
│   Layer 4: Episodic Archive                 │  ← 海量存储，按需召回
│   "过去发生过什么"                            │
│                                             │
└─────────────────────────────────────────────┘

```

下面逐层展开。

---

## Layer 1: Identity & Profile —— 核心身份层

**一句话定义**：不超过 200 个字段的结构化 key-value，始终注入 system prompt，不需要任何检索。

**存什么**：

```
# Agent Identity（不变）
agent_name: "CodeAssist"
agent_role: "编程助手"
agent_style: "简洁直接，先给方案再解释"

# User Profile（缓慢变化）
user_name: "张三"
user_preferred_name: "三哥"
user_role: "后端工程师"
user_company: "字节跳动"
user_expertise: ["Python", "Go", "分布式系统"]
user_language: "中文"
user_timezone: "Asia/Shanghai"

# Project Context（项目级）
project_name: "电商平台 v2"
tech_stack: ["FastAPI", "PostgreSQL", "Redis", "RabbitMQ"]
architecture: "微服务"
repo_url: "git@github.com:..."
key_decisions:
  - "2024-03: 从单体迁移到微服务"
  - "2024-06: 数据库从 MySQL 迁移到 PostgreSQL"

# Preferences（行为偏好）
code_style: "PEP 8，type hints 必须"
error_handling: "prefer explicit over implicit"
test_preference: "pytest, 测试覆盖率 > 80%"

```

**为什么这一层最重要**：

这一层解决的问题是——Agent 不应该每次都要"搜索"才知道用户叫什么。这些信息的特点是：

1. **数量少**：几十到几百个字段，不到 1K tokens
2. **变化慢**：用户的名字不会每天变，技术栈不会每小时变
3. **命中率 100%**：每次对话都需要这些信息
4. **必须精确**：搞错用户的名字或技术栈是不可接受的

所以它不走向量搜索，不走 RAG，直接钉在 prompt 里。用结构化 schema（类似 LangMem 的 Profile），按 user_id 精确查找。

**更新机制**：

- **Hot Path 即时更新**：用户说"我换工作了"→ 立刻更新 `` user_company ``
- **Conflict 处理**：新值直接覆盖旧值（只保留当前状态）
- **谁来更新**：可以是 Agent 自主更新（Letta 的做法），也可以是后台 LLM 提取后更新（LangMem 的做法）

**对应关系**：

- Letta 的 Core Memory
- LangMem 的 Profile
- Claude-Mem ❌ 缺少这一层

---

## Layer 2: Evolving Knowledge —— 演化知识层

**一句话定义**：不断积累的事实、关系、洞察，用语义搜索按需召回。

**存什么**：

```
记忆 #1: [重要] 用户的电商项目使用了自定义的 JWT 刷新机制，
         access token 15分钟过期，refresh token 7天过期
         
记忆 #2: [决策] 2024年8月决定用 RabbitMQ 而不是 Kafka，
         原因是团队更熟悉，消息量暂时不大

记忆 #3: [发现] 用户的项目中 auth.py 的 token 验证逻辑有一个已知 bug，
         refresh 时不检查过期，已在 session #45 中修复

记忆 #4: [偏好] 用户讨厌过度工程，喜欢"先跑起来再优化"的策略

记忆 #5: [关系] 用户提到过 "小王" 是他的同事，负责前端

```

**这一层和 Layer 1 的区别**：

| <br> | Layer 1 Profile<br> | Layer 2 Knowledge<br> |
| --- | --- | --- |
| 数量<br> | 几十到几百个字段<br> | 成百上千条<br> |
| 结构<br> | 固定 schema<br> | 自由文本 + 元数据<br> |
| 更新<br> | 覆盖式<br> | 追加式 + 合并 + 过期<br> |
| 检索<br> | 按 key 精确查找<br> | 语义搜索 + 过滤<br> |
| 注入方式<br> | 始终在 prompt 中<br> | 按需注入<br> |
| 变化频率<br> | 低（周/月级）<br> | 中（天/周级）<br> |
**这一层的核心挑战是"记忆管理"**：

1. **矛盾检测与解决**：用户三月说"用 MySQL"，六月说"迁到 PostgreSQL"——系统需要识别矛盾，更新旧记忆或标记为过时。Mem0 的做法是让 LLM 比对新旧信息做 conflict resolution。
2. **记忆合并（Consolidation）**：多条零散的记忆可以合并成一条更精炼的。比如"用户喜欢简洁代码"+"用户讨厌过度抽象"+"用户要求 type hints" → 合并为"用户偏好严格但简洁的代码风格：type hints 必须、不过度抽象、PEP 8"。
3. **重要性 × 新鲜度 × 相关性**：检索时不能只看语义相似度，还需要综合考虑这条记忆有多重要（修复 bug 的决策 > 闲聊中提到的电影）、有多新鲜（最近的信息通常更相关）、和当前上下文有多相关。
4. **遗忘机制**：长期不被访问、低重要性的记忆应该逐渐降权或归档，避免知识库无限膨胀。

**对应关系**：

- Letta 的 Archival Memory（知识部分）
- LangMem 的 Collection
- Claude-Mem 的 observations 表（部分对应）

---

## Layer 3: Session Context —— 会话上下文层

**一句话定义**：当前 session 的对话历史，自动管理、滚动压缩。

这一层是最"传统"的——就是 context window 中的对话历史。但关键在于**怎么管理它不爆**。

**三种压缩策略**（可以组合使用）：

### 策略 A：滚动窗口 + 溢出摘要

```
完整保留最近 20 轮对话
        ↓
第 21 轮进来时，最老的那轮被摘要
        ↓
摘要追加到一个"对话摘要区"
        ↓
最终 context = [对话摘要区] + [最近 20 轮完整对话]

```

这是 Letta 的 Message Buffer 策略。简单有效。

### 策略 B：递归摘要

```
对话 1-10 → 摘要 A
对话 11-20 → 摘要 B
摘要 A + 摘要 B → 元摘要 C
对话 21-30 + 元摘要 C → 更新的元摘要 C'

```

每次溢出不是独立摘要，而是和之前的摘要一起递归压缩。保持了更好的连贯性。

### 策略 C：训练专用压缩模型

Cognition（Devin 的公司）的做法：微调一个小模型，专门把对话历史压缩成"关键细节、事件、决定"。比通用 LLM 做摘要更可靠、更可控。

**选择建议**：

- 简单项目：策略 A 足够
- 复杂长会话：策略 B 更好
- 有资源训练模型：策略 C 最优

**对应关系**：

- Letta 的 Message Buffer + eviction 机制
- Claude-Mem 的 Endless Mode（实时压缩变体）
- LangMem 的 Short Term Memory / Summarization

---

## Layer 4: Episodic Archive —— 历史档案层

**一句话定义**：所有历史 session 的压缩记录 + 成功的交互模式，海量存储，按需召回。

这一层存两类东西：

### 4a: Session 摘要（你做过什么）

每个历史 session 结束后生成一份结构化摘要：

```
{
  "session_id": "sess_045",
  "date": "2024-08-15",
  "request": "修复认证系统的 token 刷新 bug",
  "investigated": "auth.py 的 refresh_token 函数",
  "learned": "PyJWT decode() 默认不验证过期时间",
  "completed": "修复 bug + 添加单元测试",
  "next_steps": "检查其他 token 使用处"
}

```

这是 Claude-Mem 做得最好的部分——每个 session 都有一份结构化工作日志。

### 4b: 交互模式（你学到了什么）

成功的交互模式被提炼为 Episode，供未来类似场景复用：

```
{
  "situation": "用户对递归概念困惑",
  "approach": "用家谱/树屋类比解释",
  "result": "用户立刻理解了",
  "takeaway": "对抽象概念使用具体类比效果最好"
}

```

这是 LangMem 的 Episodic Memory 做得最好的部分。

### 检索策略：渐进式披露

借鉴 Claude-Mem 的做法，不要一次性注入全部历史。而是：

1. **索引层**：只注入标题列表（"你之前做过这些事..."）
2. **按需深入**：Agent 觉得某条记忆和当前任务相关时，主动搜索获取详情
3. **智能排序**：结合时间衰减、重要性、语义相关性排序

**对应关系**：

- Letta 的 Archival Memory + Recall Memory
- Claude-Mem 的 observations + session_summaries + Progressive Disclosure
- LangMem 的 Episodic Memory

---

## 两条写入路径：Hot Path vs Background

不同层的记忆应该用不同的写入时机：

```
Hot Path（对话中即时写入）
├── Layer 1 Profile 更新 ← 用户说"我换工作了"，立刻更新
├── Layer 2 关键发现 ← 发现了重要 bug，立刻记录
└── Layer 3 ← 自动的，不需要额外处理

Background（对话后批量写入）
├── Layer 2 知识提炼 ← 回顾整段对话，提取有价值的知识
├── Layer 4a Session 摘要 ← 对话结束后生成
├── Layer 4b 交互模式 ← 分析成功的交互，提取模式
└── Procedural 优化 ← 根据反馈优化 system prompt

```

**为什么要分两条路**：

- Hot Path 保证关键信息即时生效（但增加延迟）
- Background 保证全面提炼（但有延迟）
- 两者结合 = 即时性 + 全面性

---

## 完整数据流

把四层和两条路径串起来，一个完整的请求-响应周期是这样的：

```
新 Session 启动
    │
    ├─→ 加载 Layer 1: Profile（始终注入 system prompt）
    ├─→ 加载 Layer 4 索引：最近 N 个 session 的标题列表
    ├─→ Layer 3: 空的（新 session）
    │
    ▼
用户发送消息
    │
    ├─→ Layer 3: 消息加入对话历史
    ├─→ 检索 Layer 2: 根据消息内容做语义搜索，注入相关知识
    ├─→ (可选) 检索 Layer 4: 如果需要历史细节
    │
    ▼
LLM 推理，生成回复
    │
    ├─→ Hot Path: 检查是否有 Profile 更新 → 更新 Layer 1
    ├─→ Hot Path: 检查是否有重要发现 → 写入 Layer 2
    ├─→ Layer 3: 如果对话历史过长 → 压缩旧消息
    │
    ▼
Session 结束
    │
    ├─→ Background: 生成 Session 摘要 → 写入 Layer 4a
    ├─→ Background: 提炼知识 → 更新 Layer 2
    ├─→ Background: 提取交互模式 → 写入 Layer 4b
    └─→ Background: 优化 system prompt（Procedural Memory）

```

---

## 各层的技术选型建议

| 层级<br> | 存储<br> | 检索方式<br> | 推荐技术<br> |
| --- | --- | --- | --- |
| **L1 Profile**<br> | 结构化 KV<br> | 按 user_id 直接查找<br> | PostgreSQL / Redis / JSON<br> |
| **L2 Knowledge**<br> | 文本 + 元数据 + embedding<br> | 语义搜索 + 过滤<br> | 向量数据库（Chroma/Pinecone）+ PostgreSQL<br> |
| **L3 Session**<br> | 消息列表 + 摘要<br> | 无需检索（在 context 中）<br> | 内存 + Redis（持久化）<br> |
| **L4 Archive**<br> | 结构化摘要 + 文本 + embedding<br> | 语义搜索 + 全文搜索 + 结构化过滤<br> | SQLite FTS5 + ChromaDB / PostgreSQL + pgvector<br> |

---

## 最后：设计 Memory 系统的五条原则

经过对各家方案的调研，我总结了这五条设计原则：

### 原则一：分层是必须的

不同类型的信息需要不同的存储、检索、更新策略。把所有东西扔进一个向量数据库是最偷懒也是最低效的做法。"我叫什么"和"三个月前某次对话的一个细节"不应该用同样的方式处理。

### 原则二：精确查找 > 模糊搜索

对于核心事实（名字、角色、技术栈、关键偏好），结构化 key-value 比向量相似度可靠一百倍。向量搜索适合"我可能需要的东西"，不适合"我肯定需要的东西"。

### 原则三：压缩即认知

好的 Memory 系统不是存得多，而是**提炼得准**。把 50 轮对话压缩成 5 条有价值的记忆，本身就要求系统理解什么是重要的。存原始对话是偷懒，AI 提炼是真功夫。

### 原则四：渐进式披露省 token

不要把所有记忆一次性注入 context。给"目录"，让 Agent 按需查"全文"。Claude-Mem 的实践证明这能省 94% 的 token。

### 原则五：写入和读取都需要 LLM 参与

提炼记忆不是简单的文本处理，检索记忆不是简单的数据库查询。两端都需要 LLM 的语义理解能力：写入时判断什么值得记住、怎么压缩；读取时判断什么和当前任务相关、怎么组织。

---

## 一句话总结

> **成熟的 AI Memory = L1 核心身份（精确 KV，始终可见）+ L2 演化知识（语义搜索，按需召回）+ L3 会话上下文（滚动压缩）+ L4 历史档案（海量存储，渐进披露）。再加上 Hot Path + Background 双写入路径。这不是某一家的方案，而是综合 Letta、LangMem、Claude-Mem、Mem0 各家优点后的理想模型。**