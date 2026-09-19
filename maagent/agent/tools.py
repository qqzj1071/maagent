from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from maagent.agent.memory import AgentMemory


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def update(self, other: "ToolRegistry") -> None:
        self._tools.update(other._tools)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"未知工具：{name}"
        try:
            result = tool.func(**arguments)
        except Exception as e:
            return f"工具 {name} 执行失败：{e}"
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)


def build_memory_tools(memory: AgentMemory) -> ToolRegistry:
    registry = ToolRegistry()

    def remember(content: str, category: str = "general") -> str:
        item = memory.add_fact(content, category=category)
        return f"已记住 [{item['id']}]：{item['content']}"

    def learn_skill(name: str, description: str, steps: list[str] | None = None) -> str:
        item = memory.add_skill(name, description, steps)
        return f"已学会技能 [{item['id']}] {item['name']}"

    def recall(query: str) -> str:
        hits = memory.search(query)
        if not hits:
            return "没有找到相关记忆。"
        return "\n".join(json.dumps(h, ensure_ascii=False) for h in hits)

    def forget(mem_id: str) -> str:
        return "已删除。" if memory.remove(mem_id) else f"未找到记忆 {mem_id}。"

    def list_memory() -> str:
        items = memory.all_items()
        if not items:
            return "记忆为空。"
        return "\n".join(json.dumps(i, ensure_ascii=False) for i in items)

    registry.register(Tool(
        name="remember",
        description="记住一条关于用户的事实、偏好或长期有效的指令。用户说“记住…”或透露偏好时调用。",
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要记住的内容"},
                "category": {
                    "type": "string",
                    "description": "分类，如 preference / fact / rule / game",
                    "default": "general",
                },
            },
            "required": ["content"],
        },
        func=remember,
    ))
    registry.register(Tool(
        name="learn_skill",
        description="学习一个可复用的技能/操作流程，供以后执行类似任务时参考。",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名"},
                "description": {"type": "string", "description": "技能说明"},
                "steps": {"type": "array", "items": {"type": "string"}, "description": "步骤列表"},
            },
            "required": ["name", "description"],
        },
        func=learn_skill,
    ))
    registry.register(Tool(
        name="recall",
        description="按关键词检索长期记忆，返回相关的事实或技能。",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "检索关键词"}},
            "required": ["query"],
        },
        func=recall,
    ))
    registry.register(Tool(
        name="forget",
        description="删除一条长期记忆（按 id）。",
        parameters={
            "type": "object",
            "properties": {"mem_id": {"type": "string", "description": "记忆 id"}},
            "required": ["mem_id"],
        },
        func=forget,
    ))
    registry.register(Tool(
        name="list_memory",
        description="列出全部长期记忆。",
        parameters={"type": "object", "properties": {}},
        func=list_memory,
    ))
    return registry


def build_knowledge_tools(kb: Any) -> ToolRegistry:
    registry = ToolRegistry()

    def search_knowledge(query: str, limit: int = 4) -> str:
        hits = kb.search(query, limit=int(limit))
        if not hits:
            return "知识库中没有找到相关内容。"
        return "\n\n".join(
            f"【{h.get('title')}】\n{h.get('text')}\n（来源：{h.get('source')}）" for h in hits
        )

    registry.register(Tool(
        name="search_knowledge",
        description=(
            "检索《明日方舟》PRTS 知识库（新人入门、理智、基建、公开招募等）。"
            "回答游戏机制、术语、玩法相关问题时先检索，不要凭空编造。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词"},
                "limit": {"type": "integer", "description": "返回片段数，默认 4"},
            },
            "required": ["query"],
        },
        func=search_knowledge,
    ))
    return registry


