"""Study guidance follows the selected task without becoming research evidence."""
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agents
import serve


class LearningContext(unittest.TestCase):
    def setUp(self):
        self.ctx = serve.Ctx('study-fixture')
        self.task = {'task_id': 'T1', 'week': 2, 'workspace_slot': 'model', 'deliverable': '复算'}
        self.project = {'local_team': {'enabled': True, 'seats': [{'id': 'model', 'title': '算法'}]}}
        self.curriculum = {'roles': {'model': [
            {'project_week': 1, 'week': 6, 'theme': '不相关旧周', 'learning_links': ['other']},
            {'project_week': 2, 'week': 7, 'theme': '评估', 'expected': '真实日志', 'learning_links': ['metrics']}]},
            'external_resources': [
                {'id': 'metrics', 'title': '中文评估', 'url': 'https://example.org/zh/metrics', 'language': 'zh-CN'},
                {'id': 'other', 'title': '其他岗位', 'url': 'https://example.org/other'}]}
        for target, kwargs in [('serve.P.is_local', {'return_value': True}),
                               ('serve.P.load', {'return_value': self.project}),
                               ('serve.LT.curriculum', {'return_value': self.curriculum})]:
            p = patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def test_selected_role_and_week_only(self):
        result = self.ctx.learning_context(self.task)
        self.assertEqual(result['lesson']['week'], 7)
        self.assertEqual([r['id'] for r in result['resources']], ['metrics'])
        self.assertIn('不是科研证据', result['usage'])
        self.assertEqual(self.ctx.learning_context({**self.task, 'week': 99}), {})
        self.assertEqual(self.ctx.learning_context({**self.task, 'workspace_slot': 'other'}), {})

    def test_generic_and_cloud_contexts_remain_isolated(self):
        with patch('serve.P.is_local', return_value=False):
            self.assertEqual(self.ctx.learning_context(self.task), {})
        with patch.dict(os.environ, {'HELIX_CLOUD_MODE': '1'}):
            self.assertEqual(self.ctx.learning_context(self.task), {})

    def test_model_prompts_receive_the_same_study_guidance(self):
        with patch.object(self.ctx, 'task', return_value=self.task), \
             patch.object(self.ctx, 'ceiling', return_value='EXPLORATORY_ONLY'), \
             patch.object(self.ctx, 'red_lines', return_value=[]):
            runtime = agents.Runtime(self.ctx)
            out = {'plan': {'questions': []}, 'extract': {'cards': []}, 'draft': {'bases': []}}
            for step in ('plan', 'draft', 'critique'):
                messages, _ = runtime._messages({'task_id': 'T1'}, step, out)
                self.assertIn('https://example.org/zh/metrics', messages[1]['content'])
                self.assertNotIn('https://example.org/other', messages[1]['content'])
                self.assertIn('不能编造已运行结果', messages[1]['content'])

    def test_generic_runtime_without_learning_hook_is_unchanged(self):
        ctx = SimpleNamespace(task=lambda _: self.task)
        result = json.loads(agents.Runtime(ctx)._task_text({'task_id': 'T1'}))
        self.assertNotIn('learning_support', result)
        self.assertEqual(result['task_id'], 'T1')


if __name__ == '__main__':
    unittest.main()
