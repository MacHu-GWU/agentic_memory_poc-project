# LangMem 深挖：记忆不是存聊天记录，而是提炼知识

[Extension: com.atlassian.confluence.macro.core/toc]
> LangChain 的 LangMem SDK 把"AI 应该记住什么"这个问题拆解得很清楚。它不造轮子存数据库，而是专注于最难的那一步：**从对话中提炼出值得记住的东西**。

---

## LangMem 在解决什么问题？

假设你和一个 AI 助手聊了 50 轮。这 50 轮对话里：

- 有 5 轮是寒暄
- 有 10 轮是你在纠正它的错误
- 有 15 轮是来回确认细节
- 有 3 轮你提到了重要的个人信息（名字、职业、偏好）
- 有 2 轮包含了关键决策

问题来了：**50 轮对话里真正值得"记住"的信息可能只有 5 轮的内容**。如果你把全部对话做 embedding 扔进向量数据库，那搜索时 90% 的结果都是噪音。

LangMem 的核心价值就在这里：**它用 LLM 从对话中提炼出值得记住的东西，而不是存原始对话**。

---

## 三种记忆类型：知道什么、经历过什么、该怎么做

LangMem 定义了三种记忆，每种对应不同的认知功能：

### 1. Semantic Memory（语义记忆）：你知道什么

这是事实性知识——用户是谁、喜欢什么、在做什么项目、有哪些技能。

LangMem 把语义记忆进一步分成了两种存储模式，这个区分非常关键：

#### Profile（档案）—— 少量关键事实，精确查找

Profile 是一个**结构化的 JSON 文档**，遵循你预定义的 schema。比如：

```
class UserProfile(BaseModel):
    name: str
    preferred_name: str
    role: str
    language_preference: str
    special_skills: list[str]
    current_project: str
    communication_style: str

```

当新对话发生时，LangMem 不会为 Profile 新增一条记录——它会**原地更新**（in-place update）。用户说"我换工作了"，Profile 里的 `` role `` 字段直接被覆盖，而不是在旁边加一条新记忆。

**检索方式**：直接按 user_id 查找，不需要向量搜索。

这就是 Letta/MemGPT 中 Core Memory 的精确等价物：容量小、结构化、始终可用、精确查找。不同之处在于 LangMem 用 Pydantic schema 做了更严格的类型约束。

#### Collection（集合）—— 无限扩展的知识库，语义搜索

Collection 是一个不断增长的记忆条目列表。每条记忆是一段独立的文本，用 embedding 索引，按语义相似度检索。

```
记忆 #1: [重要] 用户在字节跳动的 ML 团队工作，主要做 NLP 和大模型
记忆 #2: [背景] 用户精通 Python，专注于让 AI 系统听起来更自然
记忆 #3: [爱好] 用户是竞技魔方玩家
记忆 #4: [项目] 用户正在构建一个 RAG 系统，遇到了检索精度问题

```

与 Profile 的关键区别：

- Profile 只有一个文档，更新是覆盖式的
- Collection 有无数条目，新信息是追加式的
- Profile 用直接查找，Collection 用语义搜索

**选择建议**：

- 如果信息是"当前状态"型的（你的名字、你的角色、你的偏好）→ 用 Profile
- 如果信息是"不断积累"型的（你提到过的每个项目、每次讨论的技术决策）→ 用 Collection

### 2. Episodic Memory（情景记忆）：你经历过什么

情景记忆记录的不是事实，而是**完整的交互情境**——当时的场景、思考过程、采取的行动、为什么成功。

LangMem 用一个结构化的 Episode schema 来捕获这些：

```
class Episode(BaseModel):
    observation: str   # 当时的情境是什么
    thoughts: str      # 关键的思考过程
    action: str        # 采取了什么行动
    result: str        # 结果如何，为什么有效

```

比如，Agent 发现用"家谱"来类比"二叉树"效果很好，这个交互模式就被存为一条 Episode——下次遇到类似的教学场景，Agent 可以检索到这条记忆，复用同样的方法。

**这就是 few-shot learning 的记忆化**：不是每次都从零开始想怎么回答，而是从过去的成功经验中学习。

### 3. Procedural Memory（程序记忆）：你该怎么做

程序记忆编码的是 Agent 的行为模式——不是"知道什么"，而是"该怎么做"。

LangMem 中最独特的设计：**Procedural Memory 的载体是 system prompt 本身**。

它通过一个 Prompt Optimizer，根据对话反馈自动优化 system prompt：

```
# 原始 prompt
"You are a helpful assistant."

# 用户反馈：希望更实际的例子，不要太理论化

# 优化后的 prompt（自动生成）
"You are a helpful assistant. When explaining programming concepts:
1. Start with a practical code example, not theory
2. Use simple language and break down complex concepts  
3. If the user requests a different approach, immediately adapt
4. Always include working code for programming questions"

```

这很像人类的"肌肉记忆"或"直觉"：你不会有意识地想"我该怎么骑自行车"，你只是自然而然地做了——因为行为模式已经内化了。对 Agent 来说，优化后的 system prompt 就是内化了的行为模式。

---

## 记忆是怎么"写入"的？两条路径

LangMem 提供了两种写入记忆的时机，这个设计直接影响产品体验：

