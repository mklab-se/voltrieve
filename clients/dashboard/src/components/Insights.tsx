import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { Auth } from "../auth";
import {
  about, askView, cut, day, label, loadInsights, moment, runs, sampleNote, trends,
  type Insights, type InsightsRow, type Source, type TopicView, type Trends,
} from "../insights";

export type AskState = { status: "loading" } | { status: "failed" } | { status: "ready"; data: Insights };

/** The totals that the nightly job saved. Real and made up data are asked for, and shown, one at a time. */
export function Ask({ apiUrl, auth }: { apiUrl: string; auth: Auth }) {
  const [source, setSource] = useState<Source>("real");
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<AskState>({ status: "loading" });

  useEffect(() => {
    const ctrl = new AbortController();
    setState({ status: "loading" });
    loadInsights(apiUrl, auth, source, ctrl.signal).then(
      (data) => !ctrl.signal.aborted && setState({ status: "ready", data }),
      () => !ctrl.signal.aborted && setState({ status: "failed" }),
    );
    return () => ctrl.abort();
  }, [apiUrl, auth, source, attempt]);

  return <AskPage source={source} state={state} onSource={setSource} onRetry={() => setAttempt((n) => n + 1)} />;
}

interface PageProps {
  source: Source;
  state: AskState;
  onSource: (source: Source) => void;
  onRetry: () => void;
}

const SOURCES: [Source, string][] = [["real", "Real"], ["seeded", "Made up"]];

export function AskPage({ source, state, onSource, onRetry }: PageProps) {
  // Right after the switch, the answer is still the other source's. It is never shown under this one's name.
  const data = state.status === "ready" && state.data.source === source ? state.data : undefined;
  return (
    <section className="dash ask" aria-label="What people ask">
      <div className="dash-head">
        <h2>What people ask</h2>
        <fieldset className="source">
          <legend className="sr-only">Which data to show</legend>
          {SOURCES.map(([value, name]) => (
            <label key={value}>
              <input type="radio" name="source" value={value} checked={source === value} onChange={() => onSource(value)} />
              <span>{name}</span>
            </label>
          ))}
        </fieldset>
      </div>
      <p className="ask-note">Only topics shared by at least five people are shown, and counts are rounded.</p>
      <p className={`ask-status ${source}`} role="status">
        <strong>{source === "real" ? "This is real use." : "This is made up data, for demos."}</strong>
        {data && <span>{data.run ? `Last run ${moment(data.run)}.` : "No run yet."}</span>}
        {data?.window && <span>Conversations started {day(data.window.start)} to {day(data.window.end)}.</span>}
        {data && sampleNote(data.sample_share) && <span>{sampleNote(data.sample_share)}.</span>}
        {!data && state.status !== "failed" && <span>Loading…</span>}
      </p>
      {state.status === "failed" && (
        <div className="dash-problem ask-failed" role="alert">
          <span>What people ask could not be loaded.</span>
          <button className="ghost" onClick={onRetry}>Try again</button>
        </div>
      )}
      {data && <Results data={data} />}
    </section>
  );
}

function Results({ data }: { data: Insights }) {
  const view = useMemo(() => askView(data), [data]);
  const weeks = useMemo(() => trends(data), [data]);
  const empty = data.rows.length === 0;
  return (
    <>
      {empty && (
        <p className="feed-empty">
          {data.source === "real" ? "Not enough people yet. Only what at least five people share is shown." : "No made up data yet."}
        </p>
      )}
      {view.total && (
        <div className="tiles ask-tiles">
          <div className="tile big"><span className="tile-n">{about(view.total.people)} people</span><span className="tile-l">asked something</span></div>
          <div className="tile"><span className="tile-n">{about(view.total.conversations)} conversations</span><span className="tile-l">were counted</span></div>
        </div>
      )}
      {view.topics.length > 0 && <Topics topics={view.topics} />}
      {view.models.length > 0 && <Models topics={view.topics} models={view.models} />}
      {view.clients.length > 0 && <Clients clients={view.clients} />}
      {weeks.series.length > 0 && <Weeks weeks={weeks} />}
      {!empty && (
        <p className="dash-note">
          A person counts as at most three conversations in each number, so the parts can add up to more than the whole. What is
          missing has fewer than five people, or is held back so that fewer than five cannot be worked out.
        </p>
      )}
    </>
  );
}

