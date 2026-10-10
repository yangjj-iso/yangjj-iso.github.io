---
layout: post
title: "一个可恢复长 Agent 系统的设计"
description: "为什么长时间运行的 Agent 不能和 HTTP 请求共用生命周期，以及任务状态、执行进度、副作用和事件四层状态如何支撑可恢复。"
date: 2026-07-10
tags: [Agent, 系统设计, 可恢复]
---

大多数 AI 应用一开始都很简单：用户发来一句话，后端调用一次大模型，把结果返回。几秒钟就能答完的问答，这样做完全没问题；但 Agent 接的任务一复杂，情况就变了。

<!--more-->

{% include diagram.html src="simple-chat.svg" alt="一次问答：浏览器请求到服务端，再调用模型返回响应" %}

比如用户要求：

> 帮我调研一家公司的背景，分析一个岗位的 JD，结合我的经历判断匹配程度，再生成一份完整报告。

读资料、搜索、推理、调工具、生成报告，前后可能要几十秒甚至几分钟：

{% include diagram.html src="long-task.svg" alt="长任务：从读取资料、搜索、推理到生成最终报告" %}

中途如果 Agent 发现信息不够，还可能停下来请用户补一份完整的岗位描述，而用户也许两小时后才回来。到这一步，一个普通的 Web 请求已经变成了 Long-running Workflow。很多 Agent 系统早期踩的坑，都是因为还在用普通 HTTP 请求的思路对待它。

---

## 一、Agent 不应该和 HTTP Request 共用生命周期

最直觉的写法大概是这样：

```python
@app.post("/agent/run")
async def run_agent(request):
    result = await agent.run(request.input)
    return result
```

看起来没毛病，还是 `async` 的，但它把 HTTP Request 的生命周期当成了 Agent Task 的生命周期。

{% include diagram.html src="lifecycle-coupled.svg" alt="HTTP 请求生命周期被错误地等同于 Agent 任务生命周期" %}

于是浏览器刷新、Nginx 超时、API Server 重启、用户断网，甚至客户端没收到响应又重新提交了一次，都会把任务拖下水。一种结果是任务直接丢了：

{% include diagram.html src="task-lost.svg" alt="进程重启后，跟着请求走的 Agent 协程一起消失" %}

另一种更危险：客户端不知道第一次到底成功没有，于是重新提交，同一个意图变成两个 Agent 任务，最后可能搜两次、扣两次费、写两次数据库，甚至发出两封一样的邮件。

{% include diagram.html src="duplicate-submit.svg" alt="同一次意图被提交两次，变成两个 Agent 任务" %}

所以对长时间运行的 Agent 来说，第一条架构原则和 LLM 聪不聪明无关：

> **不要让 Agent 的生命周期依赖一次 HTTP Connection。**

Request、Task、Worker 和 SSE Connection 应该各有各的生命周期，后面的设计都建立在这个前提上。

{% include diagram.html src="lifecycle-split.svg" alt="Request、Task、Worker、SSE 连接各自独立的生命周期" %}

最基本的变化是：API 不再负责把 Agent 从头跑到尾，只负责接收请求、创建并持久化任务，再把 `task_id` 放进队列；Agent 交给独立的 Worker 执行。

{% include diagram.html src="old-vs-queue.svg" alt="左边 HTTP 里直接跑 Agent，右边拆成建任务、入队、Worker 执行" %}

这里有个容易被忽略的区别：

> `async/await` 解耦的是线程等待，任务队列解耦的是服务生命周期。

FastAPI Handler 就算是 `async` 的，只要还在 `await agent.run()`，Agent 就仍然属于这次 Request。放进 Queue 之后，Request 可以先结束，Worker 再独立领取 `task_id` 去跑 Agent，API 和执行才算真正分开。

{% include diagram.html src="queue-decouple.svg" alt="API 入队后即可结束，Worker 独立领取 task_id 再跑 Agent" %}

