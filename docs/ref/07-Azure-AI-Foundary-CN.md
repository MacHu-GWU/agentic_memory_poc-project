# Azure AI Foundry Memory 深度调研：当记忆变成托管服务

[Extension: com.atlassian.confluence.macro.core/toc]
> 微软的设计哲学是：**开发者不需要知道记忆存在哪里、怎么检索——你只需要告诉系统"记住什么类型的东西"，剩下的全部托管。** 这种极简主义换来了惊人的易用性，但也留下了一些明显的空白。

---

## 一句话定位

Azure AI Foundry Memory 是 Foundry Agent Service 内置的 **纯 long-term memory 托管服务**。它在 Ignite 2025（2025年11月）以 public preview 发布。短期记忆（session context）？不管。上下文窗口压缩？不管。它只做一件事：**从对话中提取有价值的知识，持久化存储，跨 session 可用。**

---

## 整体架构：只有一个核心抽象

跟前几篇调研中那些精巧的多层架构不同，Foundry Memory 的设计极度简洁。整个系统只有一个核心抽象——**Memory Store**：

```
┌──────────────────────────────────────────────────────────┐
│                   Memory Store (per agent)                │
│                                                          │
│   创建时指定：                                             │
│   • chat_model (如 gpt-4.1) ← 用于提取和合并记忆           │
│   • embedding_model (如 text-embedding-3-small) ← 向量化   │
│   • options:                                              │
│       ├── user_profile_enabled: true/false                │
│       ├── chat_summary_enabled: true/false                │
│       └── user_profile_details: "自然语言描述"              │
│                                                          │
│   ┌────────────────────────────────────────────────────┐ │
│   │  Scope: "user_123"                                  │ │
│   │  ├── User Profile Memories (会做 consolidation)      │ │
│   │  └── Chat Summary Memories (不做 consolidation)      │ │
│   ├────────────────────────────────────────────────────┤ │
│   │  Scope: "user_456"                                  │ │
│   │  ├── User Profile Memories                          │ │
│   │  └── Chat Summary Memories                          │ │
│   └────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘

```

就这样。没有 L1/L2/L3/L4 的层次。没有 Event / Strategy / Record 的三阶段管线。一个 Memory Store 就是全部。

---

## 处理流水线：Extract → Consolidate → Retrieve

```
对话消息进入
    │
    ▼
① Extraction（提取）
    LLM 从对话中提取两类信息：
    • User Profile: 用户偏好、个人事实（"对奶制品过敏"、"偏好邮件沟通"）
    • Chat Summary: 当前对话的主题摘要
    │
    ▼
② Consolidation（合并）
    仅对 User Profile 执行：
    • 合并重复条目（"喜欢深烘咖啡" 出现两次 → 合并为一条）
    • 冲突解决（之前说"在 Google"，现在说"去了 Datadog" → 更新）
    • Chat Summary 不做合并，独立存储
    │
    ▼
③ 存储到 Memory Store
    │
    ▼
④ Retrieval（检索）
    使用 hybrid search 技术
    • Static memories (User Profile): 对话开始时一次性检索
    • Contextual memories (Chat Summary): 每轮根据当前消息动态检索

```

---

## Scope：唯一的数据分区机制

这是 Foundry Memory 最需要理解的设计决策。**它只有一个扁平的 **`` scope ``** 参数来做数据隔离。**

### 什么是 Scope

Scope 是开发者自定义的一个字符串，用于在同一个 Memory Store 内划分不同的记忆空间。每个 scope 内的记忆完全隔离。

```
# 每个用户一个 scope
scope = "user_123"

# 或者每个团队一个 scope
scope = "team_engineering"

# 开发者完全自主决定 scope 的含义

```

### 限制

- 每个 Memory Store 最多 **100 个 scope**
- 每个 scope 最多 **10,000 条记忆**
- Scope 值必须由开发者显式传入（目前不支持从 auth token 自动提取）
- **没有层级结构**：不能做前缀匹配、不能跨 scope 查询

