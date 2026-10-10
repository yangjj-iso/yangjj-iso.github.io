---
layout: post
title: "Context Engineering：上下文工程与多 Agent 设计"
description: "从保存、选择、压缩和隔离四类操作出发，梳理上下文工程的预算、记忆、检索、工具路由与多 Agent 设计。"
date: 2026-06-13
tags: [Agent, Context Engineering, 上下文工程, Multi-Agent]
---

假设要做一个智能客服，让模型依据企业的产品手册回答问题。最直接的做法是把整本手册塞进每一次请求，但很快会碰到三个问题：资料、对话和工具输出越积越多，迟早超出窗口；过期版本、无关产品线和互相矛盾的规则混在一起，模型容易抓错重点；每次都重复注入长文档，token 成本和延迟一路上涨。Context Engineering（上下文工程）处理的就是这类问题：在有限的上下文窗口里，只给模型完成当前任务所需的信息。

<!--more-->

本文主要参考了下面两篇文章，对理解多 Agent 设计和上下文工程很有帮助：

1. [Anthropic：How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
2. [Cognition：Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents)

## Context 是什么

从外部看，大模型很像一个函数：

```text
output = model(input)
```

这里的 input 远不止用户最后那句话。一次真实的聊天或 Agent 请求里，模型看到的通常还有系统指令和安全策略、对话背景、检索到的文档和数据库记录、可用工具的名称和参数说明、工具返回的结果和错误日志、历史消息和长期记忆，以及当前任务的状态和待办事项。这些合在一起才是本轮的 Context。没进入上下文的信息，对模型来说就等于不存在。

Context Window（上下文窗口）是一次调用能容纳的 token 上限。Token 是文本切分后的基本单位，可能是一个汉字、一个词、一个标点或更小的子词。窗口里还要给输出留位置，所以真正能分给资料的预算是：

```text
可分配上下文预算
  = 模型窗口上限
  - 系统指令
  - 用户请求
  - 工具定义
  - 输出预留
  - 安全余量
```

窗口在变大，但始终是有限资源；出于成本和延迟考虑，也不会每个请求都用最大的模型。何况“能放下”不等于“应该放”：未经筛选的资料会让模型混淆版本、把几个答案平均一下，或者在事实冲突时给出含糊的结论。所以上下文质量首先是信息组织问题，其次才是容量问题。

## 和 Prompt Engineering 的区别

Prompt Engineering 关心“这一轮怎么把要求说清楚”，Context Engineering 关心“这一轮该把哪些信息给模型看”。它不改模型结构，也不重新训练，靠的是设计、筛选、压缩、持久化和传递模型输入，来提升准确性、稳定性和成本效率。之所以叫“工程”，是因为要处理的是数据流、状态、权限、预算、故障和评估：上下文是一条动态管道，不是一段静态字符串。

几个相关领域的分工可以对比着看：

| 领域 | 主要问题 | 典型对象 |
| --- | --- | --- |
| Prompt Engineering | 这一轮如何把要求说清楚 | 角色、任务、示例、输出格式 |
| Context Engineering | 这一轮把哪些信息给模型看 | 记忆、检索、工具、历史、规则 |
| Agent Engineering | 模型如何感知环境并采取行动 | 工具调用、规划、执行、反馈 |
| Workflow / Loop Engineering | 多步任务如何推进和停止 | 状态、验证器、重试、人工介入 |

例如，“帮我修复这个测试”是 Prompt；把失败日志、相关代码、项目规则和测试命令按优先级放入请求，是 Context；让模型读取代码、修改文件、运行测试并根据结果继续，是 Agent；规定最多尝试五次、测试通过就停止、连续重复失败就升级给人，是 Loop。

Memory 和 RAG 也常和它混在一起。Memory 管信息长期存在哪里，RAG 管怎么从外部知识里召回相关内容，两者都是上下文工程的组件；上下文工程还包括静态规则、工具路由、历史压缩、隔离、权限过滤和预算控制，是系统层的总称。

这个词最近被频繁提起，我认为有两个直接原因。一是模型已经足够强，很多时候回答不好，问题出在输入不够清晰、完整、相关，模型越强，输入质量的影响越明显。二是 Agent 开始大量使用工具，每次搜索、读文件、跑命令或调 API 都会往上下文里加一段内容，任务跑得越久，历史和工具结果越容易占满窗口，进而影响下一步决策。

## 四类上下文操作

借鉴 LangChain 的思路，上下文工程可以归纳为四类操作：

| 操作 | 要解决的问题 | 常见实现 |
| --- | --- | --- |
| 保存 Context | 信息如何跨轮次、跨会话持久化 | 记忆库、状态表、文件、向量库 |
| 选择 Context | 海量信息中哪些应该进入本轮 | 静态规则、RAG、工具路由、重排序 |
| 压缩 Context | 历史和工具结果如何减少占用 | 摘要、截断、滚动摘要、结构化状态 |
| 隔离 Context | 不同模块如何避免相互污染 | 子 Agent、独立会话、权限域、结果协议 |

四者串起来是一条上下文生命周期：

![上下文生命周期：保存、选择、压缩、隔离、执行与记录]({{ "/images/posts/context-engineering/context-lifecycle.svg" | relative_url }})

## 保存：让信息留下来

保存是先对上下文做筛选或总结，再放进内存、文件、数据库等外部存储，需要时再取回。比如用户在对话里说：

> 我主要使用 TypeScript，项目采用 pnpm，提交代码前必须运行 pnpm test。

每次都让用户重说体验很差，永远附带整段历史又太贵。更合理的是存成一条结构化记忆，下次只在相关任务里取回：

```json
{
  "memory_id": "pref-001",
  "type": "user_preference",
  "content": "用户偏好 TypeScript；项目使用 pnpm；提交前运行 pnpm test",
  "scope": "user:yangj/project:demo",
  "source": "conversation:2026-06-13T10:00:00+08:00",
  "confidence": 0.92,
  "updated_at": "2026-06-13T10:00:00+08:00"
}
```

写入之前先要判断值不值得存。稳定的用户偏好、项目约束和编码规范、已确认的设计决策、长任务的目标/进度/失败尝试，以及经过验证、带来源和有效期的事实，适合保存；一次性的闲聊、未验证的模型猜测、没有权限控制的敏感原文、已过期的临时状态，不应直接写入。每条记忆至少要带上 scope、source、confidence 和 updated_at，没有来源和时间的记忆，以后很容易变成无法追溯的“幽灵事实”。

保存之后，下次取什么是另一回事。记忆系统还要按用户、项目、会话或任务划分作用域，用关键词、向量、标签或结构化条件检索，按相关性、时效和可信度重排，注入前去重、截断、格式化，并允许用户查看、修改和删除记忆。以 ChatGPT 的记忆功能为例，记忆库负责保存，后续请求里召回相关记忆属于“选择”，这两步在设计上应该分开。

## 选择：只把相关信息放进本轮

选择是四类操作里最核心的一类，很大程度上决定了系统的准确性和效率，分为静态和动态两种。

### 静态选择

静态选择是把每次都必须遵守的信息固定放进请求，比如安全规则和拒答边界、技术栈和目录约定、代码格式和测试命令、输出 schema、工具调用的通用限制。Cursor 的 rules 文件、Claude Code 的 CLAUDE.md 都是这个思路。静态层要短小、稳定、优先级明确，只放不管用户问什么都不能违背的内容。把整本开发手册当成规则，既吞掉每次请求的预算，也让真正重要的约束被淹没；其余资料应放到动态检索层。一个规则文件可以写成：

```markdown
# 项目规则

- 使用 TypeScript，包管理器为 pnpm。
- 只修改当前任务涉及的目录，禁止删除测试。
- 修改后先运行 pnpm test，再运行 pnpm lint。
- 不得读取或输出 .env、密钥和生产数据。
- 如果需求与现有 API 契约冲突，停止并请求确认。
```

### 动态选择与 RAG

动态选择根据用户问题、任务状态和执行阶段挑选内容：从记忆库召回相关偏好，从文档库检索产品规则，在代码仓库定位相关模块，从几十上百个工具里挑出本轮可能用到的，或者根据上一步报错只补读相关文件。

RAG（Retrieval-Augmented Generation，检索增强生成）常被当作独立技术，从上下文工程的角度看，它就是动态选择的一条典型流水线：

```text
用户问题
  -> 查询改写 / 意图识别
  -> 文档召回
  -> 过滤权限、版本和时间
  -> 重排序
  -> 去重、切片、压缩
  -> 拼装带来源的上下文
  -> 交给模型生成
```

最小的伪代码实现：

```python
def build_dynamic_context(query, user, budget):
    candidates = retriever.search(query, top_k=30)
    candidates = [item for item in candidates if can_read(user, item)]
    candidates = [item for item in candidates if not item.is_expired]

    ranked = reranker.rank(query, candidates)
    selected = pack_until_budget(ranked, budget=budget,
                                 reserve_source=True,
                                 remove_duplicates=True)

    return render_with_citations(selected)
```

top_k=30 这个数字不重要，重要的是检索结果要经过权限过滤、版本过滤、去重和预算控制。只按向量相似度召回，很可能把旧版本、无权限或与任务目标不符的文档送给模型。向量相似度也处理不了逻辑冲突，所以语义召回只是第一步，后面还要接元数据过滤、重排序、来源校验和冲突检测。

### 工具路由

Agent 有几十个工具时，把所有工具名和完整 schema 塞进每一轮，上下文会变长，模型也更容易选错工具。常见做法是两阶段路由：

```text
用户问题
  -> 小型分类器 / 规则路由
  -> 候选工具集合
  -> 模型选择具体工具并填写参数
```

工具定义也可以分层：工具名和一句话用途提前提供，被选中的工具才注入完整参数 schema。

## 压缩：让长任务不越跑越差

Agent 跑起来以后，历史里最占空间的往往是模型的长篇输出、工具结果、大段代码和日志、重复的错误堆栈，以及已经完成却还留在对话里的中间过程。不处理的话，窗口很快被挤满，模型要么超限，要么在旧信息里找不到重点。

只保留最后 N 条消息最便宜，但可能丢掉关键决策。更稳妥的做法是先分清哪些必须保留：

| 优先级 | 建议保留的内容 |
| --- | --- |
| P0 | 原始目标、安全约束、权限边界、最终验收标准 |
| P1 | 已确认的事实、关键代码改动、测试结果、外部证据 |
| P2 | 当前假设、未解决问题、下一步计划 |
| P3 | 重复日志、已处理的中间输出、无关闲聊 |

压缩结果最好写成结构化的任务状态，并附上证据：

```markdown
## Task State

- 目标：修复 UserServiceTest，不修改断言。
- 已确认：数据库字段为 `user_name`，实体映射缺少 `@Column`。
- 已修改：`UserEntity.ts` 增加字段映射。
- 验证结果：目标测试通过，模块测试尚未执行。
- 当前风险：修改可能影响登录查询。
- 下一步：运行完整模块测试并检查登录回归。
- 证据：test-run-2026-06-13-01.log
```

一个可落地的压缩流程：

```python
def compact_if_needed(state, model_window):
    usage = count_tokens(state.messages)
    if usage < model_window * 0.80:
        return state

    old_messages = state.messages[:-8]
    recent_messages = state.messages[-8:]

    summary = summarizer.invoke({
        "goal": state.goal,
        "messages": old_messages,
        "required_fields": [
            "facts", "decisions", "changed_files",
            "verification", "open_questions", "next_step"
        ],
        "instruction": "只保留有证据的信息，区分事实和假设"
    })

    return state.replace_messages([
        SystemMessage("历史摘要：\n" + summary),
        *recent_messages,
    ])
```

实现时有几点要注意：触发阈值不能等到 100%，要给输出和工具调用留余量；摘要模型可以和主模型不同，以降低成本；摘要要保留来源、时间和验证状态；压缩前先把重要状态写入外部存储，防止摘要失败丢信息；代码、JSON、SQL 这类结构化内容，优先保留文件路径、符号名、diff 和测试结果，不要只留自然语言描述。

Claude Code 的 auto-compact 是个例子：上下文接近上限时，系统总结旧内容、丢弃原始细节，用户还可以指定压缩时重点保留测试输出和代码改动。保留什么应该由任务决定：Coding Agent 优先保留 diff、测试、编译错误和当前文件范围；客服 Agent 优先保留用户约束、订单状态和已确认的政策条款；研究 Agent 优先保留来源、结论、证据和未验证的假设。

## 隔离：让不同角色互不干扰

隔离是让不同模块、角色或子任务使用独立的上下文，不共享全部历史、工具和记忆，避免一个子任务的噪声、错误假设或敏感数据污染另一个。这通常出现在多 Agent 系统里，Anthropic 的多智能体研究系统就是这种架构：

![Lead Agent 拆分研究任务，汇总子 Agent 的证据并形成报告]({{ "/images/posts/context-engineering/lead-subagents.svg" | relative_url }})

每个 sub-agent 有自己的工具集合、运行历史、记忆和状态、权限范围以及输出格式。Lead Agent 不需要看子 Agent 的全部原始对话，只接收结构化结果、证据、置信度和未解决的问题。输入输出最好用明确的协议约定：

```json
{
  "subtask": "检索产品退款政策",
  "scope": "2025-01-01 之后的正式文档",
  "allowed_sources": ["internal_docs"],
  "output_schema": {
    "answer": "string",
    "evidence": [{"title": "string", "url": "string", "quote": "string"}],
    "uncertainty": ["string"],
    "next_action": "string"
  }
}
```

隔离不等于拆出越多 Agent 越好。Agent 一多，通信、汇总和协调成本都会上来，只有任务确实存在职责、工具或权限边界时，拆分才有意义。有依赖关系的任务优先串行，拿到前一步结果再做后一步；需要并行探索时，每个 Agent 用独立工作区或只读数据源；共享信息通过摘要和结构化产物传递，不共享完整聊天记录；汇总 Agent 要检查来源、冲突和不确定性；权限尽量最小化，研究 Agent 不应自动拥有写库或发布权限。

Cognition 的《Don't Build Multi-Agents》也并非简单反对多 Agent，它提醒的是：如果多个 Agent 并行运行让上下文和结果难以协调，那么串行执行、缩小上下文、明确职责可能更可靠。

## 把四类操作串成一条管道

一个 Context Builder 大致按下面的顺序工作：

1. 规范化用户请求，识别目标、实体、时间和权限。
2. 加载静态规则：系统指令、项目规范、安全边界。
3. 读取任务状态：目标、已完成步骤、失败尝试和下一步。
4. 动态检索相关记忆、文档、代码和工具。
5. 校验权限、版本、时效、来源和相关性，再重排。
6. 计算预算，为工具定义、历史和模型输出预留空间。
7. 先去重，再摘要，最后才硬截断。
8. 按 Agent 角色只分发必要内容。
9. 用统一格式渲染，标明来源和优先级。
10. 调用模型并记录 trace：输入版本、输出、工具调用和验证结果。

对应的伪代码：

```python
def build_context(request, state, policy):
    static_rules = load_static_rules(policy.project)
    task_state = load_task_state(state.task_id)

    memories = retrieve_memories(
        query=request.text,
        scope=[request.user_id, policy.project],
        permissions=request.permissions,
    )
    documents = retrieve_documents(request, permissions=request.permissions)
    tools = route_tools(request, policy.available_tools)

    sections = [
        render("rules", static_rules),
        render("task_state", task_state),
        render("memories", memories),
        render("documents", documents),
        render("tools", tools),
        render("recent_history", state.recent_messages),
    ]

    budget = policy.context_window - policy.output_reserve - policy.safety_margin
    sections = fit_budget(sections, budget)
    return assemble_with_provenance(sections)
```

同一份信息用不同格式注入，模型理解的稳定性会不一样。渲染时比较稳妥的做法是：明确区分指令、事实、工具结果和候选资料，告诉模型哪些是必须遵守的规则、哪些只是参考；检索内容标注来源、时间和可信度；拼接顺序固定，不要每轮随机变化；用 XML、Markdown 标题或 JSON schema 做分隔。例如：

```xml
<system_rules>只能修改当前仓库的 src/ 目录</system_rules>
<task>修复订单查询接口的分页错误</task>
<evidence source="test-log-001">page=2 返回重复记录</evidence>
<retrieved_document source="order-api-v3.md" updated_at="2026-06-01">
  分页参数从 1 开始计数
</retrieved_document>
```

关键事实还要带证据链：来源 URL、文件路径或记录 ID，文档版本和更新时间，检索分数或选择原因，是否经过人工或程序验证，以及权限范围和过期时间。这样最终回答能引用依据，出错时也能追溯到是哪段上下文导致的。

## 例子：查询订单并解释退款政策的客服 Agent

上下文可以这样分层：

```text
静态层：安全规则、身份认证要求、客服口吻、不可承诺的事项
记忆层：用户语言偏好、已确认的联系方式
动态知识层：当前商品和退款政策的最新版本
任务状态层：订单号、已验证身份、已调用的接口
工具层：订单查询、物流查询、退款申请
历史层：最近几轮对话的压缩摘要
```

一次请求的处理流程：

```text
用户：这个订单为什么还不能退款？
  -> 识别意图：退款状态解释
  -> 静态加载：身份和合规规则
  -> 记忆召回：用户语言偏好
  -> 动态检索：订单号相关记录、对应商品政策
  -> 工具路由：只注入订单查询工具和政策查询工具
  -> 调用工具并验证身份
  -> 只把结构化结果返回模型
  -> 模型生成解释和下一步建议
  -> 保存已确认的订单状态，不保存完整敏感响应
```

和把所有资料放进 Prompt 相比，过期政策不会因为全文注入混进回答，用户无权访问的订单记录在检索阶段就被过滤掉，工具列表更短、误调用更少，历史聊天被摘要后成本不会随对话线性增长，每个结果也都有来源和时间，方便客服解释和审计。

## 怎么评估

只看最终回答“感觉不错”是不够的，上下文管道也要做可观测和评估。指标大致分三类：

- 效果：任务成功率、事实正确率、关键结论的引用覆盖率、工具调用成功率（参数是否正确、是否频繁重试）、注入内容中真正被任务用到的比例，以及上下文中出现相互矛盾信息的冲突率。
- 效率：每次请求的输入输出 token、首 token 延迟和端到端延迟、检索/重排序/摘要各阶段耗时、单个任务的平均模型调用次数、记忆命中率和无效召回率、压缩触发次数及压缩后的 token 降幅。
- 安全：越权资料进入上下文的次数、Prompt Injection 被召回或被执行的次数、敏感字段脱敏覆盖率、高风险工具被拦截/审批/审计的比例、跨用户和跨项目的记忆污染次数。

每次模型调用最好记录一份可脱敏的 trace：

```json
{
  "trace_id": "ctx-20260613-001",
  "task_id": "refund-123",
  "static_tokens": 820,
  "retrieved_items": ["policy-v3", "order-123"],
  "history_tokens_before": 8200,
  "history_tokens_after": 2100,
  "tools_exposed": ["get_order", "get_refund_policy"],
  "model_result": "grounded",
  "sources": ["policy-v3", "order-service"]
}
```

## 容易踩的坑

前面几节已经覆盖了大部分：上下文越长不代表信息密度越高，可以用 A/B 测试比较“全部注入”和“精选注入”的实际效果；不同用户、项目和 Agent 共用未隔离的记忆库，会造成隐私泄露和事实污染，召回时必须按作用域、权限和有效期强制过滤。

另一个容易被忽略的是，检索内容并不天然可信。文档、网页和代码注释里都可能藏着 Prompt Injection。检索结果要当作不可信数据处理，资料里的指令不能覆盖系统规则，高风险动作必须经过工具层的权限校验和人工确认。

## 落地顺序

如果从零开始，我更倾向于按下面的顺序推进。

先把上下文画出来。针对一个真实任务，列出模型本轮能看到哪些输入，哪些固定、哪些动态，哪些最占 token，哪些信息不该在角色之间共享，哪些结论需要保留来源。很多问题在这一步就能暴露出来，比如完整工具结果被重复塞入、过期规则和新规则混在一起、用户私密信息被写进全局记忆。

然后做一个最小的 Context Builder，只实现静态规则加载、动态检索和权限过滤、历史摘要和 token 预算、每次调用的输入输出 trace。不要一开始就同时引入多 Agent、向量数据库和自动记忆，先证明精选上下文比全部历史更稳定。

接着加验证和回放。为典型任务建立离线数据集，固定用户请求、可用资料和期望结果，然后对比不同策略：全量历史和摘要历史、全量工具和动态路由、纯向量检索和向量加元数据过滤、单 Agent 和隔离的子任务 Agent，在准确率、成本、延迟和安全性之间综合评估，同时准备好失败时的降级方案和人工介入点。

最后，等单 Agent 已经稳定运行、且确实存在职责和权限边界，再引入 Lead Agent、Sub-agent、串行工作流和人工审批。上下文工程是为了降低复杂度，如果只是把复杂度藏进更多 Agent，就偏离了初衷。
