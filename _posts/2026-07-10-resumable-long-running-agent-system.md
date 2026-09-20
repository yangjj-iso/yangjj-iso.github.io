---
layout: post
title: "一个可恢复长 Agent 系统的设计"
description: "说明为什么 Agent 不能和 HTTP 请求共用生命周期，以及任务状态、执行进度、副作用和事件四层如何支撑可恢复。"
date: 2026-07-10
tags: [Agent, 系统设计, 可恢复]
---

大多数 AI 应用最开始的形态都很简单：用户发来一句话，后端调用一次大模型，然后把结果返回。

{% include diagram.html src="simple-chat.svg" alt="一次问答：浏览器请求到服务端，再调用模型返回响应" %}

对于一个几秒钟就能完成的问答，这种模型没有任何问题；但当 Agent 开始承担更复杂的任务，事情会迅速发生变化。例如用户要求：

> 帮我调研一家公司的背景，分析一个岗位的 JD，结合我的经历判断匹配程度，再生成一份完整报告。
<!--more-->
这一次任务可能包含读取资料、搜索、推理、调用工具并生成最终报告，整个过程持续几十秒甚至几分钟：

{% include diagram.html src="long-task.svg" alt="长任务：从读取资料、搜索、推理到生成最终报告" %}

如果执行过程中 Agent 发现信息不足，它还可能暂停下来请用户补充完整的岗位描述，而用户也许两小时之后才回来。到这里，一个原本普通的 Web 请求实际上已经变成了一个 **Long-running Workflow**；很多 Agent 系统最早遇到的工程问题，正是因为我们仍然在用“普通 HTTP 请求”的方式理解它。

---

## 一、Agent 不应该和 HTTP Request 共用生命周期

最直觉的实现通常类似这样：

```python
@app.post("/agent/run")
async def run_agent(request):
    result = await agent.run(request.input)
    return result
```

看起来没有问题，而且还是 `async`，但这里隐藏着一个非常重要的问题：HTTP Request 的生命周期被错误地等同于 Agent Task 的生命周期。

{% include diagram.html src="lifecycle-coupled.svg" alt="HTTP 请求生命周期被错误地等同于 Agent 任务生命周期" %}

也就是说，只要这个请求出了问题，Agent 的执行就可能跟着出问题——浏览器刷新、Nginx 超时、API Server 重启、用户网络中断，甚至只是客户端没有收到响应又重新提交了一次相同请求，都会把任务拖进两种糟糕情况。第一种是任务直接丢失：

{% include diagram.html src="task-lost.svg" alt="进程重启后，跟着请求走的 Agent 协程一起消失" %}

第二种更加危险：客户端不知道第一次请求到底执行成功没有，于是重新提交，同一次意图变成两个 Agent 任务，最终可能出现两次搜索、两次扣费、两次数据库写入，甚至两封重复邮件。

{% include diagram.html src="duplicate-submit.svg" alt="同一次意图被提交两次，变成两个 Agent 任务" %}

所以对于长时间运行的 Agent，第一个重要的架构原则不是“怎么让 LLM 更聪明”，而是：

> **不要让 Agent 的生命周期依赖一次 HTTP Connection。**

我们真正需要拆开的，是 Request、Task、Worker 和 SSE Connection 这几层各自独立的生命周期；一旦接受这个前提，后面的架构就会自然很多。

{% include diagram.html src="lifecycle-split.svg" alt="Request、Task、Worker、SSE 连接各自独立的生命周期" %}

最基本的变化是：API 不再负责把 Agent 从头跑到尾，而只负责接收请求、创建任务、持久化任务，并将 `task_id` 放入队列；真正执行 Agent 的，是独立 Worker。

{% include diagram.html src="old-vs-queue.svg" alt="左边 HTTP 里直接跑 Agent，右边拆成建任务、入队、Worker 执行" %}

这里有一个很容易被忽略的区别：

> `async/await` 解耦的是线程等待，任务队列解耦的是服务生命周期。

即使一个 FastAPI Handler 是 `async` 的，只要你还在 `await agent.run()`，Agent 仍然属于这次 Request；进入 Queue 之后，Request 可以先结束，Worker 再独立领取 `task_id` 去跑 Agent，API 和执行终于变成两个独立生命周期。这才是任务队列对于 Agent 系统真正重要的地方。