### 这意味着什么

如果你在做保险理赔系统：

- `` scope = policyholder_id `` → 同一客户所有 case 的记忆混在一起（跨 case 共享，但无法 per-case 隔离）
- `` scope = case_id `` → 每个 case 独立（per-case 隔离，但丢了跨 case 共享能力）
- **不能同时做到两者**。没有 "按客户共享偏好，按 case 隔离摘要" 的能力。

这是跟我在前几篇调研中强调的 "Layers × Namespaces" 模型最大的差距。

---

## 两种 Memory Type：静态画像 vs 动态摘要

| 维度<br> | User Profile Memory<br> | Chat Summary Memory<br> |
| --- | --- | --- |
| 内容<br> | 用户偏好、个人信息、习惯<br> | 对话主题摘要<br> |
| 例子<br> | "对奶制品过敏"、"偏好邮件沟通"<br> | "讨论了 auth 模块的 bug 修复方案"<br> |
| Consolidation<br> | ✅ 会合并去重、解决冲突<br> | ❌ 不做合并<br> |
| 检索时机<br> | 对话开始时一次性注入（静态）<br> | 每轮根据当前消息动态检索（上下文）<br> |
| 类比<br> | 接近 Letta 的 Core Memory / LangMem 的 Profile<br> | 接近 Letta 的 Archival Memory（对话摘要部分）<br> |
关键设计决策：

**User Profile 被视为"静态"记忆**——它不依赖当前对话内容来决定要不要检索，而是在每次对话开始时全量注入。这是一个接近但不等同于 "always pinned" 的设计：信息确实会在对话开始时出现在上下文中，但它不是像 Letta Core Memory 那样作为 system prompt 的固定部分——它仍然需要一次 search 调用。

**Chat Summary 被视为"上下文"记忆**——根据当前对话内容做语义检索，只拉回相关的历史摘要。

---

## API 设计：两条路径

### 路径一：Memory Search Tool（一键集成，推荐）

把 `` memory_search `` 作为 tool 挂到 agent 上，系统自动处理一切：

```
from azure.ai.projects.models import MemorySearchTool, PromptAgentDefinition

tool = MemorySearchTool(
    memory_store_name="my_memory_store",
    scope="user_123",
    update_delay=300,  # 300秒无活动后触发写入（默认值）
)

agent = project_client.agents.create_version(
    agent_name="MyAgent",
    definition=PromptAgentDefinition(
        model="gpt-4.1",
        instructions="You are a helpful assistant",
        tools=[tool],
    )
)

```

挂载后，系统自动完成：

1. 对话开始 → 注入 static memories（User Profile）
2. 每轮响应前 → 检索 contextual memories（Chat Summary）
3. 每次响应后 → 内部触发 `` update_memories ``（受 `` update_delay `` debounce 控制）

**开发者几乎不需要写任何记忆管理代码。**

### 路径二：Memory Store APIs（完整控制）

