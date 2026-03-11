# AWS AgentCore Memory 架构调研报告

[Extension: com.atlassian.confluence.macro.core/toc]
> 以汽车保险理赔场景为例，深入解析 AgentCore Memory 的架构设计、核心概念与实战应用。

---

## 一、AgentCore Memory 是什么

Amazon Bedrock AgentCore Memory 是 AWS 在 2025 年 AWS Summit NYC 上发布的全托管 Agent 记忆服务。它解决的核心问题是：**LLM 天生无状态，每次对话都是一张白纸，开发者需要自己搭建复杂的记忆基础设施。**

AgentCore Memory 把这件事托管了。你不需要自己搭 Redis、向量数据库、写 embedding pipeline、做 session 管理——它提供一套 API，让 Agent 能够记住当前对话的上下文（短期记忆），也能从历史对话中提取有价值的知识长期保留（长期记忆）。

---

## 二、架构设计

### 2.1 整体架构：两层记忆 + 策略引擎

AgentCore Memory 的架构可以拆成三个核心组件：

```
┌──────────────────────────────────────────────────────────────┐
│                    Memory Resource（逻辑容器）                 │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │              Short-Term Memory（短期记忆）                │  │
│  │                                                        │  │
│  │  • 存储原始对话事件（Events）                              │  │
│  │  • 按 actorId + sessionId 组织                          │  │
│  │  • 不可变的追加写入（immutable append）                    │  │
│  │  • 支持 TTL 自动过期（最长 365 天）                        │  │
│  │  • 支持 Branching（对话分叉）和 Checkpointing（断点续传）  │  │
│  └──────────────────┬─────────────────────────────────────┘  │
│                     │                                        │
│                     │ CreateEvent 写入后异步触发               │
│                     ▼                                        │
│  ┌────────────────────────────────────────────────────────┐  │
│  │            Memory Strategies（策略引擎）                  │  │
│  │                                                        │  │
│  │  • Semantic Strategy   → 提取事实和知识                   │  │
│  │  • Summary Strategy    → 生成对话摘要                     │  │
│  │  • User Preference Strategy → 提取用户偏好               │  │
│  │  • Custom Strategy     → 自定义提取 prompt + 模型         │  │
│  │                                                        │  │
│  │  职责：Extraction（从原始对话中提取）                       │  │
│  │       Consolidation（与已有记忆合并去重）                   │  │
│  └──────────────────┬─────────────────────────────────────┘  │
│                     │                                        │
│                     │ 提取结果写入                             │
│                     ▼                                        │
│  ┌────────────────────────────────────────────────────────┐  │
│  │              Long-Term Memory（长期记忆）                 │  │
│  │                                                        │  │
│  │  • 存储提取后的结构化知识（Memory Records）                │  │
│  │  • 按 Namespace 层级组织                                 │  │
│  │  • 支持语义搜索（RetrieveMemoryRecords）                  │  │
│  │  • 支持精确列举（ListMemoryRecords）                      │  │
│  │  • 支持手动批量写入（BatchCreateMemoryRecords）            │  │
│  │  • 默认过滤 PII 敏感信息                                  │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘

```

### 2.2 数据流：从一条消息到一段记忆

一条用户消息在系统里的完整生命周期：

```
用户发消息 "我上周四在杭州追尾了"
        │
        ▼
  ① CreateEvent API（同步）
        │  写入 Short-Term Memory
        │  绑定到 actorId + sessionId
        │  返回 eventId
        │
        ▼
  ② Strategy Engine（异步，后台自动触发）
        │  Semantic Strategy → 提取: "客户在杭州发生追尾事故，全责"
        │  Summary Strategy → 更新: "客户报告追尾事故，等待材料上传..."
        │  User Preference Strategy → 本次无偏好信息，跳过
        │
        ▼
  ③ 写入 Long-Term Memory
        │  每条提取结果存入对应的 Namespace
        │  如果与已有记忆冲突 → Consolidation 合并
        │
        ▼
  ④ 未来某次对话中
        RetrieveMemoryRecords（语义搜索）
        → 按相关性返回历史记忆
        → Agent 注入到 prompt 中使用

```

