import { useEffect, useMemo, useReducer, useState } from "react";
import type { Auth } from "../auth";
import { feedReducer, initialFeed, stats, streamLive, type FeedTurn } from "../dashboard";

interface Props {
  apiUrl: string;
  auth: Auth;
  liveMetricsUrl?: string;
  workbookUrl?: string;
}

const OUTCOME: Record<FeedTurn["outcome"], string> = {
  running: "Running",
  answered: "Answered",
  curated: "Curated answer",
  client_tool: "Client tool",
  error: "Error",
};

function seconds(ms: number | null | undefined): string {
  return typeof ms === "number" ? `${(ms / 1000).toFixed(1)} s` : "–";
}

function clock(at: number): string {
  return new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function sek(value: number): string {
  return value < 1 ? `${(value * 100).toFixed(1)} öre` : `${value.toFixed(2)} SEK`;
}

/** The live operations view: what is happening now. History lives in the Azure Workbook. */
export function Dashboard({ apiUrl, auth, liveMetricsUrl, workbookUrl }: Props) {
  const [feed, dispatch] = useReducer(feedReducer, initialFeed);
  const [showPrompts, setShowPrompts] = useState(false);
  const [connected, setConnected] = useState(false);
  const [problem, setProblem] = useState<string>();

  useEffect(() => {
    const ctrl = new AbortController();
    let retry: ReturnType<typeof setTimeout> | undefined;
    async function connect() {
      try {
        const token = await auth.getToken();
        setProblem(undefined);
        setConnected(true);
        dispatch({ event: "reset" });
        for await (const msg of streamLive(apiUrl, token, showPrompts, ctrl.signal)) dispatch(msg);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setProblem(e instanceof Error ? e.message : "The live feed stopped.");
      }
      setConnected(false);
      if (!ctrl.signal.aborted) retry = setTimeout(() => void connect(), 5000);
    }
    void connect();
    return () => {
      ctrl.abort();
      clearTimeout(retry);
    };
  }, [apiUrl, auth, showPrompts]);

  const s = useMemo(() => stats(feed.turns), [feed.turns]);
  const maxTool = Math.max(1, ...s.tools.map(([, n]) => n));
  const maxKb = Math.max(1, ...s.knowledgeBases.map(([, n]) => n));

  return (
    <section className="dash" aria-label="Operations dashboard">
      <div className="dash-head">
        <h2>Live dashboard</h2>
        <span className={`live-dot ${connected ? "on" : ""}`}>{connected ? "Live" : "Reconnecting"}</span>
        <label className="switch" title="Prompts are masked, and the live feed stores nothing. Off by default for screens in front of an audience.">
          <input type="checkbox" checked={showPrompts} onChange={(e) => setShowPrompts(e.target.checked)} />
          <span>Show prompts</span>
        </label>
        {liveMetricsUrl && <a className="ghost link" href={liveMetricsUrl} target="_blank" rel="noreferrer">Live Metrics</a>}
        {workbookUrl && <a className="ghost link" href={workbookUrl} target="_blank" rel="noreferrer">History (workbook)</a>}
      </div>
      {problem && <p className="dash-problem">{problem}</p>}

      <div className="tiles">
        <div className="tile big"><span className="tile-n">{feed.summary.active_users}</span><span className="tile-l">users active now</span></div>
        <div className="tile"><span className="tile-n">{feed.summary.turns}</span><span className="tile-l">messages today</span></div>
        <div className="tile"><span className="tile-n">{s.running}</span><span className="tile-l">answering now</span></div>
        <div className="tile"><span className="tile-n">{sek(s.costSek)}</span><span className="tile-l">estimated, last {feed.turns.length} turns</span></div>
        {Object.entries(s.medianFirstText).map(([backend, ms]) => (
          <div className="tile" key={backend} data-backend={backend}>
            <span className="tile-n">{seconds(ms)}</span>
            <span className="tile-l">first text, {backend}</span>
          </div>
        ))}
      </div>

      <div className="dash-body">
        <ol className="feed" aria-live="polite">
          {feed.turns.length === 0 && <li className="feed-empty">Waiting for the first question…</li>}
          {feed.turns.map((t) => (
            <li key={t.turn_id} className={`feed-row ${t.outcome}`}>
              <div className="feed-top">
                <time>{clock(t.at)}</time>
                <span className="pill" data-backend={t.backend}>{t.backend}</span>
                <span className="who-id" title="A pseudonym, not the user's identity">user {t.user.slice(0, 6)}</span>
                <span className={`state ${t.outcome}`}>{OUTCOME[t.outcome]}{t.error_code ? `: ${t.error_code}` : ""}</span>
                <span className="timing" title="First text / whole answer">{seconds(t.first_text_ms)} / {seconds(t.duration_ms)}</span>
                {typeof t.cost_sek === "number" && <span className="cost">{sek(t.cost_sek)}</span>}
              </div>
              {showPrompts && t.prompt && <p className="prompt">“{t.prompt}”</p>}
              {t.kind === "tool_results" && <p className="prompt muted">Client tool results</p>}
              <div className="chips">
                {(t.knowledge_bases ?? []).map((kb) => <span key={kb} className="chip kb">{kb}</span>)}
                {(t.tools ?? []).map((tool) => <span key={tool} className="chip tool">{tool}</span>)}
              </div>
            </li>
          ))}
        </ol>
        <aside className="dash-side">
          <h3>Tools</h3>
          <ul className="bars">
            {s.tools.map(([name, n]) => (
              <li key={name}><span>{name}</span><i style={{ width: `${(n / maxTool) * 100}%` }} /><b>{n}</b></li>
            ))}
            {s.tools.length === 0 && <li className="muted">No tool calls yet</li>}
          </ul>
          <h3>Knowledge bases</h3>
          <ul className="bars kb">
            {s.knowledgeBases.map(([name, n]) => (
              <li key={name}><span>{name}</span><i style={{ width: `${(n / maxKb) * 100}%` }} /><b>{n}</b></li>
            ))}
            {s.knowledgeBases.length === 0 && <li className="muted">No searches yet</li>}
          </ul>
          <p className="dash-note">
            The feed lives in the API's memory only: prompts are masked, and gone after a restart. The live feed stores nothing.
            Users appear as pseudonyms. Costs are estimates from token counts and list prices.
          </p>
        </aside>
      </div>
    </section>
  );
}
