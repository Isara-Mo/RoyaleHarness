"""Offline regression suite; --model additionally checks a real checkpoint.
This command never opens an ADB shell or deploys a card.
"""
import sys
import unittest


def main():
    suite = unittest.defaultTestLoader.discover('tests')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    if '--model' in sys.argv:
        from tests.test_pipeline import state
        from agent.feature_adapter import FeatureAdapter
        from agent.policy_engine import PolicyEngine
        from bridge.coordinates import ScreenCalibration
        engine = PolicyEngine()
        for owner in (0, 1):
            s = state(owner)
            adapter = FeatureAdapter()
            adapter.reset_match(s, f'offline-owner-{owner}')
            batch, obs = adapter.tensorize(s)
            engine.warmup(batch)
            engine.reset()
            for step in range(3):
                s.tick = 90 + step * 5
                batch, obs = adapter.tensorize(s)
                decoded, ms = engine.decide(batch, obs, adapter)
                for action in decoded.actions:
                    if action.target_grid is not None:
                        print('target', ScreenCalibration().action_to_screen(action))
                print(f'owner={owner} tick={s.tick} inference={ms:.1f}ms actions={[a.kind.value for a in decoded.actions]}')


if __name__ == '__main__':
    main()