### 2.3 三种内置提取策略

| 策略<br> | 提取什么<br> | 典型输出示例<br> | Namespace 粒度<br> |
| --- | --- | --- | --- |
| **Semantic Strategy**<br> | 对话中的事实、知识、实体关系<br> | "客户驾驶黑色比亚迪汉，对方为白色特斯拉 Model 3"<br> | 通常按 actorId（跨 session 聚合）<br> |
| **Summary Strategy**<br> | 每次对话的摘要<br> | "客户报告追尾事故(全责)，已有事故认定书，等待上传材料"<br> | 通常按 actorId + sessionId（每次对话一份）<br> |
| **User Preference Strategy**<br> | 用户的偏好、习惯、风格<br> | "客户希望通过微信沟通，不喜欢电话"<br> | 通常按 actorId（跨 session 共享）<br> |
此外还支持 **Custom Strategy**：你可以指定用哪个 LLM、用什么 prompt 来做提取。比如在保险场景中，你可以写一个自定义 prompt 让它专门提取"事故时间、地点、责任方、车辆信息、损伤描述"这些结构化字段。

### 2.4 API 全景

**短期记忆（Short-Term）操作：**

| API<br> | 作用<br> |
| --- | --- |
| `` CreateEvent ``<br> | 存入一条对话事件（消息或 blob 二进制数据）<br> |
| `` GetEvent ``<br> | 根据 eventId 精确获取一条事件<br> |
| `` ListEvents ``<br> | 按 actorId + sessionId 列出事件，支持 metadata 过滤<br> |
| `` DeleteEvent ``<br> | 删除单条事件<br> |
| `` ListSessions ``<br> | 列出某个 actor 的所有 session<br> |
| `` ListActors ``<br> | 列出所有 actor<br> |
**长期记忆（Long-Term）操作：**

| API<br> | 作用<br> |
| --- | --- |
| `` RetrieveMemoryRecords ``<br> | 语义搜索，按相关性返回最匹配的记忆<br> |
| `` ListMemoryRecords ``<br> | 按 namespace 精确列出记忆记录<br> |
| `` GetMemoryRecord ``<br> | 按 ID 精确获取一条记忆记录<br> |
| `` BatchCreateMemoryRecords ``<br> | 手动批量写入记忆（绕过 Strategy，直接写入）<br> |
| `` BatchUpdateMemoryRecords ``<br> | 批量更新<br> |
| `` BatchDeleteMemoryRecords ``<br> | 批量删除<br> |
| `` StartMemoryExtractionJob ``<br> | 手动触发一次提取任务<br> |
**控制面（Control Plane）操作：**

| API<br> | 作用<br> |
| --- | --- |
| `` CreateMemory ``<br> | 创建 Memory Resource（配置策略、TTL、加密等）<br> |
| `` UpdateMemory ``<br> | 更新配置（增删策略）<br> |
| `` GetMemory `` / `` ListMemories ``<br> | 查看 Memory Resource<br> |
| `` DeleteMemory ``<br> | 删除整个 Memory Resource<br> |

---

## 三、核心概念：Actor、Session 和 Namespace

这是理解 AgentCore Memory 数据组织方式的关键。

### 3.1 三层 ID 体系

```
memoryId（系统生成）
  ├── actorId（开发者自己定义传入）
  │     ├── sessionId（开发者自己定义传入）
  │     │     ├── eventId（系统自动生成）
  │     │     ├── eventId
  │     │     └── ...
  │     ├── sessionId
  │     │     └── ...
  │     └── ...
  └── actorId
        └── ...

```

**memoryId** 是创建 Memory Resource 时系统返回的全局唯一标识。一个公司通常只有一个或几个 Memory Resource（比如理赔助手一个、客服助手一个）。

**actorId** 标识"谁"——在保险场景中就是投保人。这个 ID 是你的应用代码自己传入的，通常直接使用你业务系统中的用户唯一标识（如 policyholder_id）。AgentCore 不负责身份识别，你的 Auth 系统识别出用户是谁，你就把对应的 ID 传给它。