{% include diagram.html src="queue-decouple.svg" alt="API 入队后即可结束，Worker 独立领取 task_id 再跑 Agent" %}

---

## 二、Queue 只解决调度，真正的“可恢复”需要多层状态

做到这里以后，很容易产生另一个误区：任务已经放进 Redis 了，系统是不是就可恢复了？答案仍然是否定的。Queue 能告诉我们 `task_123` 需要执行，却不知道它当前处于什么业务状态、Agent 已经运行到哪一步、刚才那个 Tool 到底有没有执行成功，以及用户断线之前已经看到哪些事件。所以一个真正可恢复的 Agent 系统，需要把状态拆成几个不同维度。

### 1. Task State：这个任务现在是什么状态？

最基础的是业务任务本身，例如 `PENDING → RUNNING → COMPLETED`；Agent 场景通常还需要 `WAITING`、`FAILED`、`CANCELLED`，最终可能形成这样一套状态机：

{% include diagram.html src="task-states.svg" alt="任务状态机：PENDING 到 RUNNING，再分出 WAITING、FAILED、COMPLETED" %}

这里的 `WAITING` 非常重要：它意味着 Agent 没有失败，只是在等待用户提供新的信息。这类状态最好持久化在数据库中，而不是依赖 Worker 内存，因为一个任务可能上午十点创建、十点零二分等待用户、下午两点半才重新继续——业务任务活了四个半小时，Worker 却不应该被占用四个半小时。

---

### 2. Execution State：Agent 到底执行到哪里？

任务状态告诉我们它在 `RUNNING`，却没有回答 Agent 已经执行完几个步骤了。一次执行可能是读上下文、决策、搜索、再决策；如果 Worker 在 Search 已完成、准备继续推理时崩溃，新 Worker 必须知道上一次已经执行到哪里，这就是 Checkpoint 的作用。

{% include diagram.html src="execution-steps.svg" alt="一次 Agent 执行：读上下文、决策、搜索、再决策" %}

{% include diagram.html src="worker-crash.svg" alt="Search 已完成，准备继续推理时 Worker 崩溃" %}

Checkpoint 保存的不是简单的 `RUNNING`，而是 `messages`、control state、tool history、pending questions、budget、domain state 和 execution metadata 这类执行现场。因此重新调度以后，新 Worker 加载 Checkpoint、恢复 RunState 再继续，而不是从第一步重新开始。

{% include diagram.html src="checkpoint-restore.svg" alt="新 Worker 加载 Checkpoint，恢复 RunState 后继续" %}

所以可以把两类状态区分为：Task State 回答“任务是什么状态”，Checkpoint 回答“执行到哪里”，这是两个完全不同的问题。

{% include diagram.html src="two-kinds-of-state.svg" alt="Task State 回答任务是什么状态，Checkpoint 回答执行到哪里" %}

---

### 3. Side Effect State：这个 Tool 到底执行过没有？

这可能是 Agent 恢复中最危险的问题。假设 Agent 调用了 `create_report`、`send_email`、`charge_user`、`write_database` 这类有副作用的工具，Tool 已经成功、外部系统已经变化，结果还没记下 Worker 就崩溃了；新 Worker 恢复以后如果只是“再执行一次”，就可能产生重复副作用。

{% include diagram.html src="side-effect-crash.svg" alt="Tool 已经成功、外部系统已变化，结果还没记下 Worker 就崩溃" %}

所以真正的可恢复系统不能只做 Retry，还需要 Idempotency 加上 Tool Execution Ledger：每一次 Tool Call 都应该有稳定身份（例如 `tool_call_id = abc123`），执行前 `claim abc123`，执行成功记 `abc123 = completed`，恢复时先问它是否已经执行过；如果已经完成，就不应该再次产生同样的副作用。

{% include diagram.html src="idempotency-ledger.svg" alt="幂等与 Tool Execution Ledger 一起防止重复副作用" %}

这也是为什么在分布式系统里，一个更加现实的目标通常不是追求绝对的 exactly-once，而是 at-least-once delivery 加上 idempotent execution：消息可以重复到达，但业务动作不能随便重复发生。

---

### 4. Event State：用户漏掉了哪些执行过程？

Agent 系统通常还需要实时展示正在分析、正在搜索、调用 Tool、正在生成报告。最简单的方式是 Agent 直接推 SSE 到浏览器，但这意味着浏览器一断线，中间事件就丢了；因此更加可靠的链路应该先写入 durable event log，再经 SSE 投递。

