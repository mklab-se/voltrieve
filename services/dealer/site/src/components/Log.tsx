import { clientName, clock } from "../format";
import { sentence } from "../sentence";
import { entryKey, type DealerState } from "../state";

/** Every call to the service, the newest on top. */
export function Log({ state }: { state: DealerState }) {
  const names = Object.fromEntries(state.models.map((m) => [m.slug, m.name]));
  return (
    <section className="log" aria-labelledby="log-title">
      <div className="section-head">
        <h2 id="log-title">Live log</h2>
      </div>
      {state.log.length === 0 ? (
        <p className="empty">Nothing yet. Every call to the dealer system is shown here.</p>
      ) : (
        <ol className="log-list" tabIndex={0} aria-labelledby="log-title">
          {[...state.log].reverse().map((e) => (
            <li key={entryKey(e)} className="log-entry" data-kind={e.kind} title={e.correlation_id ? `Correlation id ${e.correlation_id}` : undefined}>
              <p className="log-what">{sentence(e, names)}</p>
              <time dateTime={e.at}>{clock(e.at)}</time>
              <p className="log-who">{[e.who ?? "Unknown caller", e.client && clientName(e.client)].filter(Boolean).join(", ")}</p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