**sessionId** 标识"哪次交互"——在保险场景中就是某次理赔案件。同样由你的应用代码传入，通常对应业务系统中的工单号或 claim_id。同一个 actorId 下可以有无限个 sessionId。

**eventId** 是唯一由系统自动生成的 ID。每次调用 `` CreateEvent `` 后，响应中会返回一个唯一的 eventId。你通常不需要关心它，除非你要做 Branching（对话分叉需要指定 rootEventId）。

### 3.2 actorId 的本质：数据库的 Partition Key

理解 actorId 最关键的一点：**它不是 AgentCore 帮你发现的，是你的应用层告诉 AgentCore 的。**

```
用户登录 App
    │
    ▼
你的 Auth 系统识别出: 这是张伟，policyholder_id = "zhangwei-001"
    │
    ▼
你的应用代码: actorId = "policyholder-zhangwei-001"
    │
    ▼
传入 CreateEvent / RetrieveMemoryRecords
    │
    ▼
AgentCore 按这个 key 存取数据

```

"下次怎么知道用哪个 actorId"这个问题本质上不存在——因为你的 Auth 系统永远知道当前用户是谁。就像你查 MySQL 不需要"记住"某行的 primary key，你的应用逻辑天然知道该查什么条件。

### 3.3 sessionId 的本质：业务事件的唯一标识

sessionId 把同一个用户的不同交互场景隔离开来：

- 张伟 2 月的追尾理赔 → `` session: claim-2026-0212-001 ``
- 张伟 5 月的新事故理赔 → `` session: claim-2026-0515-002 ``
- 张伟 8 月的保单续保咨询 → `` session: renewal-2026-0801-001 ``

每个 session 内部的 events 按时间顺序存储，通过 `` ListEvents `` 可以完整还原当时的对话。

### 3.4 Namespace：长期记忆的文件夹系统

Namespace 用来组织长期记忆，就像文件系统的路径。创建 Memory Resource 时，你在每个 Strategy 上定义 namespace 模板：

```
strategies=[
    {
        'summaryMemoryStrategy': {
            'name': 'ClaimSummarizer',
            'namespaces': ['/claims/{actorId}/{sessionId}/summary']
        }
    },
    {
        'semanticMemoryStrategy': {
            'name': 'ClaimFacts',
            'namespaces': ['/claims/{actorId}/facts']
        }
    },
    {
        'userPreferenceMemoryStrategy': {
            'name': 'CustomerPrefs',
            'namespaces': ['/customers/{actorId}/preferences']
        }
    }
]

```

运行时，`` {actorId} `` 和 `` {sessionId} `` 会被实际值替换：

```
/claims/policyholder-zhangwei-001/claim-2026-0212-001/summary   ← 2月追尾的摘要
/claims/policyholder-zhangwei-001/claim-2026-0515-002/summary   ← 5月事故的摘要
/claims/policyholder-zhangwei-001/facts                         ← 张伟所有理赔的事实（跨case聚合）
/customers/policyholder-zhangwei-001/preferences                ← 张伟的沟通偏好（跨case共享）

```

Namespace 设计的精妙之处在于 **粒度可控**：

- 带 `` {sessionId} `` → 每个 case 独立一份（如摘要）
- 只带 `` {actorId} `` → 同一客户所有 case 共享（如事实知识、偏好）
- 什么都不带 → 全局共享（如产品知识库）

---

## 四、场景实战：汽车保险理赔

### 4.1 场景设定

**第一次理赔（2月）**：张伟在杭州追尾了一辆特斯拉，全责。他打开保险 App 跟 AI 理赔助手对话，提交事故信息。

**第二次理赔（5月）**：张伟侧翻进了路边花坛，单车事故。他再次打开 App 理赔。

我们要解决的核心问题：

1. 第一次理赔时，怎么精确存储和关联数据？
2. 第二次理赔时，Agent 怎么"认出"这是同一个客户，并调取历史理赔信息？
3. 跨 case 的知识怎么共享？

### 4.2 初始化：创建 Memory Resource（仅一次）

