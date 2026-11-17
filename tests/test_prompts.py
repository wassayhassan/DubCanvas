import unittest
from types import SimpleNamespace

from anime_dubber.prompts import render_prompt


class PromptTests(unittest.TestCase):
    def test_plain_and_current_and_legacy_tokenizers(self):
        self.assertEqual(render_prompt(SimpleNamespace(), 'hello'), 'hello')

        def current(messages, *, add_generation_prompt, enable_thinking):
            self.assertTrue(add_generation_prompt)
            self.assertFalse(enable_thinking)
            return messages[0]['content']

        def legacy(messages, *, add_generation_prompt):
            self.assertTrue(add_generation_prompt)
            return messages[0]['content']

        for apply in (current, legacy):
            with self.subTest(tokenizer=apply.__name__):
                tokenizer = SimpleNamespace(chat_template='chat', apply_chat_template=apply)
                self.assertEqual(render_prompt(tokenizer, 'hello'), 'hello')
