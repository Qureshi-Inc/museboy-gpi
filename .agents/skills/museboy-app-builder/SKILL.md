---
name: museboy-app-builder
description: Handle MuseBoy GPi App Builder requests and changes to its request handoff, progress reporting, or app delivery workflow.
metadata:
  short-description: Build a MuseBoy GPi app from its queued transcript
---

# MuseBoy App Builder

Use this skill when processing a request created by MuseBoy's App Builder or
changing the App Builder request/build protocol.

## One-time device onboarding

MuseBoy may send a one-time message asking whether Muse may read this skill
from `/opt/gpi/apps/builder/SKILL.md`. Do not read that device file before the
user agrees. If the user replies yes to that request, read that exact file once
with the gadget's `file.read` command, confirm that the skill is loaded, and do
not ask for the same permission again in later App Builder requests. A no
allows normal App Builder handoff using `MUSE-HANDOFF.md`, but does not grant
permission to fetch the skill. The one-time grant covers that skill file only.

## Process a queued request

1. Read `/opt/gpi/apps/builder/MUSE-HANDOFF.md` before acting. Treat
   `/var/lib/gpi-builder/requests/<request-id>/transcript.txt` as the user's
   exact request and `meta.json` as its handoff metadata. Follow `operation`:
   `create_new_app` means build a new app; `modify_existing_app` means update
   the exact app identified by `target_app.id` and `target_app.name`.
2. The user has already reviewed the transcript and authorized the build by
   pressing A on the GPi. Do not ask for another plan approval. Do not replace
   the request with stale context from another job. If a key requirement is
   genuinely ambiguous, ask a focused question through Muse before making a
   risky assumption.
3. Keep the implementation within the request's stated scope and the GPi
   hardware/software constraints. Inspect the existing app architecture and
   controller bindings before editing. For `modify_existing_app`, inspect and
   change the selected app in place, preserve its app ID and existing user data,
   and do not create a replacement or duplicate. Preserve unrelated apps and
   user data.
4. Muse owns
   `/var/lib/gpi-builder/builds/<request-id>/status.json`. Never pre-create or
   rewrite it to simulate progress. Write it atomically after real milestones,
   use the shared stage meanings in `MUSE-HANDOFF.md`, and never guess a
   percentage or claim a test passed before running it.
5. Build, run relevant checks, install the app to the GPi when required, and
   verify its acceptance behavior on the target when possible. Report concrete
   blockers and the next action instead of leaving a vague `installing` or
   `testing` state.

## Change the handoff

Keep the device request directory limited to `transcript.txt` and `meta.json`.
Do not send audio or a locally generated plan. The request-submit notification
is sent with `musegadget send-user-msg`; it should identify the request folder
without duplicating the transcript into a chat message. Preserve the initial-A
authorization behavior and Muse's ownership of build status. Update
`docs/MUSE-HANDOFF.md`, tests, installer copies, and README wording whenever the
contract changes.

## Controller and UI rules

- Map every visible action to a supported GPi control and keep B as Back.
- Provide loading, empty, success, and error feedback for network or build work.
- Keep text readable at the 640×480 GPi display resolution and retain access to
  the complete request/response text when the app needs it.
- Avoid unsupported endpoints or invented data. If the request names a data
  source, verify its endpoint, parameters, timeout, and failure behavior.
