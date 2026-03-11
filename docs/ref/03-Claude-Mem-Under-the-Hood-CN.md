# Claude-Mem 核心原理：18K Star 插件是怎么做 Memory 的

[Extension: com.atlassian.confluence.macro.core/toc]
> 这个 Claude Code 最火的记忆插件，技术上到底做了什么？不是存聊天记录，而是用 AI 把每次操作压缩成结构化的"观察笔记"，然后用渐进式披露省掉 94% 的 token。

---

## 问题：Claude Code 的"失忆症"

你用 Claude Code 写了一天代码。它帮你重构了认证系统、修了三个 bug、做了性能优化。你关掉终端，第二天打开，Claude 对你说：

"你好！有什么可以帮你的？"

一切从零开始。它不记得昨天做了什么，不记得你的项目结构，不记得你们达成的架构决策。你得重新解释一遍。

Claude-Mem 解决的就是这个问题：**让 Claude Code 跨 session 记住你们一起做过的事情**。

---

## 核心思路：不存原文，存"观察笔记"

Claude-Mem 的核心思路用一句话概括：

> **每次 Claude Code 调用一个 tool（读文件、写文件、执行命令），就用 AI 把这次操作压缩成一条结构化的"观察笔记"(observation)，存起来。下次 session 时，把相关的观察笔记注入 context。**

这和"把聊天记录存到 RAG"的区别是本质的：

| <br> | 聊天记录 RAG<br> | Claude-Mem<br> |
| --- | --- | --- |
| 存什么<br> | 原始对话文本<br> | AI 提炼后的结构化 observation<br> |
| 数据大小<br> | 10KB-500KB/次 tool 调用<br> | ~2KB/条 observation<br> |
| 包含什么<br> | 所有内容（包括噪音）<br> | title / facts / concepts / type / files<br> |
| 检索方式<br> | 纯向量相似度<br> | 语义搜索 + 全文搜索 + 结构化过滤<br> |
| 压缩比<br> | 无压缩<br> | 10:1 到 100:1<br> |
一个实际的例子。你让 Claude Code 读取了 `` auth.py ``，原始 tool 输出可能是整个文件内容（500 行、几十 KB）。Claude-Mem 把它压缩成：

```
{
  "title": "Reviewed auth.py token validation logic",
  "subtitle": "Found JWT expiration check missing in refresh flow",
  "type": "investigation",
  "facts": [
    "auth.py uses PyJWT for token handling",
    "refresh_token() skips expiration validation",
    "Access tokens expire in 15min, refresh in 7 days"
  ],
  "concepts": ["JWT", "token refresh", "authentication"],
  "files_read": ["src/auth.py"],
  "files_modified": [],
  "narrative": "Investigated auth module to diagnose token refresh bug..."
}

```

从几十 KB 压到 ~500 token（~2KB），但关键信息全部保留。

---

## 架构：5 个 Hook + 1 个 Worker

Claude-Mem 的架构分两部分：**轻量 Hook**（在 Claude Code 进程内，负责抓数据）和 **Worker Service**（独立后台进程，负责重活）。

### 5 个生命周期 Hook

Claude Code 提供了插件 hook 机制，Claude-Mem 在 5 个时间点插入逻辑：

```
① SessionStart    → 注入历史上下文（"你之前做过这些事..."）
② UserPromptSubmit → 创建新的 SDK session
③ PostToolUse     → 🔑 核心！每次 tool 调用后捕获数据
④ Stop            → 生成 session 摘要
⑤ SessionEnd      → 标记 session 完成

```

**最关键的是 ③ PostToolUse**。每次 Claude Code 执行完一个 tool（读文件、写文件、bash 命令等），save-hook 会：

1. 对原始数据做隐私过滤（`` stripMemoryTags() ``）
2. 发一个 HTTP POST 到 Worker Service
3. **45ms 内返回**，不阻塞 IDE

所有的 hook 都被设计得极其轻量——它们只是 HTTP 客户端，每个 ~75 行代码。重活全部交给 Worker。

### Worker Service：后台的大脑

Worker Service 是一个 Express 应用，跑在 localhost:37777，由 PM2 管理。它负责：

1. **SessionManager**：管理 session 生命周期 + 消息队列
2. **SDKAgent**：调用 Claude Agent SDK 做 AI 压缩——这是核心
3. **SearchManager**：混合搜索编排
4. **DatabaseManager**：双数据库管理

#### AI 压缩流水线

```
PostToolUse hook 发来的原始数据
    ↓
SessionManager 放入 EventEmitter 队列
    ↓
SDKAgent 取出，构建 XML prompt
    ↓
调用 Claude API（Agent SDK）
    ↓
解析 XML 响应 → 提取 title/facts/concepts/type/files
    ↓
写入 SQLite（结构化存储）
    ↓
同步到 ChromaDB（向量嵌入）

```

SDKAgent 有一个聪明的两阶段设计：

- **Init Prompt**：第一次调用时发送完整的角色定义——"你是一个代码观察压缩器，你的任务是把 tool 输出提炼成结构化笔记"
- **Continuation Prompt**：后续调用只发增量数据，复用上下文

---

## 检索系统：3 层渐进式披露

这是 Claude-Mem 在 token 效率上最巧妙的设计。

### 问题：历史记忆太多，全注入太贵

假设你有 200 条 observation，每条 500 token，全注入就是 100K tokens——这都快占满整个 context window 了。

