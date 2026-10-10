---
layout: post
title: "异步任务处理模式的工程实践"
description: "梳理异步任务的状态机、任务表、幂等控制、可靠投递、重试补偿与宕机恢复，以及 MySQL、Redis、MQ 和 Outbox 的取舍。"
date: 2026-06-23
tags: [后端开发, 系统设计, 异步任务, 幂等, 消息队列]
---

有一类接口不适合按普通同步接口来做：内部要调用第三方服务、AI Agent，或者做文件处理、报表生成、视频转码、批量导入、风控审核、支付后置处理。这些任务耗时不稳定，可能 2 秒返回，也可能 30 秒甚至几分钟。执行期间服务可能重启，第三方服务可能超时，用户还可能重复点击提交。

这时候与其让 HTTP 请求一直等到任务执行完，不如改成异步任务：后端先创建任务、马上返回 taskId，后台慢慢执行，前端再去查结果。

<!--more-->

一直同步等下去，连接长时间占用，容易触发网关、浏览器或服务端超时；重复提交会重复调用昂贵的外部能力；服务一重启，内存里的任务状态就丢了，失败后也没有统一的重试和排查入口；高峰期长任务还会挤占业务线程。改成异步任务后，流程是这样：

```text
用户提交请求
  -> 后端创建任务，快速返回 taskId
  -> 后台异步执行任务
  -> 前端轮询或订阅任务状态
  -> 任务完成后获取结果
```

## 什么时候用异步任务

最典型的是耗时不稳定的任务，比如 AI Agent 生成内容、Excel 批量导入、大数据量报表生成。与其把用户请求一直挂着，不如先告诉用户“任务已受理”，之后再查进度和结果。

其次是需要削峰填谷。异步化之后，请求线程只负责创建任务，消费者按固定并发消费 MQ 或任务队列，把瞬时高峰转成后台平稳处理。

依赖外部服务的任务还要默认它会失败：网络超时、第三方限流、Agent 返回异常、下游临时不可用。异步任务有独立的任务记录、状态和失败原因，天然适合做重试，服务重启后也能从任务表恢复。

前提是用户能接受最终一致：提交成功不代表执行完成。如果业务要求请求返回时结果必须已经完成，比如登录、下单扣库存前置校验、实时支付确认，就不能简单改成后台慢慢跑。

## 整体设计

几个组件的分工是：MySQL 保存任务事实和最终状态，MQ 或任务队列驱动后台执行，Redis 缓存任务状态、进度和幂等标记，状态机约束任务只能按合法路径流转。一个典型流程如下：

```text
请求进入
  -> 校验参数和幂等键
  -> 创建 task 记录，状态 pending
  -> 投递 MQ 消息
  -> 返回 taskId

消费者收到消息
  -> 抢占任务，把 pending 改为 running
  -> 执行业务逻辑
  -> 成功：保存结果，状态改为 success
  -> 失败：记录错误，决定 retry 或 failed

查询任务
  -> 优先查 Redis
  -> Redis 未命中查 MySQL
  -> 回填 Redis
```

**数据库是最终事实来源**，Redis 只是缓存和辅助控制，MQ 只是执行驱动。Redis 可能过期，MQ 消息可能重复投递，消费者可能执行到一半宕机，真正能支撑恢复和排查的还是任务表。

## 任务状态机

任务不只有成功和失败，先把状态定义清楚：pending（已创建，等待执行）、running（正在执行）、success（执行成功）、failed（执行失败，不再重试）、retrying（等待下一次重试）、timeout（执行超时）、canceled（被取消）。基本流转可以是：

```text
pending -> running -> success
pending -> running -> retrying -> pending
pending -> running -> failed
pending -> running -> timeout
pending -> canceled
```

状态流转最好统一用带条件的更新来控制，别让业务代码随便改。比如消费者抢任务时：

```sql
UPDATE async_task
SET status = 'running',
    started_at = NOW(),
    worker_id = ?,
    updated_at = NOW()
WHERE id = ?
  AND status = 'pending';
```

只有更新成功的消费者才能执行。MQ 可能重复投递，多个消费者可能同时拿到同一条任务消息，没有这个状态条件就会重复执行。

## 任务表设计

一个基础任务表可以这样设计：

```sql
CREATE TABLE async_task (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '任务ID',
  task_no VARCHAR(64) NOT NULL COMMENT '业务任务号',
  user_id BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
  biz_type VARCHAR(64) NOT NULL COMMENT '业务类型',
  idempotency_key VARCHAR(128) NOT NULL COMMENT '幂等键',
  status VARCHAR(32) NOT NULL COMMENT '任务状态',
  request_payload JSON NULL COMMENT '请求参数',
  result_payload JSON NULL COMMENT '执行结果',
  error_message VARCHAR(1024) NULL COMMENT '失败原因',
  retry_count INT NOT NULL DEFAULT 0 COMMENT '已重试次数',
  max_retry INT NOT NULL DEFAULT 3 COMMENT '最大重试次数',
  next_retry_at DATETIME NULL COMMENT '下次重试时间',
  worker_id VARCHAR(128) NULL COMMENT '当前执行节点',
  started_at DATETIME NULL COMMENT '开始执行时间',
  finished_at DATETIME NULL COMMENT '结束时间',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (id),
  UNIQUE KEY uk_task_no (task_no),
  UNIQUE KEY uk_user_biz_idempotency (user_id, biz_type, idempotency_key),
  KEY idx_status_next_retry (status, next_retry_at),
  KEY idx_status_updated (status, updated_at),
  KEY idx_user_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='异步任务表';
```

