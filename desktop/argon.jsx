// Argon desktop widget for Übersicht.
//
// Renders the view model argon-widget.py builds — the same object SwiftBar
// renders, so the two readouts cannot disagree. Everything needing a clock
// arrives as a finished string; this file decides how it looks and nothing else.
//
// Clicking a task starts it, which also raises the shield: starting work and
// blocking distractions used to be two separate actions, so in practice the
// second never happened. Start, Stop and Done all go through the same HTTP
// surface the phone uses, so a task started here is indistinguishable from one
// started by asking Argon.
import { run } from "uebersicht";

const SCRIPT = "$HOME/.argon/argon-widget.py";

export const command = SCRIPT + " --json";
export const refreshFrequency = 20000;

const sh = (s) => "'" + String(s).replace(/'/g, "'\\''") + "'";

// Module scope, not React state. Übersicht re-invokes render on every poll, so
// anything held in component state snaps shut every twenty seconds; a module
// variable outlives the render because the module is only loaded once.
let laterOpen = false;

// Dim on click; the next poll replaces the DOM with the truth, which is exactly
// when the dimming should stop, so nothing has to undo it.
const act = (event, ...args) => {
  event.stopPropagation();
  const row = event.currentTarget.closest(".row") || event.currentTarget;
  row.classList.add("pending");
  run(SCRIPT + " --do " + args.map(sh).join(" "));
};

export const className = `
  top: 40px; right: 40px; width: 360px;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
  color: #E9EDF2;
  -webkit-font-smoothing: antialiased;
  background: rgba(18, 23, 32, 0.62);
  backdrop-filter: blur(24px) saturate(140%);
  border: 1px solid rgba(255,255,255,0.09);
  border-radius: 18px;
  padding: 16px 18px 12px;
  box-shadow: 0 16px 48px rgba(0,0,0,0.45), 0 0 24px rgba(77,163,255,0.10);

  h1 { font-size: 15px; font-weight: 600; margin: 0; letter-spacing: -0.2px; }
  .meta { font-size: 11px; opacity: 0.55; margin-top: 3px; }
  .sep { height: 1px; background: rgba(255,255,255,0.08); margin: 12px 0 6px; }

  .running { margin-top: 10px; padding: 10px 12px; border-radius: 12px;
             background: linear-gradient(135deg, rgba(77,163,255,0.22),
                                                 rgba(38,103,196,0.12));
             border: 1px solid rgba(77,163,255,0.28); }
  .running .eyebrow { font-size: 9px; letter-spacing: 1.1px; text-transform: uppercase;
                      color: #83C2FF; font-weight: 600; }
  .running .what { font-size: 13px; font-weight: 600; margin-top: 3px; }
  .running .note { font-size: 10px; opacity: 0.6; margin-top: 2px; }

  .row { display: flex; align-items: baseline; gap: 8px; padding: 7px 0;
         border-top: 1px solid rgba(255,255,255,0.06); }
  .row:first-of-type { border-top: none; }
  .row.pending { opacity: 0.35; }

  .tick { flex: 0 0 auto; width: 15px; height: 15px; border-radius: 5px;
          border: 1.5px solid rgba(255,255,255,0.28); cursor: pointer;
          align-self: center; transition: all 120ms ease; }
  .tick:hover { border-color: #4DA3FF; background: rgba(77,163,255,0.18); }

  .body { flex: 1; min-width: 0; cursor: pointer; }
  .title { font-size: 12px; overflow: hidden; text-overflow: ellipsis;
           white-space: nowrap; }
  .subject { font-size: 9.5px; opacity: 0.45; margin-top: 1px; }
  .due { font-size: 10px; opacity: 0.5; flex: 0 0 auto; align-self: center; }
  .overdue { color: #FF6B6B; opacity: 0.95; }
  .today { color: #83C2FF; opacity: 0.95; }

  .btn { font-size: 9.5px; padding: 3px 8px; border-radius: 999px; cursor: pointer;
         border: 1px solid rgba(255,255,255,0.16); flex: 0 0 auto; align-self: center;
         opacity: 0; transition: opacity 120ms ease; }
  .row:hover .btn { opacity: 0.9; }
  .btn:hover { background: rgba(255,255,255,0.10); }
  .btn.stop { border-color: rgba(255,107,107,0.45); color: #FF8F8F; opacity: 0.9; }

  .more { font-size: 10.5px; opacity: 0.6; cursor: pointer; padding: 8px 0 4px;
          user-select: none; letter-spacing: 0.2px; }
  .more:hover { opacity: 0.95; color: #83C2FF; }
  .chev { display: inline-block; width: 12px; opacity: 0.8; }

  /* Collapsed by default. The header stays, so the work is never hidden —
     only folded, and the count says how much is behind it. */
  .laterList { display: none; }
  .open > .laterList { display: block; }

  .empty { font-size: 12px; opacity: 0.5; padding: 10px 0 4px; }
  .err { font-size: 11px; color: #FF8F8F; padding-top: 6px; line-height: 1.45; }
`;

export const render = ({ output }) => {
  let view;
  try {
    view = JSON.parse(output);
  } catch (e) {
    // The script prints diagnostics on stdout when it cannot reach the server;
    // showing them beats a blank panel that looks like "nothing to do".
    return <div className="err">{String(output).slice(0, 200)}</div>;
  }
  if (!view.ok) return <div className="err">{view.lines[0]}</div>;

  const running = view.tasks.find((t) => t.started);
  const rest = view.tasks.filter((t) => !t.started);
  // Tonight is what he can still act on today; everything dated further out is
  // real work but not this evening's, and listing it all made the panel a wall
  // he stopped reading.
  const isTonight = (t) =>
    t.due === "overdue" || t.due === "today" || t.due === "tomorrow";
  const tonight = rest.filter(isTonight);
  const later = rest.filter((t) => !isTonight(t));

  const taskRow = (t) => (
    <div className="row" key={t.id}>
      <div className="tick" onClick={(e) => act(e, "complete", t.id)} />
      <div className="body" onClick={(e) => act(e, "start", t.id)}>
        <div className="title">{t.title}</div>
        {t.subject && <div className="subject">{t.subject}</div>}
      </div>
      <div className={"due " + (t.due === "overdue" ? "overdue"
                              : t.due === "today" ? "today" : "")}>
        {t.due}
      </div>
      <div className="btn" onClick={(e) => act(e, "start", t.id)}>Start</div>
    </div>
  );

  return (
    <div className={laterOpen ? "open" : ""}>
      <h1>{view.title}</h1>
      {view.lines.map((line, i) => <div className="meta" key={i}>{line}</div>)}

      {running && (
        <div className="running row">
          <div className="body" onClick={(e) => act(e, "stop", running.id)}>
            <div className="eyebrow">Working on — apps blocked</div>
            <div className="what">{running.title}</div>
            <div className="note">Click to stop, or tick to finish</div>
          </div>
          <div className="tick" onClick={(e) => act(e, "complete", running.id)} />
        </div>
      )}

      <div className="sep" />

      {tonight.length === 0 && <div className="empty">Nothing due tonight</div>}
      {tonight.map(taskRow)}

      {later.length > 0 && (
        <div
          className="more"
          onClick={(e) => {
            // Flip and repaint now rather than waiting for the next poll —
            // twenty seconds is a long time to wonder whether a click landed.
            laterOpen = !laterOpen;
            const box = e.currentTarget.parentNode;
            box.classList.toggle("open", laterOpen);
            const chev = e.currentTarget.querySelector(".chev");
            if (chev) chev.textContent = laterOpen ? "▾" : "▸";
          }}
        >
          <span className="chev">{laterOpen ? "▾" : "▸"}</span>
          Later — {later.length} not due tonight
        </div>
      )}
      {later.length > 0 && (
        <div className="laterList">{later.map(taskRow)}</div>
      )}
    </div>
  );
};
