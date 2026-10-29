# Argon: from homework feed to adaptive work companion

## Product promise

Argon keeps the task list trustworthy and helps Niranjan begin a realistic next step as his day changes. It works through Today, the existing focus controls, and occasional short messages. Chat is an escape hatch for explaining a change, not the main workflow.

## The experience

**After school:** Today opens with one compact card: “Next: Physics practice · about 20 min” and “Aim for 8:00 PM.” Below it are `Start`, `Adjust`, and `See all`. The board still contains every assignment and its real due date. Argon only pushes a brief when something new changes tonight's choice; a push is one sentence and links to Today. It does not paste the whole board into a notification.

**When the day changes:** `Adjust` offers one-tap choices such as `Need a nap`, `Low energy`, `Busy until…`, and `Different task`, plus optional text. Argon uses the calendar, current tasks, and this signal to change tonight's focus and start time. It shows the result in the card: “Moved start to 9:15; do 20 minutes of Physics first.” A tap can correct it. No four-screen form is required for the common case.

**At the intended start:** If there is no active task, the selected distracting apps can enter a short holding block. The shield points back to Argon. Today offers `Start 20 min`, `Move start`, and `Not tonight`. Choosing any of these immediately updates the schedule and releases or replaces the holding block. Asking for a reason is optional; the one-tap choice is enough. A single short nudge may accompany the block when useful. No repeated check-in if he ignores it.

**During work:** Starting is an explicit button, not a tap anywhere on a task row. It starts a short focus interval, shows its end time, and can be extended. Stop releases the task lock; Done also marks the task complete. An accidental tap never silently creates a four-hour lock or cancels an existing release.

**On the next open:** Argon shows what changed on the board, not a second recap. It can reconcile duplicate or stale task records from verified sources, suggest a smaller next step, and update tonight's focus after Niranjan corrects it. It never asserts completion, changes a teacher deadline, or treats a guessed task as fact.

## Build order

The first implementation now adds the Today card, low-energy adjustment, a
one-hour nap, a night-off action, and a 20-minute focus session with extension.
Screen Time enforcement still needs confirmation on the physical phone.

| Order | Change | Ship when |
| --- | --- | --- |
| 1 | Switch to GPT-6.1 Terra at medium reasoning; shorten the brief; allow one contextual question; give the model a `plan_tonight` tool. | Model access, budget accounting, agent and runtime self-checks pass; live config and service verified. |
| 2 | Replace the automatic four-step planner sheet with the compact Today decision card. Keep the existing planner API and full sheet behind `See all`. | The common path from opening Today to a usable plan takes at most two taps. No loss of Classroom due dates or overdue dispositions. |
| 3 | Add one-tap capacity changes and make the card and chat use the same plan operation. | “Low energy” or “Need a nap” changes focus/start time in both server state and the phone schedule without a long chat. |
| 4 | Separate the holding block from a task focus lock; make Start explicit and short. | Plan, defer, and release all work from the phone; a stopped task no longer leaves a surprise block. Test on a physical phone with the app closed. |
| 5 | Tune notifications from a week of use. | No duplicate due recaps; one useful start intervention per missed start; pressing `Not tonight` ends work prompts for the evening. |

## Decision rules

- Argon may quietly update the board from verified sources and explicit corrections. It may suggest focus and timing from due dates and calendar conflicts. Niranjan can accept, edit, or dismiss the suggestion.
- A short notification is justified by a new decision, not by another five-minute model tick. Silence is the default. Delivery failures and duplicate sends remain bounded.
- A capacity signal changes the plan; it is not a request for justification. The product should acknowledge the new situation and do the rescheduling work.
- A block is a temporary prompt to choose a path, never a punishment for low energy. `Move start` and `Not tonight` are first-class exits.

## One-person success test

Use the live event record for a one-week baseline and one week after each change. Count human actions, not `/v2/state` polling: plan accepted or revised, next step started, start stopped within 60 seconds, holding block released, duplicate notification, and whether the chosen start shifted on a low-energy day. The qualitative question is simpler: did Argon make the evening easier to start without becoming another thing to manage?
