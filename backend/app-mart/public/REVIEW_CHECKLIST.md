# MuseBoy App Mart — Reviewer Checklist

Read this before pressing **Approve & publish**. Approved apps run as the
device user on real handhelds with full network access (pygame + `requests`).
The automated screening blocks the worst cases, but it is not a substitute
for a human reading the code.

## Before you approve

1. **Download the ZIP** ("Inspect ZIP") and look at `main.py` yourself.
   - Does it do what the description says, conceptually?
   - Is it a real app or a stub that just opens a window?
2. **Check the auto-screening flags** shown under the submission.
   - Flags are *hints*, not verdicts — `requests` to a weather API is fine,
     `requests` to a random pastebin is not.
   - `subprocess` / `os.system` / `socket` in a kids' game is a red flag.
3. **Credential harvesting.** Grep for: `sdk_token`, `appmart.json`,
   `.ssh`, `/etc/`, `input(` prompts that ask for passwords or tokens.
   Apps must never read `/var/lib/musegadget/sdk_token` or
   `/etc/gpi/appmart.json`.
4. **Network behavior.** Every outbound URL should be named in the
   description or obvious from the app's purpose. No crypto miners, no
   botnet C2, no exfiltration to personal servers.
5. **Destructive behavior.** No `rm -rf`, no writes outside the app's own
   directory, no touching system paths (`/opt`, `/etc`, `/boot`).
6. **Metadata accuracy.** Name, description, and category match the actual
   app. Screenshots/description must not promise features that aren't there.
7. **Version sanity.** New version > published version for updates.
   Reject if the submitter is squatting on someone else's app ID or
   impersonating an official app ("MuseBoy", "Settings", "App Mart").
8. **Test the download** if anything looks off: install it on your own GPi
   first and launch it before approving.

## When in doubt

**Reject.** The submitter can fix and resubmit. A bad approve puts code on
everyone's handheld; a cautious reject costs one person an email.