几个字段说明一下：`task_no` 是对外暴露的任务号，避免直接暴露自增 ID；`request_payload` 保存输入参数，用于重试和排查；`retry_count` 和 `next_retry_at` 用于延迟重试；`worker_id` 记录当前执行节点，方便排查任务卡在哪台机器；`updated_at` 用来判断 running 任务是否长时间没有心跳。

唯一索引 `uk_user_biz_idempotency` 是防重复提交的关键：同一个用户、同一种业务、同一个幂等键只能创建一个任务。

## 中间件取舍

MySQL、Redis、MQ、定时任务、分布式锁、对象存储都可能用到，但不是每个业务都要全上。

### MySQL

MySQL 通常是必须的。要求宕机恢复、可追踪、可排查，最终状态、请求参数、执行结果、失败原因、重试次数、幂等约束就都应该落库；高频进度刷新、秒级心跳、临时限流计数则不适合只放 MySQL。

### Redis

Redis 适合做辅助：缓存任务状态，减轻 DB 查询压力；保存任务进度，比如 30%、step=generating；做短期幂等控制，拦截用户连续点击；做限流，比如限制同一用户同时运行的任务数量；也可以做分布式锁，但要谨慎设计锁超时和续期。但要记住边界：Redis 可以丢，MySQL 不能丢。

### MQ

MQ 的价值是解耦和削峰：请求线程只负责创建任务和投递消息，消费者按自己的能力慢慢处理。

| MQ | 适合场景 |
| --- | --- |
| RabbitMQ | 普通业务异步、路由灵活、延迟队列 |
| RocketMQ | 高吞吐、事务消息、延迟消息、业务可靠性要求高 |
| Kafka | 日志流、事件流、高吞吐顺序消费 |
| Redis Stream | 轻量队列、已有 Redis、任务可靠性要求中等 |
| 本地线程池 | 单机 Demo、低可靠性后台任务 |

单机内部的轻量异步任务，可以先用线程池；任务不能丢、需要重试、需要跨服务消费，就应该用 MQ；任务创建和消息发送必须强一致，就要考虑事务消息或 Outbox。

### 定时任务与分布式锁

定时任务负责补偿：running 太久的判断是否超时，retrying 且 `next_retry_at <= now()` 的、pending 很久没被消费的重新投递，再顺手清理过期缓存。没有补偿，任务很容易永远卡在某个状态。

分布式锁不是必选项。抢占任务用前面的数据库条件更新就够了，只有“同一用户同一时间只能运行一个任务”这类跨任务约束，才考虑 Redis 锁。即使用了锁，锁也可能过期，服务可能停顿，最终还是要靠数据库状态和唯一索引兜底。

## 幂等

异步任务的幂等要解决两个问题：用户重复提交，不能重复创建任务；MQ 重复投递，不能重复执行任务。

### 提交幂等

前端提交任务时带一个幂等键：

```http
POST /api/tasks
Idempotency-Key: ai-generate:article:hash
```

后端把它和用户、业务类型一起做唯一约束：

```text
user_id + biz_type + idempotency_key
```

第一次提交成功，返回新任务：

```json
{
  "taskNo": "T202606090001",
  "status": "pending"
}
```

用户重复提交时，后端查到已存在的任务，返回同一个 taskNo：

```json
{
  "taskNo": "T202606090001",
  "status": "running"
}
```

这样就不会重复调用外部 Agent。

### 消费幂等

消费者收到 MQ 消息后不能直接执行，要先抢占任务：

```sql
UPDATE async_task
SET status = 'running',
    started_at = NOW(),
    worker_id = ?,
    updated_at = NOW()
WHERE task_no = ?
  AND status IN ('pending', 'retrying');
```

影响行数为 0，说明任务已被其他消费者处理或已经完成，直接忽略这条消息。执行成功时同样要带状态条件：

```sql
UPDATE async_task
SET status = 'success',
    result_payload = ?,
    finished_at = NOW(),
    updated_at = NOW()
WHERE task_no = ?
  AND status = 'running';
```

这些条件更新是异步任务里最朴素也最可靠的幂等控制。

## 可靠投递

容易被忽略的一点是，落库和发 MQ 之间并不天然一致。先 INSERT async_task 成功、再发送 MQ 失败，任务就会停在 pending，没有消费者执行；反过来先发送 MQ 成功、INSERT 失败，消费者收到消息后查不到任务。

最简单的是补偿扫描：可靠性要求中等的业务，先落库再发 MQ，发送失败时记录日志，由定时任务扫描长时间 pending 的任务重新投递。实现简单，代价是任务可能延迟执行，要靠补偿任务兜底。

