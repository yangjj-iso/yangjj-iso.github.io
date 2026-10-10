---
layout: post
title: "异步任务处理模式的工程实践"
description: "梳理异步任务的状态机、任务表、幂等控制、可靠投递、重试补偿与宕机恢复，以及 MySQL、Redis、MQ 和 Outbox 的取舍。"
date: 2026-06-23
tags: [后端开发, 系统设计, 异步任务, 幂等, 消息队列]
---

## 1. 前言

在后端开发里，有一类接口不适合按普通同步接口处理。

<!--more-->

比如接口内部要调用第三方服务、AI Agent、文件处理、报表生成、视频转码、批量导入、风控审核、支付后置处理等。这些任务的耗时不稳定，可能 2 秒返回，也可能 30 秒甚至几分钟才返回。更麻烦的是，任务执行期间服务可能重启，第三方服务可能超时，用户也可能重复点击提交。

如果直接让 HTTP 请求一直等到任务执行完成，会带来几个问题：

- 请求连接长时间占用，容易触发网关、浏览器或服务端超时。
- 用户重复提交时，后端可能重复调用昂贵的外部能力。
- 服务重启后，内存中的任务状态丢失。
- 任务失败后缺少统一重试、补偿和排查入口。
- 高峰期大量长任务挤占业务线程，影响普通接口。

这时更合适的做法是把同步接口改造成异步任务：

```text
用户提交请求
  -> 后端创建任务，快速返回 taskId
  -> 后台异步执行任务
  -> 前端轮询或订阅任务状态
  -> 任务完成后获取结果
```

这种设计可以称为 异步任务处理模式。它不是某一个框架的专属能力，而是一套通用的工程实践。

## 2. 适用场景

异步任务处理模式适合下面这些场景。

### 2.1 耗时不稳定

任务可能很快完成，也可能长时间没有结果。

典型例子：

- AI Agent 生成内容
- 文件上传后的解析
- Excel 批量导入
- 大数据量报表生成
- 第三方接口聚合调用

这类任务不适合把用户请求一直挂住。更好的体验是先告诉用户“任务已受理”，后续再查询进度和结果。

### 2.2 需要削峰填谷

如果用户请求一进来就立即执行重任务，流量高峰时业务服务很容易被打满。

异步化之后，请求线程只负责创建任务，真正执行任务的消费者可以按固定并发消费 MQ 或任务队列。这样可以把瞬时高峰转成后台平稳处理。

### 2.3 需要失败重试

只要依赖外部服务，就要默认它可能失败。

比如：

- 网络超时
- 第三方限流
- Agent 返回异常
- 下游服务临时不可用

异步任务天然适合做重试，因为任务已经有了独立的任务记录、状态和失败原因。

### 2.4 需要宕机恢复

如果任务只放在内存里，服务一重启，任务状态就没了。

所以长任务要先落库。数据库里的任务表应该是任务状态的最终事实来源。只要数据库记录还在，服务重启后就能知道哪些任务还没完成、哪些任务需要重试、哪些任务已经成功。

### 2.5 用户可以接受最终一致

异步任务不是所有场景都适合。

如果业务强要求“请求返回时结果必须已经完成”，比如登录、下单扣库存前置校验、实时支付确认，就不能简单改成后台慢慢跑。

异步任务适合用户可以接受下面这种体验的场景：

- 提交成功，不代表执行完成。
- 后续通过 taskId 查询执行状态和结果。

## 3. 核心思想

异步任务处理模式的核心可以概括为四句话：

- MySQL 保存任务事实和最终状态。
- MQ 或任务队列驱动后台执行。
- Redis 缓存任务状态、进度和幂等标记。
- 状态机约束任务只能按合法路径流转。

一个典型流程如下：

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

这里最重要的一点是：数据库是最终事实来源，Redis 只是缓存和辅助控制，MQ 只是执行驱动。

不要把任务唯一状态只放在 Redis 或 MQ 里。Redis 可能过期，MQ 消息可能重复投递，消费者可能执行到一半宕机。真正能支撑恢复和排查的，通常还是任务表。

## 4. 任务状态机

任务不是只有成功和失败。一个可维护的异步任务系统，应该先定义清楚状态。

常见状态：

| 状态 | 含义 |
| --- | --- |
| pending | 任务已创建，等待执行 |
| running | 任务正在执行 |
| success | 任务执行成功 |
| failed | 任务执行失败，不再重试 |
| retrying | 任务等待下一次重试 |
| timeout | 任务执行超时 |
| canceled | 任务被取消 |

基本流转可以是：