---

## 二、Queue 只解决调度，可恢复要靠多层状态

任务进了 Redis，系统也还谈不上可恢复。Queue 只知道 `task_123` 需要执行，不知道它处于什么业务状态、Agent 跑到了哪一步、刚才那个 Tool 成功没有，也不知道用户断线前看到了哪些事件。这些得拆成几层状态分别保存。

### 1. Task State：任务现在是什么状态？

最基础的是业务任务本身的状态，比如 `PENDING → RUNNING → COMPLETED`。Agent 场景通常还要加上 `WAITING`、`FAILED`、`CANCELLED`，最后大致是这样一套状态机：

{% include diagram.html src="task-states.svg" alt="任务状态机：PENDING 到 RUNNING，再分出 WAITING、FAILED、COMPLETED" %}

`WAITING` 很关键，它表示 Agent 没有失败，只是在等用户补充信息。这类状态最好存在数据库里，别放在 Worker 内存里：一个任务可能十点创建、十点零二分开始等用户、下午两点半才继续，Worker 不该陪着它耗四个半小时。

---

### 2. Execution State：Agent 执行到哪了？

任务状态只能告诉我们它在 `RUNNING`，说不出 Agent 已经做完了几步。一次执行可能是读上下文、决策、搜索、再决策。如果 Worker 在搜索完成、正要继续推理时崩了，新 Worker 必须知道上次做到了哪里，这就是 Checkpoint 要解决的问题。

{% include diagram.html src="execution-steps.svg" alt="一次 Agent 执行：读上下文、决策、搜索、再决策" %}

{% include diagram.html src="worker-crash.svg" alt="Search 已完成，准备继续推理时 Worker 崩溃" %}

Checkpoint 存的不是一个简单的 `RUNNING`，而是 `messages`、control state、tool history、pending questions、budget、domain state、execution metadata 这些执行现场。重新调度后，新 Worker 加载 Checkpoint、恢复 RunState 接着跑，不用从第一步重来。

{% include diagram.html src="checkpoint-restore.svg" alt="新 Worker 加载 Checkpoint，恢复 RunState 后继续" %}

Task State 回答“任务是什么状态”，Checkpoint 回答“执行到了哪里”，这是两个不同的问题。

{% include diagram.html src="two-kinds-of-state.svg" alt="Task State 回答任务是什么状态，Checkpoint 回答执行到哪里" %}

---

### 3. Side Effect State：这个 Tool 到底执行过没有？

这大概是 Agent 恢复里最危险的问题。假设 Agent 调用了 `create_report`、`send_email`、`charge_user`、`write_database` 这类有副作用的工具，Tool 已经成功、外部系统已经变了，结果还没记下来 Worker 就崩了。新 Worker 如果只是“再执行一次”，副作用就重复了。

{% include diagram.html src="side-effect-crash.svg" alt="Tool 已经成功、外部系统已变化，结果还没记下 Worker 就崩溃" %}

所以光有 Retry 不够，还需要 Idempotency 加上 Tool Execution Ledger。每次 Tool Call 都要有稳定的身份，比如 `tool_call_id = abc123`：执行前先 `claim abc123`，成功后记 `abc123 = completed`，恢复时先查它有没有执行过，执行过就不再重复产生副作用。

{% include diagram.html src="idempotency-ledger.svg" alt="幂等与 Tool Execution Ledger 一起防止重复副作用" %}

这也是分布式系统里通常不去追求绝对 exactly-once 的原因。更现实的目标是 at-least-once delivery 加上 idempotent execution：消息可以重复到达，业务动作不能随便重复发生。

---

### 4. Event State：用户错过了哪些执行过程？

Agent 系统一般还要实时展示进度：正在分析、正在搜索、调用 Tool、正在生成报告。最省事的是 Agent 直接往浏览器推 SSE，但浏览器一断线，中间的事件就丢了。更可靠的是先写 durable event log，再经 SSE 投递。

