#!/usr/bin/env python3
"""Generate architecture SVGs for the resumable-agent post."""

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parent
FONT = "-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Noto Sans SC',sans-serif"
ACCENT = "#2d72d9"
TEXT = "#1d2433"
MUTED = "#5b6475"
FILL = "#f4f7fb"
STROKE = "#c9d3e2"
FAIL = "#c0392b"
FAIL_FILL = "#fdf2f0"
OK = "#2a9d6e"
OK_FILL = "#eef8f3"
WAIT = "#c47d12"
WAIT_FILL = "#fbf4e8"
BG = "#ffffff"


def write(name: str, w: int, h: int, body: str) -> None:
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img">
  <rect width="{w}" height="{h}" rx="12" fill="{BG}"/>
  {body}
</svg>
'''
    (OUT / name).write_text(svg, encoding="utf-8")


def box(x, y, w, h, label, fill=FILL, stroke=ACCENT, color=TEXT, size=14, weight="600"):
    return f'''<g>
  <rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>
  <text x="{x + w / 2:.1f}" y="{y + h / 2 + 5:.1f}" text-anchor="middle" font-family="{FONT}" font-size="{size}" font-weight="{weight}" fill="{color}">{escape(label)}</text>
</g>'''


def caption(x, y, text, size=12, color=MUTED, anchor="middle"):
    return f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-family="{FONT}" font-size="{size}" fill="{color}">{escape(text)}</text>'


def arrow_v(x, y1, y2, color=ACCENT):
    return f'''<line x1="{x:.1f}" y1="{y1:.1f}" x2="{x:.1f}" y2="{y2 - 7:.1f}" stroke="{color}" stroke-width="1.5"/>
  <polygon points="{x - 5:.1f},{y2 - 8:.1f} {x + 5:.1f},{y2 - 8:.1f} {x:.1f},{y2:.1f}" fill="{color}"/>'''


def arrow_h(x1, y, x2, color=ACCENT):
    if x2 > x1:
        return f'''<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2 - 7:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="1.5"/>
  <polygon points="{x2 - 8:.1f},{y - 5:.1f} {x2 - 8:.1f},{y + 5:.1f} {x2:.1f},{y:.1f}" fill="{color}"/>'''
    return f'''<line x1="{x1:.1f}" y1="{y:.1f}" x2="{x2 + 7:.1f}" y2="{y:.1f}" stroke="{color}" stroke-width="1.5"/>
  <polygon points="{x2 + 8:.1f},{y - 5:.1f} {x2 + 8:.1f},{y + 5:.1f} {x2:.1f},{y:.1f}" fill="{color}"/>'''


def vflow(name, steps, *, box_w=240, box_h=40, gap=28, pad=28, fill=FILL, stroke=ACCENT):
    w = pad * 2 + box_w
    h = pad * 2 + len(steps) * box_h + (len(steps) - 1) * gap
    x = pad
    parts = []
    y = pad
    cx = pad + box_w / 2
    for i, step in enumerate(steps):
        style = {}
        label = step
        if isinstance(step, tuple):
            label, style = step[0], step[1]
        parts.append(box(x, y, box_w, box_h, label, **style) if style else box(x, y, box_w, box_h, label, fill=fill, stroke=stroke))
        if i < len(steps) - 1:
            parts.append(arrow_v(cx, y + box_h, y + box_h + gap, style.get("stroke", stroke) if style else stroke))
        y += box_h + gap
    write(name, w, h, "\n  ".join(parts))


def two_col(name, left_title, left_steps, right_title, right_steps, left_stroke=FAIL, right_stroke=OK):
    box_w, box_h, gap, pad, header = 200, 38, 24, 24, 28
    col_w = box_w
    w = pad * 3 + col_w * 2
    n = max(len(left_steps), len(right_steps))
    h = pad * 2 + header + n * box_h + (n - 1) * gap
    parts = [
        caption(pad + col_w / 2, pad + 16, left_title, 13, left_stroke),
        caption(pad * 2 + col_w + col_w / 2, pad + 16, right_title, 13, right_stroke),
    ]
    cols = [
        (left_steps, left_stroke, FAIL_FILL if left_stroke == FAIL else FILL, pad),
        (right_steps, right_stroke, OK_FILL if right_stroke == OK else FILL, pad * 2 + col_w),
    ]
    for steps, stroke, fill, x0 in cols:
        y = pad + header
        cx = x0 + col_w / 2
        for i, label in enumerate(steps):
            parts.append(box(x0, y, col_w, box_h, label, fill=fill, stroke=stroke))
            if i < len(steps) - 1:
                parts.append(arrow_v(cx, y + box_h, y + box_h + gap, stroke))
            y += box_h + gap
    write(name, w, h, "\n  ".join(parts))


def main():
    vflow("simple-chat.svg", ["Browser", "POST /chat", "Server", "LLM", "Response"])
    vflow("long-task.svg", [
        "读取用户资料",
        "调用 LLM",
        "搜索外部信息",
        "再次推理",
        "调用业务工具",
        "生成结构化结果",
        "生成最终报告",
    ], box_w=220)

    # HTTP lifecycle equals agent
    write("lifecycle-coupled.svg", 360, 168, "\n  ".join([
        box(40, 28, 280, 44, "HTTP Request 生命周期"),
        caption(180, 96, "=", 22, ACCENT),
        box(40, 112, 280, 44, "Agent Task 生命周期", fill=FAIL_FILL, stroke=FAIL),
    ]))

    vflow("task-lost.svg", [
        "API Process",
        "Agent Coroutine",
        ("Process Restart", {"fill": FAIL_FILL, "stroke": FAIL}),
        ("任务消失", {"fill": FAIL_FILL, "stroke": FAIL, "color": FAIL}),
    ])

    write("duplicate-submit.svg", 440, 210, "\n  ".join([
        box(30, 30, 160, 44, "Request #1"),
        box(250, 30, 160, 44, "Request #2"),
        arrow_v(110, 74, 118),
        arrow_v(330, 74, 118),
        box(30, 120, 160, 52, "Agent Task A", fill=FAIL_FILL, stroke=FAIL),
        box(250, 120, 160, 52, "Agent Task B", fill=FAIL_FILL, stroke=FAIL),
        caption(220, 196, "同一次意图，两次副作用", 13),
    ]))

    write("lifecycle-split.svg", 420, 300, "\n  ".join([
        box(70, 20, 280, 42, "Request 生命周期"),
        caption(210, 84, "≠", 20, ACCENT),
        box(70, 96, 280, 42, "Task 生命周期"),
        caption(210, 160, "≠", 20, ACCENT),
        box(70, 172, 280, 42, "Worker 生命周期"),
        caption(210, 236, "≠", 20, ACCENT),
        box(70, 248, 280, 42, "SSE Connection 生命周期"),
    ]))

    two_col(
        "old-vs-queue.svg",
        "原来",
        ["HTTP", "Agent.run()", "Response"],
        "拆开以后",
        ["HTTP Request", "Create Task", "Database", "Queue", "Worker", "Agent Runtime"],
        FAIL,
        OK,
    )

    write("queue-decouple.svg", 500, 250, "\n  ".join([
        caption(130, 28, "API", 13, ACCENT),
        caption(370, 28, "Worker", 13, OK),
        box(30, 44, 200, 38, "Request"),
        arrow_v(130, 82, 108),
        box(30, 108, 200, 38, "enqueue(task_id)"),
        arrow_v(130, 146, 172),
        box(30, 172, 200, 44, "Request 可以结束", fill=OK_FILL, stroke=OK),
        box(270, 44, 200, 38, "Worker", fill=OK_FILL, stroke=OK),
        arrow_v(370, 82, 108, OK),
        box(270, 108, 200, 38, "task_id", fill=OK_FILL, stroke=OK),
        arrow_v(370, 146, 172, OK),
        box(270, 172, 200, 44, "Agent.run()", fill=OK_FILL, stroke=OK),
    ]))

    # state machine
    write("task-states.svg", 520, 310, "\n  ".join([
        box(170, 20, 180, 40, "PENDING"),
        arrow_v(260, 60, 88),
        box(170, 88, 180, 40, "RUNNING"),
        arrow_v(140, 128, 176, WAIT),
        arrow_v(260, 128, 176, FAIL),
        arrow_v(380, 128, 176, OK),
        box(40, 176, 140, 40, "WAITING", fill=WAIT_FILL, stroke=WAIT),
        box(190, 176, 140, 40, "FAILED", fill=FAIL_FILL, stroke=FAIL),
        box(340, 176, 140, 40, "COMPLETED", fill=OK_FILL, stroke=OK),
        arrow_v(110, 216, 248, WAIT),
        box(40, 248, 140, 40, "PENDING", fill=WAIT_FILL, stroke=WAIT),
        caption(260, 300, "WAITING 不是失败，只是等用户", 12),
    ]))

    vflow("execution-steps.svg", ["读取 Context", "LLM 决策", "调用 Search", "得到结果", "再次 DECIDE"])
    vflow("worker-crash.svg", [
        "Search 已完成",
        "准备继续推理",
        ("Worker Crash", {"fill": FAIL_FILL, "stroke": FAIL, "color": FAIL}),
    ])
    vflow("checkpoint-restore.svg", ["New Worker", "Load Checkpoint", "Restore RunState", "Continue"], fill=OK_FILL, stroke=OK)

    write("two-kinds-of-state.svg", 480, 150, "\n  ".join([
        box(24, 28, 200, 88, ""),
        caption(124, 62, "Task State", 15, ACCENT, "middle"),
        caption(124, 86, "任务是什么状态", 13, MUTED, "middle"),
        box(256, 28, 200, 88, ""),
        caption(356, 62, "Checkpoint", 15, ACCENT, "middle"),
        caption(356, 86, "执行到哪里", 13, MUTED, "middle"),
    ]))

    vflow("side-effect-crash.svg", [
        "调用 Tool",
        ("Tool 成功", {"fill": OK_FILL, "stroke": OK}),
        "外部系统已变化",
        ("Worker Crash", {"fill": FAIL_FILL, "stroke": FAIL, "color": FAIL}),
        "结果还没记下来",
    ], box_w=230)

    write("idempotency-ledger.svg", 420, 140, "\n  ".join([
        box(24, 40, 176, 56, "Idempotency"),
        caption(210, 74, "+", 22, ACCENT),
        box(228, 40, 168, 56, "Tool Ledger"),
    ]))

    two_col(
        "sse-vs-outbox.svg",
        "会丢事件",
        ["Agent", "SSE", "Browser"],
        "可回放",
        ["Agent", "Event Outbox", "SSE", "Browser"],
        FAIL,
        OK,
    )

    # big architecture
    write("architecture.svg", 560, 890, architecture())

    write("durability-vs-queue.svg", 500, 150, "\n  ".join([
        box(20, 24, 220, 100, ""),
        caption(130, 62, "PostgreSQL", 15, ACCENT),
        caption(130, 86, "Durability Backbone", 13, MUTED),
        box(260, 24, 220, 100, ""),
        caption(370, 62, "Redis / Queue", 15, ACCENT),
        caption(370, 86, "Scheduling / Coordination", 13, MUTED),
    ]))

    two_col(
        "hitl-wait.svg",
        "错误：占着 Worker",
        ["Worker", "等待", "等待", "等待三个小时"],
        "正确：交还 Slot",
        ["Agent", "ASK_USER", "Save Checkpoint", "RUNNING → WAITING", "Worker Job 结束"],
        FAIL,
        OK,
    )

    vflow("hitl-resume.svg", [
        "User Answer",
        "WAITING → PENDING",
        "enqueue(task_id)",
        "New Worker",
        "Load Checkpoint",
        "Continue Agent",
    ], fill=OK_FILL, stroke=OK)

    write("task-vs-job.svg", 480, 230, "\n  ".join([
        caption(240, 28, "业务任务 10:00 – 14:31", 13, MUTED),
        box(40, 48, 400, 36, "WAITING  10:02 → 14:30", fill=WAIT_FILL, stroke=WAIT),
        box(40, 108, 150, 52, "Job #1  10:00–10:02", fill=OK_FILL, stroke=OK, size=12),
        box(290, 108, 150, 52, "Job #2  14:30–14:31", fill=OK_FILL, stroke=OK, size=12),
        caption(240, 196, "Task 可以远长于 Worker Job", 13),
    ]))

    vflow("worker-dies.svg", [
        "Worker",
        "PENDING → RUNNING",
        "Agent 执行",
        ("Worker Crash", {"fill": FAIL_FILL, "stroke": FAIL, "color": FAIL}),
    ])

    write("lease-heartbeat.svg", 500, 120, "\n  ".join([
        box(16, 32, 148, 52, "Lease"),
        box(176, 32, 148, 52, "Heartbeat"),
        box(336, 32, 148, 52, "Reaper"),
    ]))

    vflow("lease-resume.svg", [
        "RUNNING",
        "lease expired",
        "PENDING",
        "重新 enqueue",
        "新 Worker",
        "Load Checkpoint",
        "Resume",
    ])

    write("pg-without-job.svg", 460, 180, "\n  ".join([
        box(24, 36, 190, 100, ""),
        caption(119, 76, "PostgreSQL", 14, ACCENT),
        caption(119, 100, "task = PENDING", 13, MUTED),
        box(246, 36, 190, 100, "", fill=FAIL_FILL, stroke=FAIL),
        caption(341, 76, "Redis", 14, FAIL),
        caption(341, 100, "没有这个 job", 13, FAIL),
    ]))

    vflow("outbox-dispatch.svg", ["读取 dispatch_outbox", "投递 Redis", "标记 dispatched"])

    write("retry-layers.svg", 460, 170, "\n  ".join([
        box(30, 24, 400, 58, ""),
        caption(230, 48, "Capability Retry", 14, ACCENT),
        caption(230, 68, "单个外部调用的临时错误", 12, MUTED),
        box(30, 96, 400, 58, ""),
        caption(230, 120, "Job Retry", 14, ACCENT),
        caption(230, 140, "整个 Worker Job 的基础设施故障", 12, MUTED),
    ]))

    vflow("recover-loop.svg", ["Failure", "Detect", "Persist", "Recover", "Resume"], fill=OK_FILL, stroke=OK)
    vflow("task-vanishes.svg", [
        "某个进程崩了",
        ("任务直接消失", {"fill": FAIL_FILL, "stroke": FAIL, "color": FAIL}),
    ])
    vflow("task-recovers.svg", [
        "某个进程崩了",
        "系统检测到异常",
        "找到 durable state",
        "重新调度",
        "从 checkpoint 恢复",
    ], fill=OK_FILL, stroke=OK)

    write("full-model.svg", 360, 680, full_model())


def architecture() -> str:
    parts = [
        box(180, 16, 200, 40, "Client"),
        arrow_v(280, 56, 80),
        box(180, 80, 200, 40, "API Server"),
        arrow_v(280, 120, 144),
        box(180, 144, 200, 36, "Idempotency", fill=WAIT_FILL, stroke=WAIT, size=13),
        arrow_v(280, 180, 204),
        box(180, 204, 200, 40, "Create Task"),
        # split
        f'<line x1="280" y1="244" x2="280" y2="268" stroke="{ACCENT}" stroke-width="1.5"/>',
        f'<line x1="120" y1="268" x2="440" y2="268" stroke="{ACCENT}" stroke-width="1.5"/>',
        arrow_v(120, 268, 292),
        arrow_v(440, 268, 292),
        box(20, 292, 200, 52, "PostgreSQL  Task State", size=13),
        box(340, 292, 200, 52, "Queue  Redis", size=13),
        arrow_v(440, 344, 372),
        box(340, 372, 200, 40, "Worker"),
        arrow_v(440, 412, 436),
        box(340, 436, 200, 36, "Atomic Claim", size=13),
        arrow_v(440, 472, 496),
        box(180, 496, 360, 44, "Agent Runtime"),
        arrow_v(260, 540, 564),
        arrow_v(460, 540, 564),
        box(160, 564, 200, 40, "LLM"),
        box(380, 564, 160, 40, "Tools"),
        arrow_v(260, 604, 628),
        arrow_v(460, 604, 628),
        box(160, 628, 200, 40, "Checkpoint", size=13),
        box(380, 628, 160, 40, "Tool Ledger", size=13),
        f'<line x1="260" y1="668" x2="260" y2="688" stroke="{ACCENT}" stroke-width="1.5"/>',
        f'<line x1="460" y1="668" x2="460" y2="688" stroke="{ACCENT}" stroke-width="1.5"/>',
        f'<line x1="260" y1="688" x2="460" y2="688" stroke="{ACCENT}" stroke-width="1.5"/>',
        arrow_v(360, 688, 712),
        box(190, 712, 180, 36, "Event Outbox", size=13),
        arrow_v(280, 748, 772),
        box(190, 772, 180, 36, "SSE", size=13),
        arrow_v(280, 808, 832),
        box(190, 832, 180, 36, "Client"),
    ]
    return "\n  ".join(parts)


def full_model() -> str:
    steps = [
        "Request",
        "Idempotency",
        "Durable Task",
        "Queue",
        "Worker",
        "Atomic Claim",
        "Agent Runtime",
    ]
    parts = []
    y = 20
    x, w, h, gap = 70, 220, 38, 22
    cx = x + w / 2
    for i, label in enumerate(steps):
        parts.append(box(x, y, w, h, label))
        if i < len(steps) - 1:
            parts.append(arrow_v(cx, y + h, y + h + gap))
        y += h + gap
    y += 4
    parts.append(box(24, y, 100, 36, "Checkpoint", size=12))
    parts.append(box(130, y, 100, 36, "Tool Ledger", size=12))
    parts.append(box(236, y, 100, 36, "Event Outbox", size=12))
    y += 36
    parts.append(arrow_v(286, y, y + 22))
    y += 22
    parts.append(box(x, y, w, h, "SSE"))
    parts.append(arrow_v(cx, y + h, y + h + gap))
    y += h + gap
    parts.append(box(x, y, w, h, "Client"))
    return "\n  ".join(parts)


if __name__ == "__main__":
    main()
    print("wrote", len(list(OUT.glob("*.svg"))), "svgs")
