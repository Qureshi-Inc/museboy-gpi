# MuseBoy App Mart backend

Cloudflare Worker + D1 + R2 behind `museboy.interestingsoup.com`. Community
GPi apps are submitted as ZIP bundles, held in a pending queue, reviewed by a
human at `/review.html`, and published to the public catalog on approval.

## Safety model

- Nothing is public until a reviewer approves it (`submissions.status =
  'pending'` is invisible to the catalog).
- At submit time the worker validates the ZIP central directory server-side:
  no absolute paths, `..` segments, backslashes, colons, or symlinks; at most
  256 files; at most 32 MiB expanded; `app.json` and `main.py` required.
- `main.py` is statically screened: egregious patterns (SDK token reads,
  SSH keys, `/etc/shadow`, destructive `rm -rf`, fork bombs, >4 KB base64
  blobs) are hard-rejected; suspicious-but-legitimate patterns (`eval`,
  `subprocess`, `socket`, absolute-path `open`) are stored as `flags` on the
  submission and shown on the review desk.
- Submissions are rate-limited (10 per rolling hour per token hash + client
  IP); the pending queue is capped at 200; pending submissions older than 30
  days are purged.
- Approving an existing app ID requires a strictly greater version.
- The device installer re-validates every bundle before install (path,
  symlink, size checks; SHA-256 against the catalog) and never executes
  bundle code at install time.

## Roadmap (before the device fleet grows)

- **Per-device submit tokens.** Today there is one shared `SUBMIT_TOKEN`
  installed on every GPi (`/etc/gpi/appmart.json`). That means no
  attribution, no per-submitter quota, and rotating the token breaks
  submissions on all devices until each is reconfigured. Planned: one token
  per device, issued at provisioning time.
- **R2 lifecycle cleanup for orphaned objects.** Re-publishing the same
  app ID and reject-then-resubmit leave old `apps/<id>/<old-id>/` keys
  behind. Planned: delete the previous published keys on re-publish and add
  an R2 lifecycle rule for the `pending/` prefix.

## Local dev

- `npm test` — runs the worker test suite (no wrangler needed).
- `npm run setup` — provisions D1/R2 and prints `ADMIN_TOKEN`/`SUBMIT_TOKEN`
  (store with `wrangler secret put`).
- See `REVIEW_CHECKLIST.md` for the human reviewer's pre-approve checklist.