{% include diagram.html src="sse-vs-outbox.svg" alt="左边 Agent 直推 SSE 会丢事件，右边先写 Event Outbox 再推送" %}

Agent 每产生一个事件，都先写进这份 log：

```text
task_123
seq=101  model.started
seq=102  tool.started
seq=103  tool.completed
seq=104  message.delta
seq=105  status.changed
```

浏览器记下 `last_seq = 105`。断网后 Agent 照常运行，事件继续写到 106、107、108、109；用户回来时带上 `after_seq = 105`，服务端读出 `seq > 105` 的事件接着推。这样分工就清楚了：**SSE 只是 Delivery Channel，Outbox 才是 Event Source of Truth。** 断线断的只是实时连接，任务历史还在。

---

## 三、可恢复的 Agent 是怎么跑起来的

把前面这些放到一起，每个组件只管一件事：Queue 决定谁来执行，Task Database 存任务状态，Checkpoint 记执行进度，Tool Ledger 判断副作用是否已经发生，Event Outbox 留下执行过程，SSE Replay 补上客户端错过的事件，Idempotency Key 识别是不是同一次请求。

{% include diagram.html src="architecture.svg" alt="可恢复 Agent 架构：API 建任务，PostgreSQL 存状态，Queue 调度 Worker，Runtime 写 Checkpoint、Ledger 和 Event Outbox，再经 SSE 回到客户端" %}

这些要是全塞进一个 Redis Queue，很快就会失控。比较健康的边界是 PostgreSQL 做 Durability Backbone，Redis / Queue 只管 Scheduling 和 Coordination。Queue 只需要知道 `task_123` 该执行了；任务属于谁、输入是什么、当前状态、是否在等用户、报告生成到哪，都由 durable storage 保存。

{% include diagram.html src="durability-vs-queue.svg" alt="PostgreSQL 负责持久，Redis Queue 只负责调度和协调" %}

HITL（Human in the Loop）是很典型的场景。假设 Agent 跑到一半，发现需要用户提供完整的合同内容。最糟的做法是让 Worker 空等三个小时；正确的做法是发出 `ASK_USER`、保存 Checkpoint，把任务从 `RUNNING` 改成 `WAITING`，然后结束当前 Job，把 Worker Slot 还回去。

{% include diagram.html src="hitl-wait.svg" alt="左边 Worker 空等三小时，右边 ASK_USER 后保存 Checkpoint 并结束 Job" %}

三小时后用户回复，任务从 `WAITING` 回到 `PENDING` 重新入队，新 Worker 加载 Checkpoint 继续执行。**Task 的生命周期可以远远长于 Worker Job 的生命周期**：业务任务从 10:00 活到 14:31，实际只跑了两段很短的 Job。

{% include diagram.html src="hitl-resume.svg" alt="用户回答后任务重新入队，新 Worker 加载 Checkpoint 继续" %}

{% include diagram.html src="task-vs-job.svg" alt="业务任务从 10:00 活到 14:31，实际只跑了两段很短的 Worker Job" %}

Worker 本身也会挂。进程崩了以后，数据库里的任务可能永远停在 `RUNNING`，实际上已经没人执行。所以生产环境还需要 Lease、Heartbeat 和 Reaper：Worker 领取任务时记下 `claimed_by = worker_7` 和 `lease_until = 10:05`，执行中持续发送 heartbeat；一旦发现 `status = RUNNING` 且 `lease_until < now`，说明 Worker 多半已经死了，系统就把任务放回 `PENDING` 重新入队，由新 Worker 从 Checkpoint 恢复。

{% include diagram.html src="worker-dies.svg" alt="任务已标成 RUNNING，Worker 崩溃后没有人继续执行" %}

{% include diagram.html src="lease-heartbeat.svg" alt="Lease、Heartbeat、Reaper 三个组件" %}

