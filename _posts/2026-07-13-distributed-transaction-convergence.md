---
layout: post
title: "分布式事务要解决的，其实是异常后的收敛"
description: "从支付类交易出发，讨论分布式事务为什么不只是让所有 RPC 同时成功，更要在超时、重试、部分成功和并发冲突之后，让多个资源最终收敛到同一个业务方向。"
date: 2026-07-13
tags: [分布式事务, 系统设计, 一致性, 分布式]
---

一次支付要创建支付单、锁定优惠券、冻结额度，再调用资金渠道扣款。很多人第一次碰到这类场景，会把分布式事务理解成“把数据库事务扩展到多个服务”：这些资源要么全部成功，要么全部失败。

但在复杂交易系统里，难点往往不在怎么让几个 RPC 同时成功，而在网络超时、进程崩溃、消息重复，甚至正向流程和回滚流程同时发生之后，系统还能不能判断这笔交易该往哪个方向走，并让所有资源最终收敛。

<!--more-->

XA、2PC 这类协议确实在追求跨资源的原子提交。但跨服务、跨资金渠道的业务里，通常没有一个能被统一锁住的数据库：外部服务不一定支持同一种事务协议，调用链可能持续数秒甚至更久，用户还可能在中间参与交互。更要紧的是中途出现部分成功以后，系统能不能恢复到一个唯一、正确、可解释的最终状态。所以我更愿意用“收敛”来理解分布式事务。

## 一、本地事务为什么不够

如果上面四个动作都在一个数据库里，事情很好办：

~~~sql
BEGIN;

INSERT INTO payment(...);
UPDATE coupon SET status = 'LOCKED' ...;
UPDATE quota SET frozen = frozen + 100 ...;
UPDATE balance SET amount = amount - 100 ...;

COMMIT;
~~~

任何一步失败，整个事务回滚。但真实系统里，这四个动作往往分散在四个独立服务中：

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 350" role="img" aria-label="一次支付跨越多个独立服务" style="width:100%;height:auto;display:block">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}.m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .box{fill:#fff;stroke:#374151;stroke-width:2}.soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}.ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .line{stroke:#475569;stroke-width:2;fill:none}.dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs><rect width="1200" height="350" fill="#fff"/><rect x="50" y="95" width="250" height="110" rx="12" class="box"/><text x="175" y="142.63" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="175" dy="0">Payment</tspan><tspan x="175" dy="29.700000000000003">Service</tspan></text><rect x="335" y="95" width="250" height="110" rx="12" class="box"/><text x="460" y="142.63" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="460" dy="0">Coupon</tspan><tspan x="460" dy="29.700000000000003">Service</tspan></text><rect x="620" y="95" width="250" height="110" rx="12" class="box"/><text x="745" y="142.63" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="745" dy="0">Quota</tspan><tspan x="745" dy="29.700000000000003">Service</tspan></text><rect x="905" y="95" width="250" height="110" rx="12" class="box"/><text x="1030" y="142.63" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="1030" dy="0">Money</tspan><tspan x="1030" dy="29.700000000000003">Service</tspan></text><text x="600" y="285" text-anchor="middle" class="t" font-size="25" font-weight="650"><tspan x="600" dy="0">一次支付跨越多个独立服务、数据库和故障边界</tspan></text></svg>
</figure>

它们各有独立的数据库、部署和故障边界，最后一个还是外部系统。这时麻烦的是，调用结果和真实资源状态可能对不上。最典型的是下面这个场景：

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" role="img" aria-label="远端成功但响应超时以及错误回滚造成的状态分叉" style="width:100%;height:auto;display:block">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}.m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .box{fill:#fff;stroke:#374151;stroke-width:2}.soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}.ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .line{stroke:#475569;stroke-width:2;fill:none}.dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs><rect width="1200" height="630" fill="#fff"/><rect x="70" y="45" width="430" height="70" rx="12" class="box"/><text x="285" y="87.14" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="285" dy="0">订单成功</tspan></text><path d="M285 115 L285 140" class="line" marker-end="url(#arrow)"/><rect x="70" y="150" width="430" height="70" rx="12" class="box"/><text x="285" y="192.14" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="285" dy="0">优惠券锁定成功</tspan></text><path d="M285 220 L285 245" class="line" marker-end="url(#arrow)"/><rect x="70" y="255" width="430" height="70" rx="12" class="box"/><text x="285" y="297.14" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="285" dy="0">额度冻结成功</tspan></text><path d="M285 325 L285 350" class="line" marker-end="url(#arrow)"/><rect x="70" y="360" width="430" height="70" rx="12" class="box"/><text x="285" y="402.14" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="285" dy="0">资金渠道实际扣款成功</tspan></text><path d="M285 430 L285 455" class="line" marker-end="url(#arrow)"/><rect x="70" y="465" width="430" height="70" rx="12" class="warn"/><text x="285" y="507.14" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="285" dy="0">响应丢失 / Timeout</tspan></text><path d="M500 500 L650 500" class="line" marker-end="url(#arrow)"/><rect x="680" y="165" width="430" height="90" rx="12" class="ok"/><text x="895" y="202.63" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="895" dy="0">资金</tspan><tspan x="895" dy="29.700000000000003">已扣款</tspan></text><rect x="680" y="285" width="430" height="90" rx="12" class="warn"/><text x="895" y="322.63" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="895" dy="0">订单</tspan><tspan x="895" dy="29.700000000000003">被错误关闭</tspan></text><rect x="680" y="405" width="430" height="90" rx="12" class="warn"/><text x="895" y="442.63" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="895" dy="0">外围资源</tspan><tspan x="895" dy="29.700000000000003">被错误回滚</tspan></text><text x="895" y="575" text-anchor="middle" class="t" font-size="23" font-weight="650"><tspan x="895" dy="0">把 Timeout 当失败，会让资源向不同方向分叉</tspan></text></svg>
</figure>

资金渠道其实已经扣款成功，调用方却只看到一个 timeout。如果直接把超时当失败，开始释放优惠券、解冻额度，就会出现很危险的分叉：钱已经扣了，订单却被关闭，外围资源也被回滚。**RPC 的返回结果，不等于资源的最终事实。**