```text
pending -> running -> success
pending -> running -> retrying -> pending
pending -> running -> failed
pending -> running -> timeout
pending -> canceled
```

状态流转最好不要靠业务代码随便改，而是通过明确的条件更新控制。

比如消费者抢任务时：

```sql
UPDATE async_task
SET status = 'running',
    started_at = NOW(),
    worker_id = ?,
    updated_at = NOW()
WHERE id = ?
  AND status = 'pending';
```

只有更新成功的消费者才有资格执行任务。

这个条件非常关键。因为 MQ 可能重复投递，多个消费者可能同时拿到同一个任务消息。如果没有状态条件，就可能重复执行。

## 5. 表结构设计

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

字段重点：

| 字段 | 说明 |
| --- | --- |
| task_no | 对外暴露的任务号，避免直接暴露自增 ID |
| idempotency_key | 幂等键，用于防止重复提交 |
| status | 当前任务状态 |
| request_payload | 任务输入参数，用于重试和排查 |
| result_payload | 任务结果，任务完成后查询使用 |
| retry_count | 已重试次数 |
| next_retry_at | 延迟重试时间 |
| worker_id | 当前执行节点，方便排查任务卡在哪台机器 |
| updated_at | 用于判断 running 任务是否长时间无心跳 |

UNIQUE KEY uk_user_biz_idempotency 是防重复提交的关键。它保证同一个用户、同一种业务、同一个幂等键只能创建一个任务。

## 6. 中间件取舍

这个模式可能涉及 MySQL、Redis、MQ、定时任务、分布式锁、对象存储等，但不是每个业务都必须全上。

### 6.1 MySQL

MySQL 通常是必须的。

它负责保存：

- 任务状态
- 请求参数
- 执行结果
- 失败原因
- 重试次数
- 操作时间

如果业务要求宕机恢复、可追踪、可排查，任务状态就应该落库。

适合放 MySQL 的数据：

- 最终任务状态
- 重要业务结果
- 审计和排查信息
- 幂等约束

不适合只放 MySQL 的数据：

- 高频进度刷新
- 秒级心跳
- 临时限流计数

### 6.2 Redis

Redis 不是异步任务的最终状态库，它更适合做辅助。

常见用途：

- 缓存任务状态，减少查询 DB 压力。
- 保存任务进度，比如 30%、step=generating。
- 做短期幂等控制，拦截用户连续点击。
- 做限流，比如限制同一用户同时运行的任务数量。
- 做分布式锁，但要谨慎设计锁超时和续期。

适合用 Redis 的情况：

- 前端会频繁轮询任务状态。
- 任务进度更新很频繁。
- 需要快速判断用户是否刚提交过相同任务。
- 查询量远高于任务执行量。

不建议只依赖 Redis 的情况：

- 任务结果不能丢。
- 任务状态需要长期保留。
- 任务失败后需要排查历史。

Redis 可以丢，MySQL 不能丢。这个边界要明确。

### 6.3 MQ

MQ 的价值是解耦和削峰。

请求线程只负责创建任务和投递消息，消费者按自己的能力慢慢处理任务。

常见选择：

| MQ | 适合场景 |
| --- | --- |
| RabbitMQ | 普通业务异步、路由灵活、延迟队列 |
| RocketMQ | 高吞吐、事务消息、延迟消息、业务可靠性要求高 |
| Kafka | 日志流、事件流、高吞吐顺序消费 |
| Redis Stream | 轻量队列、已有 Redis、任务可靠性要求中等 |
| 本地线程池 | 单机 Demo、低可靠性后台任务 |

如果业务只是单机内部的轻量异步任务，可以先用线程池。

如果任务不能丢、需要重试、需要跨服务消费，就应该使用 MQ。

如果任务创建和消息发送必须强一致，就要考虑事务消息或 Outbox。

### 6.4 定时任务

定时任务主要负责补偿。

比如：

- 扫描长时间 running 的任务，判断是否超时。
- 扫描 retrying 且 next_retry_at <= now() 的任务，重新投递 MQ。
- 扫描 pending 很久没有被消费的任务，重新投递。
- 清理过期的 Redis 缓存。

没有补偿机制的异步任务系统，很容易出现任务永远卡在某个状态。

### 6.5 分布式锁

分布式锁不是必选项。

很多时候，数据库条件更新就能解决抢占问题：

```sql
UPDATE async_task
SET status = 'running'
WHERE id = ?
  AND status = 'pending';
```

只有当任务涉及“同一用户同一时间只能运行一个任务”这类跨任务约束时，才考虑 Redis 锁。

