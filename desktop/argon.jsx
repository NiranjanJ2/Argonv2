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
  .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .refresh { font-size: 13px; line-height: 20px; width: 22px; height: 22px;
             text-align: center; cursor: pointer; color: #83C2FF; opacity: 0.7;
             border: 1px solid rgba(131,194,255,0.30); border-radius: 6px;
             flex: 0 0 auto; user-select: none; }
  .refresh:hover { opacity: 1; background: rgba(131,194,255,0.12); }
  .refresh.spinning { animation: spin 0.9s linear infinite; opacity: 1; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .meta { font-size: 11px; opacity: 0.55; margin-top: 3px; }
  .sep { height: 1px; background: rgba(255,255,255,0.08); margin: 12px 0 6px; }

  /* Focus is its own row: a task can run with nothing blocked, and he can be
     locked in without a task, so the panel says which it is looking at. */
  .focus { display: flex; align-items: center; gap: 7px; margin-top: 10px;
           padding: 7px 10px; border-radius: 10px; font-size: 11px;
           background: rgba(255,255,255,0.05);
           border: 1px solid rgba(255,255,255,0.08); }
  .focus .dot { width: 6px; height: 6px; border-radius: 50%;
                background: rgba(255,255,255,0.35); flex: 0 0 auto; }
  .focus.on { background: rgba(255,107,107,0.14);
              border-color: rgba(255,107,107,0.34); color: #FFD9D9; }
  .focus.on .dot { background: #FF6B6B; box-shadow: 0 0 8px #FF6B6B; }
  .focus .why { margin-left: auto; opacity: 0.55; font-size: 9.5px;
                overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                max-width: 45%; }

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

// The refresh button. Asks the server to re-read Classroom, then draws what
// comes back at once — the next twenty-second poll would only redraw the same
// board — by handing the output to Übersicht as if the command had just run.
const refresh = (event, dispatch) => {
  event.stopPropagation();
  const btn = event.currentTarget;
  btn.classList.add("spinning");
  // Stop the spin by hand either way: React reuses this element on redraw
  // and never removes a class it did not add, so it spun forever.
  const done = () => btn.classList.remove("spinning");
  run(SCRIPT + " --do refresh --json").then(
    (out) => { done(); dispatch({ type: "UB/COMMAND_RAN", output: out }); },
    done);
};

export const render = ({ output }, dispatch) => {
  let view;
  try {
    view = JSON.parse(output);
  } catch (e) {
    // The script prints diagnostics on stdout when it cannot reach the server;
    // showing them beats a blank panel that looks like "nothing to do".
    return <div className="err">{String(output).slice(0, 200)}</div>;
  }
  if (!view.ok) {
    return (
      <div>
        <div className="head"><h1>Argon</h1>
          <div className="refresh" onClick={(e) => refresh(e, dispatch)}>↻</div></div>
        <div className="err">{view.lines[0]}</div>
      </div>
    );
  }

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
      <div className="head">
        <h1>{view.title}</h1>
        <div className="refresh" title="Re-read Classroom"
             onClick={(e) => refresh(e, dispatch)}>↻</div>
      </div>
      {view.lines.map((line, i) => <div className="meta" key={i}>{line}</div>)}

      {view.focus && (
        <div className={view.focus.on ? "focus on" : "focus"}>
          <span className="dot" />
          {view.focus.on
            ? `Apps blocked until ${view.focus.until}`
            : `Block starts ${view.focus.starts}`}
          <span className="why">{view.focus.reason}</span>
        </div>
      )}

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
