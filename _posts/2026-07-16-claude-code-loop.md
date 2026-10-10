---
layout: post
title: "Claude Code 中的 Loop"
description: "Claude Code 中四类 loop 的触发方式、停止条件和适用场景，以及如何兼顾代码质量与 token 用量。"
date: 2026-07-16
tags: [Claude Code, Agent, Loop]
---

原文来源：[ClaudeDevs on X](https://x.com/ClaudeDevs/status/2074208949205881033)

说明：本文为中文翻译，命令、原语名与代码标识符保持英文原文。

最近大家讨论得比较多的是“设计 loop”，而不只是给 coding agent 写 prompt。不过如果你在 X 上花点时间搜一下 loop 到底是什么，会看到好几种不同的说法。

在 Claude Code 团队，我们把 **loop** 定义为：

> Agent 一轮接一轮地重复工作，直到满足某个停止条件。

<!--more-->

我们从下面几个维度给 loop 分类：

- 怎么触发
- 怎么停止
- 用到哪种 Claude Code 原语
- 最适合哪类任务

下面会介绍几种主要的 loop、各自适合的场景，以及怎样在保证代码质量的同时控制 token 用量。不是每个任务都需要复杂的 loop，能用最简单的方案就用最简单的，这些模式要有选择地用。

---

## Turn-based loops（按轮次的 loop）

- 触发方式：用户 prompt
- 停止条件：Claude 判断任务已完成，或需要更多上下文
- 最适合：较短的任务，且不属于固定流程或定期任务
- 控制用量的方式：把 prompt 写得更具体；用 skills 改进验证，减少轮次

你发出的每条 prompt 都会启动一个手动 loop，每一轮都由你来推动。Claude 收集上下文、执行操作、检查结果，必要时重复这个过程，最后给出回复，这个过程我们叫作 **agentic loop**。

比如你让 Claude 做一个点赞按钮，它会读代码、改代码、跑测试，然后交回一个它认为能用的版本。接下来你手动检查，再写下一条 prompt。

要改进验证环节，可以把手动验证的步骤写进 `SKILL.md`，让 Claude 自己多做一些端到端检查。里面应该包括相应的工具或连接器，让 Claude 能看到、测量结果，或者直接和结果交互。检查越量化，Claude 越容易自己完成验证。

比如 `SKILL.md` 可以这样写：

```markdown
---
name: verify-frontend-change
description: Verify any UI change end-to-end before declaring it done.
---

# Verifying frontend changes

Never report a UI change as complete based on a successful edit alone. Verify it the way a human reviewer would:

1. Start the dev server and open the edited page in the browser.

2. Interact with the change directly. For a new control (button, input, toggle): click it, confirm the expected state change, and screenshot before/after.

3. Check the browser console: zero new errors or warnings.

4. Use the Chrome Devtools MCP, run a performance trace and audit Core Web Vitals.

If any step fails, fix the issue and rerun from step 1 — do not hand back partially verified work.
```

---

## Goal-based loop（基于目标的 loop，`/goal`）

- 触发方式：人在场时手动发出的 prompt
- 停止条件：目标达成，或达到最大轮次
- 最适合：有可验证退出条件的任务
- 控制用量的方式：设定明确的完成标准，并给出明确的轮次上限，例如 “stop after 5 tries.”

有时候一轮不够，任务越复杂越是如此，而 Agent 在能反复迭代时表现更好。用 `/goal` 定义“完成是什么样子”，可以让 Claude 迭代更长时间。

定义好成功标准后，Claude 就不用自己判断“够不够好”，也就不会过早结束 loop。每次 Claude 想停下来，评估模型都会对照你的条件检查一遍；只要目标没达成、轮次也没到上限，就把它送回去继续工作。

所以确定性的标准特别好用，比如通过的测试数量、某个评分阈值。

例如：

```bash
/goal get the homepage Lighthouse score to 90 or above, stop after 5 tries.
```

---

## Time-based loop（基于时间的 loop，`/loop` 与 `/schedule`）

- 触发方式：固定的时间间隔
- 停止条件：你手动取消，或工作本身完成（例如 PR 已合并、队列已清空）
- 最适合：周期性工作，或需要对接外部环境、外部系统的工作
- 控制用量的方式：把间隔设长一些，或者改成响应事件，而不是按时间轮询

有些 agent 工作是周期性的：任务不变，变的只是输入，比如每天早上总结 Slack 消息。还有些工作依赖外部系统，对接它们的一种简单办法是定期检查、发现变化就响应，比如某个 PR 收到了 code review，或者 CI 失败了。

这时可以用 `/loop` 按固定间隔重复运行一条 prompt，例如：

```bash
/loop 5m check my PR, address review comments, and fix failing CI
```

`/loop` 跑在你自己的电脑上，电脑一关它就停了。你也可以用 `/schedule` 创建 routine，把 loop 挪到云端。

---

## Proactive loops（主动式 loop）

- 触发方式：事件或定时任务，不需要人实时在场
- 停止条件：每个任务在目标达成时结束；routine 本身会一直运行，直到你关掉它
- 最适合：定义清晰、持续流入的工作，比如 bug 报告、issue 分诊、迁移、依赖升级
- 控制用量的方式：把 routine 交给更小、更快的模型，需要判断的决策再用最强的模型

上面这些原语，再加上 Claude Code 的其他能力，比如 auto mode 和 dynamic workflows（research preview），就能组合出适合长时间运行的 loop。

比如要处理源源不断进来的反馈，可以这样组合：

- 用 `/schedule`（research preview）运行 routine，检查有没有新报告
- 用 `/goal` 定义完成标准，用 skills 写清楚怎么验证
- 用 dynamic workflows 编排多个 agent，分别负责分诊报告、修复问题、审查修复
- 用 auto mode 让 routine 不必反复请求权限，一直运行下去

合在一起，prompt 可能长这样：

```bash
/schedule every hour: check the project-feedback channel for bug reports. /goal: don't stop until every report found this run is triaged, actioned, and responded to. When fixing a bug, use a workflow to explore three solutions in parallel worktrees and have a judge adversarially review them.
```

---

## 维护代码质量

loop 的产出质量，取决于它所处的系统。设计这个系统时：

- 保持代码库干净：Claude 会沿用代码库里已有的模式和约定。
- 给 Claude 自我验证的手段：用 skills 把“什么算好”沉淀成团队标准。
- 让文档容易获取：框架和库的文档里应该有最新的最佳实践。
- 用第二个 agent 做 code review：上下文全新的 reviewer 偏见更少，也不容易被主 agent 的推理过程带偏。可以用内置的 `/code-review` skill，或者 GitHub 的 Code Review。

某次结果没达到标准时，别只修这一次的问题，尽量把它沉淀到系统里，让之后的每次迭代都受益。

---

## 管理 token 用量

要控制 token 用量，loop 得有清晰的边界：

- 为任务选合适的原语和模型：小任务用不着多个 agent 或多个 loop，有些任务可以交给更便宜、更快的模型。
- 定义清晰的成功和停止条件：讲清楚“完成是什么样子”，让 Claude 更快找到解，同时又不会过早停下。
- 大规模运行前先试点：dynamic workflows 可能会生成上百个 agent，先拿一小块工作估算用量。
- 确定性的工作交给脚本：跑脚本通常比一步步推理便宜。比如 PDF skill 可以直接附带填表脚本，Claude 每次运行这个脚本就行，不用每次重写代码。
- routine 不要跑得比需要的更频繁：让间隔和你观察对象的实际变化频率匹配。
- 检查用量：`/usage` 会按 skills、subagents、MCPs 拆分近期用量；不带参数的 `/goal` 会显示目前的轮次和 token 用量；`/workflows` 会显示每个 agent 的 token 用量，你可以随时停掉某个 agent。

---

## 如何开始

简单总结一下：

| Loop 类型 | 你交出去的部分 | 适用时机 | 优先使用 |
|---|---|---|---|
| Turn-based | 检查（the check） | 你在探索或做决策 | 自定义 verification skills |
| Goal-based | 停止条件（the stop condition） | 你清楚“完成是什么样子” | `/goal` |
| Time-based | 触发器（the trigger） | 工作发生在项目之外，并且按计划到来 | `/loop`、`/schedule` |
| Proactive | prompt 本身 | 工作周期性出现，并且定义清晰 | 以上全部，以及 dynamic workflows |

想开始用 loop，可以先看看你手头已经在做的工作。挑一个卡在你自己身上的任务，问问自己哪部分可以交出去：

- 你能写出验证检查吗？
- 目标够清晰吗？
- 工作是按计划到来的吗？

有了想法就把 loop 跑起来，看它在哪里卡住、在哪里越界，然后大胆地迭代。

想了解更多，可以阅读 Claude Code 文档中关于并行运行 agent 的内容，以及 `loop`、`schedule`、`goal` 和 dynamic workflows 的相关页面。