{% include diagram.html src="sse-vs-outbox.svg" alt="左边 Agent 直推 SSE 会丢事件，右边先写 Event Outbox 再推送" %}

Agent 每产生一个事件都先写入这份 log，例如：

```text
task_123
seq=101  model.started
seq=102  tool.started
seq=103  tool.completed
seq=104  message.delta
seq=105  status.changed
```

浏览器保存 `last_seq = 105`；如果网络断开，Agent 仍然可以继续运行，事件变成 106、107、108、109，用户回来后只需带上 `after_seq = 105`，服务端重新读取 `seq > 105` 并继续推送。这时候 SSE 的定位就变得非常清楚：**SSE 只是 Delivery Channel，Outbox 才是 Event Source of Truth。** 断线意味着实时连接断了，而不是任务历史消失了。

---

## 三、一个真正可恢复的 Agent 是怎么运行的

到这里，可以把整个系统放在一起看。每一个组件只解决一个明确的问题：Queue 决定谁来执行，Task Database 保存任务现在是什么状态，Checkpoint 记录 Agent 执行到哪里，Tool Ledger 判断某个副作用是否已经发生，Event Outbox 留下执行过程，SSE Replay 补上客户端错过的事件，Idempotency Key 则用来识别这是不是同一次请求。

{% include diagram.html src="architecture.svg" alt="可恢复 Agent 架构：API 建任务，PostgreSQL 存状态，Queue 调度 Worker，Runtime 写 Checkpoint、Ledger 和 Event Outbox，再经 SSE 回到客户端" %}

如果我们把这些东西全塞进一个 Redis Queue，就会很快失控。比较健康的边界反而是：PostgreSQL 作为 Durability Backbone，Redis / Queue 只承担 Scheduling 和 Coordination。Queue 只需要知道 `task_123` 该执行了；至于它属于谁、输入是什么、状态是什么、有没有等待用户、报告生成到哪里，这些仍然由 durable storage 保存。

{% include diagram.html src="durability-vs-queue.svg" alt="PostgreSQL 负责持久，Redis Queue 只负责调度和协调" %}

一个很典型的 Agent 场景是 HITL，也就是 Human in the Loop。假设 Agent 运行到一半发现需要用户提供完整的合同内容，最糟糕的做法是让 Worker 空等三个小时；正确方式是发出 `ASK_USER`、保存 Checkpoint，把任务从 `RUNNING` 放到 `WAITING` 并结束当前 Job，把 Worker Slot 交还回去。

{% include diagram.html src="hitl-wait.svg" alt="左边 Worker 空等三小时，右边 ASK_USER 后保存 Checkpoint 并结束 Job" %}

用户三个小时后回答时，任务从 `WAITING` 回到 `PENDING` 并重新入队，新 Worker 加载 Checkpoint 再继续。这时候会发现一个非常重要的关系：**Task 生命周期应该允许远远长于 Worker Job 生命周期。** 业务任务可以从 10:00 活到 14:31，实际却只跑两段很短的 Job，系统并不需要为暂停中的 Agent 保留四小时 Worker。

{% include diagram.html src="hitl-resume.svg" alt="用户回答后任务重新入队，新 Worker 加载 Checkpoint 继续" %}

{% include diagram.html src="task-vs-job.svg" alt="业务任务从 10:00 活到 14:31，实际只跑了两段很短的 Worker Job" %}

另外一个必须考虑的问题是 Worker 本身也会挂：任务已经标成 `RUNNING`，进程崩溃后数据库可能永远停在这个状态，实际上却已经没人执行它。所以生产级实现还需要一套 Lease、Heartbeat 和 Reaper——Worker 领取任务时记下 `claimed_by = worker_7` 和 `lease_until = 10:05`，执行过程中持续发送 heartbeat；如果发现 `status = RUNNING` 且 `lease_until < now`，说明 Worker 大概率已经死亡，系统就把任务放回 `PENDING`、重新入队，由新 Worker 从 Checkpoint 恢复。

{% include diagram.html src="worker-dies.svg" alt="任务已标成 RUNNING，Worker 崩溃后没有人继续执行" %}

{% include diagram.html src="lease-heartbeat.svg" alt="Lease、Heartbeat、Reaper 三个组件" %}

