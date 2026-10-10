---
layout: post
title: "Neon 不只是 Serverless PostgreSQL：存算分离如何带来弹性、分支与时间旅行"
description: "从 Compute、Safekeeper、Pageserver 和对象存储出发，理解 Neon 为什么能让 PostgreSQL 的计算层可启停、可扩缩，同时原生支持数据库分支、PITR 和按需计算。"
date: 2026-07-24
tags: [PostgreSQL, Neon, 数据库, Serverless]
---

第一次接触 Neon，很容易把它当成“一个会自动休眠的 PostgreSQL”。scale-to-zero 确实是最直观的体验，但它只是结果。Neon 实际做的，是把传统 PostgreSQL 里绑在一起的计算和持久化拆开：Postgres 只负责执行 SQL，数据和历史交给独立的存储层。

<!--more-->

持久化数据不再属于某一台 Postgres 机器以后，计算节点就可以随时启动、停止、换机器和扩缩容，而数据库历史仍然独立存在。autoscaling、scale-to-zero、database branching、read replica 和 point-in-time restore，都是从这一步架构变化推出来的。

## 传统 PostgreSQL 为什么难做成 Serverless

传统 PostgreSQL 的模型很直接：一台机器上跑着 Postgres 进程，内存里有 shared buffers，下面挂着自己的数据目录。表和索引落成数据页，修改先生成 WAL，再由数据库把状态安全写到存储设备。计算和持久化状态属于同一个实例，所以换机器、扩容或缩容时，不能只关心 CPU 和内存，还得保证数据目录、WAL、恢复过程和副本关系都能接着工作。

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

对单机数据库来说，这种结构简单、成熟。麻烦出在云上波峰波谷明显的负载：为了高峰期的 8 核 CPU，你可能要让一台 8 核机器全天运行；为了 2 TB 数据，又不能因为半夜没有 SQL 就把整台数据库删掉重建。Neon 的做法是保留 PostgreSQL 的执行引擎，把长期存储拆出去。

## 架构：WAL 和 Page 分开处理

Neon 的 Compute 仍然运行 PostgreSQL，负责解析 SQL、执行计划、事务、索引访问和 buffer cache，只是不再把本地数据目录当作长期事实源。持久化状态放进独立的分布式存储层，由三类角色分担。

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

Compute 有 CPU、内存、shared buffers，也可以有本地缓存，但数据库的长期正确性不依赖这些。Compute 挂掉以后，要恢复的只是一个新的 PostgreSQL 执行环境，不需要先把整个数据库文件系统搬到新机器。

Safekeeper 负责 WAL 的持久化。事务不依赖 Compute 本机把 WAL 刷进自己的磁盘，而是把 WAL 发给多个 Safekeeper，由它们通过共识确认 WAL 已经有足够的持久化副本。所以**客户端收到 COMMIT 成功时，WAL 已经被可靠保存，但数据页不一定写进了对象存储。**

Pageserver 负责把 WAL 重新变成数据页。查询最终读的是 PostgreSQL 的数据页而不是 WAL，Pageserver 根据已有的页面版本加上之后的 WAL 变化，构造出 Compute 此刻需要的页，当前状态和历史状态都能服务。

对象存储负责长期、可扩展的持久化。Pageserver 可以把热数据留在更快的本地 SSD / NVMe 上，较冷的不可变层进入对象存储。

## 写路径：提交只等 WAL

传统直觉是数据库提交时要把脏页安全写进磁盘。Neon 把“事务能不能提交”和“数据页什么时候物化完成”拆成了两件事。

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

Postgres 照常执行更新，修改内存页并生成 WAL。WAL 不以 Compute 本机磁盘为最终持久化目标，而是流向多个 Safekeeper；足够多的 Safekeeper 确认之后，事务就可以向客户端返回成功。Pageserver 随后消费这些 WAL，生成和整理页面版本，再把长期层异步写到对象存储。WAL 记录的是发生了什么变化，Pageserver 负责把这些变化还原成某个 LSN 时刻的数据页，这一步不在事务的关键路径上。

## 读路径：GetPage@LSN

LSN 是 PostgreSQL WAL 的 Log Sequence Number，可以看作 WAL 时间轴上的位置，也是 Neon 存储引擎里很核心的概念。Compute 读一个 Page 时，Pageserver 不只能回答“这个页现在是什么样”，还能回答“这个页在某个 LSN 时是什么样”。

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

具体做法是先找到目标 LSN 之前合适的 Image Layer，也就是某个完整页面版本，再把之后的 Delta Layer 逐步应用到目标位置，得到该页在指定 LSN 的状态。

这和原地覆盖旧页的传统模型很不一样。Neon 的存储天然保留了版本历史，过去的数据库状态成了存储系统可以直接寻址的一部分，不用再去翻离线备份。后面的 Branching 和 PITR 之所以轻量，都建立在这一点上。

## Branching 为什么不用复制整库

