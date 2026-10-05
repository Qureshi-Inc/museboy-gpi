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
    'stage_values': ['preview', 'building', 'installing', 'testing', 'done', 'error'],
    'milestones': [
        {'stage': 'preview',
         'completed_when': 'The request has been understood and the plan is ready.'},
        {'stage': 'building',
         'completed_when': 'Requested app changes have been implemented.'},
        {'stage': 'installing',
         'completed_when': 'The app is being installed or staged on the GPi.'},
        {'stage': 'testing',
         'completed_when': 'Relevant checks are being run.'},
        {'stage': 'done',
         'completed_when': 'The app launched and acceptance checks were verified.'},
        {'stage': 'error',
         'completed_when': 'Work is blocked; report the failure and next action.'},
    ],
    'rules': [
        'Write status.json after each completed milestone; updated is Unix epoch seconds.',
        'Report a percentage only when the worker knows the completed-milestone value; never estimate by elapsed time.',
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
    temporary.write_text(json.dumps({'step': step, 'stage': step,
                                     'message': message,
                                     'updated': time.time()}))
    os.replace(temporary, target)


def transcribe(audio_path):
    command = [str(WHISPER), '-m', str(WHISPER_MODEL), '-f', str(audio_path),
               '-t', '2', '-ac', '768', '-nt', '-np', '-l', 'en']
    result = subprocess.run(command, text=True, capture_output=True, timeout=180,
                            check=True, preexec_fn=lambda: os.nice(8))
    transcript = ' '.join(line.strip() for line in result.stdout.splitlines()
                          if line.strip())
    return ' '.join(transcript.split())[:1800]


_SHORT = {'type': 'string', 'maxLength': 64}
DRAFT_SCHEMA = {
    'type': 'object',
    'properties': {
        'app_name': {'type': 'string', 'minLength': 1, 'maxLength': 32},
        'description': {'type': 'string', 'minLength': 1, 'maxLength': 100},
        'features': {'type': 'array', 'minItems': 2, 'maxItems': 5,
                     'items': _SHORT},
        'data_sources': {'type': 'array', 'minItems': 1, 'maxItems': 3,
                         'items': {'type': 'string', 'maxLength': 100}},
    },
    'required': ['app_name', 'description', 'features', 'data_sources'],
    'additionalProperties': False,
}


def make_prompt(transcript, context=''):
    previous = ''
    if context:
        try:
            old = json.loads(context) if isinstance(context, str) else context
            previous = ('Existing brief to update: ' + json.dumps(
                {k: old[k] for k in ('app_name', 'description', 'features', 'data_sources')
                 if k in old}, ensure_ascii=False, separators=(',', ':')) + '\n')
        except (ValueError, TypeError, KeyError):
            previous = ''
    return (
        'Create a tiny first-release app brief for RetroFlag GPi Case 2. '
        'Return JSON matching the schema. Use short phrases, 2–5 concrete '
        'features, and a lowercase hyphenated app_id. Never invent an API: '
        'list only a source explicitly named in this transcript or existing brief; otherwise use ["none"] '
        'and keep the app offline. No code or optional features.\n' + previous +
        'Exact voice transcript: ' + transcript
    )


def validate_plan(draft, transcript, context=''):
    required = ('app_name', 'description', 'features', 'data_sources')
    if not isinstance(draft, dict) or any(key not in draft for key in required):
        raise RuntimeError('Gemma brief is missing a required field')
    app_name = str(draft['app_name']).strip()[:32]
    if not app_name:
        raise RuntimeError('Gemma did not name the app')
    app_id = re.sub(r'[^a-z0-9]+', '-', app_name.lower()).strip('-')[:40]
    if not app_id:
        raise RuntimeError('Could not make an app ID from the app name')
    description = ' '.join(str(draft['description']).split())[:100]
    features = [' '.join(str(value).split())[:64]
                for value in draft['features'] if str(value).strip()]
    sources = [' '.join(str(value).split())[:100]
               for value in draft['data_sources'] if str(value).strip()]
    if not description:
        raise RuntimeError('Gemma did not describe the app')
    if not 2 <= len(features) <= 5:
        raise RuntimeError('Gemma brief must contain 2–5 concrete features')
    if not 1 <= len(sources) <= 3:
        raise RuntimeError('Gemma brief must identify a data source or "none"')
    if any(value.lower() == 'none' for value in sources):
        sources = ['none']
    else:
        allowed_text = transcript
        try:
            old = json.loads(context) if isinstance(context, str) else context
            allowed_text += ' ' + ' '.join(old.get('data_sources', []))
        except (ValueError, TypeError, AttributeError):
            pass
        if any(value.casefold() not in allowed_text.casefold() for value in sources):
            sources = ['none']
    # Transcript is added by code, verbatim from local Whisper, never regenerated.
    return {'transcript': transcript, 'app_name': app_name, 'app_id': app_id,
            'description': description, 'features': features,
            'data_sources': sources}


def preview(plan):
    if set(plan) == {'transcript'}:
        return 'Transcript-only request: ' + plan['transcript'][:520]
    if 'app_name' not in plan:
        return (f"{plan.get('title', 'App')}: {plan.get('goal', '')}. "
                f"{'; '.join(plan.get('user_flow', [])[:2])}.")[:560]
    features = '; '.join(plan.get('features', [])[:2])
    data = ', '.join(plan.get('data_sources', [])[:2])
    return (f"{plan.get('app_name', 'App')}: {plan.get('description', '')}. "
            f"Features: {features}. Data: {data}.")[:560]


def transcript_only_plan(transcript):
    """The production Builder handoff keeps only Whisper's exact transcript."""
    return {'transcript': transcript}


def usable_transcript(transcript):
    return (isinstance(transcript, str) and len(transcript.strip()) >= 3 and
            len(re.findall(r"[A-Za-z0-9']+", transcript)) >= 2)


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
            {'role': 'system', 'content': 'Return only a compact valid JSON app brief. Be literal and concise.'},
            {'role': 'user', 'content': make_prompt(transcript, context)},
        ],
        'temperature': 0.15,
        'top_p': 0.85,
        'max_tokens': 280,
        'stream': False,
        'response_format': {'type': 'json_object', 'schema': DRAFT_SCHEMA},
    }
    request = Request(LLM_URL, data=json.dumps(body).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    # On a CM4 the first response includes slow prompt evaluation as well as
    # generation. Allow a bounded, longer window so a valid plan is not cut
    # off just as Gemma finishes reading the prompt.
    with urlopen(request, timeout=280) as response:
        payload = json.loads(response.read())
    raw = payload['choices'][0]['message']['content'].strip()
    try:
        plan = validate_plan(json.loads(raw), transcript, context)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError('Gemma did not return a valid structured plan') from exc
    return {'plan': plan, 'preview': preview(plan), 'planner': model_id,
            'progress_protocol': PROGRESS_PROTOCOL}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('audio', nargs='?', type=Path)
    parser.add_argument('--text', help='Use this transcript directly (for isolated Gemma tests)')
    parser.add_argument('--context', default='')
    parser.add_argument('--progress-file')
    parser.add_argument('--transcript-only', action='store_true')
    args = parser.parse_args()
    try:
        if args.text is not None:
            transcript = args.text
        elif args.audio:
            progress(args.progress_file, 'transcribing', 'Transcribing speech on the GPi…')
            transcript = transcribe(args.audio)
        else:
            raise RuntimeError('Provide a local audio file or --text')
        if not transcript.strip() or (args.transcript_only and not usable_transcript(transcript)):
            raise RuntimeError('Whisper heard too little to make a safe request. Check the mic and record at least two clear words again.')
        started = time.monotonic()
        if args.transcript_only:
            plan = transcript_only_plan(transcript)
            drafted = {'plan': plan, 'preview': preview(plan),
                       'planner': 'local-whisper',
                       'progress_protocol': PROGRESS_PROTOCOL}
            progress(args.progress_file, 'sending',
                     'Transcription complete; sending only the text to Muse…')
        else:
            progress(args.progress_file, 'planning', 'Gemma is drafting a short app plan…')
            drafted = draft(transcript, args.context)
            plan = drafted['plan']
        elapsed = round(time.monotonic() - started, 1)
        result = {'plan': plan, 'preview': drafted['preview'],
                  'planner': drafted['planner'],
                  'progress_protocol': drafted['progress_protocol'],
                  'transcription_only': args.transcript_only,
                  'planning_seconds': elapsed}
        if args.progress_file:
            transcript_path = Path(args.progress_file).parent / 'transcript.txt'
            temporary = transcript_path.with_suffix('.txt.tmp')
            temporary.write_text(transcript, encoding='utf-8')
            os.replace(temporary, transcript_path)
            meta = {'planner': drafted['planner'], 'planning_seconds': elapsed,
                    'progress_protocol': drafted['progress_protocol'],
                    'audio_sent': False,
                    'transcription_only': args.transcript_only,
                    'transcript_file': 'transcript.txt', 'created': time.time()}
            (Path(args.progress_file).parent / 'meta.json').write_text(
                json.dumps(meta, ensure_ascii=False))
            progress(args.progress_file,
                     'sending' if args.transcript_only else 'preview',
                     'Transcript ready; completing Muse handoff.' if args.transcript_only
                     else 'Local plan ready to review.')
            if args.transcript_only and args.audio:
                try:
                    args.audio.unlink()
                except FileNotFoundError:
                    pass
        print(json.dumps(result))
    except (OSError, URLError, subprocess.SubprocessError, ValueError,
            KeyError, RuntimeError) as exc:
        progress(args.progress_file, 'error', str(exc)[:220])
        print(json.dumps({'error': str(exc)[:240]}))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
