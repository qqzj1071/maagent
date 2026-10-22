from __future__ import annotations

import sys
import json
import shlex
from typing import Any

from maagent.agent.agent import Agent

BANNER = """{name} 对话模式
  直接输入说话；:tools 查看技能，:memory 查看记忆，:reset 清空当前对话，:quit 退出。
  :prts 干员 芬 / :prts 敌人 源石虫；:prts 干员 --all 全量更新（可 Ctrl+C 中断）。"""


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
        if line.startswith(":prts "):
            try:
                parts = shlex.split(line)
                kind = {"干员": "operator", "敌人": "enemy", "operator": "operator", "enemy": "enemy"}.get(parts[1])
                if not kind or len(parts) < 3:
                    raise ValueError("用法：:prts 干员 芬；:prts 敌人 源石虫；:prts 干员 --all")
                from maagent.agent.prts import PrtsUpdater
                from maagent.agent.storage import read_json, write_json
                from pathlib import Path
                updater = PrtsUpdater(agent.knowledge)
                state_path = Path(cfg["data_dir"]) / ("prts_progress_" + kind + ".json")
                all_pages = parts[2] in ("--all", "--resume")
                state = read_json(state_path, {}) if parts[2] == "--resume" else {}
                cursor = state.get("cursor", "")
                if state.get("enumeration_complete"):
                    print("上一轮分类遍历已完成；失败页请按名称重试，重新全量更新请用 --all。")
                    continue
                while True:
                    result = updater.update(kind, None if all_pages else parts[2:], cursor)
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    if not all_pages:
                        break
                    state.setdefault("failed", []).extend(result["failed"])
                    state.update(cursor=result["cursor"], enumeration_complete=result["enumeration_complete"])
                    write_json(state_path, state)
                    if result["enumeration_complete"]:
                        print(f"分类遍历完成；本轮累计失败 {len(state['failed'])} 页，失败项已记录。")
                        break
                    cursor = result["cursor"]
            except KeyboardInterrupt:
                print("已中断；已完成批次保留，可用 :prts 干员 --resume 或 :prts 敌人 --resume 续传。")
            except Exception as e:
                print(f"PRTS 更新失败，旧资料保留：{e}")
            continue
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
            print(f"（网络好像不太稳定，博士稍等一下再问我一次吧。技术细节：{e}）")
            continue
        print(f"{name}> {reply}\n")

    return 0