```
# ① 创建 Memory Store
memory_store = project_client.memory_stores.create(
    name="my_memory_store",
    definition=MemoryStoreDefaultDefinition(
        chat_model="gpt-4.1",
        embedding_model="text-embedding-3-small",
        options=MemoryStoreDefaultOptions(
            user_profile_enabled=True,
            chat_summary_enabled=True,
            user_profile_details="Food preferences for a meal planning agent"
        )
    ),
)

# ② 写入记忆（异步长时间操作，约1分钟）
update_poller = project_client.memory_stores.begin_update_memories(
    name="my_memory_store",
    scope="user_123",
    items=[ResponsesUserMessageItemParam(content="I prefer dark roast coffee")],
    update_delay=0,  # 0 = 立即触发
)
result = update_poller.result()
# result.memory_operations → [{kind: "create", memory_item: {memory_id, content}}]

# ③ 链式更新（串联多轮对话）
new_poller = project_client.memory_stores.begin_update_memories(
    name="my_memory_store",
    scope="user_123",
    items=[ResponsesUserMessageItemParam(content="I also like cappuccinos")],
    previous_update_id=update_poller.update_id,  # 串联上一次更新
    update_delay=0,
)

# ④ 检索 static memories（不传 items → 返回 User Profile）
static = project_client.memory_stores.search_memories(
    name="my_memory_store",
    scope="user_123",
)

# ⑤ 检索 contextual memories（传 items → 返回相关的 Chat Summary + Profile）
contextual = project_client.memory_stores.search_memories(
    name="my_memory_store",
    scope="user_123",
    items=[ResponsesUserMessageItemParam(content="What are my coffee preferences?")],
    options=MemorySearchOptions(max_memories=5),
)

# ⑥ 删除某个 scope 的所有记忆
project_client.memory_stores.delete_scope(name="my_memory_store", scope="user_123")

# ⑦ 删除整个 Memory Store
project_client.memory_stores.delete("my_memory_store")

```

### 完整 API 表面

| API<br> | 用途<br> |
| --- | --- |
| `` memory_stores.create() ``<br> | 创建 Memory Store<br> |
| `` memory_stores.update() ``<br> | 更新 Memory Store 配置<br> |
| `` memory_stores.list() ``<br> | 列出所有 Memory Store<br> |
| `` memory_stores.delete() ``<br> | 删除 Memory Store<br> |
| `` memory_stores.begin_update_memories() ``<br> | 写入记忆（异步，~1分钟）<br> |
| `` memory_stores.search_memories() ``<br> | 检索记忆（同步）<br> |
| `` memory_stores.delete_scope() ``<br> | 删除某 scope 所有记忆<br> |
对比一下你会发现，这个 API 表面**非常小**。没有 list_events、没有 list_sessions、没有 list_actors、没有 batch_create、没有 get_memory_record。就是 create / update / search / delete，完事。

---

## 写入时序：Debounce 机制

这是 Foundry Memory 最有特色的设计之一。

```
对话进行中...
    │
Turn 1: 用户说了什么 → agent 响应 → 内部标记"需要更新记忆"
    │                                  但不立即执行
Turn 2: 用户又说了什么 → agent 响应 → 重置 debounce 计时器
    │
Turn 3: 用户又说了什么 → agent 响应 → 重置 debounce 计时器
    │
    │  ... 用户停止说话 ...
    │
    │  等待 update_delay 秒（默认300秒 = 5分钟）
    │
    ▼
触发 extraction + consolidation（约1分钟完成）
    │
    ▼
记忆写入 Memory Store

```

**设计意图**：避免在快速对话中频繁触发 LLM extraction（每次都要调 LLM，成本和延迟都高）。等对话"冷却"后再一次性处理。

**权衡**：这意味着在当前对话中说的话，**不会立即可用于同一对话的后续轮次**。如果用户说"我刚换了工作"，这个信息要等 5 分钟 + 1 分钟处理 = 至少 6 分钟后才进入 long-term memory。

---

## `` user_profile_details ``：用自然语言控制提取行为

这是 Foundry Memory 最独特的设计——**没有 schema、没有 strategy config、没有 extraction prompt template**。你用一句自然语言告诉系统应该关注什么：

```
# 旅行助手：关注航班和饮食偏好
user_profile_details="flight carrier preference and dietary restrictions"

# 隐私敏感场景：排除敏感信息
user_profile_details="Avoid irrelevant or sensitive data, such as age, financials, precise location, and credentials"

# 编程助手：关注技术栈和代码风格
user_profile_details="programming languages, frameworks, code style preferences, and current project context"

```

系统会把这段文字翻译成内部的 extraction instructions，引导 LLM 的提取行为。

**优点**：极低的上手门槛，不需要理解 extraction pipeline 的内部机制。 **缺点**：完全黑盒，你无法控制提取的精确度、格式、或优先级。

---