## 二、超时是“未知”，不是“失败”

在单进程代码里，函数抛出异常通常意味着它没有成功完成。远程调用不是这样：

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 910" role="img" aria-label="超时是未知状态以及后续恢复流程" style="width:100%;height:auto;display:block">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}.m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .box{fill:#fff;stroke:#374151;stroke-width:2}.soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}.ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .line{stroke:#475569;stroke-width:2;fill:none}.dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs><rect width="1200" height="910" fill="#fff"/><rect x="70" y="85" width="280" height="100" rx="12" class="box"/><text x="210" y="143.84" text-anchor="middle" class="t" font-size="26" font-weight="500"><tspan x="210" dy="0">调用方 A</tspan></text><rect x="850" y="85" width="280" height="100" rx="12" class="box"/><text x="990" y="143.84" text-anchor="middle" class="t" font-size="26" font-weight="500"><tspan x="990" dy="0">服务 B</tspan></text><path d="M360 120 L840 120" class="line" marker-end="url(#arrow)"/><text x="600" y="95" text-anchor="middle" class="m" font-size="18" font-weight="500"><tspan x="600" dy="0">request</tspan></text><path d="M840 165 L360 165" class="dash"/><text x="600" y="195" text-anchor="middle" class="m" font-size="19" font-weight="500"><tspan x="600" dy="0">response 可能丢失</tspan></text><rect x="410" y="260" width="380" height="95" rx="12" class="warn"/><text x="600" y="299.795" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="600" dy="0">Timeout</tspan><tspan x="600" dy="31.05">真实结果 UNKNOWN</tspan></text><path d="M600 355 L600 415" class="line" marker-end="url(#arrow)"/><rect x="340" y="430" width="520" height="90" rx="12" class="box"/><text x="600" y="467.63" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="600" dy="0">UNKNOWN / PENDING</tspan><tspan x="600" dy="29.700000000000003">进入未决状态</tspan></text><path d="M600 520 L600 575" class="line" marker-end="url(#arrow)"/><rect x="340" y="590" width="520" height="90" rx="12" class="box"/><text x="600" y="642.82" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="600" dy="0">查询真实资源状态</tspan></text><path d="M600 680 L600 735" class="line" marker-end="url(#arrow)"/><rect x="260" y="750" width="680" height="105" rx="12" class="box"/><text x="600" y="795.13" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="600" dy="0">Forward / Rollback / Pending</tspan><tspan x="600" dy="29.700000000000003">根据事实决定方向</tspan></text></svg>
</figure>

A 没收到 response，至少有三种可能：B 根本没收到请求，操作没有发生；B 收到了但执行失败，资源没有变化；B 已经执行成功，只是响应丢了，真实资源已经变了，A 却不知道。这三种情况在 A 看来可能都只是同一个 `timeout`。

交易系统必须承认，**分布式系统中存在“未知状态”**。超时不能证明远端没有成功，更合理的做法是把它显式建模为 `UNKNOWN / PENDING`：先保留未决状态，再查询真实资源，根据事实决定继续正向、进入回滚，还是继续等待。反查因此成了交易系统的基础能力。

## 三、需要一个业务状态锚点

订单、券、额度、资金渠道同时存在时，很快会遇到一个问题：到底该相信谁？优惠券显示已使用，资金渠道却显示失败；渠道显示成功，支付单还停在处理中；额度已经解冻，实时流程却准备确认成功。这时需要一个比“某次 RPC 成功了没有”更高层的业务事实，来确定整笔交易的方向。我把它叫做 **Transaction Anchor（事务状态锚点）**，比如下面这个状态机：

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 1010" role="img" aria-label="核心状态锚点、资源收敛和事务编排职责" style="width:100%;height:auto;display:block">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}.m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .box{fill:#fff;stroke:#374151;stroke-width:2}.soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}.ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .line{stroke:#475569;stroke-width:2;fill:none}.dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs><rect width="1200" height="1010" fill="#fff"/><rect x="420" y="35" width="360" height="80" rx="12" class="box"/><text x="600" y="82.82" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="600" dy="0">CREATED</tspan></text><path d="M600 115 L600 165" class="line" marker-end="url(#arrow)"/><rect x="400" y="180" width="400" height="85" rx="12" class="box"/><text x="600" y="230.32" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="600" dy="0">PROCESSING</tspan></text><path d="M400 222 L235 320" class="line" marker-end="url(#arrow)"/><path d="M800 222 L965 320" class="line" marker-end="url(#arrow)"/><rect x="80" y="335" width="310" height="90" rx="12" class="ok"/><text x="235" y="387.82" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="235" dy="0">SUCCEEDED</tspan></text><rect x="810" y="335" width="310" height="90" rx="12" class="warn"/><text x="965" y="387.82" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="965" dy="0">CLOSING</tspan></text><path d="M965 425 L965 490" class="line" marker-end="url(#arrow)"/><rect x="810" y="505" width="310" height="90" rx="12" class="box"/><text x="965" y="557.82" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="965" dy="0">CLOSED</tspan></text><text x="600" y="660" text-anchor="middle" class="t" font-size="27" font-weight="700"><tspan x="600" dy="0">核心业务状态决定交易方向</tspan></text><rect x="50" y="715" width="340" height="125" rx="12" class="box"/><text x="220" y="770.465" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="220" dy="0">核心业务状态</tspan><tspan x="220" dy="28.35">决定：应该往哪里去</tspan></text><rect x="430" y="715" width="340" height="125" rx="12" class="box"/><text x="600" y="770.465" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="600" dy="0">事务编排器</tspan><tspan x="600" dy="28.35">负责：下一步怎么走</tspan></text><rect x="810" y="715" width="340" height="125" rx="12" class="box"/><text x="980" y="770.465" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="980" dy="0">资源状态</tspan><tspan x="980" dy="28.35">证明：已经发生了什么</tspan></text><text x="600" y="920" text-anchor="middle" class="t" font-size="22" font-weight="600"><tspan x="600" dy="0">SUCCEEDED → 券核销 / 额度实扣 / 渠道确认</tspan><tspan x="600" dy="34">CLOSING / CLOSED → 券释放 / 额度解冻 / 退款冲正</tspan></text></svg>
</figure>

