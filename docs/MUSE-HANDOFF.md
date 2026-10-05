# Muse App Builder handoff

## One-time skill consent

After Muse pairing is established, the GPi sends one onboarding message asking
whether Muse may read `/opt/gpi/apps/builder/SKILL.md`. The request is sent once
per installation and only after the paired state file exists. Do not read that
file unless the user replies yes to this prompt. A yes authorizes reading this
single skill file once and using it for future App Builder requests; it does
not authorize reading unrelated files. A no is respected without repeating the
prompt. Normal transcript handoff continues to use this document.

## Request contract

Builder records and transcribes locally with Whisper. It shows the exact
transcript to the user before submission. **A** is the user's approval; **X**
records again; **B** discards the local recording/transcript. A transcript with
fewer than two words is rejected locally and must be re-recorded.

After approval, the GPi atomically publishes exactly two files in
`/var/lib/gpi-builder/requests/<request-id>/`:

```text
transcript.txt
meta.json
```

`transcript.txt` contains the verbatim Whisper output. `meta.json` sets
`audio_sent: false`, `transcription_only: true`,
`transcript_approval_authorizes_build: true`, names `transcript.txt`, and
provides the progress protocol below. It also sets `operation` to
`create_new_app` or `modify_existing_app`. For an edit, `meta.json` includes
`target_app` with the exact installed app's `id` and `name`. Muse must edit that
app in place, preserve its ID and installation, and must not create a duplicate.
For a new app, Muse creates a new app as usual. No audio is sent. No `plan.json`,
`status.json`, `approved`, or other file is created in the request directory.

After publishing the request, Builder sends Muse a short `send-user-msg`
notification naming the request ID and folder. The message does not include the
transcript; Muse reads it from `transcript.txt`. If the SDK is temporarily
unavailable, Builder retries the notification while keeping the request intact.

The request directory appearing after A is the user's approval of the
transcript and authorization to build the plan Muse derives from it. Muse owns
`builds/<request-id>/status.json` and all build lifecycle reports. Builder
never creates or changes that status file. If Muse reports `stage: preview`
and `current_step: awaiting_approval` (or says “press A to approve”), Builder
creates only `builds/<request-id>/approved`, because the user's original A
already authorized the build. This approval marker is written only after
Muse's preview exists, never pre-created. Muse should proceed without a second
user approval prompt. Builder reads Muse's status and uses the marker solely as
the agreed authorization signal.

## One shared Muse status contract

`meta.json.progress_protocol.stage_values` is exactly:

```text
preview, building, installing, testing, done, error
```

Use `preview` when the request has been understood and the plan is ready;
`building` while implementing; `installing` only during installation; `testing`
only while checks run; `done` only after launch and acceptance checks pass; and
`error` when blocked, with the real failure and next useful action. The protocol
defines stage meanings, not percentage values. Report `progress` only when the
worker has an authoritative completed-milestone value; omit it otherwise.
Never infer a percentage from stage names or elapsed time.

Write `builds/<request-id>/status.json` atomically after each completed
milestone. Include `stage`, `message`, `current_step`, and `updated` (Unix epoch
seconds); preserve the latest `plan` summary if provided. Update `updated` for
each report. Builder polls this file and displays the reported stage and age.
Before the worker creates it, Builder shows “waiting for Muse's first status
report” without writing a placeholder.

Treat the transcript as the user's complete instruction. Do not substitute an
old idea, repeat a stale interpretation, or invent details that contradict the
words. Ask through Muse when meaning is unclear. Keep the GPi D-pad and A/B/X/Y
controls usable, with B returning to the previous screen.
