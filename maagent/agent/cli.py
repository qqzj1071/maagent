from __future__ import annotations

import sys
from typing import Any

from maagent.agent.agent import Agent

BANNER = """{name} 对话模式
  直接输入说话；:tools 查看技能，:memory 查看记忆，:reset 清空当前对话，:quit 退出。"""


def run_chat(config: dict[str, Any]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        agent = Agent(config)
    except RuntimeError as e:
        print(f"无法启动：{e}")
        return 1

    cfg = config.get("agent") or {}
    name = agent.name
    print(BANNER.format(name=name))
    print(f"模型：{cfg.get('model', 'glm-4.5-flash')}（视觉：{cfg.get('vision_model', 'glm-4.1v-thinking-flash')}）\n")

    while True:
        try:
            line = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in (":quit", ":q", ":exit", "退出"):
            break
        if line == ":reset":
            agent.reset()
            print("（已清空当前对话）")
            continue
        if line in (":memory", ":mem"):
            print(agent.memory.pretty())
            continue
        if line == ":tools":
            print("、".join(agent.tools.names()))
            continue

        print(f"{name}> 思考中……", flush=True)
        try:
            reply = agent.chat(
                line,
                on_event=lambda kind, payload: print(f"  [工具] {payload['name']} -> {payload['result']}"),
            )
        except Exception as e:
            print(f"（出错：{e}）")
            continue
        print(f"{name}> {reply}\n")

    return 0