外围资源不能各自决定最终状态，而要围绕核心状态收敛：核心状态一旦确定，成功方向就核销与实扣，关闭方向就释放、解冻、退款或冲正。分布式事务的结构也就从“多个调用组成的一条链”，变成了一个核心业务方向，加上多个跟随它收敛的资源状态机。

### 编排器负责推进，业务状态才是事实源

图下半部分的三个角色容易混淆：核心业务状态决定交易往哪里去，事务编排器负责下一步怎么走，资源状态证明实际发生了什么。编排器驱动流程，但不应该是业务真相的唯一保存者。需要长期保存的是业务事实：核心交易处于哪个方向、哪些资源已经成功、哪些动作仍然未知、某次执行是新动作还是旧请求的重放。

如果恢复完全依赖编排进程的内存上下文，进程一重启，事务就失去了恢复依据。更稳妥的做法是把核心状态持久化在业务单据里，编排器每次恢复时重新读取这些事实再做决定。所以我判断一个事务系统是否可靠，不太看它有没有编排框架，而是看**编排器退出以后，系统能不能仅凭持久化事实重建上下文、继续收敛**。

## 四、回滚不能只是倒着执行一遍

最直觉的补偿逻辑，是把正向步骤倒过来执行，也就是图上半部分的思路：

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 840" role="img" aria-label="正向流程与回滚流程并发造成的状态竞争" style="width:100%;height:auto;display:block">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}.m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .box{fill:#fff;stroke:#374151;stroke-width:2}.soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}.ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .line{stroke:#475569;stroke-width:2;fill:none}.dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs><rect width="1200" height="840" fill="#fff"/><text x="250" y="50" text-anchor="middle" class="t" font-size="25" font-weight="700"><tspan x="250" dy="0">正向</tspan></text><rect x="60" y="85" width="80" height="60" rx="12" class="box"/><text x="100" y="122.82" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="100" dy="0">A</tspan></text><path d="M140 115 L168 115" class="line" marker-end="url(#arrow)"/><rect x="170" y="85" width="80" height="60" rx="12" class="box"/><text x="210" y="122.82" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="210" dy="0">B</tspan></text><path d="M250 115 L278 115" class="line" marker-end="url(#arrow)"/><rect x="280" y="85" width="80" height="60" rx="12" class="box"/><text x="320" y="122.82" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="320" dy="0">C</tspan></text><path d="M360 115 L388 115" class="line" marker-end="url(#arrow)"/><rect x="390" y="85" width="80" height="60" rx="12" class="box"/><text x="430" y="122.82" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="430" dy="0">D</tspan></text><text x="900" y="50" text-anchor="middle" class="t" font-size="25" font-weight="700"><tspan x="900" dy="0">反向补偿</tspan></text><rect x="680" y="85" width="80" height="60" rx="12" class="box"/><text x="720" y="121.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="720" dy="0">D⁻¹</tspan></text><path d="M760 115 L788 115" class="line" marker-end="url(#arrow)"/><rect x="790" y="85" width="80" height="60" rx="12" class="box"/><text x="830" y="121.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="830" dy="0">C⁻¹</tspan></text><path d="M870 115 L898 115" class="line" marker-end="url(#arrow)"/><rect x="900" y="85" width="80" height="60" rx="12" class="box"/><text x="940" y="121.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="940" dy="0">B⁻¹</tspan></text><path d="M980 115 L1008 115" class="line" marker-end="url(#arrow)"/><rect x="1010" y="85" width="80" height="60" rx="12" class="box"/><text x="1050" y="121.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="1050" dy="0">A⁻¹</tspan></text><rect x="80" y="230" width="430" height="105" rx="12" class="box"/><text x="295" y="275.13" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="295" dy="0">流程 A：正向</tspan><tspan x="295" dy="29.700000000000003">准备确认交易成功</tspan></text><rect x="690" y="230" width="430" height="105" rx="12" class="box"/><text x="905" y="275.13" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="905" dy="0">流程 B：反向</tspan><tspan x="905" dy="29.700000000000003">准备释放优惠券</tspan></text><path d="M295 335 L480 420" class="line" marker-end="url(#arrow)"/><path d="M905 335 L720 420" class="line" marker-end="url(#arrow)"/><rect x="430" y="435" width="340" height="100" rx="12" class="warn"/><text x="600" y="477.63" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="600" dy="0">并发竞争</tspan><tspan x="600" dy="29.700000000000003">谁先固定交易方向？</tspan></text><rect x="110" y="610" width="300" height="95" rx="12" class="ok"/><text x="260" y="649.7950000000001" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="260" dy="0">交易</tspan><tspan x="260" dy="31.05">SUCCESS</tspan></text><rect x="450" y="610" width="300" height="95" rx="12" class="ok"/><text x="600" y="649.7950000000001" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="600" dy="0">资金</tspan><tspan x="600" dy="31.05">SUCCESS</tspan></text><rect x="790" y="610" width="300" height="95" rx="12" class="warn"/><text x="940" y="649.7950000000001" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="940" dy="0">优惠券</tspan><tspan x="940" dy="31.05">RELEASED</tspan></text><text x="600" y="780" text-anchor="middle" class="t" font-size="24" font-weight="650"><tspan x="600" dy="0">没有方向约束时，局部成功可以组合成全局错误状态</tspan></text></svg>
</figure>

没有并发时这没问题。但真实系统里，正向和反向流程可能同时存在：实时流程 A 正在确认成功，异步恢复流程 B 判断交易超时，准备释放优惠券。如果 B 先释放了优惠券，A 随后又把核心交易推进成成功，就会出现“交易成功、资金成功、优惠券却已退回”的组合。每个操作单独看都成功了，组合起来却是错的。

