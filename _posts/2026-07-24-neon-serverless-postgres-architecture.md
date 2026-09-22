---
layout: post
title: "Neon 不只是 Serverless PostgreSQL：存算分离如何带来弹性、分支与时间旅行"
description: "从 Compute、Safekeeper、Pageserver 和对象存储出发，理解 Neon 为什么能让 PostgreSQL 的计算层可启停、可扩缩，同时原生支持数据库分支、PITR 和按需计算。"
date: 2026-07-24
tags: [PostgreSQL, Neon, 数据库, Serverless]
---

第一次接触 Neon，很容易把它理解成“一个会自动休眠的 PostgreSQL”。

这当然是它最直观的体验之一，但如果只看到 Serverless，就会错过 Neon 真正有意思的地方。

Neon 的核心不是在 PostgreSQL 外面加一层“自动开关机”，而是重新拆开了传统 PostgreSQL 里长期绑在一起的两件事。

**计算负责执行 SQL，存储负责保存事实。**

当持久化数据不再属于某一台 Postgres 机器以后，计算节点就可以变得短命。它可以启动、停止、换机器、扩容、缩容，而数据库历史仍然独立存在。

这一步架构变化，后面才自然长出了 autoscaling、scale-to-zero、database branching、read replica 和 point-in-time restore。

<!--more-->

---

## 一、传统 PostgreSQL 为什么很难真正 Serverless

传统 PostgreSQL 的心智模型非常直接。

一台机器上跑着 Postgres 进程，内存里有 shared buffers，下面挂着自己的数据目录。表和索引最终都落成数据页，修改先生成 WAL，再由数据库负责把状态安全写到存储设备。

问题也恰恰来自这里。

**计算和持久化状态属于同一个实例。**