{% include diagram.html src="lease-resume.svg" alt="租约过期后任务回到 PENDING，重新入队并由新 Worker 从 Checkpoint 恢复" %}

可恢复并不是哪个中间件自带的能力。Redis、Worker、API 都可能挂，关键在于系统是否知道任务之前发生过什么，以及重启后该从哪里继续。

---

## 四、难的不是异步，是故障边界

光把任务丢进 Redis 并不难，复杂的是组件之间的故障窗口。比如先 `INSERT task`，再 `COMMIT PostgreSQL`，最后 `enqueue Redis`。如果第二步成功后 API 在第三步之前崩了，数据库里有一条 `PENDING` 的任务，Redis 里却没有对应的 job，这个任务就永远没人执行。

{% include diagram.html src="pg-without-job.svg" alt="PostgreSQL 里任务已是 PENDING，Redis 里却没有对应 job" %}

常见的改法是 **Transactional Outbox**，用来解决数据库和消息队列之间的双写一致性：在同一个数据库事务里同时写 EvaluationTask 和 DispatchOutbox，再由独立的 Dispatcher 读取 `dispatch_outbox`、投递到 Redis 并标记为 dispatched。即使 API 刚提交完事务就崩了，outbox 记录还在，系统起来后照样会把任务送进 Queue。

```sql
BEGIN;

INSERT INTO task(...);

INSERT INTO dispatch_outbox(
    task_id,
    job_type
);

COMMIT;
```

{% include diagram.html src="outbox-dispatch.svg" alt="Dispatcher 读取 dispatch_outbox，投递 Redis，再标记已派出" %}

另一个常见的坑是 Retry。`LLM Retry = 3`、`Tool Retry = 3`、`Job Retry = 3` 叠在一起，一次普通的网络波动就可能放大成十几次外部调用。所以 Retry 要分层：Capability Retry 处理单个外部调用的临时错误，Job Retry 处理整个 Worker Job 的基础设施故障。哪些错误能重试也必须分清楚，`GET search result` 重复几次无所谓，`charge card`、`send email`、`create order` 就是另一回事了。在可恢复的 Agent 系统里，**Idempotency 往往比 Retry 更重要。**

{% include diagram.html src="retry-layers.svg" alt="Capability Retry 管单次外部调用，Job Retry 管整个 Worker Job" %}

---

## 五、可恢复不等于永不失败

可恢复架构并不保证“无论发生什么，任务都能成功”。它的目标是失败之后能 Detect、Persist、Recover，再 Resume：从“某个进程崩了，任务直接消失”，变成“系统发现异常，找到 durable state，重新调度，从 checkpoint 恢复”。

{% include diagram.html src="recover-loop.svg" alt="失败之后检测、持久化、恢复、再继续，而不是假装永不失败" %}

{% include diagram.html src="task-vanishes.svg" alt="某个进程崩了，任务直接消失" %}

{% include diagram.html src="task-recovers.svg" alt="进程崩溃后系统检测异常，找到 durable state，从 checkpoint 恢复" %}

对一个长时间运行的 Agent，我认为至少要回答清楚：请求重复、API Server 或 Worker 重启、浏览器断线、Agent 等待用户、Tool 已成功但 Worker 挂了、任务已入库但 Job 没入队，这些情况分别怎么办。如果答案只是“再跑一次”，这个系统就还谈不上可恢复。

把这些串起来，就是下面这个相对完整的模型：

{% include diagram.html src="full-model.svg" alt="完整模型：请求经幂等进入持久任务，队列调度 Worker，Runtime 依赖 Checkpoint、Ledger 和 Outbox，再经 SSE 回到客户端" %}

整套设计做的其实是同一件事：把 HTTP Request、业务 Task、Worker Job、Agent Run 和 SSE Connection 的生命周期拆开，让它们可以单独失败，也能单独恢复。做到这一步，Agent 才不再是一个长时间占着请求的 LLM 调用，而是后端可以可靠管理的任务。
