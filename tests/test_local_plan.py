import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "builder"))

from local_plan import preview, validate_plan


def sample_plan():
    return {
        'title': 'Pocket weather',
        'goal': 'Show the forecast offline-friendly on the GPi',
        'user_flow': ['Open the app', 'Choose a saved location'],
        'screens': [{
            'name': 'Forecast',
            'purpose': 'Show current and next-day weather',
            'states': {'normal': 'Show temperatures', 'empty': 'Ask to pick a location',
                       'loading': 'Show a spinner', 'error': 'Show the last saved forecast'},
            'controls': {'up': 'Previous day', 'down': 'Next day', 'left': 'Previous location',
                         'right': 'Next location', 'A': 'Open details', 'B': 'Back to launcher',
                         'X': 'Refresh', 'Y': 'Toggle units', 'Start': 'Open app menu'},
        }],
        'data_sources': [{'name': 'Bundled/on-device data', 'endpoint': 'none (offline-only)',
                          'params': 'none', 'timeout_seconds': 0,
                          'network_failure_behavior': 'No network call is made.'}],
        'input_constraints': 'Choose from saved locations only',
        'out_of_scope': ['Adding arbitrary locations'],
        'icon_art_direction': 'A compact sun behind a cloud in warm pixel colors',
        'acceptance_checks': ['The user can move between saved locations with left and right',
                              'The user can toggle temperature units',
                              'The user can return to the launcher with B'],
        'open_questions': [],
    }


class PlanContractTests(unittest.TestCase):
    def test_valid_plan_has_exact_preview_and_audio_transcript(self):
        transcript = 'Make a pocket weather app.'
        plan = validate_plan(sample_plan(), transcript)
        plan['preview'] = preview(plan)
        self.assertEqual(plan['idea_transcript'], transcript)
        self.assertEqual(plan['schema_version'], '1.0')
        self.assertEqual(plan['preview'].count('.'), 3)

    def test_context_transcript_is_preserved_for_add_detail(self):
        context = json.dumps({'idea_transcript': 'Build a weather viewer.'})
        plan = validate_plan(sample_plan(), 'Add Fahrenheit toggle.', context)
        self.assertEqual(plan['idea_transcript'],
                         'Build a weather viewer.\nAdditional request: Add Fahrenheit toggle.')

    def test_missing_button_mapping_is_rejected(self):
        plan = sample_plan()
        del plan['screens'][0]['controls']['B']
        with self.assertRaisesRegex(RuntimeError, 'all button actions'):
            validate_plan(plan, 'Make a weather app.')

    def test_acceptance_checks_must_be_testable_user_actions(self):
        plan = sample_plan()
        plan['acceptance_checks'][0] = 'It feels polished'
        with self.assertRaisesRegex(RuntimeError, 'begin'):
            validate_plan(plan, 'Make a weather app.')


if __name__ == '__main__':
    unittest.main()