### Hot Path（实时提炼）

在对话进行中，Agent 当场提取记忆并存储。

```
用户: "我叫张三，在字节跳动做后端"
                ↓
        LLM 判断：这是重要的用户信息
                ↓
        立即更新 Profile: {name: "张三", role: "后端工程师", company: "字节跳动"}
                ↓
        同时正常回复用户

```

**优点**：记忆立即生效，下一轮对话就能用上。 **缺点**：增加延迟（多了一次 LLM 调用来提炼记忆），而且 Agent 需要同时处理"回复用户"和"管理记忆"两个任务。

### Background（后台提炼）

对话结束后（或空闲时），异步地回顾整段对话，提炼记忆。

```
[对话结束]
        ↓
后台 LLM 回顾全部 50 轮对话
        ↓
提炼出 5 条语义记忆 + 2 条情景记忆
        ↓
更新 Profile + 追加 Collection
        ↓
优化 system prompt（Procedural Memory）

```

**优点**：不影响对话体验；后台 LLM 可以看到完整对话，提炼更全面。 **缺点**：记忆有延迟，当前 session 内不能立即使用新提炼的记忆。

**最佳实践**：两者结合——核心 Profile（名字、角色等）用 Hot Path 即时更新，Collection 和 Episode 用 Background 批量提炼。

---

## 记忆的更新与冲突解决——最难的部分

提取新记忆容易，**管理已有记忆**才是真正的挑战。

### 矛盾处理

用户三个月前说"我在阿里工作"，现在说"我在字节跳动"。

- **Profile**：直接覆盖，只保留最新状态。简单粗暴但有效。
- **Collection**：需要 conflict resolution。LangMem 让 LLM 来做这个判断——把新信息和已有的相关记忆一起送给 LLM，让它决定是"更新旧记忆"还是"新增一条"还是"删除旧记忆"。

### 记忆膨胀

如果每次对话都提取记忆，Collection 会越来越大。LangMem 的应对：

1. **Consolidation（合并）**：把多条相关记忆合并成一条更精炼的
2. **Importance + Recency 加权**：检索时不只看语义相似度，还要看记忆的"重要性"和"新鲜度"
3. **开发者可控的提取强度**：通过 instructions 参数调整——提取太多导致精度下降，提取太少导致召回不足

---

## LangMem 的架构哲学

LangMem 有一个很清醒的设计哲学：**它不管存储，只管提炼**。

```
LangMem 的核心 API（无状态函数）
├── create_memory_manager()   → 从对话中提炼记忆
├── create_prompt_optimizer() → 从反馈中优化 prompt
└── 不依赖任何特定数据库

LangMem 的集成层（有状态，依赖 LangGraph Store）
├── create_memory_store_manager() → 自动持久化记忆
└── create_manage_memory_tool()   → 给 Agent 直接操作记忆的工具

```

核心 API 是纯函数：输入对话 + 已有记忆，输出更新后的记忆。你可以用任何数据库来存——PostgreSQL、Redis、向量数据库、甚至文件系统。

这和 Letta/MemGPT 的区别很大：Letta 是一个完整的 Agent 框架，自带存储层、自带 context window 管理、Agent 自己管理自己的记忆。而 LangMem 更像一个"记忆提炼引擎"，你可以把它插入任何现有的 Agent 架构中。

---

## 用 Namespace 组织记忆——企业级设计

LangMem 用层级化的 namespace 来组织记忆：

```
# 按组织 → 用户 → 应用场景组织
namespace = ("acme_corp", "{user_id}", "code_assistant")

# 同一个用户在不同场景下的记忆是隔离的
("acme_corp", "user_123", "code_assistant")   # 编程助手的记忆
("acme_corp", "user_123", "customer_support")  # 客服助手的记忆

# 也可以有全局共享记忆
("acme_corp", "global", "product_knowledge")   # 全公司共享的产品知识

```

这在实际产品中很重要：一个用户可能在不同的场景下和 AI 交互，记忆需要按场景隔离但也允许跨场景共享。

---

## 与 Letta/MemGPT 的对应关系

如果你读过第一篇关于 Letta 的文章，你可能想知道 LangMem 和 Letta 的关系。简单说：

- LangMem 的 **Profile** ≈ Letta 的 **Core Memory**（精确 KV，始终可用）
- LangMem 的 **Collection** ≈ Letta 的 **Archival Memory**（无限扩展，语义搜索）
- LangMem 的 **Episode** ≈ Letta 没有的独立层（Letta 的情景信息混在 Archival 里）
- LangMem 的 **Procedural Memory** ≈ Letta Core Memory 中的 Persona block（但 LangMem 更进一步，能自动优化 prompt）

关键区别在于：Letta 是一个完整的 Agent 运行时，Agent 自主管理四层记忆；LangMem 是一个可插拔的记忆提炼 SDK，专注于"从对话中提取什么"和"怎么更新已有记忆"。

---

## 一句话总结

> **LangMem 的核心贡献不是存储，而是"提炼"：用 LLM 从噪音对话中萃取 Profile（你是谁）、Collection（你知道什么）、Episode（你经历过什么）、Procedural（你该怎么做）四种知识，并能智能地更新和合并已有记忆。它是记忆系统中"写入端"最精细的方案。**