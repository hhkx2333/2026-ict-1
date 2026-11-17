import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from backend.grading.providers import ChatCompletionsProvider, load_local_config
from backend.grading.core import align_unique_quotes, ValidationError


class LocalConfigTests(unittest.TestCase):
    def test_alignment_changes_only_unique_exact_quote_coordinates(self):
        answer={'text':'甲乙丙乙'}
        raw=json.dumps({'items':[{'criterion_id':'R1','evidence':[
            {'quote':'甲乙','start':9,'end':11},
            {'quote':'乙','start':9,'end':10},
            {'quote':'虚构','start':0,'end':2}]}]})
        aligned,repairs=align_unique_quotes(raw,answer)
        spans=json.loads(aligned)['items'][0]['evidence']
        self.assertEqual(spans[0],{'quote':'甲乙','start':0,'end':2})
        self.assertEqual(spans[1]['start'],9)
        self.assertEqual(spans[2]['quote'],'虚构')
        self.assertEqual(len(repairs),1)

    def test_local_settings_never_execute_shell_or_override_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'.env'
            path.write_text('GRADING_MODEL=from-file\nGRADING_API_KEY="fake-test-key"\nUNRELATED=ignore\n')
            with patch.dict('os.environ', {'GRADING_MODEL':'from-process'}, clear=True):
                values = load_local_config(path)
                self.assertEqual(values['GRADING_MODEL'], 'from-process')
                self.assertEqual(values['GRADING_API_KEY'], 'fake-test-key')
                self.assertNotIn('UNRELATED', values)

    def test_deepseek_limits_thinking_and_token_usage(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value=json.dumps({
            'choices':[{'message':{'content':'{"ok":true}'}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5}}).encode()
        with patch('backend.grading.providers.urlopen',return_value=response) as request:
            provider=ChatCompletionsProvider('https://api.deepseek.com/chat/completions','deepseek-flash',
                                             'fake-key',max_tokens=2048,thinking='disabled')
            provider.complete('Return JSON',{})
            body=json.loads(request.call_args.args[0].data)
            self.assertEqual(body['thinking'],{'type':'disabled'})
            self.assertEqual(body['max_tokens'],2048)
            self.assertEqual(provider.last_usage['completion_tokens'],5)
        with self.assertRaises(ValueError):
            ChatCompletionsProvider('https://example.invalid','model','key',thinking='disabled')
