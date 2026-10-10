---
layout: post
title: "CQRS 不只是读写分离：写事实，读投影"
description: "CQRS 的关键在于把写侧当作业务事实源、把读侧当作面向查询的投影，再用 CDC、Outbox、事件流和幂等投影连接两边，而不只是多加一个从库。"
date: 2026-07-17
tags: [CQRS, 系统设计, 数据架构]
---

很多人第一次听到 CQRS，会把它理解成“写主库、读从库”。这不算错，但主从读写分离解决的是数据库读压力，CQRS 多给了一层自由度：写侧围绕业务正确性建模，读侧围绕查询场景重新建模，两边可以用不同的存储，数据结构也不必相同。

我习惯把它概括成六个字：**写事实，读投影。**

<!--more-->

写侧回答“系统里真实发生了什么”，必须守住业务不变量；读侧回答“用户现在想怎么看这些事实”，应该让查询尽量简单、稳定、便宜。两者之间由 CDC、Outbox、事件流和投影器把事实转换成各种查询视图。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1210 820" role="img" aria-label="CQRS 总架构：写事实，读投影" style="width:100%;height:auto;display:block">
<defs>
  <marker id="cq1-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .cq1-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .cq1-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .cq1-box{fill:#fff;stroke:#374151;stroke-width:2}
    .cq1-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .cq1-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .cq1-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .cq1-line{stroke:#475569;stroke-width:2;fill:none}
  </style>
</defs>
<rect width="1210" height="820" fill="#fff"/>
<rect x="40" y="80" width="190" height="86" rx="12" class="cq1-box"/><text x="135" y="116.3" text-anchor="middle" class="cq1-t" font-size="20" font-weight="500"><tspan x="135" dy="0">Command</tspan><tspan x="135" dy="27">写请求</tspan></text><path d="M230 123 L320 123" class="cq1-line" marker-end="url(#cq1-arrow)"/><rect x="335" y="65" width="245" height="116" rx="12" class="cq1-ok"/><text x="457.5" y="115.965" text-anchor="middle" class="cq1-t" font-size="21" font-weight="500"><tspan x="457.5" dy="0">写模型</tspan><tspan x="457.5" dy="28.35">业务不变量</tspan></text><path d="M580 123 L670 123" class="cq1-line" marker-end="url(#cq1-arrow)"/><rect x="685" y="65" width="250" height="116" rx="12" class="cq1-box"/><text x="810" y="116.63499999999999" text-anchor="middle" class="cq1-t" font-size="19" font-weight="500"><tspan x="810" dy="0">写侧事实</tspan><tspan x="810" dy="25.650000000000002">MySQL / Event Store</tspan></text><path d="M810 181 L810 240" class="cq1-line" marker-end="url(#cq1-arrow)"/><rect x="660" y="255" width="300" height="92" rx="12" class="cq1-soft"/><text x="810" y="294.3" text-anchor="middle" class="cq1-t" font-size="20" font-weight="500"><tspan x="810" dy="0">CDC / Outbox</tspan><tspan x="810" dy="27">变化复制</tspan></text><path d="M810 347 L810 405" class="cq1-line" marker-end="url(#cq1-arrow)"/><rect x="660" y="420" width="300" height="92" rx="12" class="cq1-soft"/><text x="810" y="458.965" text-anchor="middle" class="cq1-t" font-size="21" font-weight="500"><tspan x="810" dy="0">Projection</tspan><tspan x="810" dy="28.35">投影器</tspan></text><path d="M810 512 L810 555" class="cq1-line"/><path d="M280 555 L1060 555" class="cq1-line"/><path d="M280 555 L280 615" class="cq1-line" marker-end="url(#cq1-arrow)"/><path d="M540 555 L540 615" class="cq1-line" marker-end="url(#cq1-arrow)"/><path d="M800 555 L800 615" class="cq1-line" marker-end="url(#cq1-arrow)"/><path d="M1060 555 L1060 615" class="cq1-line" marker-end="url(#cq1-arrow)"/><rect x="165" y="630" width="230" height="80" rx="12" class="cq1-box"/><text x="280" y="663.635" text-anchor="middle" class="cq1-t" font-size="19" font-weight="500"><tspan x="280" dy="0">Redis</tspan><tspan x="280" dy="25.650000000000002">点查</tspan></text><rect x="425" y="630" width="230" height="80" rx="12" class="cq1-box"/><text x="540" y="664.305" text-anchor="middle" class="cq1-t" font-size="17" font-weight="500"><tspan x="540" dy="0">Elasticsearch</tspan><tspan x="540" dy="22.950000000000003">筛选 / 搜索</tspan></text><rect x="685" y="630" width="230" height="80" rx="12" class="cq1-box"/><text x="800" y="663.97" text-anchor="middle" class="cq1-t" font-size="18" font-weight="500"><tspan x="800" dy="0">宽表</tspan><tspan x="800" dy="24.3">时间线 / 流水</tspan></text><rect x="945" y="630" width="230" height="80" rx="12" class="cq1-box"/><text x="1060" y="663.97" text-anchor="middle" class="cq1-t" font-size="18" font-weight="500"><tspan x="1060" dy="0">ClickHouse</tspan><tspan x="1060" dy="24.3">分析 / 报表</tspan></text><rect x="40" y="630" width="90" height="80" rx="12" class="cq1-soft"/><text x="85" y="676.12" text-anchor="middle" class="cq1-t" font-size="18" font-weight="500"><tspan x="85" dy="0">Query</tspan></text><text x="600" y="775" text-anchor="middle" class="cq1-t" font-size="22" font-weight="650"><tspan x="600" dy="0">写侧围绕正确性建模，读侧围绕查询建模；两边通过异步复制连接。</tspan></text>
</svg>
</figure>

## 和主从读写分离差在哪

普通主从架构里，从库基本是主库的同构副本：主库有什么表、怎么建模，从库照样复制。它能分担读压力，但查询能力仍然受原始写模型约束。

假设订单写侧为了保证业务正确性，拆成了 <code>order</code>、<code>payment</code>、<code>merchant</code>、<code>coupon</code> 等多张表。商户后台想查“最近一个月、已支付、金额大于 500 元、使用过优惠券的订单”，从库依然要做 Join、过滤和排序。换成 CQRS，读侧不必照抄这些表，可以单独维护一张商户订单列表宽表，把查询需要的字段提前铺平。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 480" role="img" aria-label="普通主从读写分离与 CQRS 的区别" style="width:100%;height:auto;display:block">
<defs>
  <marker id="cq2-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .cq2-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .cq2-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .cq2-box{fill:#fff;stroke:#374151;stroke-width:2}
    .cq2-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .cq2-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .cq2-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .cq2-line{stroke:#475569;stroke-width:2;fill:none}
  </style>
</defs>
<rect width="1200" height="480" fill="#fff"/>
<text x="300" y="42" text-anchor="middle" class="cq2-t" font-size="24" font-weight="700"><tspan x="300" dy="0">普通主从读写分离</tspan></text><rect x="80" y="90" width="440" height="95" rx="12" class="cq2-box"/><text x="300" y="131.135" text-anchor="middle" class="cq2-t" font-size="19" font-weight="500"><tspan x="300" dy="0">主库</tspan><tspan x="300" dy="25.650000000000002">order / payment / merchant</tspan></text><path d="M300 185 L300 245" class="cq2-line" marker-end="url(#cq2-arrow)"/><rect x="80" y="260" width="440" height="95" rx="12" class="cq2-soft"/><text x="300" y="301.135" text-anchor="middle" class="cq2-t" font-size="19" font-weight="500"><tspan x="300" dy="0">从库</tspan><tspan x="300" dy="25.650000000000002">基本同构的表结构</tspan></text><text x="300" y="420" text-anchor="middle" class="cq2-t" font-size="20" font-weight="500"><tspan x="300" dy="0">扩展读取能力</tspan><tspan x="300" dy="29">模型自由度有限</tspan></text><text x="900" y="42" text-anchor="middle" class="cq2-t" font-size="24" font-weight="700"><tspan x="900" dy="0">CQRS</tspan></text><rect x="680" y="90" width="440" height="95" rx="12" class="cq2-ok"/><text x="900" y="131.135" text-anchor="middle" class="cq2-t" font-size="19" font-weight="500"><tspan x="900" dy="0">写模型</tspan><tspan x="900" dy="25.650000000000002">围绕聚合与不变量</tspan></text><path d="M900 185 L900 245" class="cq2-line" marker-end="url(#cq2-arrow)"/><rect x="680" y="260" width="440" height="95" rx="12" class="cq2-soft"/><text x="900" y="301.135" text-anchor="middle" class="cq2-t" font-size="19" font-weight="500"><tspan x="900" dy="0">读模型</tspan><tspan x="900" dy="25.650000000000002">围绕查询场景重新建模</tspan></text><text x="900" y="420" text-anchor="middle" class="cq2-t" font-size="20" font-weight="500"><tspan x="900" dy="0">Redis / ES / 宽表 / ClickHouse</tspan><tspan x="900" dy="29">结构不必与写侧同构</tspan></text>
</svg>
</figure>

两者的区别就在这里：**主从复制的是同一个模型，CQRS 允许读侧拥有独立的模型。**

## 写侧：先守住事实

CQRS 的写侧并不只是“专门接 INSERT 和 UPDATE 的数据库”，它更重要的职责是维护业务不变量。比如支付系统里的订单状态，不能因为某个查询方便，就允许从 <code>CLOSED</code> 随便跳回 <code>PAID</code>；余额变化也不能为了查询速度，把多个互相约束的写操作拆成无法保证一致性的更新。所以写侧关心的是：聚合边界怎么划，哪些修改必须在一个本地事务里完成，并发写怎么控制，Command 重试时怎么保证幂等，哪些状态迁移合法。

实现上，写侧有两种常见形态。

| 写侧形态 | 保存什么 | 常见存储 | 特点 |
| --- | --- | --- | --- |
| 状态库型 | 聚合的当前状态 | MySQL、PostgreSQL | 工程成熟，容易理解 |
| Event Sourcing | 只追加事件流 | EventStoreDB、events 表 | 历史完整，可以重放 |

有个常见误解要先排除：**CQRS 不要求 Event Sourcing**。完全可以用 MySQL 保存订单当前状态，再通过 CDC 或 Outbox 构建 Redis、Elasticsearch 等读模型。Event Sourcing 是一种更彻底的事实建模方式，但不是使用 CQRS 的前提。如果采用 Event Sourcing，事件序列号通常还承担并发控制：同一个聚合的两次并发写都基于同一个 <code>expectedVersion</code> 时，只有一个应该成功写入下一个事件序号。

## 读侧：面向查询的投影

写侧为了正确性，需要规范化、聚合边界和事务约束；读侧没有这些包袱，可以把 Join、聚合、排序甚至部分派生字段提前算好，让一次查询尽量退化成一次直接读取。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 405" role="img" aria-label="写侧保存业务事实，读侧保存面向查询的投影" style="width:100%;height:auto;display:block">
<defs>
  <marker id="cq3-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .cq3-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .cq3-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .cq3-box{fill:#fff;stroke:#374151;stroke-width:2}
    .cq3-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .cq3-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .cq3-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .cq3-line{stroke:#475569;stroke-width:2;fill:none}
  </style>
</defs>
<rect width="1200" height="405" fill="#fff"/>
<text x="300" y="42" text-anchor="middle" class="cq3-t" font-size="24" font-weight="700"><tspan x="300" dy="0">写侧：保存事实</tspan></text><rect x="70" y="88" width="460" height="92" rx="12" class="cq3-ok"/><text x="300" y="140.46" text-anchor="middle" class="cq3-t" font-size="19" font-weight="500"><tspan x="300" dy="0">Order · Payment · Coupon · Merchant</tspan></text><text x="300" y="245" text-anchor="middle" class="cq3-t" font-size="20" font-weight="500"><tspan x="300" dy="0">规范化 · 聚合边界 · 事务约束</tspan><tspan x="300" dy="30">目标：业务状态必须正确</tspan></text><path d="M530 134 L650 134" class="cq3-line" marker-end="url(#cq3-arrow)"/><text x="900" y="42" text-anchor="middle" class="cq3-t" font-size="24" font-weight="700"><tspan x="900" dy="0">读侧：保存投影</tspan></text><rect x="670" y="88" width="460" height="92" rx="12" class="cq3-soft"/><text x="900" y="127.63499999999999" text-anchor="middle" class="cq3-t" font-size="19" font-weight="500"><tspan x="900" dy="0">merchant_order_view</tspan><tspan x="900" dy="25.650000000000002">订单 + 商户 + 券 + 渠道已经铺平</tspan></text><text x="900" y="245" text-anchor="middle" class="cq3-t" font-size="20" font-weight="500"><tspan x="900" dy="0">反规范化 · 预计算 · 面向查询</tspan><tspan x="900" dy="30">目标：查询简单、稳定、便宜</tspan></text><text x="600" y="345" text-anchor="middle" class="cq3-t" font-size="23" font-weight="650"><tspan x="600" dy="0">读模型不是事实的原样副本，而是对事实的一种查询视图。</tspan></text>
</svg>
</figure>

同一份业务事实因此可以有很多种投影：按订单号查单用 Redis 或专用查询表，商户后台多维筛选用 Elasticsearch，用户交易流水用时间线宽表，对账和分析报表用 ClickHouse 或物化视图。

“一个查询场景一个投影”不等于每个接口都要新建一套数据库，意思是读模型的结构由查询形态决定，不必跟着写库的表结构走。如果一个新的查询需求逼着写侧改表、加冗余字段、破坏聚合边界，往往说明读写职责还没有真正分开。

## 把变化从写侧搬到读侧

把读写模型画成两个框很容易，难的是中间：写侧状态变了以后，怎样可靠地让读侧知道？按携带的业务语义从少到多，复制机制大致可以分成四级。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 370" role="img" aria-label="CQRS 写侧到读侧复制机制的四级演进" style="width:100%;height:auto;display:block">
<defs>
  <marker id="cq4-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .cq4-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .cq4-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .cq4-box{fill:#fff;stroke:#374151;stroke-width:2}
    .cq4-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .cq4-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .cq4-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .cq4-line{stroke:#475569;stroke-width:2;fill:none}
  </style>
</defs>
<rect width="1200" height="370" fill="#fff"/>
<rect x="45" y="115" width="250" height="120" rx="12" class="cq4-soft"/><text x="170" y="156.82" text-anchor="middle" class="cq4-t" font-size="18" font-weight="500"><tspan x="170" dy="0">L0</tspan><tspan x="170" dy="24.3">主从复制</tspan><tspan x="170" dy="24.3">同构副本</tspan></text><path d="M295 175 L330 175" class="cq4-line" marker-end="url(#cq4-arrow)"/><rect x="335" y="115" width="250" height="120" rx="12" class="cq4-soft"/><text x="460" y="156.82" text-anchor="middle" class="cq4-t" font-size="18" font-weight="500"><tspan x="460" dy="0">L1</tspan><tspan x="460" dy="24.3">CDC</tspan><tspan x="460" dy="24.3">行变更 → 事件流</tspan></text><path d="M585 175 L620 175" class="cq4-line" marker-end="url(#cq4-arrow)"/><rect x="625" y="115" width="250" height="120" rx="12" class="cq4-soft"/><text x="750" y="156.82" text-anchor="middle" class="cq4-t" font-size="18" font-weight="500"><tspan x="750" dy="0">L2</tspan><tspan x="750" dy="24.3">Outbox</tspan><tspan x="750" dy="24.3">业务事件可靠发布</tspan></text><path d="M875 175 L910 175" class="cq4-line" marker-end="url(#cq4-arrow)"/><rect x="915" y="115" width="250" height="120" rx="12" class="cq4-warn"/><text x="1040" y="156.82" text-anchor="middle" class="cq4-t" font-size="18" font-weight="500"><tspan x="1040" dy="0">L3</tspan><tspan x="1040" dy="24.3">Event Sourcing</tspan><tspan x="1040" dy="24.3">事件流成为事实源</tspan></text><text x="600" y="45" text-anchor="middle" class="cq4-t" font-size="23" font-weight="650"><tspan x="600" dy="0">从写侧到读侧，复制机制可以逐级增加业务语义和建模自由。</tspan></text><text x="600" y="315" text-anchor="middle" class="cq4-t" font-size="21" font-weight="500"><tspan x="600" dy="0">越往右，重放与重建能力越强，工程复杂度也越高。</tspan></text>
</svg>
</figure>

L0 是数据库自带的主从复制，通过 binlog 或 WAL 把变化复制到从库。它成熟、透明，业务代码几乎无感，但读写两侧基本同构，建模自由有限。

L1 是 CDC：读取 binlog 或 WAL，把行级变化转成事件流送进 Kafka 等消息系统。一份变化可以被多个消费者使用，分别更新 Redis、Elasticsearch、ClickHouse 等投影。要注意 CDC 只是变化捕获和复制技术，它本身并不等于 CQRS。

L2 是应用事件加 Transactional Outbox。当系统想表达 <code>OrderPaid</code>、<code>OrderRefunded</code>、<code>OrderClosed</code> 这样的业务语义，而不只是“某一行变了”，就会进入这一层，这时最容易撞上的是双写问题，下一节单独说。

L3 是 Event Sourcing，事件流本身成为唯一事实源。当前状态由历史事件折叠得到，读模型是同一份事件流的不同投影。它天然支持审计、重放和重建投影，代价是事件 schema 演进、快照、回放和版本兼容都会复杂很多。这四级没有高下之分，按能承受的复杂度选就行。

## Outbox 与双写问题

应用事件最危险的写法，是先更新数据库，再发 Kafka。订单已经在数据库里变成 <code>PAID</code>，随后发 Kafka 失败，读侧就永远收不到这次变化；反过来，消息先发成功、数据库最后回滚，消费者又会看到一个实际并不存在的业务事实。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 470" role="img" aria-label="直接双写的裂缝与 Transactional Outbox" style="width:100%;height:auto;display:block">
<defs>
  <marker id="cq5-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .cq5-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .cq5-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .cq5-box{fill:#fff;stroke:#374151;stroke-width:2}
    .cq5-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .cq5-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .cq5-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .cq5-line{stroke:#475569;stroke-width:2;fill:none}
  </style>
</defs>
<rect width="1200" height="470" fill="#fff"/>
<text x="300" y="42" text-anchor="middle" class="cq5-t" font-size="24" font-weight="700"><tspan x="300" dy="0">直接双写</tspan></text><rect x="70" y="90" width="230" height="80" rx="12" class="cq5-ok"/><text x="185" y="136.8" text-anchor="middle" class="cq5-t" font-size="20" font-weight="500"><tspan x="185" dy="0">业务 DB</tspan></text><rect x="340" y="90" width="230" height="80" rx="12" class="cq5-box"/><text x="455" y="136.8" text-anchor="middle" class="cq5-t" font-size="20" font-weight="500"><tspan x="455" dy="0">Kafka</tspan></text><path d="M300 130 L330 130" class="cq5-line" marker-end="url(#cq5-arrow)"/><text x="300" y="230" text-anchor="middle" class="cq5-t" font-size="19" font-weight="500"><tspan x="300" dy="0">DB 成功 / Kafka 失败</tspan><tspan x="300" dy="29">Kafka 成功 / DB 回滚</tspan></text><text x="900" y="42" text-anchor="middle" class="cq5-t" font-size="24" font-weight="700"><tspan x="900" dy="0">Transactional Outbox</tspan></text><rect x="650" y="80" width="500" height="108" rx="12" class="cq5-ok"/><text x="900" y="127.3" text-anchor="middle" class="cq5-t" font-size="20" font-weight="500"><tspan x="900" dy="0">同一本地事务</tspan><tspan x="900" dy="27">业务状态 + Outbox 事件</tspan></text><path d="M900 188 L900 245" class="cq5-line" marker-end="url(#cq5-arrow)"/><rect x="735" y="260" width="330" height="88" rx="12" class="cq5-soft"/><text x="900" y="297.635" text-anchor="middle" class="cq5-t" font-size="19" font-weight="500"><tspan x="900" dy="0">CDC / Relay</tspan><tspan x="900" dy="25.650000000000002">异步发布 Kafka</tspan></text><text x="900" y="415" text-anchor="middle" class="cq5-t" font-size="20" font-weight="500"><tspan x="900" dy="0">原子性留在数据库事务里</tspan><tspan x="900" dy="30">Kafka 负责后续传播</tspan></text>
</svg>
</figure>

Transactional Outbox 的做法，是把业务状态变化和需要发布的事件放进同一个本地数据库事务：

~~~sql
BEGIN;

UPDATE orders
SET status = 'PAID'
WHERE order_id = 'xxx';

INSERT INTO outbox(id, topic, payload)
VALUES (..., 'OrderPaid', ...);

COMMIT;
~~~

事务提交后，再由 CDC 或独立的 relay 把 Outbox 中的事件发布到 Kafka。这样**原子性边界就收回到了本地数据库事务里**：数据库保证业务状态和待发布事件一起成功或一起失败，Kafka 不再参与业务库的原子事务，只负责后续传播。

## 投影器：默认消息会重复，自己会崩

数据一旦进入 Kafka 或事件总线，投影器就得按分布式系统的现实来设计：消息可能重复，消费者可能重启，事件可能积压，同一个聚合的多个事件还可能有顺序要求。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" role="img" aria-label="CQRS 投影器需要保证顺序、幂等、消费位置和可观测性" style="width:100%;height:auto;display:block">
<defs>
  <marker id="cq6-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .cq6-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .cq6-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .cq6-box{fill:#fff;stroke:#374151;stroke-width:2}
    .cq6-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .cq6-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .cq6-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .cq6-line{stroke:#475569;stroke-width:2;fill:none}
  </style>
</defs>
<rect width="1200" height="630" fill="#fff"/>
<rect x="70" y="50" width="220" height="85" rx="12" class="cq6-soft"/><text x="180" y="99.64" text-anchor="middle" class="cq6-t" font-size="21" font-weight="650"><tspan x="180" dy="0">顺序</tspan></text><rect x="330" y="50" width="300" height="85" rx="12" class="cq6-box"/><text x="480" y="98.62" text-anchor="middle" class="cq6-t" font-size="18" font-weight="500"><tspan x="480" dy="0">aggregate_id 作为 key</tspan></text><rect x="670" y="50" width="460" height="85" rx="12" class="cq6-box"/><text x="900" y="98.28" text-anchor="middle" class="cq6-t" font-size="17" font-weight="500"><tspan x="900" dy="0">同一聚合尽量进入同一 partition</tspan></text><rect x="70" y="170" width="220" height="85" rx="12" class="cq6-soft"/><text x="180" y="219.64" text-anchor="middle" class="cq6-t" font-size="21" font-weight="650"><tspan x="180" dy="0">幂等</tspan></text><rect x="330" y="170" width="300" height="85" rx="12" class="cq6-box"/><text x="480" y="218.62" text-anchor="middle" class="cq6-t" font-size="18" font-weight="500"><tspan x="480" dy="0">event_id / version</tspan></text><rect x="670" y="170" width="460" height="85" rx="12" class="cq6-box"/><text x="900" y="218.28" text-anchor="middle" class="cq6-t" font-size="17" font-weight="500"><tspan x="900" dy="0">重复事件不会重复生效</tspan></text><rect x="70" y="290" width="220" height="85" rx="12" class="cq6-soft"/><text x="180" y="339.64" text-anchor="middle" class="cq6-t" font-size="21" font-weight="650"><tspan x="180" dy="0">位置</tspan></text><rect x="330" y="290" width="300" height="85" rx="12" class="cq6-box"/><text x="480" y="338.62" text-anchor="middle" class="cq6-t" font-size="18" font-weight="500"><tspan x="480" dy="0">offset / position</tspan></text><rect x="670" y="290" width="460" height="85" rx="12" class="cq6-box"/><text x="900" y="338.28" text-anchor="middle" class="cq6-t" font-size="17" font-weight="500"><tspan x="900" dy="0">进程重启后从断点继续</tspan></text><rect x="70" y="410" width="220" height="85" rx="12" class="cq6-soft"/><text x="180" y="459.64" text-anchor="middle" class="cq6-t" font-size="21" font-weight="650"><tspan x="180" dy="0">可观测</tspan></text><rect x="330" y="410" width="300" height="85" rx="12" class="cq6-box"/><text x="480" y="458.62" text-anchor="middle" class="cq6-t" font-size="18" font-weight="500"><tspan x="480" dy="0">projection lag</tspan></text><rect x="670" y="410" width="460" height="85" rx="12" class="cq6-box"/><text x="900" y="458.28" text-anchor="middle" class="cq6-t" font-size="17" font-weight="500"><tspan x="900" dy="0">知道读侧落后写侧多少</tspan></text><text x="600" y="570" text-anchor="middle" class="cq6-t" font-size="21" font-weight="650"><tspan x="600" dy="0">常见目标不是绝对 exactly-once，而是 at-least-once + 幂等投影 + 可重放。</tspan></text>
</svg>
</figure>

顺序方面，同一个订单的 <code>OrderCreated</code>、<code>OrderPaid</code>、<code>OrderRefunded</code> 最好按聚合顺序消费，典型做法是用 <code>aggregate_id</code> 作为 Kafka key，让同一聚合进入同一个 partition。

投递语义上，很多系统没必要执着于端到端的绝对 exactly-once，更常见、也更容易验证的做法是 **at-least-once + 消费端幂等**。投影器可以用 <code>event_id</code> 唯一键去重，也可以比较 <code>version / seq</code>，只有新事件版本高于当前投影版本时才更新。

此外，消费者要保存 offset 或 position，进程重启后才能从断点继续；还要持续监控 <code>consumer lag</code> 或 <code>projection lag</code>，它直接告诉你读侧比写侧落后多少。我觉得这个指标比“消息有没有报错”更能反映 CQRS 链路的健康程度。

## 最终一致与 Read Your Writes

CQRS 最大的代价是最终一致。写侧提交以后，变化还要经过 CDC 或 Outbox、Kafka、投影器才进入读侧，中间必然有时间差，读模型看到的总是写模型稍早的某个版本。

报表、列表、统计这类场景，几十毫秒甚至几秒的延迟通常不是问题，但支付查单不一样。用户刚支付成功，写侧已经是 <code>PAID</code>，读侧可能还停在 <code>WAIT_PAY</code>。用户立即查单看到“待支付”，不仅体验差，还可能诱发再次支付。这就是 CQRS 里最棘手的体验问题之一：**Read Your Writes**。常见解法有三种。

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| 版本门 | 写侧返回 version，读侧至少追到该版本再返回 | 查询可能等待 projection lag |
| 关键路径短期读主 | 刚写完的一小段时间直接读写侧 | 增加写库压力 |
| 会话粘性 | 同一会话尽量命中一致的投影分片 | 路由和状态管理更复杂 |

支付场景里最直观的是版本门。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 500" role="img" aria-label="Read Your Writes 的版本门机制" style="width:100%;height:auto;display:block">
<defs>
  <marker id="cq7-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .cq7-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .cq7-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .cq7-box{fill:#fff;stroke:#374151;stroke-width:2}
    .cq7-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .cq7-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .cq7-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .cq7-line{stroke:#475569;stroke-width:2;fill:none}
  </style>
</defs>
<rect width="1200" height="500" fill="#fff"/>
<rect x="50" y="90" width="230" height="90" rx="12" class="cq7-ok"/><text x="165" y="128.635" text-anchor="middle" class="cq7-t" font-size="19" font-weight="500"><tspan x="165" dy="0">写侧</tspan><tspan x="165" dy="25.650000000000002">order_version = 105</tspan></text><path d="M280 135 L390 135" class="cq7-line" marker-end="url(#cq7-arrow)"/><rect x="405" y="90" width="260" height="90" rx="12" class="cq7-soft"/><text x="535" y="128.635" text-anchor="middle" class="cq7-t" font-size="19" font-weight="500"><tspan x="535" dy="0">事件流</tspan><tspan x="535" dy="25.650000000000002">投影仍在追赶</tspan></text><path d="M665 135 L775 135" class="cq7-line" marker-end="url(#cq7-arrow)"/><rect x="790" y="90" width="350" height="90" rx="12" class="cq7-warn"/><text x="965" y="128.635" text-anchor="middle" class="cq7-t" font-size="19" font-weight="500"><tspan x="965" dy="0">读侧</tspan><tspan x="965" dy="25.650000000000002">projection.version = 103</tspan></text><rect x="405" y="275" width="330" height="90" rx="12" class="cq7-box"/><text x="570" y="313.635" text-anchor="middle" class="cq7-t" font-size="19" font-weight="500"><tspan x="570" dy="0">客户端查单</tspan><tspan x="570" dy="25.650000000000002">min_version = 105</tspan></text><path d="M570 275 L570 225" class="cq7-line" marker-end="url(#cq7-arrow)"/><path d="M570 225 L965 225" class="cq7-line"/><path d="M965 225 L965 190" class="cq7-line" marker-end="url(#cq7-arrow)"/><text x="600" y="430" text-anchor="middle" class="cq7-t" font-size="21" font-weight="650"><tspan x="600" dy="0">103 &lt; 105：暂时不返回旧结果</tspan><tspan x="600" dy="32">投影追到 105 后再返回，或关键路径短时间读主</tspan></text>
</svg>
</figure>

假设支付成功后写侧返回 <code>order_version = 105</code>，而读侧投影还在 <code>103</code>。客户端立即查单时带上 <code>min_version = 105</code>，查询服务发现 <code>103 &lt; 105</code>，就知道当前投影还没包含刚才那次写入。它可以短暂等待投影追上，也可以在关键路径上退回写侧读取。

版本门的意思很朴素：系统整体可以最终一致，但这次查询至少要看到我刚写进去的结果。这比要求所有读请求都强一致便宜得多，也比完全放任陈旧读取安全。

## 按查询分配一致性预算

反过来，也没必要把所有读都赶去异步投影，或者把所有查询都做成“等版本”。不同查询场景应该有不同的一致性预算。

| 场景 | 一致性要求 | 更合适的策略 |
| --- | --- | --- |
| 支付成功后的关键查单 | 高 | 版本门或短期读主 |
| 普通订单详情 | 中 | 读模型，必要时带版本 |
| 商户订单列表 | 中低 | Elasticsearch / 宽表 |
| 用户流水 | 中低 | 时间线投影 |
| 对账、日报、分析 | 低 | ClickHouse，可接受分钟级或更长延迟 |

设计时与其笼统地问“CQRS 是强一致还是最终一致”，不如逐个查询去问：看到旧数据的业务后果是什么？最多能旧多久？需不需要 Read Your Writes？

## Saga、CDC 和 CQRS 的分工

这几个概念经常一起出现，也最容易混为一谈。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1220 610" role="img" aria-label="Saga、CDC / Outbox 与 CQRS 的职责关系" style="width:100%;height:auto;display:block">
<defs>
  <marker id="cq8-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .cq8-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .cq8-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .cq8-box{fill:#fff;stroke:#374151;stroke-width:2}
    .cq8-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .cq8-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .cq8-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .cq8-line{stroke:#475569;stroke-width:2;fill:none}
  </style>
</defs>
<rect width="1220" height="610" fill="#fff"/>
<rect x="45" y="150" width="180" height="82" rx="12" class="cq8-box"/><text x="135" y="197.8" text-anchor="middle" class="cq8-t" font-size="20" font-weight="500"><tspan x="135" dy="0">Command</tspan></text><path d="M225 191 L315 191" class="cq8-line" marker-end="url(#cq8-arrow)"/><rect x="330" y="135" width="240" height="112" rx="12" class="cq8-ok"/><text x="450" y="183.96499999999997" text-anchor="middle" class="cq8-t" font-size="21" font-weight="500"><tspan x="450" dy="0">写模型</tspan><tspan x="450" dy="28.35">业务事实</tspan></text><path d="M570 160 L680 85" class="cq8-line" marker-end="url(#cq8-arrow)"/><rect x="695" y="40" width="305" height="100" rx="12" class="cq8-warn"/><text x="847.5" y="83.63499999999999" text-anchor="middle" class="cq8-t" font-size="19" font-weight="500"><tspan x="847.5" dy="0">Saga</tspan><tspan x="847.5" dy="25.650000000000002">横向：跨服务写一致性</tspan></text><path d="M570 215 L680 290" class="cq8-line" marker-end="url(#cq8-arrow)"/><rect x="695" y="250" width="305" height="100" rx="12" class="cq8-soft"/><text x="847.5" y="293.635" text-anchor="middle" class="cq8-t" font-size="19" font-weight="500"><tspan x="847.5" dy="0">CDC / Outbox</tspan><tspan x="847.5" dy="25.650000000000002">纵向：变化复制</tspan></text><path d="M1000 300 L1080 300" class="cq8-line" marker-end="url(#cq8-arrow)"/><rect x="1055" y="390" width="130" height="80" rx="12" class="cq8-box"/><text x="1120" y="436.46" text-anchor="middle" class="cq8-t" font-size="19" font-weight="500"><tspan x="1120" dy="0">Kafka</tspan></text><path d="M1120 350 L1120 390" class="cq8-line"/><path d="M1055 430 L930 430" class="cq8-line" marker-end="url(#cq8-arrow)"/><rect x="610" y="385" width="300" height="95" rx="12" class="cq8-soft"/><text x="760" y="426.135" text-anchor="middle" class="cq8-t" font-size="19" font-weight="500"><tspan x="760" dy="0">CQRS Read Model</tspan><tspan x="760" dy="25.650000000000002">面向查询的异构投影</tspan></text><text x="600" y="555" text-anchor="middle" class="cq8-t" font-size="21" font-weight="650"><tspan x="600" dy="0">Saga 管跨服务写，CDC / Outbox 管变化传播，CQRS 管事实如何变成查询模型。</tspan></text>
</svg>
</figure>

Saga 处理横向问题：一次 Command 跨多个服务时，多个写操作如何最终收敛。CDC 或 Outbox 处理传播问题：写侧已经发生的变化，怎样可靠地进入事件流并传到下游。CQRS 处理职责划分：写模型和读模型为什么分开，同一份业务事实怎样变成适合不同查询的投影。三者可以叠加使用，但不能互相替代。

## 什么时候值得用

CQRS 不该是默认选项。如果系统只有少量简单 CRUD，读写模型几乎一致，查询也没有明显的性能或建模冲突，引入事件流、投影器和最终一致只会徒增复杂度。它更适合这些情况：

- 写侧业务规则复杂，但读侧查询形态很多。
- 查询量远大于写入量，需要独立扩展。
- 列表、搜索、时间线、报表分别适合不同存储。
- 一个写模型正在被越来越多的查询冗余字段污染。
- 需要通过事件流给多个下游构建不同视图。
- 可以接受大部分读场景最终一致，并能明确处理关键路径的 Read Your Writes。

我自己的判断标准跟系统规模关系不大，主要看**写模型和读模型是否已经在争夺同一份数据结构的设计权**。写侧为了查询不断牺牲业务模型，或者读侧为了遵守写模型不得不做越来越重的 Join，这时候引入 CQRS 才划算。

真用上以后，两边的定位要守住：写侧少操心“页面怎么查”，专心回答什么状态合法、什么事实已经发生；读侧别冒充事实源，它只是事实经过异步复制和计算形成的查询视图，可以延迟，可以重建，也可以为不同查询存在很多份。