```
import boto3
import time

control_client = boto3.client('bedrock-agentcore-control')
data_client = boto3.client('bedrock-agentcore')

# 创建 Memory Resource —— 整个理赔系统共用一个
response = control_client.create_memory(
    name="AutoClaimAssistantMemory",
    description="汽车保险理赔助手的记忆系统",
    eventExpiryDuration=180,  # 原始对话保留 180 天
    memoryStrategies=[
        # 策略1: 每次理赔生成一份对话摘要
        {
            'summaryMemoryStrategy': {
                'name': 'ClaimSessionSummarizer',
                'namespaces': ['/claims/{actorId}/{sessionId}/summary']
            }
        },
        # 策略2: 提取理赔相关的事实知识（跨 case 聚合）
        {
            'semanticMemoryStrategy': {
                'name': 'ClaimFactExtractor',
                'namespaces': ['/claims/{actorId}/facts']
            }
        },
        # 策略3: 提取客户偏好（跨 case 共享）
        {
            'userPreferenceMemoryStrategy': {
                'name': 'CustomerPreferences',
                'namespaces': ['/customers/{actorId}/preferences']
            }
        }
    ]
)

MEMORY_ID = response['memory']['id']
# → "mem-AutoClaimAs-xK9mP2nQ7r"

```

### 4.3 第一次理赔：张伟追尾特斯拉

**Step 1: 确定 actorId 和 sessionId**

```
# actorId → 从你的 Auth + 业务系统获取
# 张伟登录 App → Auth 识别 → 查保单数据库 → 得到投保人 ID
ACTOR_ID = "policyholder-zhangwei-001"

# sessionId → 从你的工单系统获取
# 张伟发起新理赔 → 工单系统创建理赔单 → 生成 claim_id
SESSION_ID = "claim-2026-0212-001"

```

**Step 2: 对话过程中持续存入 Events**

```
# ===== 第一轮对话 =====
data_client.create_event(
    memoryId=MEMORY_ID,
    actorId=ACTOR_ID,
    sessionId=SESSION_ID,
    eventTimestamp=int(time.time() * 1000),
    payload=[
        {
            'conversational': {
                'role': 'USER',
                'content': {'text': '我上周四在杭州西湖区文三路追尾了前面的车，我全责。'}
            }
        },
        {
            'conversational': {
                'role': 'ASSISTANT',
                'content': {'text': '张先生您好，我来帮您处理这次理赔。请问当时有没有报警？交警出具了事故认定书吗？'}
            }
        }
    ],
    metadata={
        'claim_type': {'stringValue': 'rear_end_collision'},
        'location': {'stringValue': 'hangzhou_xihu'}
    }
)

# ===== 第二轮对话 =====
data_client.create_event(
    memoryId=MEMORY_ID,
    actorId=ACTOR_ID,
    sessionId=SESSION_ID,
    eventTimestamp=int(time.time() * 1000),
    payload=[
        {
            'conversational': {
                'role': 'USER',
                'content': {'text': '报了警，有事故认定书。对方是白色特斯拉 Model 3，我开的是黑色比亚迪汉 EV。对方后杠凹了一块，我前杠裂了。另外以后理赔进度请通过微信通知我，不要打电话。'}
            }
        },
        {
            'conversational': {
                'role': 'ASSISTANT',
                'content': {'text': '收到，我已记录所有信息，后续进度会通过微信通知您。请上传事故认定书和现场照片，我这边尽快推进。'}
            }
        }
    ]
)

# ===== 第三轮对话 =====
data_client.create_event(
    memoryId=MEMORY_ID,
    actorId=ACTOR_ID,
    sessionId=SESSION_ID,
    eventTimestamp=int(time.time() * 1000),
    payload=[
        {
            'conversational': {
                'role': 'USER',
                'content': {'text': '照片和认定书都传了，你看一下。'}
            }
        },
        {
            'conversational': {
                'role': 'ASSISTANT',
                'content': {'text': '材料已收到，初步定损金额为 8,500 元（对方维修 5,200 元 + 己方维修 3,300 元）。定损报告会在 2 个工作日内出具，届时通过微信通知您。'}
            }
        }
    ]
)

```