如果用的是 RocketMQ 这类支持事务消息的中间件，可以用事务消息保证本地事务和消息投递的一致性，适合消息不能丢、业务链路复杂、团队已经有成熟 MQ 运维能力的情况。

更通用的是 Outbox，在本地事务里同时写两张表：

```sql
INSERT async_task
INSERT outbox_message
```

再由一个后台 relay 任务扫描 outbox_message 发送 MQ，发送成功后标记为 sent。任务记录和待发送消息在同一个事务里提交，就不会出现“任务有了但消息没了”。可靠性要求高、MQ 又不支持事务消息或不想绑定特定 MQ 时，适合用它。

## 失败重试

重试不能无限重试，要先想清楚哪些异常可以重试、最多重试几次、每次间隔多久、失败后是否进入死信队列、用户查询时看到什么状态。常见策略：

```text
第 1 次失败：10 秒后重试
第 2 次失败：1 分钟后重试
第 3 次失败：5 分钟后重试
超过最大次数：failed
```

消费者捕获异常后可以这样处理：

```python
if retry_count < max_retry:
    status = retrying
    retry_count = retry_count + 1
    next_retry_at = now + backoff
else:
    status = failed
    error_message = last_exception
```

重试可以由 MQ 延迟消息实现，也可以由定时任务扫描 retrying 任务后重新投递。

外部服务已经执行成功、后端却在保存结果前失败的情况，要靠业务层面的幂等兜住，比如调用 Agent 时传入 taskNo，让 Agent 侧也能识别重复请求，或者后端在调用前后保存足够的阶段状态。

## 查询任务状态

前端拿到 taskNo 之后轮询：

```http
GET /api/tasks/T202606090001
```

后端查询逻辑：

```text
先查 Redis
  命中：直接返回状态
  未命中：查 MySQL
      查到：回填 Redis，再返回
      查不到：返回任务不存在
```

Redis key 可以这样设计：

```text
task:status:{taskNo}
task:result:{taskNo}
task:progress:{taskNo}
```

任务完成后，最终结果写 MySQL，同时在 Redis 缓存一段时间，提升查询速度。结果很大的话，比如文件、图片、报告，可以放对象存储，MySQL 只保存结果地址。

## 宕机恢复

服务重启最容易导致状态混乱：任务已经改成 running，服务在执行中宕机；任务已经执行成功，还没来得及更新数据库；MQ 消息已经消费，但还没确认；Redis 状态更新了，MySQL 没更新。

状态以 MySQL 为准、执行前条件抢占，这两条前面已经有了。在此基础上，running 状态必须有超时兜底，例如定时任务扫描：

```sql
SELECT *
FROM async_task
WHERE status = 'running'
  AND updated_at < DATE_SUB(NOW(), INTERVAL 10 MINUTE);
```

扫到后根据业务决定：改为 retrying 重新投递；改为 timeout 等待人工处理；或者先查询外部服务确认是否已完成，再修正本地状态。

长任务执行过程中定期更新心跳：

```sql
UPDATE async_task
SET updated_at = NOW()
WHERE task_no = ?
  AND status = 'running';
```

这样补偿任务能区分“真的卡死”和“还在执行”。

最后，消费者要在业务结果落库后再 ACK 消息。处理中途宕机的话，消息会重新投递，消费者再根据数据库状态判断是否需要继续执行。

## 落地流程

把上面的内容串起来：

1. 提交：按 user_id + biz_type + idempotency_key 查任务，已存在就返回已有 taskNo；否则创建 pending 任务，投递 MQ 或写 outbox_message，返回 taskNo。
2. 消费：条件更新 pending/retrying -> running，成功才执行；成功更新为 success，失败按 retry_count 更新为 retrying 或 failed；更新 Redis 缓存后 ACK。
3. 查询：先查 Redis 再查 MySQL；success 时返回结果，failed 时返回可展示的失败原因。
4. 补偿：pending 太久和 retrying 到期的重新投递，running 超时的转 retrying 或 timeout，清理过期缓存。

## 常见误区

只把任务放进 MQ 不落库、把 Redis 当最终状态库、running 没有超时补偿，这几个坑前面都讲过。还有一个容易被忽视的：只依赖前端防重复点击。它只能改善体验，用户可以刷新页面、超时后再提交，MQ 也会重复投递，正确性要靠后端的唯一索引、条件更新和幂等键。

另一个反方向的误区是一上来就堆满中间件。低频后台任务，线程池加数据库状态表可能就够了；任务不能丢、并发高、需要跨服务消费，再引入 MQ、Redis、Outbox、延迟队列。

## 场景题

你在做一个 Spring Boot 后端服务，接口会调用一个 AI Agent 生成结果，但生成耗时不稳定，可能从 2 秒到 30 秒不等。

前端希望用户提交后能尽快得到“任务已受理”，后续再查询结果；同时要避免同一个用户重复提交导致 Agent 被重复调用。

如果让你设计这个流程，你会怎么用 Redis、MySQL、消息队列或异步任务来做任务状态、幂等控制和失败重试？

顺便想想：任务执行中服务重启了，怎么保证结果不丢、状态不乱？