{% include diagram.html src="lease-resume.svg" alt="租约过期后任务回到 PENDING，重新入队并由新 Worker 从 Checkpoint 恢复" %}

所以“可恢复”从来不是某一个中间件提供的神奇能力。Redis、Worker、API 都可以挂，真正重要的是：系统是否知道任务之前发生了什么，以及重新启动以后应该从哪里继续。

---

## 四、真正困难的不是异步，而是故障边界

如果只是把任务丢进 Redis，其实并不难；真正复杂的地方是不同组件之间的故障窗口。例如先 `INSERT task`，再 `COMMIT PostgreSQL`，最后 `enqueue Redis`——假设第二步成功以后 API 在第三步之前崩溃，数据库里明明有 `PENDING` 的任务，Redis 里却没有对应 job，于是永远没人执行。

{% include diagram.html src="pg-without-job.svg" alt="PostgreSQL 里任务已是 PENDING，Redis 里却没有对应 job" %}

一个常见的改进方式是 **Transactional Outbox**：在同一个数据库事务中同时写 EvaluationTask 和 DispatchOutbox，再由独立 Dispatcher 读取 `dispatch_outbox`、投递 Redis 并标记 dispatched。这样即使 API 在提交事务后立刻崩掉，outbox 仍然存在，系统启动以后可以继续把任务送进 Queue。这解决的是数据库和消息队列之间的双写一致性问题。

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

还有一个很常见的坑是 Retry。我们经常会看到 `LLM Retry = 3`、`Tool Retry = 3`、`Job Retry = 3`，最后一次普通网络波动可能放大成十几次外部调用，所以 Retry 也应该分层：Capability Retry 负责单个外部调用的临时错误，Job Retry 负责整个 Worker Job 的基础设施故障。哪些错误能重试必须明确分类，因为对 `GET search result` 重复可能没什么，对 `charge card`、`send email`、`create order` 却完全不是同一个问题。所以在一个可恢复 Agent 系统里，**Idempotency 往往比 Retry 更重要。**

{% include diagram.html src="retry-layers.svg" alt="Capability Retry 管单次外部调用，Job Retry 管整个 Worker Job" %}

---

## 五、可恢复，不等于永不失败

我觉得这是设计 Agent 系统时非常重要的一个认知：一个可恢复架构并不是“无论发生什么，任务一定能成功”，真正目标应该是失败之后能够 Detect、Persist、Recover，再 Resume——把“某个进程崩了、任务直接消失”变成“系统检测到异常、找到 durable state、重新调度，并从 checkpoint 恢复”。

{% include diagram.html src="recover-loop.svg" alt="失败之后检测、持久化、恢复、再继续，而不是假装永不失败" %}

{% include diagram.html src="task-vanishes.svg" alt="某个进程崩了，任务直接消失" %}

{% include diagram.html src="task-recovers.svg" alt="进程崩溃后系统检测异常，找到 durable state，从 checkpoint 恢复" %}

对于一个长时间运行的 Agent，我认为至少应该回答清楚：请求重复了怎么办，API Server 或 Worker 重启怎么办，浏览器断线怎么办，Agent 等待用户怎么办，Tool 已经成功但 Worker 挂了怎么办，以及任务状态已经入库但 Job 没有成功入队怎么办。如果这些问题的答案只是“再跑一次”，那这个系统还不算真正具备可恢复能力。

一个相对完整的模型，是请求经幂等进入持久任务，由队列调度 Worker，Runtime 依赖 Checkpoint、Tool Ledger 和 Event Outbox，再经 SSE 回到客户端：

{% include diagram.html src="full-model.svg" alt="完整模型：请求经幂等进入持久任务，队列调度 Worker，Runtime 依赖 Checkpoint、Ledger 和 Outbox，再经 SSE 回到客户端" %}

最后可以把整套设计压缩成五句话：任务队列负责执行解耦，数据库负责状态不丢，Checkpoint 负责执行进度不丢，幂等与 Ledger 负责副作用尽量不重复，Event Outbox 与 Replay 负责客户端断线以后仍然能够恢复视图。

Agent 系统真正需要解耦的从来不只是线程，而是生命周期。HTTP Request、业务 Task、Worker Job、Agent Run 和 SSE Connection 都应该允许独立失败，也应该能够独立恢复。只有做到这一点，Agent 才开始从一个“长时间占着请求的 LLM 调用”，真正变成一个可以被后端系统可靠管理的任务。