即使用 Redis 锁，也不能只靠锁保证正确性。锁可能过期、服务可能停顿、网络可能抖动。最终还是要靠数据库状态和唯一索引兜底。

## 7. 幂等设计

异步任务里，幂等非常重要。

幂等要解决两个问题：

- 用户重复提交，不能重复创建任务。
- MQ 重复投递，不能重复执行任务。

### 7.1 提交幂等

前端提交任务时，应该带一个幂等键：

```http
POST /api/tasks
Idempotency-Key: ai-generate:article:hash
```

后端把它和用户、业务类型一起做唯一约束：

```text
user_id + biz_type + idempotency_key
```

如果第一次提交成功，返回新任务：

```json
{
  "taskNo": "T202606090001",
  "status": "pending"
}
```

如果用户重复提交，后端查询已存在任务并返回同一个 taskNo：

```json
{
  "taskNo": "T202606090001",
  "status": "running"
}
```

这样可以避免重复调用外部 Agent。

### 7.2 消费幂等

消费者收到 MQ 消息后，不能直接执行任务。先尝试抢占任务：

```sql
UPDATE async_task
SET status = 'running',
    started_at = NOW(),
    worker_id = ?,
    updated_at = NOW()
WHERE task_no = ?
  AND status IN ('pending', 'retrying');
```

如果影响行数为 0，说明任务已经被其他消费者处理，或者已经完成，当前消息可以直接忽略。

执行成功时，也要带状态条件：

```sql
UPDATE async_task
SET status = 'success',
    result_payload = ?,
    finished_at = NOW(),
    updated_at = NOW()
WHERE task_no = ?
  AND status = 'running';
```

这些条件更新就是异步任务最朴素也最可靠的幂等控制。

## 8. 可靠投递问题

一个容易被忽略的问题是：落库和发送 MQ 之间不是天然一致的。

如果代码这样写：

1. INSERT async_task 成功
2. 发送 MQ 失败

任务就会停在 pending，没有消费者执行。

如果反过来：

1. 发送 MQ 成功
2. INSERT async_task 失败

消费者收到消息后查不到任务。

解决方式有几种。

### 8.1 简化方案：失败后补偿扫描

对于可靠性要求中等的业务，可以先落库，再发 MQ。发 MQ 失败时记录日志，由定时任务扫描长时间 pending 的任务重新投递。

优点是简单。

缺点是任务执行可能延迟，依赖补偿任务兜底。

### 8.2 事务消息

如果使用 RocketMQ 这类支持事务消息的中间件，可以用事务消息保证本地事务和消息投递之间的一致性。

适合场景：

- 消息不能丢。
- 业务链路复杂。
- 团队已经有成熟 MQ 运维能力。

### 8.3 Outbox 模式

更通用的做法是 Outbox。

本地事务里同时写两张表：

```sql
INSERT async_task
INSERT outbox_message
```

然后由一个后台 relay 任务扫描 outbox_message，发送 MQ，发送成功后标记为 sent。

这样任务记录和待发送消息在同一个数据库事务里提交，避免了“任务有了但消息没了”的问题。

Outbox 适合可靠性要求较高、但 MQ 不支持事务消息或不想绑定特定 MQ 能力的场景。

## 9. 失败重试

失败重试不能简单地无限重试。

需要考虑：

- 哪些异常可以重试。
- 最多重试几次。
- 每次重试间隔多久。
- 失败后是否进入死信队列。
- 用户查询时看到什么状态。

常见策略：

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

如果外部服务已经执行成功，但后端在保存结果前失败，还要考虑业务幂等。比如调用 Agent 时传入 taskNo，让 Agent 侧也能识别重复请求，或者后端在调用前后保存足够的阶段状态。

## 10. 查询任务状态

前端提交任务后，后端快速返回：

```json
{
  "taskNo": "T202606090001",
  "status": "pending"
}
```

之后前端可以轮询：

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

任务完成后，结果可以同时写 MySQL 和 Redis：

- MySQL 保存最终结果
- Redis 缓存一段时间，提升查询速度

如果结果很大，比如文件、图片、报告，可以把结果放对象存储，MySQL 只保存结果地址。

## 11. 宕机恢复

服务重启最容易导致状态混乱。

常见问题：

- 任务已经改成 running，但服务在执行中宕机。
- 任务已经执行成功，但还没来得及更新数据库。
- MQ 消息已经消费，但还没确认。
- Redis 状态更新了，但 MySQL 没更新。

解决思路：

**第一，任务状态最终以 MySQL 为准。**

Redis 状态可以重建，MQ 消息可以重投，但 MySQL 里的任务记录不能丢。

**第二，消费者执行任务前用条件更新抢占任务。**

