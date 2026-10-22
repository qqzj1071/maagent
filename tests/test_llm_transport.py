import unittest
from unittest.mock import Mock,patch
from maagent.agent.llm import OpenAICompatLLM, LLMError

class LLMTransportTests(unittest.TestCase):
 def test_final_failed_attempt_does_not_sleep(self):
  client=OpenAICompatLLM('https://example.invalid','test','test',max_retries=2)
  client.session=Mock()
  client.session.post.return_value=Mock(status_code=503,text='busy',headers={})
  with patch('maagent.agent.llm.time.sleep') as sleep:
   with self.assertRaises(LLMError): client._post({})
  self.assertEqual(client.session.post.call_count,2)
  sleep.assert_called_once()

 def test_two_requests_reuse_session(self):
  client=OpenAICompatLLM('https://example.invalid','test','test')
  client.session=Mock()
  client.session.post.return_value=Mock(status_code=200)
  client.session.post.return_value.json.return_value={'choices':[{'message':{'content':'ok'}}]}
  self.assertEqual(client.chat([]).content,'ok')
  self.assertEqual(client.chat([]).content,'ok')
  self.assertEqual(client.session.post.call_count,2)