**Step 3: 后台异步提取（自动发生，无需调用）**

Strategy Engine 会自动分析上述对话，提取出：

```
Semantic Strategy 提取的事实 → /claims/policyholder-zhangwei-001/facts
  • "2026年2月在杭州西湖区文三路发生追尾事故，张伟全责"
  • "张伟驾驶黑色比亚迪汉 EV，对方为白色特斯拉 Model 3"
  • "已报警，有交警事故认定书"
  • "初步定损 8,500 元（对方 5,200 + 己方 3,300）"

Summary Strategy 提取的摘要 → /claims/policyholder-zhangwei-001/claim-2026-0212-001/summary
  • "客户张伟在杭州西湖区发生追尾事故(全责)，涉及比亚迪汉 EV 与特斯拉 Model 3。
     已报警并有事故认定书。材料已上传，初步定损 8,500 元，定损报告待出具。"

User Preference Strategy 提取的偏好 → /customers/policyholder-zhangwei-001/preferences
  • "客户偏好通过微信接收理赔进度通知，不喜欢电话沟通"

```

此时整个 Namespace 树长这样：

```
/claims/policyholder-zhangwei-001/
  ├── claim-2026-0212-001/
  │     └── summary → "客户张伟在杭州追尾...定损8500元..."
  └── facts
        ├── "2026年2月杭州追尾，全责..."
        ├── "比亚迪汉 EV vs 特斯拉 Model 3..."
        └── "初步定损8500元..."

/customers/policyholder-zhangwei-001/
  └── preferences → "偏好微信通知，不喜欢电话"

```

### 4.4 第二次理赔：张伟侧翻进花坛

三个月后，张伟又出事了。他打开 App，发起新理赔。

**Step 1: 确定 actorId 和 sessionId**

```
# actorId → 同一个人，ID 不变
ACTOR_ID = "policyholder-zhangwei-001"

# sessionId → 新的理赔案件，新的 ID
SESSION_ID = "claim-2026-0515-002"

```

**Step 2: Agent 启动时加载历史记忆**

这是跨 session 记忆的关键环节——在对话开始前，Agent 先检索这个客户的历史信息：

```
# ① 检索客户偏好（跨 case 共享）
prefs = data_client.retrieve_memory_records(
    memoryId=MEMORY_ID,
    namespace=f"/customers/{ACTOR_ID}/preferences",
    searchCriteria={
        'searchQuery': '客户沟通偏好',
        'topK': 5
    }
)
# → 返回: "偏好微信通知，不喜欢电话"

# ② 检索历史理赔事实（跨 case 聚合）
facts = data_client.retrieve_memory_records(
    memoryId=MEMORY_ID,
    namespace=f"/claims/{ACTOR_ID}/facts",
    searchCriteria={
        'searchQuery': '历史事故记录 车辆信息',
        'topK': 10
    }
)
# → 返回: "2月杭州追尾，比亚迪汉 EV，定损8500元..."

# ③ 检索历史 case 的摘要（prefix match 匹配所有 session）
summaries = data_client.retrieve_memory_records(
    memoryId=MEMORY_ID,
    namespace=f"/claims/{ACTOR_ID}",  # 不加 sessionId → prefix match 所有 case
    searchCriteria={
        'searchQuery': '理赔处理结果',
        'topK': 5
    }
)

# ④ 注入 system prompt
system_prompt = f"""你是平安保险的 AI 理赔助手。

【客户信息】
客户姓名: 张伟
投保人ID: {ACTOR_ID}
当前理赔案件: {SESSION_ID}

【客户偏好】
{format_records(prefs)}

【历史理赔记录】
{format_records(facts)}

【历史理赔摘要】
{format_records(summaries)}

请基于以上信息提供个性化的理赔服务。注意：
- 客户偏好微信通知，不要建议打电话
- 客户此前有过追尾理赔经历，注意关联风控分析
"""

```

**Step 3: 新对话进行**