/** Text from the API: always text, at most 80 characters, with all of it as title. */
function Name({ text }: { text: string }) {
  const short = cut(text);
  return <bdi title={short === text ? undefined : text}>{short}</bdi>;
}

function Card({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="card" aria-label={title}>
      <h3>{title}</h3>
      {note && <p className="dash-note">{note}</p>}
      {children}
    </section>
  );
}

function counts(item: { people: number; conversations: number }): string {
  return `${about(item.people)} people, ${about(item.conversations)} conversations`;
}

function Answered({ share }: { share: number | null }) {
  return <span className="answered">Answered: {share === null ? "too few to say" : `about ${share} % of the answers counted`}</span>;
}

function Bar({ value, max }: { value: number; max: number }) {
  return <span className="bar" aria-hidden="true"><i style={{ width: `${(value / max) * 100}%` }} /></span>;
}

function Topics({ topics }: { topics: TopicView[] }) {
  const max = Math.max(1, ...topics.map((t) => t.people));
  return (
    <Card title="Topics" note="A bar is the number of people. Open a topic to see its groups. Only results that at least five people share are in the share answered.">
      <ul className="topics">
        {topics.map((t) => (
          <li key={t.topic}>
            <details>
              <summary>
                <span className="topic-name"><Name text={label("topic", t.topic)} /></span>
                <span className="topic-n">{counts(t)}</span>
                <Bar value={t.people} max={max} />
                <Answered share={t.answered} />
              </summary>
              <Inside topic={t} />
            </details>
          </li>
        ))}
      </ul>
    </Card>
  );
}

const TOP_GROUPS = 10;

function Inside({ topic }: { topic: TopicView }) {
  const { groups } = topic;
  const max = Math.max(1, ...groups.map((g) => g.people));
  return (
    <div className="inside">
      <h4>{groups.length > TOP_GROUPS ? `The ${TOP_GROUPS} largest of ${groups.length} groups` : "Groups"}</h4>
      {groups.length === 0 && <p className="muted">No group has enough people.</p>}
      <ul className="topics">
        {groups.slice(0, TOP_GROUPS).map((g) => (
          <li key={g.name} className="group">
            <span className="topic-name"><Name text={label("group", g.name)} /></span>
            <span className="topic-n">{counts(g)}</span>
            <Bar value={g.people} max={max} />
            <Answered share={g.answered} />
          </li>
        ))}
      </ul>
      <People title="Results" rows={topic.results} name={(r) => label("result", r.result)} />
      <People title="What they want" rows={topic.kinds} name={(r) => label("kind", r.request_kind)} />
    </div>
  );
}

function People({ title, rows, name }: { title: string; rows: InsightsRow[]; name: (row: InsightsRow) => string }) {
  if (rows.length === 0) return null;
  return (
    <>
      <h4>{title}</h4>
      <ul className="facts">
        {rows.map((r) => <li key={name(r)}><Name text={name(r)} />: {about(r.people)} people</li>)}
      </ul>
    </>
  );
}

function Scroll({ title, children }: { title: string; children: ReactNode }) {
  return <div className="scroll" role="region" aria-label={title} tabIndex={0}>{children}</div>;
}

function NotShown() {
  return <><span aria-hidden="true">–</span><span className="sr-only">not shown</span></>;
}