所以补偿流程的第一步不是释放外围资源，而是**先固定整笔交易的方向**，比如回滚开始前先尝试把核心业务状态从 `PROCESSING` 推进为 `CLOSING`：

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1030" role="img" aria-label="先固定交易方向再按顺序执行回滚" style="width:100%;height:auto;display:block">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}.m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .box{fill:#fff;stroke:#374151;stroke-width:2}.soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}.ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .line{stroke:#475569;stroke-width:2;fill:none}.dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs><rect width="1000" height="1030" fill="#fff"/><rect x="340" y="35" width="320" height="80" rx="12" class="box"/><text x="500" y="82.82" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="500" dy="0">PROCESSING</tspan></text><path d="M500 115 L500 165" class="line" marker-end="url(#arrow)"/><rect x="285" y="180" width="430" height="95" rx="12" class="box"/><text x="500" y="220.13" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="500" dy="0">CAS / 状态机校验</tspan><tspan x="500" dy="29.700000000000003">先抢占交易方向</tspan></text><path d="M500 275 L500 330" class="line" marker-end="url(#arrow)"/><rect x="340" y="345" width="320" height="80" rx="12" class="warn"/><text x="500" y="392.82" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="500" dy="0">CLOSING</tspan></text><rect x="280" y="470" width="440" height="75" rx="12" class="box"/><text x="500" y="514.98" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="500" dy="0">释放优惠券</tspan></text><path d="M500 545 L500 575" class="line" marker-end="url(#arrow)"/><rect x="280" y="585" width="440" height="75" rx="12" class="box"/><text x="500" y="629.98" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="500" dy="0">解冻额度</tspan></text><path d="M500 660 L500 690" class="line" marker-end="url(#arrow)"/><rect x="280" y="700" width="440" height="75" rx="12" class="box"/><text x="500" y="744.98" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="500" dy="0">退款 / 冲正资金</tspan></text><path d="M500 775 L500 805" class="line" marker-end="url(#arrow)"/><rect x="280" y="815" width="440" height="75" rx="12" class="box"/><text x="500" y="859.98" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="500" dy="0">最终 CLOSED</tspan></text><text x="500" y="975" text-anchor="middle" class="t" font-size="23" font-weight="650"><tspan x="500" dy="0">一旦进入 CLOSING，新的正向确认必须停止</tspan></text></svg>
</figure>

一旦成功进入 `CLOSING`，新的正向确认就不再允许发生，后续资源依次补偿，重复的回滚请求可以幂等地继续执行。先确定核心状态，再让外围资源跟着它收敛，这条原则不只适用于回滚。

## 五、幂等是前提

高可用系统一定会重试：客户端没收到响应会重试，网关超时会重新转发，Worker 崩溃后任务会重新调度，MQ 是至少一次投递，异步反查也可能再次触发同一个补偿动作。Retry 几乎必然意味着 Duplicate Execution。

业务动作没有稳定的幂等身份，系统就分不清这是一次新的业务操作，还是同一次操作的技术重试。典型做法是为一次业务尝试生成稳定的 `idempotency_key`，之后的技术重试都复用它：

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 586" role="img" aria-label="幂等键让技术重试复用同一次业务动作" style="width:100%;height:auto;display:block">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}.m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .box{fill:#fff;stroke:#374151;stroke-width:2}.soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}.ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .line{stroke:#475569;stroke-width:2;fill:none}.dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs><rect width="1000" height="586" fill="#fff"/><rect x="170" y="45" width="660" height="88" rx="12" class="box"/><text x="500" y="81.29499999999999" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="500" dy="0">业务操作</tspan><tspan x="500" dy="31.05">business_operation</tspan></text><path d="M500 133 L500 171" class="line" marker-end="url(#arrow)"/><rect x="170" y="181" width="660" height="88" rx="12" class="box"/><text x="500" y="217.295" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="500" dy="0">稳定幂等键</tspan><tspan x="500" dy="31.05">idempotency_key = op_123</tspan></text><path d="M500 269 L500 307" class="line" marker-end="url(#arrow)"/><rect x="170" y="317" width="660" height="88" rx="12" class="box"/><text x="500" y="353.295" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="500" dy="0">第一次请求</tspan><tspan x="500" dy="31.05">创建并执行</tspan></text><path d="M500 405 L500 443" class="line" marker-end="url(#arrow)"/><rect x="170" y="453" width="660" height="88" rx="12" class="box"/><text x="500" y="489.295" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="500" dy="0">后续重复请求</tspan><tspan x="500" dy="31.05">命中 op_123，复用原结果</tspan></text></svg>
</figure>

数据库里用唯一键把它固化下来：

~~~sql
INSERT INTO payment_attempt (
    idempotency_key,
    status
)
VALUES ('op_123', 'PROCESSING')
ON CONFLICT (idempotency_key) DO NOTHING;
~~~

然后读取已有记录决定下一步。这里要分清两件事：并发控制解决两个不同操作同时修改资源，幂等解决同一个操作被执行多次，两者缺一不可。

## 六、用状态机代替全局大锁

遇到复杂并发，很自然会想到加一把全局锁，把整笔交易锁住。这在工程上往往很难：锁要跨越多个服务、多个数据库、消息队列、外部渠道，以及可能持续很久的网络交互。持锁越久，可用性和吞吐量越差；持锁方一旦崩溃，还要处理租约、续约和脑裂。