当你想把一台机器换掉、扩容或者缩容时，不能只关心 CPU 和内存，还必须确保它的数据目录、WAL、恢复过程和副本关系都能继续工作。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 480" role="img" aria-label="传统 PostgreSQL 与 Neon 存算分离架构对比" style="width:100%;height:auto;display:block">
<defs>
  <marker id="n1-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .n1-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .n1-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .n1-box{fill:#fff;stroke:#374151;stroke-width:2}
    .n1-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .n1-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .n1-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .n1-line{stroke:#475569;stroke-width:2;fill:none}
    .n1-dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs>
<rect width="1200" height="480" fill="#fff"/>
<text x="300" y="45" text-anchor="middle" class="n1-t" font-size="25" font-weight="700"><tspan x="300" dy="0">传统 PostgreSQL</tspan></text><rect x="75" y="95" width="450" height="110" rx="12" class="n1-warn"/><text x="300" y="142.96499999999997" text-anchor="middle" class="n1-t" font-size="21" font-weight="500"><tspan x="300" dy="0">Postgres</tspan><tspan x="300" dy="28.35">CPU + RAM + 本地数据目录</tspan></text><rect x="135" y="270" width="330" height="85" rx="12" class="n1-box"/><text x="300" y="305.8" text-anchor="middle" class="n1-t" font-size="20" font-weight="500"><tspan x="300" dy="0">本地 / 块存储</tspan><tspan x="300" dy="27">数据页 + WAL</tspan></text><path d="M300 205 L300 255" class="n1-line" marker-end="url(#n1-arrow)"/><text x="300" y="420" text-anchor="middle" class="n1-t" font-size="20" font-weight="500"><tspan x="300" dy="0">计算和持久化状态绑在一起</tspan><tspan x="300" dy="30">换机器、扩缩容、复制都要搬数据</tspan></text><text x="900" y="45" text-anchor="middle" class="n1-t" font-size="25" font-weight="700"><tspan x="900" dy="0">Neon</tspan></text><rect x="700" y="90" width="400" height="95" rx="12" class="n1-ok"/><text x="900" y="130.465" text-anchor="middle" class="n1-t" font-size="21" font-weight="500"><tspan x="900" dy="0">Compute</tspan><tspan x="900" dy="28.35">Postgres 执行 SQL</tspan></text><path d="M900 185 L900 245" class="n1-line" marker-end="url(#n1-arrow)"/><rect x="675" y="260" width="450" height="100" rx="12" class="n1-soft"/><text x="900" y="303.3" text-anchor="middle" class="n1-t" font-size="20" font-weight="500"><tspan x="900" dy="0">Distributed Storage</tspan><tspan x="900" dy="27">WAL + Pages + Object Storage</tspan></text><text x="900" y="420" text-anchor="middle" class="n1-t" font-size="20" font-weight="500"><tspan x="900" dy="0">计算可以启动、停止、迁移和扩缩</tspan><tspan x="900" dy="30">持久化状态独立存在</tspan></text>
</svg>
</figure>

这种结构并不是不好。对于单机数据库，它简单而且成熟。

但到了云里，一旦工作负载具有明显波峰波谷，问题就出现了。

为了高峰期的 8 核 CPU，你可能要让一台 8 核机器全天运行；为了 2 TB 数据，又不能因为半夜没有 SQL 就把整台数据库“删掉重建”。

所以 Neon 做的第一件事情，不是发明另一套 SQL，而是把 PostgreSQL 的执行引擎和长期存储拆开。

---

## 二、Neon 的核心架构，其实是把 WAL 和 Page 分别处理

在 Neon 里，Compute 仍然运行 PostgreSQL，负责解析 SQL、执行计划、事务、索引访问和 buffer cache。

不同的是，它不再把本地数据目录当作长期事实源。

持久化状态被放进独立的分布式存储层，并进一步拆成三类角色。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 770" role="img" aria-label="Neon 的 Compute、Safekeeper、Pageserver 与对象存储" style="width:100%;height:auto;display:block">
<defs>
  <marker id="n2-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .n2-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .n2-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .n2-box{fill:#fff;stroke:#374151;stroke-width:2}
    .n2-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .n2-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .n2-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .n2-line{stroke:#475569;stroke-width:2;fill:none}
    .n2-dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs>
<rect width="1200" height="770" fill="#fff"/>
<rect x="455" y="45" width="290" height="90" rx="12" class="n2-ok"/><text x="600" y="82.63000000000001" text-anchor="middle" class="n2-t" font-size="22" font-weight="500"><tspan x="600" dy="0">Compute</tspan><tspan x="600" dy="29.700000000000003">Postgres</tspan></text><text x="600" y="172" text-anchor="middle" class="n2-t" font-size="18" font-weight="650"><tspan x="600" dy="0">写 WAL</tspan></text><path d="M540 135 L300 240" class="n2-line" marker-end="url(#n2-arrow)"/><rect x="90" y="250" width="420" height="115" rx="12" class="n2-warn"/><text x="300" y="300.8" text-anchor="middle" class="n2-t" font-size="20" font-weight="500"><tspan x="300" dy="0">Safekeepers</tspan><tspan x="300" dy="27">复制 WAL · 共识 · 提交持久化</tspan></text><text x="860" y="172" text-anchor="middle" class="n2-t" font-size="18" font-weight="650"><tspan x="860" dy="0">读 Page</tspan></text><path d="M660 135 L900 240" class="n2-line" marker-end="url(#n2-arrow)"/><rect x="690" y="250" width="420" height="115" rx="12" class="n2-soft"/><text x="900" y="300.8" text-anchor="middle" class="n2-t" font-size="20" font-weight="500"><tspan x="900" dy="0">Pageserver</tspan><tspan x="900" dy="27">按 LSN 重建并服务数据页</tspan></text><path d="M300 365 L500 470" class="n2-line" marker-end="url(#n2-arrow)"/><path d="M900 365 L700 470" class="n2-line" marker-end="url(#n2-arrow)"/><rect x="390" y="485" width="420" height="105" rx="12" class="n2-box"/><text x="600" y="530.8" text-anchor="middle" class="n2-t" font-size="20" font-weight="500"><tspan x="600" dy="0">Object Storage</tspan><tspan x="600" dy="27">长期保存不可变历史</tspan></text><text x="600" y="665" text-anchor="middle" class="n2-t" font-size="21" font-weight="600"><tspan x="600" dy="0">Safekeeper 解决“新写入先安全落下”</tspan><tspan x="600" dy="32">Pageserver 解决“查询需要哪个版本的数据页”</tspan><tspan x="600" dy="32">Object Storage 负责长期、低成本持久化</tspan></text>
</svg>
</figure>

### Compute 负责执行

Compute 可以理解成“会执行 PostgreSQL 的计算节点”。

它有 CPU、内存、shared buffers，也可以有本地缓存，但这些都不是数据库长期正确性的最终依赖。

因此 Compute 挂掉以后，真正需要恢复的是一个新的 PostgreSQL 执行环境，而不是先把整个数据库文件系统搬到新机器。

### Safekeeper 负责先把 WAL 保存安全

PostgreSQL 的修改都会产生 WAL。

Neon 不让事务依赖 Compute 本机把 WAL 刷进自己的长期磁盘，而是把 WAL 发送给多个 Safekeeper。Safekeeper 之间通过共识机制确认 WAL 已经达到足够的持久化副本。

对一次事务来说，这一步非常关键。

**客户端收到 COMMIT 成功时，核心条件是 WAL 已经被可靠保存，而不是所有数据页都已经写进对象存储。**

### Pageserver 负责把 WAL 重新变成 Page

查询最终读的不是 WAL，而是 PostgreSQL 的数据页。

Pageserver 的职责，就是根据已有页面版本和之后的 WAL 变化，构造 Compute 此刻需要的数据页。

它既服务当前状态，也能服务历史状态。

### Object Storage 负责长期保存历史

对象存储承担长期、可扩展的持久化职责。

Pageserver 可以把热数据留在更快的本地 SSD / NVMe 上，而较冷的不可变层进入对象存储。

所以从角色上看，可以粗略记成一句话。

**Safekeeper 守住最新 WAL，Pageserver 组织 Page，Object Storage 保存长期历史。**

---

## 三、一次写入真正发生了什么

理解 Neon 最重要的一条链路，是写路径。

传统直觉会认为，数据库提交时必须把脏页安全写进磁盘。

Neon 把“事务是否可以提交”和“数据页什么时候物化完成”拆开了。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 725" role="img" aria-label="Neon 写路径：WAL 先持久化，页面物化在事务之后" style="width:100%;height:auto;display:block">
<defs>
  <marker id="n3-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .n3-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .n3-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .n3-box{fill:#fff;stroke:#374151;stroke-width:2}
    .n3-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .n3-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .n3-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .n3-line{stroke:#475569;stroke-width:2;fill:none}
    .n3-dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs>
<rect width="1200" height="725" fill="#fff"/>
<rect x="60" y="35" width="110" height="80" rx="12" class="n3-soft"/><text x="115" y="83.16" text-anchor="middle" class="n3-t" font-size="24" font-weight="700"><tspan x="115" dy="0">1</tspan></text><rect x="205" y="35" width="360" height="80" rx="12" class="n3-box"/><text x="385" y="81.8" text-anchor="middle" class="n3-t" font-size="20" font-weight="500"><tspan x="385" dy="0">Postgres 执行事务</tspan></text><rect x="600" y="35" width="530" height="80" rx="12" class="n3-box"/><text x="865" y="81.12" text-anchor="middle" class="n3-t" font-size="18" font-weight="500"><tspan x="865" dy="0">更新内存页并生成 WAL</tspan></text><path d="M115 115 L115 145" class="n3-line" marker-end="url(#n3-arrow)"/><rect x="60" y="155" width="110" height="80" rx="12" class="n3-soft"/><text x="115" y="203.16" text-anchor="middle" class="n3-t" font-size="24" font-weight="700"><tspan x="115" dy="0">2</tspan></text><rect x="205" y="155" width="360" height="80" rx="12" class="n3-box"/><text x="385" y="201.8" text-anchor="middle" class="n3-t" font-size="20" font-weight="500"><tspan x="385" dy="0">WAL 发往 Safekeepers</tspan></text><rect x="600" y="155" width="530" height="80" rx="12" class="n3-box"/><text x="865" y="201.12" text-anchor="middle" class="n3-t" font-size="18" font-weight="500"><tspan x="865" dy="0">不依赖本机磁盘持久化</tspan></text><path d="M115 235 L115 265" class="n3-line" marker-end="url(#n3-arrow)"/><rect x="60" y="275" width="110" height="80" rx="12" class="n3-ok"/><text x="115" y="323.16" text-anchor="middle" class="n3-t" font-size="24" font-weight="700"><tspan x="115" dy="0">3</tspan></text><rect x="205" y="275" width="360" height="80" rx="12" class="n3-box"/><text x="385" y="321.8" text-anchor="middle" class="n3-t" font-size="20" font-weight="500"><tspan x="385" dy="0">多数 Safekeeper 确认</tspan></text><rect x="600" y="275" width="530" height="80" rx="12" class="n3-box"/><text x="865" y="321.12" text-anchor="middle" class="n3-t" font-size="18" font-weight="500"><tspan x="865" dy="0">事务可以向客户端返回成功</tspan></text><path d="M115 355 L115 385" class="n3-line" marker-end="url(#n3-arrow)"/><rect x="60" y="395" width="110" height="80" rx="12" class="n3-soft"/><text x="115" y="443.16" text-anchor="middle" class="n3-t" font-size="24" font-weight="700"><tspan x="115" dy="0">4</tspan></text><rect x="205" y="395" width="360" height="80" rx="12" class="n3-box"/><text x="385" y="441.8" text-anchor="middle" class="n3-t" font-size="20" font-weight="500"><tspan x="385" dy="0">Pageserver 异步消费 WAL</tspan></text><rect x="600" y="395" width="530" height="80" rx="12" class="n3-box"/><text x="865" y="441.12" text-anchor="middle" class="n3-t" font-size="18" font-weight="500"><tspan x="865" dy="0">生成 / 压缩页面版本</tspan></text><path d="M115 475 L115 505" class="n3-line" marker-end="url(#n3-arrow)"/><rect x="60" y="515" width="110" height="80" rx="12" class="n3-soft"/><text x="115" y="563.16" text-anchor="middle" class="n3-t" font-size="24" font-weight="700"><tspan x="115" dy="0">5</tspan></text><rect x="205" y="515" width="360" height="80" rx="12" class="n3-box"/><text x="385" y="561.8" text-anchor="middle" class="n3-t" font-size="20" font-weight="500"><tspan x="385" dy="0">历史层写入对象存储</tspan></text><rect x="600" y="515" width="530" height="80" rx="12" class="n3-box"/><text x="865" y="561.12" text-anchor="middle" class="n3-t" font-size="18" font-weight="500"><tspan x="865" dy="0">脱离事务关键路径</tspan></text><text x="600" y="665" text-anchor="middle" class="n3-t" font-size="22" font-weight="650"><tspan x="600" dy="0">Commit 等的是 WAL 达到持久化共识，不需要等待整个数据页上传到对象存储。</tspan></text>
</svg>
</figure>

一次事务大致可以这样理解。

Postgres 首先照常执行更新，在内存页上修改状态并生成 WAL。

这些 WAL 不以 Compute 本机磁盘作为最终持久化目标，而是流向多个 Safekeeper。

当足够多的 Safekeeper 确认 WAL 已安全保存以后，事务就具备了向客户端返回成功的条件。

Pageserver 随后再消费这些 WAL，生成和整理数据页版本，并把长期层异步持久化到对象存储。

这带来一个很重要的架构结论。

**事务关键路径不必等待整张数据页进入长期对象存储。**

WAL 负责描述“发生了什么变化”，Pageserver 再把这些变化转换成“某个 LSN 时刻的数据页长什么样”。

---

## 四、读的时候，Neon 实际上在做 GetPage@LSN

Neon 存储引擎里一个非常核心的概念，是 LSN，也就是 PostgreSQL WAL 的 Log Sequence Number。

可以把它理解成 WAL 时间轴上的位置。

当 Compute 需要读取一个 Page 时，Pageserver 不只知道“这个页现在是什么样”，还可以回答一个更强的问题。

**这个 Page 在某个 LSN 时是什么样？**

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1240 610" role="img" aria-label="Pageserver 通过 Image Layer 和 Delta Layer 重建指定 LSN 的数据页" style="width:100%;height:auto;display:block">
<defs>
  <marker id="n4-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .n4-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .n4-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .n4-box{fill:#fff;stroke:#374151;stroke-width:2}
    .n4-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .n4-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .n4-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .n4-line{stroke:#475569;stroke-width:2;fill:none}
    .n4-dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs>
<rect width="1240" height="610" fill="#fff"/>
<rect x="70" y="95" width="240" height="90" rx="12" class="n4-ok"/><text x="190" y="133.3" text-anchor="middle" class="n4-t" font-size="20" font-weight="500"><tspan x="190" dy="0">Compute</tspan><tspan x="190" dy="27">需要 Page P</tspan></text><path d="M310 140 L430 140" class="n4-line" marker-end="url(#n4-arrow)"/><rect x="445" y="80" width="310" height="120" rx="12" class="n4-soft"/><text x="600" y="132.965" text-anchor="middle" class="n4-t" font-size="21" font-weight="500"><tspan x="600" dy="0">Pageserver</tspan><tspan x="600" dy="28.35">GetPage(P, LSN)</tspan></text><path d="M755 115 L870 65" class="n4-line" marker-end="url(#n4-arrow)"/><rect x="885" y="35" width="250" height="90" rx="12" class="n4-box"/><text x="1010" y="73.63499999999999" text-anchor="middle" class="n4-t" font-size="19" font-weight="500"><tspan x="1010" dy="0">Image Layer</tspan><tspan x="1010" dy="25.650000000000002">某个历史完整页</tspan></text><path d="M755 165 L870 235" class="n4-line" marker-end="url(#n4-arrow)"/><rect x="885" y="205" width="250" height="90" rx="12" class="n4-box"/><text x="1010" y="243.63500000000002" text-anchor="middle" class="n4-t" font-size="19" font-weight="500"><tspan x="1010" dy="0">Delta Layer</tspan><tspan x="1010" dy="25.650000000000002">之后的 WAL 变化</tspan></text><path d="M1010 125 L1010 185" class="n4-line"/><path d="M1010 295 L1010 350" class="n4-line" marker-end="url(#n4-arrow)"/><rect x="825" y="365" width="370" height="95" rx="12" class="n4-ok"/><text x="1010" y="405.8" text-anchor="middle" class="n4-t" font-size="20" font-weight="500"><tspan x="1010" dy="0">重放到目标 LSN</tspan><tspan x="1010" dy="27">得到该时刻的数据页</tspan></text><path d="M825 412 L600 412" class="n4-line" marker-end="url(#n4-arrow)"/><rect x="335" y="365" width="250" height="95" rx="12" class="n4-box"/><text x="460" y="406.135" text-anchor="middle" class="n4-t" font-size="19" font-weight="500"><tspan x="460" dy="0">返回给 Compute</tspan><tspan x="460" dy="25.650000000000002">并进入缓存</tspan></text><text x="600" y="545" text-anchor="middle" class="n4-t" font-size="22" font-weight="650"><tspan x="600" dy="0">同一页可以被重建成不同历史版本，这正是 PITR 和 Branching 的底层基础。</tspan></text>
</svg>
</figure>

Pageserver 会寻找目标 LSN 之前合适的 Image Layer，也就是某个完整页面版本，再把之后的 Delta Layer 逐步应用到目标位置。

最后得到的，就是该页面在指定 LSN 的状态。

这个设计和传统“原地覆盖旧页”的文件系统模型有很大不同。

Neon 的存储天然保留了版本历史，因此“过去的数据库状态”不再只是某份离线备份，而是存储系统能够直接寻址的一部分。

这也是后面 Branching 和 PITR 能做到非常轻量的原因。

---

## 五、Database Branching 为什么不等于复制一个数据库

数据库分支是 Neon 最容易让人觉得“像 Git”的能力。

但它真正厉害的地方，并不是起了一个叫 branch 的名字，而是底层不需要在创建分支时复制整份数据库。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 690" role="img" aria-label="Neon Branching 使用 copy-on-write，共享历史数据只保存差异" style="width:100%;height:auto;display:block">
<defs>
  <marker id="n5-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .n5-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .n5-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .n5-box{fill:#fff;stroke:#374151;stroke-width:2}
    .n5-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .n5-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .n5-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .n5-line{stroke:#475569;stroke-width:2;fill:none}
    .n5-dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs>
<rect width="1200" height="690" fill="#fff"/>
<rect x="430" y="35" width="340" height="85" rx="12" class="n5-ok"/><text x="600" y="70.8" text-anchor="middle" class="n5-t" font-size="20" font-weight="500"><tspan x="600" dy="0">main</tspan><tspan x="600" dy="27">共享历史页 A / B / C</tspan></text><path d="M600 120 L600 180" class="n5-line"/><path d="M270 180 L930 180" class="n5-line"/><path d="M270 180 L270 240" class="n5-line" marker-end="url(#n5-arrow)"/><path d="M930 180 L930 240" class="n5-line" marker-end="url(#n5-arrow)"/><rect x="90" y="255" width="360" height="100" rx="12" class="n5-soft"/><text x="270" y="298.635" text-anchor="middle" class="n5-t" font-size="19" font-weight="500"><tspan x="270" dy="0">branch/pr-123</tspan><tspan x="270" dy="25.650000000000002">起点仍引用 A / B / C</tspan></text><rect x="750" y="255" width="360" height="100" rx="12" class="n5-box"/><text x="930" y="298.635" text-anchor="middle" class="n5-t" font-size="19" font-weight="500"><tspan x="930" dy="0">main</tspan><tspan x="930" dy="25.650000000000002">继续引用 A / B / C</tspan></text><path d="M270 355 L270 420" class="n5-line" marker-end="url(#n5-arrow)"/><rect x="90" y="435" width="360" height="110" rx="12" class="n5-warn"/><text x="270" y="483.3" text-anchor="middle" class="n5-t" font-size="20" font-weight="500"><tspan x="270" dy="0">branch 写入</tspan><tspan x="270" dy="27">只新增 B' / D</tspan></text><text x="600" y="615" text-anchor="middle" class="n5-t" font-size="22" font-weight="650"><tspan x="600" dy="0">创建分支时不复制整库</tspan><tspan x="600" dy="32">父子分支共享旧页面，发生写入后才各自保存差异</tspan></text>
</svg>
</figure>

假设生产分支已经有 500 GB 数据。

传统做法要给一个 PR 准备完整测试库，通常意味着快照、恢复或者复制，数据库越大，环境准备越重。

Neon 的分支更接近 copy-on-write。

新分支创建时，先共享父分支已有的历史页面；只有分支发生新的写入时，才保存自己新增的差异。

因此一个新 branch 的初始成本不与“整库有多大”线性绑定。

这会直接改变数据库环境的使用方式。

数据库不再只能分成 production、staging、dev 三套长期环境，而可以变成一种短生命周期资源。

一个 PR 一个 branch，一个测试任务一个 branch，甚至一个 Agent 一次执行一个 branch，都开始变得合理。

---

## 六、Scale to Zero 为什么是存算分离的结果，而不是魔法

如果数据库的数据真的属于 Compute 本机，那么把 Compute 缩到 0 会非常危险。

因为机器一旦消失，数据库也一起消失。

Neon 之所以能 scale-to-zero，是因为真正的持久化状态一直在独立存储层里。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 500" role="img" aria-label="Neon Scale to Zero：暂停的是计算，不是数据库历史" style="width:100%;height:auto;display:block">
<defs>
  <marker id="n6-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .n6-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .n6-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .n6-box{fill:#fff;stroke:#374151;stroke-width:2}
    .n6-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .n6-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .n6-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .n6-line{stroke:#475569;stroke-width:2;fill:none}
    .n6-dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs>
<rect width="1200" height="500" fill="#fff"/>
<rect x="65" y="90" width="250" height="90" rx="12" class="n6-ok"/><text x="190" y="128.3" text-anchor="middle" class="n6-t" font-size="20" font-weight="500"><tspan x="190" dy="0">有请求</tspan><tspan x="190" dy="27">Compute 运行</tspan></text><path d="M315 135 L450 135" class="n6-line" marker-end="url(#n6-arrow)"/><rect x="465" y="90" width="270" height="90" rx="12" class="n6-soft"/><text x="600" y="128.635" text-anchor="middle" class="n6-t" font-size="19" font-weight="500"><tspan x="600" dy="0">空闲一段时间</tspan><tspan x="600" dy="25.650000000000002">Activity Monitor</tspan></text><path d="M735 135 L870 135" class="n6-line" marker-end="url(#n6-arrow)"/><rect x="885" y="90" width="250" height="90" rx="12" class="n6-warn"/><text x="1010" y="128.3" text-anchor="middle" class="n6-t" font-size="20" font-weight="500"><tspan x="1010" dy="0">Suspend</tspan><tspan x="1010" dy="27">Compute → 0</tspan></text><path d="M1010 180 L1010 290" class="n6-line"/><path d="M1010 290 L765 290" class="n6-line" marker-end="url(#n6-arrow)"/><rect x="485" y="245" width="265" height="90" rx="12" class="n6-soft"/><text x="617.5" y="283.635" text-anchor="middle" class="n6-t" font-size="19" font-weight="500"><tspan x="617.5" dy="0">新连接到来</tspan><tspan x="617.5" dy="25.650000000000002">重新启动 Compute</tspan></text><path d="M485 290 L350 290" class="n6-line" marker-end="url(#n6-arrow)"/><rect x="70" y="245" width="265" height="90" rx="12" class="n6-box"/><text x="202.5" y="283.635" text-anchor="middle" class="n6-t" font-size="19" font-weight="500"><tspan x="202.5" dy="0">重新连接存储</tspan><tspan x="202.5" dy="25.650000000000002">数据无需恢复搬迁</tspan></text><text x="600" y="430" text-anchor="middle" class="n6-t" font-size="22" font-weight="650"><tspan x="600" dy="0">能 scale-to-zero 的前提不是“数据库没了”</tspan><tspan x="600" dy="32">而是 Compute 可以消失，持久化状态仍然留在独立存储层</tspan></text>
</svg>
</figure>

当一个 Compute 长时间没有活动时，控制面可以把它挂起。

这时消失的是 CPU 和内存资源，不是数据库历史。

下一次连接到来时，再启动新的 Compute，把它重新接到原来的存储状态上。

所以更准确的说法不是“Neon 把数据库暂停了”，而是**Neon 把执行数据库查询的 Compute 暂停了。**

这对于开发、测试、预览环境尤其有价值，因为这些数据库真正执行 SQL 的时间通常很短，大部分时候只是“存在但没人访问”。

对持续有流量的生产系统则不同。官方当前也更建议让生产 Compute 保持在线，通过 autoscaling 在一个最小值和最大值之间动态调整，而不是频繁 scale-to-zero。

---

## 七、PITR 为什么能很快

传统数据库做 Point-in-Time Recovery，常见思路是找到一个基础备份，再重放后续 WAL 到目标时间点。

这个过程天然带有“先恢复出一份数据库”的味道。

Neon 的存储模型不一样。

因为 Pageserver 本来就在保存可按 LSN 重建的数据页历史，所以恢复和时间旅行更接近“选择时间轴上的另一个位置”。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 590" role="img" aria-label="Neon 通过 WAL 历史和 LSN 支持时间旅行与 PITR" style="width:100%;height:auto;display:block">
<defs>
  <marker id="n7-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .n7-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .n7-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .n7-box{fill:#fff;stroke:#374151;stroke-width:2}
    .n7-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .n7-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .n7-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .n7-line{stroke:#475569;stroke-width:2;fill:none}
    .n7-dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs>
<rect width="1200" height="590" fill="#fff"/>
<path d="M100 210 L1110 210" class="n7-line"/><circle cx="170" cy="210" r="9" fill="#374151"/><text x="170" y="255" text-anchor="middle" class="n7-m" font-size="17" font-weight="500"><tspan x="170" dy="0">LSN 100</tspan></text><circle cx="390" cy="210" r="9" fill="#374151"/><text x="390" y="255" text-anchor="middle" class="n7-m" font-size="17" font-weight="500"><tspan x="390" dy="0">LSN 120</tspan></text><circle cx="610" cy="210" r="9" fill="#374151"/><text x="610" y="255" text-anchor="middle" class="n7-m" font-size="17" font-weight="500"><tspan x="610" dy="0">LSN 140</tspan></text><circle cx="830" cy="210" r="9" fill="#374151"/><text x="830" y="255" text-anchor="middle" class="n7-m" font-size="17" font-weight="500"><tspan x="830" dy="0">LSN 160</tspan></text><circle cx="1050" cy="210" r="9" fill="#374151"/><text x="1050" y="255" text-anchor="middle" class="n7-m" font-size="17" font-weight="500"><tspan x="1050" dy="0">LSN 180</tspan></text><text x="600" y="65" text-anchor="middle" class="n7-t" font-size="23" font-weight="650"><tspan x="600" dy="0">不可覆盖的历史 + WAL 位置，让数据库状态成为一条可寻址的时间轴。</tspan></text><path d="M610 200 L610 115" class="n7-line" marker-end="url(#n7-arrow)"/><rect x="455" y="75" width="310" height="90" rx="12" class="n7-soft"/><text x="610" y="113.63499999999999" text-anchor="middle" class="n7-t" font-size="19" font-weight="500"><tspan x="610" dy="0">选择某个时间点</tspan><tspan x="610" dy="25.650000000000002">timestamp / LSN</tspan></text><path d="M610 265 L610 335" class="n7-line" marker-end="url(#n7-arrow)"/><rect x="390" y="350" width="440" height="100" rx="12" class="n7-ok"/><text x="610" y="393.3" text-anchor="middle" class="n7-t" font-size="20" font-weight="500"><tspan x="610" dy="0">创建历史分支 / Restore</tspan><tspan x="610" dy="27">从该点继续独立演化</tspan></text><text x="600" y="525" text-anchor="middle" class="n7-t" font-size="21" font-weight="650"><tspan x="600" dy="0">PITR 不必先把整份备份复制出来，而是重新指向已有历史并启动新的 Compute。</tspan></text>
</svg>
</figure>

如果误删了一张表，不一定要立刻把整个生产库原地回滚。

可以先从删除操作之前的时间点创建一个新 branch，查询旧状态，验证目标数据，甚至把需要的数据抽出来。

这种能力在排障里非常实用。

生产数据的历史版本，不再只是灾难发生以后才会使用的冷备份，而可以成为日常调试和验证工具。

---

## 八、Read Replica 也因为共享存储发生了变化

传统 PostgreSQL 的 read replica 通常需要另一份完整的数据副本，并持续接收 WAL。

Neon 把持久化数据放在共享的存储系统之后，read replica 的结构也不同。

多个 Compute 可以面向同一份底层存储工作，而不是每增加一个读节点，就先完整复制一份数据库数据。

这会让“计算扩展”和“数据复制”解耦。

当然，读副本仍然需要自己的 CPU、内存和缓存，也仍然存在 WAL 应用和可见性延迟。

但系统不用再把“多一个读 Compute”和“多存一整份数据库”绑定成一件事。

---

## 九、Neon 最适合哪些场景

看完架构以后，再看使用场景会更容易理解。

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 620" role="img" aria-label="Neon 的典型使用场景" style="width:100%;height:auto;display:block">
<defs>
  <marker id="n8-arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .n8-t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}
    .n8-m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .n8-box{fill:#fff;stroke:#374151;stroke-width:2}
    .n8-soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .n8-ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .n8-warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}
    .n8-line{stroke:#475569;stroke-width:2;fill:none}
    .n8-dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs>
<rect width="1200" height="620" fill="#fff"/>
<rect x="55" y="45" width="520" height="120" rx="12" class="n8-box"/><text x="315" y="86.82000000000001" text-anchor="middle" class="n8-t" font-size="18" font-weight="500"><tspan x="315" dy="0">PR / Preview 环境</tspan><tspan x="315" dy="24.3">每个 PR 一条数据库分支</tspan><tspan x="315" dy="24.3">用完删除，Compute 可休眠</tspan></text><rect x="630" y="45" width="520" height="120" rx="12" class="n8-box"/><text x="890" y="86.82000000000001" text-anchor="middle" class="n8-t" font-size="18" font-weight="500"><tspan x="890" dy="0">开发 / 测试 / Staging</tspan><tspan x="890" dy="24.3">生产样本快速分支</tspan><tspan x="890" dy="24.3">隔离迁移和破坏性实验</tspan></text><rect x="55" y="200" width="520" height="120" rx="12" class="n8-box"/><text x="315" y="241.82" text-anchor="middle" class="n8-t" font-size="18" font-weight="500"><tspan x="315" dy="0">Agent / 平台型产品</tspan><tspan x="315" dy="24.3">批量创建短生命周期数据库</tspan><tspan x="315" dy="24.3">适合大量间歇性工作负载</tspan></text><rect x="630" y="200" width="520" height="120" rx="12" class="n8-box"/><text x="890" y="241.82" text-anchor="middle" class="n8-t" font-size="18" font-weight="500"><tspan x="890" dy="0">突发型业务</tspan><tspan x="890" dy="24.3">Compute 在区间内自动扩缩</tspan><tspan x="890" dy="24.3">避免长期按峰值配机器</tspan></text><rect x="55" y="355" width="520" height="120" rx="12" class="n8-soft"/><text x="315" y="396.82" text-anchor="middle" class="n8-t" font-size="18" font-weight="500"><tspan x="315" dy="0">恢复 / 排障</tspan><tspan x="315" dy="24.3">从过去时间点创建分支</tspan><tspan x="315" dy="24.3">不直接回滚生产再调查</tspan></text><rect x="630" y="355" width="520" height="120" rx="12" class="n8-soft"/><text x="890" y="396.82" text-anchor="middle" class="n8-t" font-size="18" font-weight="500"><tspan x="890" dy="0">读扩展</tspan><tspan x="890" dy="24.3">多个 Compute 共享存储</tspan><tspan x="890" dy="24.3">无需复制完整数据集</tspan></text><text x="600" y="555" text-anchor="middle" class="n8-t" font-size="22" font-weight="650"><tspan x="600" dy="0">真正适合 Neon 的工作负载，通常都有一个共同点：计算生命周期和数据生命周期并不相同。</tspan></text>
</svg>
</figure>

### 1. 每个 PR 一个数据库

这是 Branching 最直观的场景。

代码创建 PR 时自动创建数据库 branch，运行迁移、集成测试和 preview；PR 合并以后删除分支。

相比所有开发者共用一个 staging DB，这种模式的隔离性更好，也不容易出现“你的迁移把我的测试数据改坏了”。

### 2. 开发、测试和 Staging

非生产数据库通常最大的浪费不是存储，而是长期闲置的 Compute。

这些环境很适合 autoscaling 和 scale-to-zero。

需要时启动，不需要时把计算缩掉，而数据状态仍然保留。

### 3. AI Agent 和短生命周期任务

Agent 的一个典型特点，是会大量创建临时环境。

一个 Agent 可能为了完成一次代码修改，启动应用、数据库、测试数据和 preview，然后很快销毁。

传统“先申请一台长期数据库实例”的资源模型很难匹配这种生命周期。

Branching + scale-to-zero 刚好对应“环境很多，但绝大多数时间并不活跃”的模式。

### 4. SaaS 和平台型产品

如果你的产品本身需要给大量客户动态创建 PostgreSQL 环境，存算分离也会变得很有吸引力。

数据库可以通过 API 创建，Compute 可以根据不同租户的活动状态单独调整，而不是提前为所有客户保留长期运行的实例。

### 5. 流量波动明显的业务

生产流量如果有明显波峰波谷，但全天都持续有请求，可以保留最小 Compute，再让 Neon 在设定的上下界之间 autoscale。

这里节省的不是“空闲时完全归零”，而是不用长期按照峰值配置 CPU 和内存。

### 6. 恢复、数据排障和迁移验证

需要调查某个历史状态时，可以从过去时间点创建 branch。

需要验证一次危险 schema migration 时，也可以先在生产数据的隔离分支上运行。

相比直接操作生产库，这是一种更安全的工作方式。

---

## 十、哪些场景不要因为“Serverless”三个字就盲目套

Neon 并不意味着所有 PostgreSQL 工作负载都应该开启 scale-to-zero。

如果生产业务 24 小时持续有稳定流量，频繁休眠本身就没有价值。更合理的是保持 Compute 在线，用 autoscaling 解决峰值浪费。

如果业务对第一次连接的尾延迟极其敏感，也应该谨慎配置自动挂起。scale-to-zero 一定意味着“下次使用时要重新唤醒 Compute”，这和一台永远热着的数据库不是同一个延迟模型。

另外，存算分离也不是“网络没有成本”。

Compute 访问不在本地 buffer cache 中的数据页时，需要从存储层获取，因此缓存命中率、工作集大小和网络存储路径仍然会影响性能。

所以 Neon 的价值并不是“所有指标都比本地盘 PostgreSQL 更快”。

它真正改变的是**数据库的资源生命周期，不必再和一台机器的生命周期绑定。**

---

## 十一、理解 Neon，可以记住四句话

第一，**Postgres Compute 负责算，不负责成为长期数据事实源。**

第二，**Safekeeper 用 WAL 共识守住最新写入，Pageserver 根据 WAL 和历史层构造 Page。**

第三，**因为历史不是原地覆盖，所以同一份存储可以自然支持 Branching 和 PITR。**

第四，**因为数据不属于 Compute，所以 Compute 才能 autoscale、scale-to-zero、快速替换和独立扩展。**

如果只把 Neon 看成“一个便宜的云 PostgreSQL”，很多设计会显得只是产品功能。

但把这些能力放回存算分离、WAL、Page 和非覆盖存储这条主线上，会发现它们其实都是同一个架构选择的不同结果。

Neon 真正重新定义的不是 PostgreSQL 的 SQL 能力。

它重新定义的是：**一份 PostgreSQL 数据，为什么一定要永远属于某一台正在运行的数据库机器。**