function Models({ topics, models }: { topics: TopicView[]; models: string[] }) {
  const shown = topics.filter((t) => Object.keys(t.models).length > 0);
  const max = Math.max(1, ...shown.flatMap((t) => Object.values(t.models)));
  return (
    <Card title="Topics per car model" note="People. A stronger color is more people, and – is not shown.">
      <Scroll title="Topics per car model, as a table">
        <table className="heat">
          <thead>
            <tr><th scope="col">Topic</th>{models.map((m) => <th scope="col" key={m}><Name text={label("model", m)} /></th>)}</tr>
          </thead>
          <tbody>
            {shown.map((t) => (
              <tr key={t.topic}>
                <th scope="row"><Name text={label("topic", t.topic)} /></th>
                {models.map((m) => {
                  const people = t.models[m];
                  if (people === undefined) return <td key={m} className="muted"><NotShown /></td>;
                  const strength = `${Math.round(8 + (people / max) * 32)}%`;
                  return <td key={m} style={{ background: `color-mix(in srgb, var(--accent) ${strength}, var(--surface))` }}>{about(people)}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </Scroll>
    </Card>
  );
}

function Clients({ clients }: { clients: InsightsRow[] }) {
  const max = Math.max(1, ...clients.map((c) => c.people));
  return (
    <Card title="By client" note="Where people asked. Topics are not saved per client.">
      <ul className="topics">
        {clients.map((c) => (
          <li key={c.client} className="group">
            <span className="topic-name"><Name text={label("client", c.client)} /></span>
            <span className="topic-n">{counts(c)}</span>
            <Bar value={c.people} max={max} />
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Weeks({ weeks }: { weeks: Trends }) {
  return (
    <Card
      title="People per week"
      note={`The twelve weeks to ${day(weeks.ends[weeks.ends.length - 1])}. Every chart has the same scale, from none to ${about(weeks.max)} people. A week without a dot is not shown.`}
    >
      <ul className="lines">
        {weeks.series.map((s) => (
          <li key={s.topic}>
            <span className="topic-name"><Name text={label("topic", s.topic)} /></span>
            <Line name={label("topic", s.topic)} points={s.points} weeks={weeks} />
          </li>
        ))}
      </ul>
      <details className="as-table">
        <summary>Show the weeks as a table</summary>
        <Scroll title="People per week, as a table">
          <table>
            <thead>
              <tr><th scope="col">Week to</th>{weeks.series.map((s) => <th scope="col" key={s.topic}><Name text={label("topic", s.topic)} /></th>)}</tr>
            </thead>
            <tbody>
              {weeks.ends.map((end, i) => (
                <tr key={end}>
                  <th scope="row">{day(end)}</th>
                  {weeks.series.map((s) => {
                    const people = s.points[i];
                    return <td key={s.topic}>{people === null ? <NotShown /> : about(people)}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </Scroll>
      </details>
    </Card>
  );
}

const W = 240;
const H = 72;
const PAD = 8;

function Line({ name, points, weeks }: { name: string; points: (number | null)[]; weeks: Trends }) {
  const step = (W - 2 * PAD) / (points.length - 1);
  const x = (i: number) => PAD + i * step;
  const y = (people: number) => H - PAD - (people / Math.max(1, weeks.max)) * (H - 2 * PAD);
  const seen = points.flatMap((p, i) => (p === null ? [] : [{ i, p }]));
  const last = seen[seen.length - 1];
  const missing = points.length - seen.length;
  const words = [
    `${cut(name)}, people per week.`,
    `Last shown: ${about(last.p)} in the week to ${day(weeks.ends[last.i])}.`,
    `Most: ${about(Math.max(...seen.map((s) => s.p)))}.`,
    missing > 0 ? `${missing} of ${points.length} weeks not shown.` : "",
  ].join(" ").trim();
  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={words}>
        <line className="axis" x1={PAD} x2={W - PAD} y1={H - PAD} y2={H - PAD} />
        {runs(points).map((run) => run.length > 1 && (
          <polyline key={run[0][0]} points={run.map(([i, p]) => `${x(i).toFixed(1)},${y(p).toFixed(1)}`).join(" ")} />
        ))}
        {seen.map(({ i, p }) => <circle key={i} className={i === last.i ? "end" : ""} cx={x(i)} cy={y(p)} r={i === last.i ? 4 : 2.5} />)}
        {seen.map(({ i, p }) => (
          <rect key={i} x={x(i) - step / 2} y={0} width={step} height={H}>
            <title>{`${about(p)} people, week to ${day(weeks.ends[i])}`}</title>
          </rect>
        ))}
      </svg>
      <span className="line-last">{about(last.p)} in the week to {day(weeks.ends[last.i])}</span>
    </>
  );
}