```
# 张伟发消息
data_client.create_event(
    memoryId=MEMORY_ID,
    actorId=ACTOR_ID,
    sessionId=SESSION_ID,  # 注意: 新的 sessionId
    eventTimestamp=int(time.time() * 1000),
    payload=[
        {
            'conversational': {
                'role': 'USER',
                'content': {'text': '我今天在临安山路上侧翻了，撞到路边花坛，单车事故。'}
            }
        },
        {
            'conversational': {
                'role': 'ASSISTANT',
                'content': {
                    'text': '张先生您好，抱歉再次遇到这种情况。'
                            '我看到您今年 2 月在杭州也有过一次追尾理赔，那次已经结案了。'
                            '这次是单车事故，我来帮您处理。请问人有没有受伤？车辆还能正常行驶吗？'
                            '另外，后续进度我会通过微信通知您。'
                }
            }
        }
    ]
)

```

注意 Agent 的回复中：

- **"2 月在杭州也有过一次追尾"** → 来自 Long-Term `` /claims/{actorId}/facts `` 的跨 case 记忆
- **"通过微信通知您"** → 来自 Long-Term `` /customers/{actorId}/preferences `` 的跨 case 偏好

**Step 4: 提取后 Namespace 树更新**

```
/claims/policyholder-zhangwei-001/
  ├── claim-2026-0212-001/
  │     └── summary → "2月追尾...定损8500元...已结案"
  ├── claim-2026-0515-002/
  │     └── summary → "5月临安侧翻单车事故...处理中..."   ← 新增
  └── facts
        ├── "2026年2月杭州追尾，全责..."
        ├── "比亚迪汉 EV vs 特斯拉 Model 3..."
        ├── "初步定损8500元..."
        └── "2026年5月临安山路侧翻单车事故..."           ← 新增（与旧事实共存）

/customers/policyholder-zhangwei-001/
  └── preferences → "偏好微信通知，不喜欢电话"             ← 不变

```

### 4.5 精确检索 vs 跨 Case 检索

这是实际开发中最常见的两类查询场景：

**场景 A：精确查某一次理赔的对话历史（短期记忆）**

```
# 知道具体的 sessionId → 精确列出该 case 的所有对话
events = data_client.list_events(
    memoryId=MEMORY_ID,
    actorId="policyholder-zhangwei-001",
    sessionId="claim-2026-0212-001",  # 精确到 2 月追尾那次
    maxResults=50
)
# → 按时间顺序返回那次理赔的所有对话事件

```

**场景 B：不记得 sessionId，先列出客户所有 session**

```
sessions = data_client.list_sessions(
    memoryId=MEMORY_ID,
    actorId="policyholder-zhangwei-001"
)
# → 返回:
#   [
#     {"sessionId": "claim-2026-0212-001", ...},
#     {"sessionId": "claim-2026-0515-002", ...}
#   ]

```

**场景 C：查某次理赔的摘要（长期记忆，精确 namespace）**

```
records = data_client.retrieve_memory_records(
    memoryId=MEMORY_ID,
    namespace="/claims/policyholder-zhangwei-001/claim-2026-0212-001/summary",
    searchCriteria={'searchQuery': '理赔结果', 'topK': 3}
)
# → 只返回 2 月追尾的摘要

```

**场景 D：跨 Case 搜索所有理赔事实（长期记忆，prefix match）**

```
records = data_client.retrieve_memory_records(
    memoryId=MEMORY_ID,
    namespace="/claims/policyholder-zhangwei-001/facts",  # 不含 sessionId
    searchCriteria={'searchQuery': '事故车辆信息', 'topK': 10}
)
# → 返回跨所有 case 的车辆相关事实:
#   "比亚迪汉 EV 追尾特斯拉 Model 3..."
#   "临安山路侧翻..."

```

**场景 E：跨 Case 搜索所有摘要（prefix match 更宽）**

```
records = data_client.retrieve_memory_records(
    memoryId=MEMORY_ID,
    namespace="/claims/policyholder-zhangwei-001",  # 更宽的 prefix
    searchCriteria={'searchQuery': '理赔处理进度', 'topK': 10}
)
# → 返回所有 case 的摘要 + 事实（因为 prefix 覆盖了 /facts 和所有 /summary）

```