def build_game_tools(service: Any) -> ToolRegistry:
    registry = ToolRegistry()

    def open_emulator() -> str:
        return "模拟器已启动。" if service.start_emulator() else "启动模拟器失败。"

    def open_arknights() -> str:
        return service.open_arknights()

    def screenshot() -> str:
        from maagent.control.popup import recognize

        image = service.screenshot()
        items = recognize(image)
        texts = " | ".join(i.text for i in items)[:400]
        return f"已截图 {image.size[0]}x{image.size[1]}。屏幕文字：{texts or '（无）'}"

    def analyze_screen(question: str) -> str:
        return service.analyze_screen(question)

    def find_and_click(text: str) -> str:
        return f"已点击「{text}」。" if service.find_and_click(text) else f"屏幕上没有找到「{text}」。"

    def click(x: int, y: int) -> str:
        service.click(int(x), int(y))
        return f"已点击 ({int(x)}, {int(y)})。"

    def swipe(x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> str:
        service.swipe(int(x1), int(y1), int(x2), int(y2), int(duration))
        return f"已滑动 ({x1},{y1})→({x2},{y2})。"

    def press_key(key: int) -> str:
        service.press_key(int(key))
        return f"已按键 {key}。"

    registry.register(Tool(
        name="open_emulator",
        description="启动 MuMu 安卓模拟器（若未运行）。",
        parameters={"type": "object", "properties": {}},
        func=open_emulator,
    ))
    registry.register(Tool(
        name="open_arknights",
        description=(
            "打开《明日方舟》：自动启动模拟器、截图识别桌面上的「明日方舟」图标并点击，"
            "确认游戏进入前台。用户想开始游戏或做日常时调用。"
        ),
        parameters={"type": "object", "properties": {}},
        func=open_arknights,
    ))
    registry.register(Tool(
        name="screenshot",
        description="截取模拟器当前画面并返回屏幕上的文字（OCR）。想了解当前画面时使用。",
        parameters={"type": "object", "properties": {}},
        func=screenshot,
    ))
    registry.register(Tool(
        name="analyze_screen",
        description="用视觉模型看当前模拟器画面并回答一个问题（如“现在是什么界面”“这个按钮在哪”）。",
        parameters={
            "type": "object",
            "properties": {"question": {"type": "string", "description": "关于当前画面的问题"}},
            "required": ["question"],
        },
        func=analyze_screen,
    ))
    registry.register(Tool(
        name="find_and_click",
        description="在当前画面上按文字定位并点击（如「开始行动」「基建」）。",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要点击的文字"}},
            "required": ["text"],
        },
        func=find_and_click,
    ))
    registry.register(Tool(
        name="click",
        description="在模拟器画面指定坐标点击。",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["x", "y"],
        },
        func=click,
    ))
    registry.register(Tool(
        name="swipe",
        description="在模拟器画面上滑动。",
        parameters={
            "type": "object",
            "properties": {
                "x1": {"type": "integer"},
                "y1": {"type": "integer"},
                "x2": {"type": "integer"},
                "y2": {"type": "integer"},
                "duration": {"type": "integer"},
            },
            "required": ["x1", "y1", "x2", "y2"],
        },
        func=swipe,
    ))
    registry.register(Tool(
        name="press_key",
        description="向模拟器发送安卓按键（如 4=返回，3=Home）。",
        parameters={
            "type": "object",
            "properties": {"key": {"type": "integer", "description": "安卓 keycode"}},
            "required": ["key"],
        },
        func=press_key,
    ))
    return registry


def build_search_tools(search_cfg: dict[str, Any] | None = None) -> ToolRegistry:
    from maagent.agent.search import search

    cfg = search_cfg or {}
    default_count = int(cfg.get("count", 5))
    backend = cfg.get("backend", "auto")
    proxy = cfg.get("proxy") or None
    timeout = float(cfg.get("timeout", 15))
    registry = ToolRegistry()

    def web_search(query: str, count: int | None = None) -> str:
        results = search(
            query,
            count=int(count) if count else default_count,
            backend=backend,
            proxy=proxy,
            timeout=timeout,
        )
        if not results:
            return "没有搜索到结果，或当前网络不可用。"
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.title}\n   {r.url}\n   {r.snippet}")
        return "\n".join(lines)

    registry.register(Tool(
        name="web_search",
        description=(
            "联网搜索最新信息（游戏公告、活动时间、攻略、新闻等）。"
            "当问题涉及时效性或你不确定的事实时调用，并在回答中标注来源。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "count": {"type": "integer", "description": "返回结果条数，默认 5"},
            },
            "required": ["query"],
        },
        func=web_search,
    ))
    return registry
