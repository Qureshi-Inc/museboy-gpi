#!/usr/bin/env python3
"""Offline speech-to-build-brief worker used by the GPi App Builder."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path('/opt/gpi/local-ai')
WHISPER = ROOT / 'bin/whisper-cli'
WHISPER_MODEL = ROOT / 'models/ggml-tiny.en.bin'
LLM_URL = 'http://127.0.0.1:8089/v1/chat/completions'
MODELS_URL = 'http://127.0.0.1:8089/v1/models'
PROGRESS_PROTOCOL = {
    'stage_values': ['queued', 'building', 'testing', 'installing', 'done', 'error'],
    'milestones': [
        {'progress': 0, 'completed_when': 'Request received; work has not started.'},
        {'progress': 15, 'completed_when': 'Existing app and target files inspected.'},
        {'progress': 30, 'completed_when': 'Implementation approach and checks are defined.'},
        {'progress': 60, 'completed_when': 'Requested app changes are implemented.'},
        {'progress': 80, 'completed_when': 'Relevant checks have actually passed.'},
        {'progress': 95, 'completed_when': 'App is installed or staged on the GPi.'},
        {'progress': 100, 'completed_when': 'App launched and acceptance checks verified.'},
    ],
    'rules': [
        'Write status.json after each completed milestone; updated is Unix epoch seconds.',
        'Only report a percentage at a completed milestone; never estimate by elapsed time.',
        'If percentage is unknown, omit progress so the UI shows indeterminate activity.',
        'Use current_step and message for the concrete action just completed or underway.',
        'Set stage to testing only while checks run, and installing only during real installation.',
        'Never claim done until the app starts and the acceptance checks pass on the target.',
    ],
}


def progress(path, step, message):
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps({'step': step, 'message': message}))
    os.replace(temporary, target)


def transcribe(audio_path):
    command = [str(WHISPER), '-m', str(WHISPER_MODEL), '-f', str(audio_path),
               '-t', '2', '-ac', '768', '-nt', '-np', '-l', 'en']
    result = subprocess.run(command, text=True, capture_output=True, timeout=90,
                            check=True, preexec_fn=lambda: os.nice(8))
    transcript = ' '.join(line.strip() for line in result.stdout.splitlines()
                          if line.strip())
    return ' '.join(transcript.split())[:1800]


PLAN_SCHEMA = {
    'type': 'object',
    'properties': {
        'schema_version': {'type': 'string'},
        'idea_transcript': {'type': 'string'},
        'title': {'type': 'string'},
        'goal': {'type': 'string'},
        'user_flow': {'type': 'array', 'maxItems': 5, 'items': {'type': 'string'}},
        'screens': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string'},
                'purpose': {'type': 'string'},
                'states': {'type': 'object', 'properties': {
                    'normal': {'type': 'string'}, 'empty': {'type': 'string'},
                    'loading': {'type': 'string'}, 'error': {'type': 'string'},
                }, 'required': ['normal', 'empty', 'loading', 'error']},
                'controls': {'type': 'object', 'properties': {
                    'up': {'type': 'string'}, 'down': {'type': 'string'},
                    'left': {'type': 'string'}, 'right': {'type': 'string'},
                    'A': {'type': 'string'}, 'B': {'type': 'string'},
                    'X': {'type': 'string'}, 'Y': {'type': 'string'},
                    'Start': {'type': 'string'},
                }, 'required': ['up', 'down', 'left', 'right', 'A', 'B', 'X', 'Y', 'Start']},
            }, 'required': ['name', 'purpose', 'states', 'controls'],
        }},
        'data_sources': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': {
            'type': 'object', 'properties': {
                'name': {'type': 'string'}, 'endpoint': {'type': 'string'},
                'params': {'type': 'string'}, 'timeout_seconds': {'type': 'integer'},
                'network_failure_behavior': {'type': 'string'},
            }, 'required': ['name', 'endpoint', 'params', 'timeout_seconds',
                           'network_failure_behavior'],
        }},
        'input_constraints': {'type': 'string'},
        'out_of_scope': {'type': 'array', 'maxItems': 6, 'items': {'type': 'string'}},
        'icon_art_direction': {'type': 'string'},
        'acceptance_checks': {'type': 'array', 'minItems': 3, 'maxItems': 5,
                              'items': {'type': 'string'}},
        'open_questions': {'type': 'array', 'maxItems': 6, 'items': {'type': 'string'}},
    },
    'required': ['schema_version', 'idea_transcript', 'title', 'goal', 'user_flow',
                 'screens', 'data_sources', 'input_constraints', 'out_of_scope',
                 'icon_art_direction', 'acceptance_checks', 'open_questions'],
}


def make_prompt(transcript, context):
    previous = f'Existing plan to update: {context}\n\n' if context else ''
    return (
        'Write the complete structured product contract Muse will implement. '
        'Target is a Retroflag GPi Case 2 (640x480, Raspberry Pi, physical '
        'D-pad and A/B/X/Y/Start). Return only one JSON object matching the '
        'provided schema. Every feature must describe observable, testable '
        'behavior. For every screen specify normal, empty, loading and error '
        'states, using "not applicable" with a reason when a state cannot occur. '
        'Map up/down/left/right and A/B/X/Y/Start on every screen; B always goes '
        'back. Limit the spec to one to four screens. Do not include free-text entry or an on-screen keyboard. Name each '
        'data source and exact endpoint, query parameters, timeout, and offline '
        'behavior. Never invent an endpoint: if the idea needs network data but '
        'does not name a verified source, mark it as a blocking open question and '
        'keep that feature out of scope until Muse verifies the source. For a '
        'fully offline idea, provide one data source named "Bundled/on-device '
        'data" with endpoint "none (offline-only)" and say no network call is '
        'made. Include 3 to 5 acceptance_checks, each beginning "The user can"; '
        'include out_of_scope, one-line icon_art_direction, and open_questions. '
        'Do not write code, claim implementation progress, or add features not '
        'requested. Keep every description short but specific.\n\n'
        + previous + 'Spoken idea: ' + transcript
    )


def validate_plan(plan, transcript, context=''):
    required = ('title', 'goal', 'user_flow', 'screens', 'data_sources',
                'input_constraints', 'out_of_scope', 'icon_art_direction',
                'acceptance_checks', 'open_questions')
    if not isinstance(plan, dict) or any(key not in plan for key in required):
        raise RuntimeError('Gemma plan is missing required contract fields')
    if not isinstance(plan['screens'], list) or not plan['screens']:
        raise RuntimeError('Gemma plan must define at least one screen')
    if not isinstance(plan['user_flow'], list) or not plan['user_flow']:
        raise RuntimeError('Gemma plan must describe at least one user flow')
    if not isinstance(plan['data_sources'], list) or not plan['data_sources']:
        raise RuntimeError('Gemma plan must name a data source or explicitly say offline-only')
    controls = {'up', 'down', 'left', 'right', 'A', 'B', 'X', 'Y', 'Start'}
    for screen in plan['screens']:
        if (not isinstance(screen, dict) or
                not controls.issubset(screen.get('controls', {})) or
                not {'normal', 'empty', 'loading', 'error'}.issubset(
                    screen.get('states', {}))):
            raise RuntimeError('Every screen needs all button actions and states')
        if any(not str(screen['controls'][key]).strip() for key in controls):
            raise RuntimeError('Every physical button needs a defined action')
        if any(not str(screen['states'][key]).strip()
               for key in ('normal', 'empty', 'loading', 'error')):
            raise RuntimeError('Every screen needs all four display states')
        if screen['controls']['B'].strip().lower() in ('none', 'no action', 'n/a'):
            raise RuntimeError('B must always have a back action')
    if not 3 <= len(plan['acceptance_checks']) <= 5:
        raise RuntimeError('Gemma plan must include 3–5 acceptance checks')
    if any(not str(item).strip().lower().startswith('the user can')
           for item in plan['acceptance_checks']):
        raise RuntimeError('Acceptance checks must begin "The user can"')
    for source in plan['data_sources']:
        if any(not str(source.get(key, '')).strip() for key in
               ('name', 'endpoint', 'params', 'network_failure_behavior')):
            raise RuntimeError('Every data source needs endpoint, params and offline behavior')
        try:
            timeout = int(source.get('timeout_seconds', -1))
        except (TypeError, ValueError) as exc:
            raise RuntimeError('Data source timeout must be a number of seconds') from exc
        if timeout < 0 or timeout > 60:
            raise RuntimeError('Data source timeout must be between 0 and 60 seconds')
    plan['schema_version'] = '1.0'
    try:
        previous_idea = json.loads(context).get('idea_transcript', '') if context else ''
    except (json.JSONDecodeError, AttributeError):
        previous_idea = ''
    plan['idea_transcript'] = (previous_idea + '\nAdditional request: ' + transcript
                               if previous_idea else transcript)
    return plan


def preview(plan):
    def clause(value):
        return re.sub(r'[.!?]+', ' ', str(value)).strip()
    flow = '; '.join(clause(item) for item in plan['user_flow'][:2])
    screens = ', '.join(clause(item['name']) for item in plan['screens'][:4])
    check = clause(plan['acceptance_checks'][0])
    return (f"{clause(plan['title'])}: {clause(plan['goal'])}. "
            f"Flow: {flow}; screens: {screens}. {check}.")[:560]


def active_model():
    request = Request(MODELS_URL, headers={'Accept': 'application/json'})
    with urlopen(request, timeout=5) as response:
        payload = json.loads(response.read())
    models = payload.get('data', [])
    if not models or not models[0].get('id'):
        raise RuntimeError('No local planning model is loaded; choose one in Settings')
    return models[0]['id']


def draft(transcript, context=''):
    model_id = active_model()
    body = {
        'model': model_id,
        'messages': [
            {'role': 'system', 'content': 'You are a careful product planner. Write only the requested concise build plan.'},
            {'role': 'user', 'content': make_prompt(transcript, context)},
        ],
        'temperature': 0.15,
        'top_p': 0.85,
        'max_tokens': 760,
        'stream': False,
        'response_format': {'type': 'json_object', 'schema': PLAN_SCHEMA},
    }
    request = Request(LLM_URL, data=json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    with urlopen(request, timeout=120) as response:
        payload = json.loads(response.read())
    raw = payload['choices'][0]['message']['content'].strip()
    try:
        plan = validate_plan(json.loads(raw), transcript, context)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError('Gemma did not return a valid structured plan') from exc
    plan['preview'] = preview(plan)
    plan['planner'] = model_id
    plan['progress_protocol'] = PROGRESS_PROTOCOL
    return plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('audio', type=Path)
    parser.add_argument('--context', default='')
    parser.add_argument('--progress-file')
    args = parser.parse_args()
    try:
        progress(args.progress_file, 'transcribing', 'Transcribing speech on the GPi…')
        transcript = transcribe(args.audio)
        if len(transcript) < 3:
            raise RuntimeError('I could not hear clear speech. Check the selected mic and try again.')
        progress(args.progress_file, 'planning', 'Gemma is drafting a short app plan…')
        started = time.monotonic()
        plan = draft(transcript, args.context)
        result = {'transcript': transcript, 'plan': plan,
                  'planning_seconds': round(time.monotonic() - started, 1)}
        print(json.dumps(result))
    except (OSError, URLError, subprocess.SubprocessError, ValueError,
            KeyError, RuntimeError) as exc:
        print(json.dumps({'error': str(exc)[:240]}))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