数据库分支是 Neon 最像 Git 的能力，关键在于创建分支时底层不需要复制整份数据库。

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

假设生产分支已经有 500 GB 数据。传统做法给一个 PR 准备完整测试库，要做快照、恢复或复制，库越大越重。Neon 的分支接近 copy-on-write：新分支先共享父分支已有的历史页面，只有发生新的写入时才保存自己的差异，所以创建一个 branch 的初始成本和整库大小不是线性关系。

这样一来，数据库就不必只分成 production、staging、dev 三套长期环境，而可以当作短生命周期资源来用。一个 PR 一个 branch、一个测试任务一个 branch，甚至 Agent 每次执行一个 branch，都变得合理。

## Scale to Zero 是存算分离的直接结果

如果数据属于 Compute 本机，把 Compute 缩到 0 就意味着数据库跟着机器一起消失。Neon 能 scale-to-zero，是因为持久化状态一直在独立的存储层里。

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

Compute 长时间没有活动时，控制面会把它挂起，释放的是 CPU 和内存，数据库历史还在。下一个连接到来时，再启动新的 Compute，接回原来的存储状态。准确地说，Neon 暂停的是执行查询的 Compute，而不是数据库本身。

开发、测试、预览环境最受益，这些库真正执行 SQL 的时间很短，大部分时候只是存在、没人访问。持续有流量的生产系统则不同，官方当前也更建议让生产 Compute 保持在线，通过 autoscaling 在一个最小值和最大值之间动态调整，而不是频繁 scale-to-zero。

## PITR 和历史分支

传统数据库做 Point-in-Time Recovery，一般是找到基础备份，再重放后续 WAL 到目标时间点，相当于先恢复出一份数据库。Neon 的 Pageserver 本来就保存着可以按 LSN 重建的页面历史，恢复更像是在时间轴上选另一个位置。

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

比如误删了一张表，不一定要马上把整个生产库原地回滚，可以先从删除前的时间点创建一个 branch，查询旧状态、核对数据，再把需要的部分抽出来。生产数据的历史版本因此不只是出事后才动用的冷备份，日常排障和验证也能用上。

## Read Replica

传统 PostgreSQL 的 read replica 通常需要另一份完整的数据副本，并持续接收 WAL。在 Neon 里，多个 Compute 可以面向同一份底层存储工作，每增加一个读节点不必先完整复制一份数据，计算扩展和数据复制就此解耦。当然，读副本仍然需要自己的 CPU、内存和缓存，WAL 应用和可见性延迟也依然存在。

## 适合哪些场景

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

最直观的是每个 PR 一个数据库：创建 PR 时自动建 branch，跑迁移、集成测试和 preview，合并后删掉分支。比起所有开发者共用一个 staging DB，隔离性好得多，也不容易出现“你的迁移把我的测试数据改坏了”。

开发、测试和 staging 环境最大的浪费通常是长期闲置的 Compute，正适合 autoscaling 和 scale-to-zero，需要时启动，不需要时把计算缩掉，数据状态照样保留。

AI Agent 和短生命周期任务也类似。Agent 为了完成一次代码修改，可能启动应用、数据库、测试数据和 preview，然后很快销毁，长期数据库实例很难匹配这种生命周期，branching 加 scale-to-zero 正好对应“环境很多，但绝大多数时间不活跃”。需要给大量客户动态创建 PostgreSQL 环境的 SaaS 和平台型产品同理：数据库通过 API 创建，Compute 按各租户的活动状态单独调整，不用提前为所有客户保留长期运行的实例。

流量波峰波谷明显、但全天都持续有请求的生产业务，可以保留最小 Compute，让 Neon 在设定的上下界之间 autoscale。省下的是长期按峰值配置 CPU 和内存的那部分成本。

恢复、数据排障和迁移验证也是一类：调查某个历史状态时，从过去的时间点创建 branch；验证一次危险的 schema migration 时，先在生产数据的隔离分支上跑。这比直接操作生产库安全。

## 不要因为 Serverless 就盲目套用

24 小时都有稳定流量的生产业务，频繁休眠没有价值，保持 Compute 在线、用 autoscaling 解决峰值浪费更合理。对第一次连接的尾延迟极其敏感的业务，也要谨慎配置自动挂起：scale-to-zero 意味着下次使用时要重新唤醒 Compute，和一台一直热着的数据库不是同一个延迟模型。

存算分离也不是没有网络成本。Compute 访问不在本地 buffer cache 中的数据页时，要从存储层获取，缓存命中率、工作集大小和网络存储路径仍然会影响性能。Neon 并不是在所有指标上都比本地盘 PostgreSQL 快。

如果只把 Neon 看成一个便宜的云 PostgreSQL，autoscaling、scale-to-zero、Branching、PITR、read replica 会像一组互不相关的产品功能。我的理解是，把它们放回存算分离、WAL、Page 和非覆盖存储这条线上，都是同一个架构选择的结果：一份 PostgreSQL 数据，不必再永远属于某一台正在运行的数据库机器。
