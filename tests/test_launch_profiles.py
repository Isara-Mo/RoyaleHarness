"""Keep input experiments opt-in across command-line and menu entry points."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import main as agent_main
from tools import launch_menu


class LaunchProfileTests(unittest.TestCase):
    def test_cli_default_is_reference_without_origin_chains(self):
        args = agent_main.parse_args([])
        self.assertEqual(args.observation_profile, 'reference')
        self.assertFalse(args.experimental_origins)

    def test_invalid_origins_rejected_before_agent_construction(self):
        for argv in (['--experimental-origins'],
                     ['--observation-profile', 'reference', '--experimental-origins']):
            with self.subTest(argv=argv), patch.object(agent_main, 'CustomCardDeployAgent') as agent:
                with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as exc:
                    agent_main.main(argv)
                self.assertEqual(exc.exception.code, 2)
                self.assertIn('requires --observation-profile extended', errors.getvalue())
                agent.assert_not_called()

    def test_extended_does_not_implicitly_enable_origins(self):
        args = agent_main.parse_args(['--observation-profile', 'extended'])
        self.assertFalse(args.experimental_origins)

    def test_cli_forwards_profile_and_existing_runtime_controls(self):
        with patch.object(agent_main, 'CustomCardDeployAgent') as agent:
            agent_main.main(['--observation-profile', 'extended', '--experimental-origins',
                             '--checkpoint', 'general', '--base-only', '--dry-run', '--once',
                             '--oracle-elixir', '--sample', '--seconds', '12.5'])
        kwargs = agent.call_args.kwargs
        self.assertEqual(kwargs['observation_profile'], 'extended')
        self.assertTrue(kwargs['experimental_origins'])
        self.assertTrue(kwargs['dry_run'])
        self.assertTrue(kwargs['oracle_elixir'])
        self.assertTrue(kwargs['sample'])
        self.assertIs(kwargs['hero_musketeer'], False)
        self.assertEqual(kwargs['checkpoint_path'], agent_main.config.CHECKPOINTS['general'])
        agent.return_value.run.assert_called_once_with(12.5, True, False)

    def test_agent_constructor_passes_and_logs_input_settings(self):
        for profile, origins in (('reference', False), ('extended', False), ('extended', True)):
            with self.subTest(profile=profile, origins=origins), tempfile.TemporaryDirectory() as tmp:
                log_path = Path(tmp) / 'startup.jsonl'
                with patch.object(agent_main, 'ProbeClient'), \
                     patch.object(agent_main, 'FeatureAdapter') as adapter, \
                     patch.object(agent_main, 'Actuator'), \
                     patch.object(agent_main, 'ActionExecutor'), \
                     patch.object(agent_main, 'PolicyEngine'), \
                     contextlib.redirect_stdout(io.StringIO()):
                    adapter.return_value.observation_profile = profile
                    adapter.return_value.experimental_origins = origins
                    agent = agent_main.CustomCardDeployAgent(log_path=log_path,
                        observation_profile=profile, experimental_origins=origins)
                    agent.log('snapshot', tick=90, raw={})
                agent.stream.close()
                self.assertEqual(adapter.call_args.kwargs['observation_profile'], profile)
                self.assertIs(adapter.call_args.kwargs['experimental_origins'], origins)
                startup, snapshot = (json.loads(line) for line in log_path.read_text(encoding='utf-8').splitlines())
                self.assertEqual(startup['event'], 'model_loading')
                self.assertEqual(startup['observation_profile'], profile)
                self.assertIs(startup['experimental_origins'], origins)
                self.assertEqual(snapshot['event'], 'snapshot')
                self.assertEqual(snapshot['observation_profile'], profile)
                self.assertIs(snapshot['experimental_origins'], origins)

    def test_all_menu_combinations_parse_without_enabling_origins(self):
        for mode in launch_menu.RUN_MODES:
            for model in launch_menu.MODELS:
                for forms in launch_menu.FORMS:
                    for profile in launch_menu.OBSERVATION_PROFILES:
                        with self.subTest(mode=mode, model=model, forms=forms, profile=profile):
                            command = launch_menu.build_command(mode, model, forms, profile)
                            args = agent_main.parse_args(command[3:])
                            self.assertEqual(args.observation_profile,
                                             launch_menu.OBSERVATION_PROFILES[profile][1])
                            self.assertFalse(args.experimental_origins)
                            self.assertEqual(args.checkpoint, launch_menu.MODELS[model][1])
                            self.assertEqual(args.dry_run, mode in ('3', '4'))
                            self.assertEqual(args.once, mode in ('2', '4'))
                            self.assertIs(args.hero_musketeer, False if forms == '2' else None)

    def test_four_enter_preview_uses_reference_without_starting_agent(self):
        with patch('sys.argv', ['launch_menu.py', '--preview']), \
             patch('builtins.input', side_effect=['', '', '', '']) as prompts, \
             patch.object(launch_menu.subprocess, 'call') as start, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(launch_menu.main(), 0)
        self.assertEqual(prompts.call_count, 4)
        self.assertIn('--observation-profile reference', output.getvalue())
        self.assertNotIn('--experimental-origins', output.getvalue())
        start.assert_not_called()

    def test_extended_menu_preview_is_labelled_experimental(self):
        with patch('sys.argv', ['launch_menu.py', '--preview']), \
             patch('builtins.input', side_effect=['4', '3', '1', '2']), \
             patch.object(launch_menu.subprocess, 'call') as start, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(launch_menu.main(), 0)
        self.assertIn('精确事件扩展（待效果验证）', output.getvalue())
        self.assertIn('--observation-profile extended', output.getvalue())
        self.assertNotIn('--experimental-origins', output.getvalue())
        start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
