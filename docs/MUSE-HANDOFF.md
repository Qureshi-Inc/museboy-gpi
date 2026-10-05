# Muse App Builder handoff

The full product-contract format is documented in Muse's `~/workspace/gpi-console/appmart/PLAN_SPEC.md`.

## Request flow

1. The GPi records to a private temporary WAV file, transcribes it with local Whisper, and drafts `plan.json` with local Gemma.
2. The user can scroll the full plan with the D-pad, approve with A, add detail with X, or cancel/back with B.
3. The GPi deletes the WAV after local planning. It creates the Muse request only after A is pressed. A queued request contains `plan.json` and `meta.json`; it contains no audio file. `meta.json` sets `audio_sent` to `false`.
4. `status.json` contains only the 2–4 sentence preview in its `plan` field. The full structured contract is in the sibling `plan.json`.
5. The user has already approved the contract when a request first appears in `/var/lib/gpi-builder/requests/`. The matching `/var/lib/gpi-builder/builds/<request-id>/approved` file records that approval.

Read `plan.json` as the implementation contract. Keep the user's transcript as text, honor blocking open questions, and do not invent data endpoints. Keep every screen's button map and states complete. B must go back. Do not add free-text entry or a software keyboard.

## Honest progress for Bolt

Update `/var/lib/gpi-builder/builds/<request-id>/status.json` after each completed milestone. Preserve the short preview in `plan` and the original `submitted_at`; report `stage`, `message`, `current_step`, and `updated` (Unix epoch seconds). Report `progress` only when a listed milestone in `plan.json.progress_protocol` is actually complete. Never infer a percentage from elapsed time. If the percent is unknown, omit `progress`; the GPi shows an indeterminate activity indicator and the age of your last report. Use `testing` only while checks run, `installing` only during installation, and `done` only after launch and acceptance checks have been verified. Set `error` with the actual failure and next useful action when blocked. Write status atomically (temporary file then rename) so Bolt never reads partial JSON. Each `updated` timestamp must change when you publish a new report.
