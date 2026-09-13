"""Check replay isolation and scheduling without model or device operations."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_pipeline import opening
from tools.compare_observation_profiles import FeatureAdapter, checked_epoch, compare


def snapshot(tick, epoch=2):
    raw = opening()
    raw['schema'] = 'nulls-live.v3'
    raw['tick'] = tick
    raw['impact_runtime'] = dict(schema='nulls-impact.v2', epoch=epoch, tick=tick,
                                hooks_ready=True, complete=True, window_ticks=10, events=[])
    return raw


class ProfileComparisonTests(unittest.TestCase):
    def test_epoch_requires_known_schema_and_consistent_envelopes(self):
        base = snapshot(90)
        self.assertEqual(checked_epoch(base), (2, ('impact_runtime',)))
        variants = []
        raw = copy.deepcopy(base); raw['schema'] = 'unknown'; variants.append(raw)
        raw = copy.deepcopy(base); raw['impact_runtime']['schema'] = 'nulls-impact.v99'; variants.append(raw)
        raw = copy.deepcopy(base); raw['impact_runtime']['epoch'] = True; variants.append(raw)
        raw = copy.deepcopy(base); raw.pop('impact_runtime'); variants.append(raw)
        raw = copy.deepcopy(base)
        raw['heal_runtime'] = {'schema': 'nulls-heal.v2', 'epoch': 3}
        variants.append(raw)
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                checked_epoch(raw)

    def test_sparse_ticks_use_five_tick_spacing_without_socket(self):
        rows = [snapshot(80, epoch=1)]
        rows.extend(snapshot(tick) for tick in (90, 91, 94, 96, 100, 102))
        final = snapshot(106)
        final['battle_result'] = {'validated': True, 'finalized': True, 'world_result_raw': 0}
        rows.append(final)
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / 'capture.jsonl'
            capture.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
            with patch('config.LOCAL_ACCOUNT_ID', 123), patch('socket.create_connection') as connection, \
                 patch.object(FeatureAdapter, 'build_observation', autospec=True,
                              side_effect=FeatureAdapter.build_observation) as build:
                report = compare(capture, epoch=2)
            connection.assert_not_called()
            # Per profile: one reset baseline plus three policy observations.
            self.assertEqual(build.call_count, 8)
            self.assertEqual(report['source_sha256'], hashlib.sha256(capture.read_bytes()).hexdigest())
        self.assertTrue(report['comparison_valid'], report['errors'])
        self.assertEqual([decision['tick'] for decision in report['decisions']], [90, 96, 102])
        self.assertTrue(report['finalized_observed'])
        self.assertFalse(report['partial'])
        self.assertFalse(report['input_sent'])
        self.assertIsNone(report['action_difference_rate'])
        self.assertEqual(report['counts']['other_epoch_rows'], 1)
        for profile in report['profiles'].values():
            self.assertEqual(profile['counts']['observed_frames'], 7)
            self.assertEqual(profile['counts']['tensor_frames'], 3)
            self.assertEqual(profile['counts']['model_decisions'], 0)

    def test_unknown_epoch_schema_is_reported_without_partial_history_continuation(self):
        first = snapshot(90)
        first['impact_runtime']['schema'] = 'nulls-impact.unknown'
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / 'capture.jsonl'
            capture.write_text(json.dumps(first) + '\n' + json.dumps(snapshot(95)) + '\n', encoding='utf-8')
            report = compare(capture, epoch=2)
        self.assertFalse(report['comparison_valid'])
        self.assertTrue(report['partial'])
        self.assertEqual(report['counts']['errors'], 1)
        self.assertEqual(report['counts']['lines_read'], 1)
        self.assertIn('unsupported impact_runtime schema', report['errors'][0]['error'])


if __name__ == '__main__':
    unittest.main()
