import { countOf, stockKey, type DealerState } from "../state";

interface Props {
  state: DealerState;
  flashes: Readonly<Record<string, number>>;
  onChange: (model: string, city: string, change: 1 | -1) => void;
}

export function Inventory({ state, flashes, onChange }: Props) {
  return (
    <section className="inventory" aria-labelledby="inventory-title">
      <div className="section-head">
        <h2 id="inventory-title">Inventory</h2>
      </div>
      <table>
        <thead>
          <tr>
            <th scope="col">
              <span className="sr-only">Model</span>
            </th>
            {state.cities.map((city) => (
              <th key={city} scope="col">
                {city}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {state.models.map((m) => (
            <tr key={m.slug}>
              <th scope="row">{m.name}</th>
              {state.cities.map((city) => {
                const count = countOf(state.stock, m.slug, city);
                const flash = flashes[stockKey(m.slug, city)] ?? 0;
                return (
                  <td key={city}>
                    <div className="step">
                      <button type="button" aria-label={`One ${m.name} less in ${city}`} disabled={count === 0} onClick={() => onChange(m.slug, city, -1)}>
                        <Sign d="M5 12h14" />
                      </button>
                      {/* A new key plays the flash again. */}
                      <span key={flash} className={`count${count === 0 ? " none" : ""}${flash ? " flash" : ""}`}>
                        {count}
                      </span>
                      <button type="button" aria-label={`One ${m.name} more in ${city}`} onClick={() => onChange(m.slug, city, 1)}>
                        <Sign d="M5 12h14M12 5v14" />
                      </button>
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Sign({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}