所以很多交易系统选择**允许并发发生，但限制状态只能合法迁移**：

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 480" role="img" aria-label="核心交易状态机和外围资源状态机" style="width:100%;height:auto;display:block">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}.m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .box{fill:#fff;stroke:#374151;stroke-width:2}.soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}.ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .line{stroke:#475569;stroke-width:2;fill:none}.dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs><rect width="1200" height="480" fill="#fff"/><text x="310" y="45" text-anchor="middle" class="t" font-size="25" font-weight="700"><tspan x="310" dy="0">核心交易状态机</tspan></text><rect x="60" y="110" width="240" height="75" rx="12" class="box"/><text x="180" y="154.64" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="180" dy="0">PROCESSING</tspan></text><path d="M300 147 L390 100" class="line" marker-end="url(#arrow)"/><rect x="405" y="65" width="220" height="75" rx="12" class="ok"/><text x="515" y="109.64" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="515" dy="0">SUCCEEDED</tspan></text><path d="M300 160 L390 245" class="line" marker-end="url(#arrow)"/><rect x="405" y="215" width="220" height="75" rx="12" class="warn"/><text x="515" y="259.64" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="515" dy="0">CLOSING</tspan></text><path d="M515 290 L515 345" class="line" marker-end="url(#arrow)"/><rect x="405" y="360" width="220" height="75" rx="12" class="box"/><text x="515" y="404.64" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="515" dy="0">CLOSED</tspan></text><path d="M625 252 L670 252" class="dash"/><text x="685" y="260" text-anchor="start" class="t" font-size="19" font-weight="650"><tspan x="685" dy="0">禁止</tspan><tspan x="685" dy="27">CLOSING → SUCCEEDED</tspan></text><text x="895" y="45" text-anchor="middle" class="t" font-size="25" font-weight="700"><tspan x="895" dy="0">优惠券状态机</tspan></text><rect x="710" y="110" width="200" height="75" rx="12" class="box"/><text x="810" y="154.3" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="810" dy="0">AVAILABLE</tspan></text><path d="M910 147 L975 147" class="line" marker-end="url(#arrow)"/><rect x="990" y="110" width="180" height="75" rx="12" class="box"/><text x="1080" y="154.3" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="1080" dy="0">LOCKED</tspan></text><path d="M1080 185 L1080 245" class="line" marker-end="url(#arrow)"/><rect x="990" y="260" width="180" height="75" rx="12" class="ok"/><text x="1080" y="304.3" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="1080" dy="0">USED</tspan></text><path d="M1080 185 L835 245" class="line" marker-end="url(#arrow)"/><rect x="710" y="260" width="200" height="75" rx="12" class="warn"/><text x="810" y="304.3" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="810" dy="0">RELEASED</tspan></text><text x="780" y="405" text-anchor="middle" class="t" font-size="19" font-weight="650"><tspan x="780" dy="0">禁止 RELEASED → USED</tspan></text></svg>
</figure>

进入 `CLOSING` 之后，`CLOSING → SUCCEEDED` 就是非法迁移。实现上可以用 CAS 或带状态条件的更新：

~~~sql
UPDATE payment
SET status = 'SUCCEEDED'
WHERE id = ?
  AND status = 'PROCESSING';
~~~

受影响行数为 0，说明交易方向已经被其他流程改变，这次正向推进必须停止。

外围资源也要有自己的状态机。优惠券从 `AVAILABLE → LOCKED`，再根据交易方向进入 `USED` 或 `RELEASED`；进入 `RELEASED` 后，必须明确禁止再推进到 `USED`。这样系统的安全性就不用指望某个进程始终不出错，靠的是一条更稳定的约束：任何流程想继续推进，都得先证明当前状态允许它这么做。

## 七、同步链路结束了，事务还没结束

Web 系统很容易把一次请求的结束当成业务流程的结束，交易系统不是这样。用户发起支付后，同步链路通常只运行几秒：

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 720" role="img" aria-label="实时请求链路和异步恢复链路" style="width:100%;height:auto;display:block">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}.m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .box{fill:#fff;stroke:#374151;stroke-width:2}.soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}.ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .line{stroke:#475569;stroke-width:2;fill:none}.dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs><rect width="1200" height="720" fill="#fff"/><text x="300" y="45" text-anchor="middle" class="t" font-size="26" font-weight="700"><tspan x="300" dy="0">实时链路</tspan></text><rect x="80" y="85" width="440" height="60" rx="12" class="box"/><text x="300" y="121.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="300" dy="0">Client</tspan></text><path d="M300 145 L300 163" class="line" marker-end="url(#arrow)"/><rect x="80" y="170" width="440" height="60" rx="12" class="box"/><text x="300" y="206.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="300" dy="0">API</tspan></text><path d="M300 230 L300 248" class="line" marker-end="url(#arrow)"/><rect x="80" y="255" width="440" height="60" rx="12" class="box"/><text x="300" y="291.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="300" dy="0">Transaction Orchestrator</tspan></text><path d="M300 315 L300 333" class="line" marker-end="url(#arrow)"/><rect x="80" y="340" width="440" height="60" rx="12" class="box"/><text x="300" y="376.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="300" dy="0">Resource Services</tspan></text><path d="M300 400 L300 418" class="line" marker-end="url(#arrow)"/><rect x="80" y="425" width="440" height="60" rx="12" class="box"/><text x="300" y="461.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="300" dy="0">Response</tspan></text><text x="900" y="45" text-anchor="middle" class="t" font-size="26" font-weight="700"><tspan x="900" dy="0">异步恢复链路</tspan></text><rect x="690" y="85" width="420" height="52" rx="12" class="box"/><text x="900" y="117.46000000000001" text-anchor="middle" class="t" font-size="19" font-weight="500"><tspan x="900" dy="0">同步返回</tspan></text><path d="M900 137 L900 150" class="line" marker-end="url(#arrow)"/><rect x="690" y="155" width="420" height="52" rx="12" class="box"/><text x="900" y="187.46" text-anchor="middle" class="t" font-size="19" font-weight="500"><tspan x="900" dy="0">反查</tspan></text><path d="M900 207 L900 220" class="line" marker-end="url(#arrow)"/><rect x="690" y="225" width="420" height="52" rx="12" class="box"/><text x="900" y="257.46" text-anchor="middle" class="t" font-size="19" font-weight="500"><tspan x="900" dy="0">补偿</tspan></text><path d="M900 277 L900 290" class="line" marker-end="url(#arrow)"/><rect x="690" y="295" width="420" height="52" rx="12" class="box"/><text x="900" y="327.46" text-anchor="middle" class="t" font-size="19" font-weight="500"><tspan x="900" dy="0">消息重试</tspan></text><path d="M900 347 L900 360" class="line" marker-end="url(#arrow)"/><rect x="690" y="365" width="420" height="52" rx="12" class="box"/><text x="900" y="397.46" text-anchor="middle" class="t" font-size="19" font-weight="500"><tspan x="900" dy="0">对账</tspan></text><path d="M900 417 L900 430" class="line" marker-end="url(#arrow)"/><rect x="690" y="435" width="420" height="52" rx="12" class="box"/><text x="900" y="467.46" text-anchor="middle" class="t" font-size="19" font-weight="500"><tspan x="900" dy="0">最终收敛</tspan></text><rect x="150" y="560" width="900" height="105" rx="12" class="soft"/><text x="600" y="604.4599999999999" text-anchor="middle" class="t" font-size="24" font-weight="500"><tspan x="600" dy="0">高可用不是“实时永不失败”</tspan><tspan x="600" dy="32.400000000000006">而是失败以后仍知道下一步怎么走</tspan></text></svg>
</figure>

