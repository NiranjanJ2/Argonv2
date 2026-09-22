# Changelog

## 2026-09-22

### Fixed

- Restored the iOS sync test target and made its date fixtures stable.
- Made APNs registration self-healing on every app launch and tied the saved
  receipt to the configured server, preventing silent pushes from remaining
  unregistered after a reinstall, token refresh, or server change.
- Made lockdown scheduling truthful: empty blocking profiles and future-window
  scheduling failures are reported instead of acknowledged as applied, while
  live locks still fall back to immediate focus-mode enforcement.
- Made task and chat outbox retries idempotent so timeouts cannot create
  duplicate tasks or messages; repeated task state updates now converge rather
  than becoming permanent queue failures.
- Kept Classroom completion based on the complete outstanding-submission set,
  not the phone's limited display window, so old owed work stays open.
- Persisted phone-originated Classroom completions as dispositions so later
  syncs do not resurrect work the user marked done.
- Moved date-only Classroom homework to the prior work evening while preserving
  exact local dates and times for assignments with explicit deadlines.