**场景 F：用 metadata 过滤特定类型的事故（短期记忆）**

```
events = data_client.list_events(
    memoryId=MEMORY_ID,
    actorId="policyholder-zhangwei-001",
    sessionId="claim-2026-0212-001",
    metadataFilters=[{
        'left': {'metadataKey': 'claim_type'},
        'operator': 'EQUALS',
        'right': {'metadataValue': {'stringValue': 'rear_end_collision'}}
    }]
)
# → 只返回标记为"追尾"类型的事件

```

---

## 五、跨 Session / 跨 Case 记忆的设计模式

这是 AgentCore Memory 在保险场景中最关键的设计决策——**通过 Namespace 粒度控制记忆的隔离与共享**。

### 5.1 隔离 vs 共享的 Namespace 设计

```
完全隔离（每个 case 独立）:
  namespace = "/claims/{actorId}/{sessionId}/..."
  → 2月追尾的摘要 和 5月侧翻的摘要 互不干扰
  → 适合: 对话摘要、case-specific 的处理记录

跨 case 共享（同一客户聚合）:
  namespace = "/claims/{actorId}/..."  (无 sessionId)
  → 所有 case 的事实知识汇聚在一起
  → 适合: 事实知识、客户画像、车辆信息

全局共享（所有客户共用）:
  namespace = "/knowledge/products/..."  (无 actorId)
  → 保险产品信息、理赔流程规则
  → 适合: 通过 BatchCreateMemoryRecords 手动导入的公共知识

```

### 5.2 推荐的 Namespace 架构（保险理赔）

```
strategies=[
    # 策略1: Case 级别摘要（隔离）
    {
        'summaryMemoryStrategy': {
            'name': 'CaseSummarizer',
            'namespaces': ['/cases/{actorId}/{sessionId}/summary']
        }
    },
    # 策略2: 客户级别事实（跨 case 共享）
    {
        'semanticMemoryStrategy': {
            'name': 'CustomerFactExtractor',
            'namespaces': ['/customers/{actorId}/facts']
        }
    },
    # 策略3: 客户偏好（跨 case 共享）
    {
        'userPreferenceMemoryStrategy': {
            'name': 'CustomerPreferences',
            'namespaces': ['/customers/{actorId}/preferences']
        }
    },
    # 策略4（自定义）: 提取结构化的事故信息（case 级别隔离）
    # → 用 Custom Strategy 指定专门的提取 prompt
]

```

可视化：

```
/cases/
  └── policyholder-zhangwei-001/
        ├── claim-2026-0212-001/
        │     └── summary  ← 2月追尾的完整摘要
        ├── claim-2026-0515-002/
        │     └── summary  ← 5月侧翻的完整摘要
        └── claim-2026-0901-003/
              └── summary  ← 9月新 case 的摘要（未来）

/customers/
  └── policyholder-zhangwei-001/
        ├── facts          ← 所有 case 的事实汇聚在此
        │     ├── "驾驶比亚迪汉 EV"
        │     ├── "2月杭州追尾，全责，定损8500元"
        │     ├── "5月临安侧翻，单车事故..."
        │     └── ...
        └── preferences    ← 偏好只有一份，所有 case 共用
              └── "偏好微信通知"

```

### 5.3 Agent 启动时的记忆加载策略

```
async def load_memory_for_new_session(memory_id, actor_id, session_id):
    """新 case 开始时，加载所有相关记忆"""
    
    # 1. 客户偏好 → 注入 system prompt（模拟 L1 Core Identity）
    preferences = retrieve_memory_records(
        namespace=f"/customers/{actor_id}/preferences",
        query="客户偏好和沟通方式",
        topK=10
    )
    
    # 2. 客户事实 → 注入 system prompt 或作为参考
    facts = retrieve_memory_records(
        namespace=f"/customers/{actor_id}/facts",
        query="客户历史理赔 车辆信息 事故记录",
        topK=15
    )
    
    # 3. 历史 case 摘要 → 注入 system prompt 作为背景
    past_summaries = retrieve_memory_records(
        namespace=f"/cases/{actor_id}",  # prefix match 所有 case
        query="历史理赔概况",
        topK=5
    )
    
    # 4. 如果是恢复一个已有 case（而非新建），加载当前 session 的对话
    if is_existing_session(session_id):
        recent_events = list_events(
            actorId=actor_id,
            sessionId=session_id,
            maxResults=20
        )
    
    return build_system_prompt(preferences, facts, past_summaries)

```