但交易的生命周期不会随着 HTTP 响应结束，同步返回之后，系统仍可能继续反查、补偿、重试和对账，直到所有资源收敛。所以比较完整的系统会有两条链路：实时链路尽快给用户结果、跑完大部分正常交易、控制同步等待时间；异步恢复链路处理超时、掉单、进程崩溃、消息重复、部分资源成功和补偿失败，在异常之后把系统修回来。

## 八、反查是为了重新建立事实

假设恢复流程看到的是下面这组资源状态：

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 840" role="img" aria-label="通过反查重新建立事实并选择恢复方向" style="width:100%;height:auto;display:block">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}.m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .box{fill:#fff;stroke:#374151;stroke-width:2}.soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}.ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .line{stroke:#475569;stroke-width:2;fill:none}.dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs><rect width="1200" height="840" fill="#fff"/><rect x="55" y="45" width="240" height="90" rx="12" class="box"/><text x="175" y="83.3" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="175" dy="0">Payment</tspan><tspan x="175" dy="27">PROCESSING</tspan></text><rect x="340" y="45" width="240" height="90" rx="12" class="box"/><text x="460" y="83.3" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="460" dy="0">Coupon</tspan><tspan x="460" dy="27">LOCKED</tspan></text><rect x="625" y="45" width="240" height="90" rx="12" class="box"/><text x="745" y="83.3" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="745" dy="0">Quota</tspan><tspan x="745" dy="27">FROZEN</tspan></text><rect x="910" y="45" width="240" height="90" rx="12" class="warn"/><text x="1030" y="83.3" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="1030" dy="0">Channel</tspan><tspan x="1030" dy="27">UNKNOWN</tspan></text><rect x="55" y="190" width="240" height="70" rx="12" class="box"/><text x="175" y="218.63500000000002" text-anchor="middle" class="t" font-size="19" font-weight="500"><tspan x="175" dy="0">Query</tspan><tspan x="175" dy="25.650000000000002">Payment</tspan></text><rect x="340" y="190" width="240" height="70" rx="12" class="box"/><text x="460" y="218.63500000000002" text-anchor="middle" class="t" font-size="19" font-weight="500"><tspan x="460" dy="0">Query</tspan><tspan x="460" dy="25.650000000000002">Coupon</tspan></text><rect x="625" y="190" width="240" height="70" rx="12" class="box"/><text x="745" y="218.63500000000002" text-anchor="middle" class="t" font-size="19" font-weight="500"><tspan x="745" dy="0">Query</tspan><tspan x="745" dy="25.650000000000002">Quota</tspan></text><rect x="910" y="190" width="240" height="70" rx="12" class="box"/><text x="1030" y="218.63500000000002" text-anchor="middle" class="t" font-size="19" font-weight="500"><tspan x="1030" dy="0">Query</tspan><tspan x="1030" dy="25.650000000000002">Channel</tspan></text><path d="M175 260 L175 315" class="line"/><path d="M460 260 L460 315" class="line"/><path d="M745 260 L745 315" class="line"/><path d="M1030 260 L1030 315" class="line"/><path d="M175 315 L1030 315" class="line"/><path d="M602 315 L602 370" class="line" marker-end="url(#arrow)"/><rect x="355" y="385" width="495" height="90" rx="12" class="box"/><text x="602.5" y="422.63" text-anchor="middle" class="t" font-size="22" font-weight="500"><tspan x="602.5" dy="0">重新建立当前事实</tspan><tspan x="602.5" dy="29.700000000000003">推断事务方向</tspan></text><path d="M602 475 L602 525" class="line"/><path d="M220 525 L980 525" class="line"/><path d="M220 525 L220 580" class="line" marker-end="url(#arrow)"/><path d="M602 525 L602 580" class="line" marker-end="url(#arrow)"/><path d="M980 525 L980 580" class="line" marker-end="url(#arrow)"/><rect x="70" y="595" width="300" height="105" rx="12" class="ok"/><text x="220" y="640.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="220" dy="0">Forward</tspan><tspan x="220" dy="27">渠道已成功 → 正向补偿</tspan></text><rect x="452" y="595" width="300" height="105" rx="12" class="warn"/><text x="602" y="640.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="602" dy="0">Rollback</tspan><tspan x="602" dy="27">核心已关闭 → 继续逆向</tspan></text><rect x="834" y="595" width="300" height="105" rx="12" class="box"/><text x="984" y="640.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="984" dy="0">Pending</tspan><tspan x="984" dy="27">仍未知 → 稍后再查</tspan></text><text x="600" y="780" text-anchor="middle" class="t" font-size="25" font-weight="650"><tspan x="600" dy="0">反查的本质：重新建立事实，而不是盲目重试</tspan></text></svg>
</figure>

这时不能简单地继续 Retry，因为资金渠道的上一次调用可能已经成功。恢复流程要主动查询 Payment、Coupon、Quota、Channel 这些真实资源，重新建立当前事实，再推断方向。结论通常只有三种：

