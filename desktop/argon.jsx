// Übersicht widget. Layout only — argon-widget.py decides what it says.
import { run } from "uebersicht";

export const refreshFrequency = 60000;
export const command = "python3 $HOME/.argon/argon-widget.py --json";

export const className = `
  top: 40px; right: 40px; width: 300px;
  font-family: -apple-system, BlinkMacSystemFont, sans-serif;
  color: #e8e8ea; background: rgba(20,20,24,0.72);
  backdrop-filter: blur(20px); border-radius: 14px; padding: 16px 18px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.35);
  h1 { font-size: 15px; font-weight: 600; margin: 0 0 8px; letter-spacing: -0.2px; }
  .meta { font-size: 11px; opacity: 0.55; margin-bottom: 2px; }
  .task { display: flex; gap: 8px; font-size: 12px; padding: 4px 0;
          border-top: 1px solid rgba(255,255,255,0.07); align-items: baseline; }
  .task:first-of-type { border-top: none; margin-top: 8px; }
  .title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .due { font-size: 10px; opacity: 0.5; }
  .overdue { color: #ff6b6b; opacity: 0.95; }
  .started { color: #4ade80; }
  .empty { font-size: 12px; opacity: 0.5; padding-top: 6px; }
`;

export const render = ({ output }) => {
  let view;
  try { view = JSON.parse(output); } catch { return <div>…</div>; }
  return (
    <div>
      <h1>{view.title}</h1>
      {view.lines.map((line, i) => <div className="meta" key={i}>{line}</div>)}
      {view.tasks.length === 0 && view.ok && <div className="empty">Nothing open</div>}
      {view.tasks.map((t) => (
        <div className="task" key={t.id}>
          <span className={t.started ? "title started" : "title"}>
            {t.started ? "▶ " : ""}{t.title}
          </span>
          <span className={t.due === "overdue" ? "due overdue" : "due"}>{t.due}</span>
        </div>
      ))}
    </div>
  );
};