---

## 六、AgentCore Memory 的设计边界与注意事项

### 6.1 它做了什么

- 短期记忆的存储和检索（Events 的 CRUD）
- 长期记忆的异步提取和合并（Strategy Engine）
- 语义搜索（基于向量 embedding 的 RetrieveMemoryRecords）
- Namespace 层级隔离与 IAM 权限控制
- 对话分叉（Branching）和断点续传（Checkpointing）
- 全托管，无需自建数据库或向量引擎

### 6.2 它没做什么（需要你自己补）

**没有 "always pinned" 的核心身份层。** AgentCore 没有类似 Letta Core Memory 那样"始终驻留在 prompt 中"的 key-value 层。客户的名字、偏好这些关键信息，虽然可以通过 User Preference Strategy 提取并存储，但使用时仍需主动调用 `` RetrieveMemoryRecords `` 检索。你需要在 Agent 启动时自己加载并注入 system prompt。

**没有 Hot Path 实时更新。** 长期记忆的提取是异步的，有延迟。如果客户在对话中说"我换了手机号"，这个信息不会立即进入长期记忆。对于时效性要求高的信息，你需要自己用 `` BatchCreateMemoryRecords `` 或 `` BatchUpdateMemoryRecords `` 做即时写入。

**没有 Context Window 自动管理。** AgentCore 不管你的 LLM 上下文窗口是否溢出。它只负责存取数据，不负责决定"哪些消息该压缩、哪些该保留"。Letta 的 Message Buffer 自动 overflow → summarize → archive 机制在这里不存在，你需要在 Agent 框架层（如 Strands / LangGraph）自己实现。

**没有行为模式学习。** 它不会从历史交互中提取"什么样的沟通方式更有效"并自动优化 Agent 行为。LangMem 的 Procedural Memory / Prompt Optimizer 在 AgentCore 中没有对应。

### 6.3 延迟特性

| 操作<br> | 延迟<br> |
| --- | --- |
| CreateEvent（写入短期记忆）<br> | 同步，毫秒级<br> |
| ListEvents（读取短期记忆）<br> | 同步，毫秒级<br> |
| 策略提取（短期 → 长期）<br> | 异步，通常数十秒到一分钟<br> |
| RetrieveMemoryRecords（语义搜索长期记忆）<br> | 同步，百毫秒级<br> |
| BatchCreateMemoryRecords（手动写入长期记忆）<br> | 同步，毫秒级<br> |

---

## 七、总结

AgentCore Memory 的核心设计哲学是**"原始数据同步写入，知识异步提取，按 Namespace 层级组织"**。它通过 actorId + sessionId 的两级 key 把不同用户、不同交互场景的数据隔离开，再通过 Namespace 的粒度控制决定哪些知识跨 session 共享、哪些 session 独立保存。

在保险理赔场景中，actorId 对应投保人，sessionId 对应理赔案件。Agent 能"记住"客户的关键靠的不是魔法，而是你的应用层在每次交互时传入正确的 actorId（让 AgentCore 知道"这是谁"），并在 session 开始时主动检索该客户的历史记忆（让 Agent 带着历史上下文工作）。

跨 Case 的记忆共享，核心是 Namespace 设计：把事实知识和偏好放到只含 `` {actorId} `` 的 namespace 下（自然跨 session 共享），把 case-specific 的摘要放到含 `` {sessionId} `` 的 namespace 下（自然隔离）。检索时通过 namespace prefix match 控制搜索范围——窄 prefix 精确到单个 case，宽 prefix 覆盖所有 case。

这套设计在 Enterprise 场景下足够可靠，但开发者仍需在 Agent 框架层补齐核心身份层的 prompt 注入、关键信息的实时更新、以及上下文窗口的压缩管理。