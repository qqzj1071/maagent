import unittest
from unittest.mock import Mock, patch
from maagent.agent.agent import Agent
from maagent.agent.llm import LLMReply, ToolCall
from maagent.agent.budget import remaining


class BudgetTests(unittest.TestCase):
    def agent(self):
        agent = Agent.__new__(Agent)
        agent.messages = []
        agent.max_steps = 4
        agent.task_timeout_seconds = 300
        agent.max_tool_calls = 1
        agent.repeat_limited = set()
        agent._build_messages = Mock(return_value=[])
        agent.llm = Mock()
        agent.tools = Mock()
        agent.tools.schemas.return_value = []
        agent.tools.call.return_value = '固源岩：3765'
        return agent

    def test_tool_batch_limit_and_preserved_results(self):
        agent = self.agent()
        agent.llm.chat.return_value = LLMReply(tool_calls=[ToolCall(str(i), 'query', {}) for i in range(6)])
        result = agent.chat('查库存')
        self.assertEqual(agent.tools.call.call_count, 4)
        self.assertEqual(agent.llm.chat.call_count, 1)
        self.assertIn('3765', result)
        self.assertIn('指导', result)
        self.assertEqual(len([m for m in agent.messages if m['role'] == 'tool']), 6)

    def test_expired_turn_does_not_execute_tools_and_resets_context(self):
        agent = self.agent()
        agent.llm.chat.return_value = LLMReply(tool_calls=[ToolCall('1', 'query', {})])
        with patch('maagent.agent.budget.time.monotonic', side_effect=[0, 1, 301]):
            result = agent.chat('查库存')
        agent.tools.call.assert_not_called()
        self.assertIn('执行时限', result)
        self.assertEqual(remaining(12), 12)