避免多个消费者重复执行。

**第三，running 状态必须有超时兜底。**

例如定时任务扫描：

```sql
SELECT *
FROM async_task
WHERE status = 'running'
  AND updated_at < DATE_SUB(NOW(), INTERVAL 10 MINUTE);
```

扫到后根据业务决定：

- 改为 retrying，重新投递。
- 改为 timeout，等待人工处理。
- 查询外部服务确认是否已完成，再修正本地状态。

**第四，任务执行过程可以更新心跳。**

长任务执行时定期更新：

```sql
UPDATE async_task
SET updated_at = NOW()
WHERE task_no = ?
  AND status = 'running';
```

这样补偿任务能区分“真的卡死”和“还在执行”。

**第五，MQ 确认要放在任务状态更新之后。**

一般来说，消费者应该在业务结果落库后再 ACK 消息。如果处理过程中宕机，消息可以重新投递，消费者再通过数据库状态判断是否需要继续执行。

## 12. 一个推荐流程

整体流程可以整理成下面这样。

### 提交任务：

1. 前端生成或后端计算 idempotency_key
2. 后端根据 user_id + biz_type + idempotency_key 查询任务
3. 已存在：直接返回已有 taskNo 和状态
4. 不存在：创建 async_task，状态 pending
5. 投递 MQ，或写 outbox_message
6. 返回 taskNo

### 消费任务：

1. 消费者收到 taskNo
2. 条件更新 pending/retrying -> running
3. 更新成功才执行任务
4. 调用外部服务或执行耗时逻辑
5. 成功：保存结果，更新 success
6. 失败：根据 retry_count 更新 retrying 或 failed
7. 更新 Redis 状态缓存
8. ACK MQ 消息

### 查询任务：

1. 根据 taskNo 查 Redis
2. Redis 未命中查 MySQL
3. 返回 pending/running/success/failed 等状态
4. success 时返回结果
5. failed 时返回可展示的失败原因

### 补偿任务：

1. 扫描 pending 太久的任务，重新投递
2. 扫描 retrying 到期的任务，重新投递
3. 扫描 running 超时的任务，转 retrying 或 timeout
4. 清理过期缓存

## 13. 常见误区

### 13.1 只把任务放进 MQ，不落库

这样做恢复能力很弱。

消息一旦消费失败、重复投递、进入死信或被误删，业务侧很难知道任务最终是什么状态。用户查询任务时也没有可靠依据。

### 13.2 Redis 当最终状态库

Redis 适合快，不适合做最终事实。

任务状态、结果、失败原因这些重要数据应该落 MySQL。Redis 可以作为缓存，但不能成为唯一来源。

### 13.3 没有幂等，只依赖前端防重复点击

前端防重复点击只能改善体验，不能保证正确性。

用户可以刷新页面、重试请求、网络超时后再次提交，MQ 也可能重复投递。后端必须通过唯一索引、状态条件更新、幂等键来保证正确性。

### 13.4 running 没有超时补偿

任务一旦进入 running，如果消费者宕机，就会永远卡住。

所以只要有 running 状态，就应该有超时检测和恢复策略。

### 13.5 一上来就堆满中间件

异步任务模式不是中间件越多越好。

如果只是一个低频后台任务，线程池加数据库状态表可能就够了。

如果任务不能丢、并发高、需要跨服务消费，再引入 MQ、Redis、Outbox、延迟队列等能力。

设计重点是匹配业务风险，而不是堆技术栈。

## 14. 总结

异步任务处理模式适合长耗时、不稳定、需要重试、需要恢复、允许最终一致的业务场景。

它的核心不是 MQ，也不是 Redis，而是任务状态的可靠建模：

- MySQL 保存任务事实。
- 状态机约束任务流转。
- MQ 驱动异步执行。
- Redis 提升查询和幂等控制效率。
- 条件更新保证消费幂等。
- 重试和补偿保证任务不会静默丢失。

如果把这个模式压缩成一句话：

先落库，再异步，状态可查，失败可重试，宕机可恢复。

## 15. 场景题

你在做一个 Spring Boot 后端服务，接口会调用一个 AI Agent 生成结果，但生成耗时不稳定，可能从 2 秒到 30 秒不等。

前端希望用户提交后能尽快得到“任务已受理”，后续再查询结果；同时要避免同一个用户重复提交导致 Agent 被重复调用。

如果让你设计这个流程，你会怎么用 Redis、MySQL、消息队列或异步任务来做任务状态、幂等控制和失败重试？

顺便想想：任务执行中服务重启了，怎么保证结果不丢、状态不乱？
