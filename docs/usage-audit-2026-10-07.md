# Usage audit — 2026-10-07

Reviewed production transcript events and the gateway journal from September 16
through October 7, 2026 (Pacific time). A read-only SQLite backup includes 4,222
all-time rows; the audit window includes 502 ticks, 50 inbound messages, 92
outbound messages, 218 guard refusals, eight truncated turns and nine material
read failures. No provider_error, api_error, turn_failed, tick_failed or
undelivered rows were present in this window. Absence of those rows does not
prove every request was successful.

## Findings and fixes

- Repeated `say` retries continued after spacing/cap refusals. The turn now stops
  when rewording cannot change the refusal. Failed delivery no longer counts as
  spoken or marks a brief delivered.
- An interactive release acted immediately, then drifted into old mail and
  calendar work and exhausted its steps without acknowledgement. Each worker
  now carries its triggering request, and interactive turns reserve their last
  step for a reply. An empty final reply records an explicit error. Prompt focus
  reduces model drift; it is not a guarantee of model correctness.
- Repeated mail searches could see subjects but could not read message contents.
  Search exposes IDs and `read_mail` reads bodies and attachment metadata from
  the named authorised account. Binary attachment contents are not retrieved.
- TLS errors occurred during concurrent Google reads. Google's client owns a
  non-thread-safe HTTP connection; services now cache per thread. Disk token
  signatures invalidate the cache after external reauthorisation. The socket
  isolation and token rewrite paths are tested with fakes; the production TLS
  failure itself was not reproduced in a test.
- Classroom sync attempts are serialized and failures are throttled and logged.
  Title/date changes also mark the board changed. Calendar countdowns use the
  current clock rather than minutes captured at fetch time.
- Forward message cursors previously skipped older pending history. Cursor pages
  now return the first pending page. Invalid log limits return 400 instead of
  500. External text cannot close the observation/untrusted wrappers with raw
  delimiter tags.
- Discord messages previously used the default push-first delivery order. Replies
  now use the exact inbound Discord conversation. That destination is scoped to
  the worker and cleared afterward; failure does not redirect the reply to the
  phone. Guild replies do not change the saved proactive private destination.
  Replying in the incoming conversation (including a guild channel) is the
  deliberate interpretation of the user's request to answer on Discord.

Google HTTP 500s and historical network timeouts also appear in the records.
This change cannot eliminate upstream outages. September's ledger totals were
1,193 model calls and $1.708337, with 81.4% of prompt tokens cached. October to
inspection time had 255 calls and $0.160373, with 70.4% cached. These are calendar
month totals, not totals for the audit window.

## Verification

- `./check.sh`: successful full run, including all 18 Python modules, the desktop
  selfcheck, 41 Swift sync tests and an iOS simulator build of all targets.
- After the final Python-only additions: all 18 module selfchecks and the desktop
  selfcheck rerun successfully; `python -m py_compile argon/cli.py` and
  `git diff --check` pass.
- Added fake-provider, thread, fake-Gmail and fake-Discord checks cover terminal
  refusals, failed sends, final replies, queued-request focus, concurrent imports,
  cached event expiry, mail decoding, external delimiter escaping, token file
  rewrites and exact Discord reply destinations. Discord's loop hop/fetch/send
  path is exercised with a fake client, without sending a test message.
- Before publication: 752 historical repository blobs scanned for common key,
  token and private-key patterns; no matches. This is a pattern scan, not a
  guarantee that arbitrary sensitive text is absent.

## Collaboration and private evidence

Claude Code used the existing subscription login. Codex integrated and
independently verified the changes. File ownership was disjoint:

| Session | Role | Owned files |
| --- | --- | --- |
| `2c825e86-d8b0-4d2e-a8a2-88bd2bc258a8` | Persistent brainstorming/review | Read only |
| `54f83bfd-0eb4-490c-99c5-eb703a50e373` | Google execution, reauth follow-up | `argon/integrations/google.py` |
| `7960d7b6-8df9-43af-884c-d0eea6bccb04` | API execution | `argon/api.py` |
| `6dcbf1f4-a568-4c5b-aa34-7e6c30650d0c` | Discord execution | `argon/channels.py`, `argon/cli.py` |

Exact prompts, persisted session results and command/test evidence are retained
privately in `~/.argon-audits/2026-10-07`. Production transcript and journal
snapshots are in that private directory, never in Git. Claude review caught the
queued-request focus issue, empty-final-reply gap and cross-process credential
cache invalidation; all were corrected and covered by checks.

Codex remaining allowance at work boundaries: 98%/84% at start, 95%/84%, then
91%/83%, then 83%/82% before publication (five-hour/weekly). No reset or credit purchase was used.