- 应该成功：比如资金渠道已经成功，业务单还停在 `PROCESSING`，就走正向补偿，确认渠道结果、核销外围资源，把 Payment 推进到 `SUCCEEDED`。
- 应该失败：比如核心交易已经进入 `CLOSING`，就继续释放外围资源，最终收敛到 `CLOSED`。
- 仍无法判断：比如对方系统还在返回未知状态。这时最安全的是保持 `PENDING`，稍后再查，而不是强行猜一个结果。“未知”本身就是需要建模的状态。

## 九、最终一致要靠机制驱动

最终一致不是等一等就会发生的，系统不会因为过了五分钟就自然从错误状态变回正确状态。让它收敛的是下面这一整套机制：

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 660" role="img" aria-label="驱动最终一致的收敛机制" style="width:100%;height:auto;display:block">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}.m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .box{fill:#fff;stroke:#374151;stroke-width:2}.soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}.ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .line{stroke:#475569;stroke-width:2;fill:none}.dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs><rect width="1000" height="660" fill="#fff"/><rect x="190" y="40" width="620" height="48" rx="8" class="box"/><text x="500" y="71" text-anchor="middle" class="t" font-size="20" font-weight="600"><tspan x="500" dy="0">状态机</tspan></text><rect x="212" y="102" width="576" height="48" rx="8" class="box"/><text x="500" y="133" text-anchor="middle" class="t" font-size="20" font-weight="600"><tspan x="500" dy="0">幂等</tspan></text><rect x="234" y="164" width="532" height="48" rx="8" class="box"/><text x="500" y="195" text-anchor="middle" class="t" font-size="20" font-weight="600"><tspan x="500" dy="0">重试</tspan></text><rect x="256" y="226" width="488" height="48" rx="8" class="box"/><text x="500" y="257" text-anchor="middle" class="t" font-size="20" font-weight="600"><tspan x="500" dy="0">反查</tspan></text><rect x="278" y="288" width="444" height="48" rx="8" class="box"/><text x="500" y="319" text-anchor="middle" class="t" font-size="20" font-weight="600"><tspan x="500" dy="0">补偿</tspan></text><rect x="300" y="350" width="400" height="48" rx="8" class="soft"/><text x="500" y="381" text-anchor="middle" class="t" font-size="20" font-weight="600"><tspan x="500" dy="0">消息持久化</tspan></text><rect x="322" y="412" width="356" height="48" rx="8" class="soft"/><text x="500" y="443" text-anchor="middle" class="t" font-size="20" font-weight="600"><tspan x="500" dy="0">对账</tspan></text><rect x="344" y="474" width="312" height="48" rx="8" class="soft"/><text x="500" y="505" text-anchor="middle" class="t" font-size="20" font-weight="600"><tspan x="500" dy="0">告警</tspan></text><text x="500" y="585" text-anchor="middle" class="t" font-size="23" font-weight="650"><tspan x="500" dy="0">最终一致不是“等一等”</tspan><tspan x="500" dy="34">而是这些机制共同驱动出的收敛结果</tspan></text></svg>
</figure>

状态机决定哪些方向允许继续推进，幂等处理同一个动作的重复执行，Retry 让临时故障之后还能继续，反查在 RPC 结果不可信时获取真实事实，补偿撤销或修正已经发生的外围动作，Durable Message 保证恢复任务不丢，对账兜住在线恢复没发现的问题，告警在无法自动收敛时让人介入。把它们放在一起，就是下面这个比较通用的交易恢复模型：

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 1225" role="img" aria-label="完整的分布式交易恢复模型" style="width:100%;height:auto;display:block">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}.m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .box{fill:#fff;stroke:#374151;stroke-width:2}.soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}.ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .line{stroke:#475569;stroke-width:2;fill:none}.dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs><rect width="1200" height="1225" fill="#fff"/><rect x="370" y="30" width="460" height="75" rx="12" class="box"/><text x="600" y="75.32" text-anchor="middle" class="t" font-size="23" font-weight="500"><tspan x="600" dy="0">Command</tspan></text><path d="M600 105 L600 150" class="line" marker-end="url(#arrow)"/><rect x="350" y="165" width="500" height="85" rx="12" class="box"/><text x="600" y="214.98" text-anchor="middle" class="m" font-size="22" font-weight="500"><tspan x="600" dy="0">Idempotency Gate</tspan></text><path d="M600 250 L600 295" class="line" marker-end="url(#arrow)"/><rect x="350" y="310" width="500" height="100" rx="12" class="box"/><text x="600" y="352.63" text-anchor="middle" class="m" font-size="22" font-weight="500"><tspan x="600" dy="0">Core Transaction</tspan><tspan x="600" dy="29.700000000000003">State</tspan></text><path d="M600 410 L600 455" class="line"/><path d="M220 455 L980 455" class="line"/><path d="M220 455 L220 505" class="line" marker-end="url(#arrow)"/><path d="M600 455 L600 505" class="line" marker-end="url(#arrow)"/><path d="M980 455 L980 505" class="line" marker-end="url(#arrow)"/><rect x="90" y="520" width="260" height="80" rx="12" class="box"/><text x="220" y="567.48" text-anchor="middle" class="m" font-size="22" font-weight="500"><tspan x="220" dy="0">Coupon</tspan></text><rect x="470" y="520" width="260" height="80" rx="12" class="box"/><text x="600" y="567.48" text-anchor="middle" class="m" font-size="22" font-weight="500"><tspan x="600" dy="0">Quota</tspan></text><rect x="850" y="520" width="260" height="80" rx="12" class="box"/><text x="980" y="567.48" text-anchor="middle" class="m" font-size="22" font-weight="500"><tspan x="980" dy="0">Channel</tspan></text><path d="M220 600 L220 645" class="line"/><path d="M600 600 L600 645" class="line"/><path d="M980 600 L980 645" class="line"/><path d="M220 645 L980 645" class="line"/><path d="M600 645 L600 695" class="line" marker-end="url(#arrow)"/><rect x="360" y="710" width="480" height="85" rx="12" class="box"/><text x="600" y="759.98" text-anchor="middle" class="m" font-size="22" font-weight="500"><tspan x="600" dy="0">Query / Recover</tspan></text><path d="M600 795 L600 840" class="line"/><path d="M220 840 L980 840" class="line"/><path d="M220 840 L220 890" class="line" marker-end="url(#arrow)"/><path d="M600 840 L600 890" class="line" marker-end="url(#arrow)"/><path d="M980 840 L980 890" class="line" marker-end="url(#arrow)"/><rect x="90" y="905" width="260" height="80" rx="12" class="box"/><text x="220" y="952.48" text-anchor="middle" class="m" font-size="22" font-weight="500"><tspan x="220" dy="0">Forward</tspan></text><rect x="470" y="905" width="260" height="80" rx="12" class="box"/><text x="600" y="952.48" text-anchor="middle" class="m" font-size="22" font-weight="500"><tspan x="600" dy="0">Rollback</tspan></text><rect x="850" y="905" width="260" height="80" rx="12" class="box"/><text x="980" y="952.48" text-anchor="middle" class="m" font-size="22" font-weight="500"><tspan x="980" dy="0">Pending</tspan></text><path d="M220 985 L220 1030" class="line"/><path d="M600 985 L600 1030" class="line"/><path d="M980 985 L980 1030" class="line"/><path d="M220 1030 L980 1030" class="line"/><path d="M600 1030 L600 1080" class="line" marker-end="url(#arrow)"/><rect x="390" y="1095" width="420" height="80" rx="12" class="box"/><text x="600" y="1142.82" text-anchor="middle" class="m" font-size="23" font-weight="500"><tspan x="600" dy="0">Convergence</tspan></text></svg>
</figure>