## 底层存储：完全托管的黑盒

Foundry Agent Service 的整体存储架构依赖 Azure Cosmos DB：

```
Azure Cosmos DB (enterprise_memory 数据库)
├── thread-message-store      ← 用户对话消息
├── system-thread-message-store ← 系统内部消息
└── agent-entity-store         ← 模型输入输出

```

但这是 **thread storage**（对话历史），不是 memory store。Memory Store 的底层存储对开发者完全不可见——你不知道它用的是 Cosmos DB、AI Search、还是别的什么。检索使用 "hybrid search"，具体实现未公开。

Azure 支持 BYO (Bring Your Own) 模式，让你把 Cosmos DB、Storage、AI Search 部署在自己的订阅里，但这针对的是 thread storage，不是 memory store。

---

## 它没有做什么

理解一个系统"不做什么"跟理解它"做什么"同样重要：

### 1. 没有短期记忆管理

不管 session context、不管 conversation history、不管上下文窗口溢出压缩。这些全部交给 agent orchestration framework（如 Microsoft Agent Framework）。

### 2. 没有层级化的 Namespace

只有扁平的 scope，不能做 "per-case 隔离 + per-user 共享" 的复合模式。不支持前缀匹配或跨 scope 查询。

### 3. 没有自定义提取策略

不能写自己的 extraction prompt。不能定义不同的 strategy 来提取不同类型的信息。只能通过 `` user_profile_details `` 这个自然语言参数来"暗示"。

### 4. 没有 always-pinned Core Identity

User Profile 虽然建议在对话开始时检索，但它不是自动 pinned 到 system prompt 的。需要开发者自己在 agent framework 层做注入。

### 5. 没有 Procedural Memory

不会根据历史交互优化 agent 行为。不会自动修改 system prompt。每个 session 的行为完全由 agent 自己的 instructions 决定。

### 6. 没有 Episodic Memory（结构化的）

Chat Summary 是对话摘要，但不是像 Claude-Mem 那种结构化的 observation（title / facts / concepts / type / files），也不是像 LangMem 那种 Episode（situation / approach / result / takeaway）。

### 7. Chat Summary 没有 Consolidation

User Profile 会做合并去重，但 Chat Summary 不会。随着对话越来越多，Chat Summary 会持续增长，没有明确的清理/合并/衰减机制。（受限于每个 scope 10,000 条的硬限制。）

### 8. 只支持 Azure OpenAI 模型

其他 model provider 不支持。

---

## 设计哲学总结

Foundry Memory 的设计哲学可以用三个词概括：**托管、极简、黑盒。**

**托管**：开发者不需要操心存储引擎、embedding pipeline、检索优化。一键启用，系统全包。

**极简**：API 表面极小（create / update / search / delete），概念极少（Memory Store + Scope + 两种 Memory Type），配置极简（一个自然语言字符串控制提取行为）。

**黑盒**：你不知道记忆存在哪里、怎么索引、怎么检索、提取 prompt 长什么样。系统承诺"state-of-the-art quality"，但你没有旋钮可以调。

这种设计哲学的受众很明确：**不想（或不能）花时间搭建记忆基础设施的团队。** 对他们来说，"portal 里点一个开关就有记忆" 比 "四层架构 + 双写路径 + namespace 矩阵" 有吸引力得多。

但对需要精细控制的场景——比如企业级多 case 管理、跨实体共享与隔离、结构化知识提取——Foundry Memory 目前的抽象层级还不够。

---

## 一句话总结

> **Azure Foundry Memory = 纯 long-term memory 托管服务。只有 Memory Store + Scope 两个核心概念，只提取 User Profile 和 Chat Summary 两种记忆，用自然语言引导提取行为，用 debounce 控制写入时序。极简到几行代码就能用，但也因此缺少层级化 namespace、自定义提取策略、短期记忆管理、always-pinned identity 和 procedural memory。它是"记忆即托管服务"思路的最纯粹体现。**