### 解法：只给"目录"，需要时再查"全文"

Claude-Mem 在 v7 实现了 **Progressive Disclosure（渐进式披露）**：

```
Session 启动时自动注入（总计 ~1,500 tokens）：

Layer 1: 索引层 (~800 tokens)
┌─────────────────────────────────────────────────────┐
│ #201 [bugfix] Fixed JWT refresh token validation (2h ago)  │
│ #200 [feature] Added rate limiting middleware (5h ago)      │
│ #199 [refactor] Extracted auth service from monolith (1d ago)│
│ ... (共 50 条，只有标题和元数据)                              │
└─────────────────────────────────────────────────────┘

Layer 2: 样本层 (~500 tokens)
┌─────────────────────────────────────────────────────┐
│ 5 条最新的完整 observation                           │
│ (让 Claude 理解 observation 长什么样)                 │
└─────────────────────────────────────────────────────┘

Layer 3: 指令层 (~200 tokens)
┌─────────────────────────────────────────────────────┐
│ "如果你需要更多细节，使用 mem-search 查询"            │
│ "支持按关键词、类型、日期搜索"                        │
└─────────────────────────────────────────────────────┘

```

**效果**：v3 时代注入 ~25,000 tokens，v7 只需要 ~1,500 tokens。**减少 94%**。

工作流变成了：

1. Claude 看到索引，知道 #201 是修了 JWT 的 bug
2. 如果当前任务需要，Claude 主动调用 `` mem-search `` 查 #201 的详情
3. 只有被需要的记忆才占用 context window

这和你用搜索引擎的体验一样——你不会把整个互联网塞进脑子里，你看的是搜索结果的标题，感兴趣了再点进去看全文。

### 双数据库混合搜索

Claude-Mem 用两个数据库互补：

```
搜索请求："auth 相关的 bug"
    │
    ├── ChromaDB（语义搜索）
    │   "authentication bug" → 向量相似度匹配
    │   过滤条件：90 天内
    │   返回：observation IDs
    │
    └── SQLite + FTS5（结构化搜索）
        type = "bugfix"
        files_modified LIKE "%auth%"
        全文搜索：title/facts/concepts
        
两者结果合并 → 按相关度排序 → 返回

```

- **ChromaDB** 擅长模糊语义匹配（"认证问题" 能匹配到 "JWT token validation"）
- **SQLite FTS5** 擅长精确过滤（按 type、日期、文件名）
- 两者互补，比单用任何一个都好

---

## Session 摘要：结构化的工作日志

每个 session 结束时，Claude-Mem 生成一份结构化摘要（不是简单的对话总结）：

```
{
  "request": "修复认证系统的 token 刷新 bug",
  "investigated": "分析了 auth.py 的 refresh_token 函数，发现缺少过期检查",
  "learned": "PyJWT 的 decode() 默认不验证过期时间，需要显式传 verify_exp=True",
  "completed": "修复了 bug，添加了单元测试，通过 CI",
  "next_steps": "需要检查其他用到 token 的地方是否有同样问题"
}

```

这个设计非常实用——下次 session 启动时，你一眼就能看到上次做了什么、卡在哪里、下一步该干什么。

---

## Endless Mode：实时压缩（实验性）

标准 Claude Code 的一个隐性问题：每次 tool 调用的输出（1-10K+ tokens）都留在 context window 里，而 Claude 每次回复都要重新处理全部历史——这是 O(N²) 的复杂度。大约 50 次 tool 调用后，context window 就满了。

Endless Mode 的思路：**每次 tool 调用完立刻压缩，用 ~500 token 的 observation 替换原始输出**。

```
标准模式：
tool_output_1 (5K) + tool_output_2 (8K) + ... + tool_output_50 (3K)
= context 爆炸

Endless Mode：
observation_1 (500) + observation_2 (500) + ... + observation_50 (500)  
= 25K tokens，还有大量空间

```

代价是每次 tool 调用增加 60-90 秒延迟（等 AI 压缩完成）。目前还在 beta。

---

## Claude-Mem 没做什么？

理解一个系统"没做什么"和"做了什么"一样重要：

1. **没有 Core Memory 层**：没有"始终可见的核心事实 KV"。用户的名字、项目技术栈这些信息不会被钉在 prompt 里，需要通过搜索才能访问。
2. **没有 Semantic Memory 的冲突解决**：如果你上周说"用 PostgreSQL"，这周说"迁移到 MongoDB"，两条 observation 都会存着，但没有自动的矛盾检测和更新机制。
3. **没有 Procedural Memory**：不会根据过去的交互优化 Claude 的行为模式。每次 session 的行为完全由 Claude Code 本身的 system prompt 决定。
4. **不管理 session 内的 context**：当前 session 内的 context window 管理是 Claude Code 自己的事，Claude-Mem 只管跨 session 的持久化。

简单说：**Claude-Mem 是一个纯粹的 Episodic Memory（情景记忆）系统**——它记录你做过什么，而不是你是谁、世界是什么样、行为该怎么优化。

---

## 一句话总结

> **Claude-Mem 的核心公式 = AI 压缩（10:1 到 100:1）+ 结构化存储（title/facts/concepts/type/files）+ 渐进式披露（先给目录再按需查全文，省 94% token）+ 双数据库混合搜索（语义 + 精确）。它是面向 coding session 的 Episodic Memory 最佳实践，但要成为完整的 Memory 系统，还需要补上 Core Memory 和 Procedural Memory。**