## 十、难的是故障窗口

只看正常流程，分布式事务其实很简单，就是图上半部分那样：

<figure class="diagram">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 700" role="img" aria-label="正常路径和分布式事务的典型故障窗口" style="width:100%;height:auto;display:block">
<defs>
  <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker>
  <style>
    .t{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Arial,sans-serif;fill:#1f2937}.m{font-family:"SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;fill:#1f2937}
    .box{fill:#fff;stroke:#374151;stroke-width:2}.soft{fill:#f8fafc;stroke:#64748b;stroke-width:1.6}
    .warn{fill:#fff7ed;stroke:#c2410c;stroke-width:1.8}.ok{fill:#f0fdf4;stroke:#15803d;stroke-width:1.8}
    .line{stroke:#475569;stroke-width:2;fill:none}.dash{stroke:#94a3b8;stroke-width:1.6;fill:none;stroke-dasharray:7 7}
  </style>
</defs><rect width="1200" height="700" fill="#fff"/><text x="600" y="40" text-anchor="middle" class="t" font-size="25" font-weight="700"><tspan x="600" dy="0">正常路径</tspan></text><rect x="100" y="75" width="210" height="70" rx="12" class="ok"/><text x="205" y="117.14" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="205" dy="0">A 成功</tspan></text><path d="M310 110 L355 110" class="line" marker-end="url(#arrow)"/><rect x="370" y="75" width="210" height="70" rx="12" class="ok"/><text x="475" y="117.14" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="475" dy="0">B 成功</tspan></text><path d="M580 110 L625 110" class="line" marker-end="url(#arrow)"/><rect x="640" y="75" width="210" height="70" rx="12" class="ok"/><text x="745" y="117.14" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="745" dy="0">C 成功</tspan></text><path d="M850 110 L895 110" class="line" marker-end="url(#arrow)"/><rect x="910" y="75" width="210" height="70" rx="12" class="ok"/><text x="1015" y="117.14" text-anchor="middle" class="t" font-size="21" font-weight="500"><tspan x="1015" dy="0">完成</tspan></text><text x="600" y="225" text-anchor="middle" class="t" font-size="25" font-weight="700"><tspan x="600" dy="0">真正困难的是故障窗口</tspan></text><rect x="90" y="270" width="490" height="105" rx="12" class="soft"/><text x="335" y="315.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="335" dy="0">部分成功</tspan><tspan x="335" dy="27">A 成功 · B 成功 · C 超时</tspan></text><rect x="620" y="270" width="490" height="105" rx="12" class="soft"/><text x="865" y="315.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="865" dy="0">方向竞争</tspan><tspan x="865" dy="27">正向未结束 · 回滚已启动</tspan></text><rect x="90" y="405" width="490" height="105" rx="12" class="soft"/><text x="335" y="450.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="335" dy="0">状态掉单</tspan><tspan x="335" dy="27">外部成功 · 本地未更新</tspan></text><rect x="620" y="405" width="490" height="105" rx="12" class="soft"/><text x="865" y="450.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="865" dy="0">旧消息回放</tspan><tspan x="865" dy="27">补偿后旧正向消息再次唤醒</tspan></text><rect x="90" y="540" width="1020" height="105" rx="12" class="warn"/><text x="600" y="585.8" text-anchor="middle" class="t" font-size="20" font-weight="500"><tspan x="600" dy="0">崩溃窗口</tspan><tspan x="600" dy="27">副作用已发生 · 结果尚未持久化</tspan></text></svg>
</figure>

难的是下半部分这些故障窗口。设计时要回答的是：在这些窗口里，下一次执行还能不能根据持久化事实做出正确决策。如果答案是“只能靠人工查日志”，这套事务系统就还没有闭环。

本地事务承诺要么全部发生，要么全部不发生。分布式业务给不了这个承诺，更现实的目标是中间态能被识别、部分成功能被恢复、重复请求不破坏业务不变量。所以设计分布式事务时，我更关心下面这些问题：

- 核心交易方向由谁决定？
- 超时以后如何确认真实结果？
- 同一个请求重放会不会重复扣资源？
- 正向和回滚同时执行时谁赢？
- 补偿执行一半崩溃后能不能继续？
- 已经进入关闭方向的资源能不能被旧流程重新推进？
- 异步恢复任务会不会丢？
- 自动恢复失败以后有没有对账和告警兜底？

这些问题都能回答清楚，用 Saga、TCC、事务消息、Outbox，还是自己实现状态机，往往只是具体的工程选择。没想清楚的话，即使引入了分布式事务框架，系统在异常情况下依然可能出现资源分叉。
