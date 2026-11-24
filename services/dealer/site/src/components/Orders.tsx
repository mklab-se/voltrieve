import { useRef, useState } from "react";
import { money, place, when } from "../format";
import { deliverOptions, type DealerState, type Order, type Stock } from "../state";
import { Confirm } from "./Confirm";

interface Props {
  state: DealerState;
  busy: ReadonlySet<string>;
  /** The order whose paper was put in the list last. */
  landed?: string;
  onDeliver: (number: string, city: string) => Promise<boolean>;
  onDelete: (number: string) => Promise<boolean>;
}

export function Orders({ state, busy, landed, onDeliver, onDelete }: Props) {
  const [asking, setAsking] = useState<string>();
  const heading = useRef<HTMLHeadingElement>(null);
  const asked = state.orders.find((o) => o.number === asking);
  const open = state.orders.filter((o) => o.status === "open").length;

  function remove(number: string) {
    setAsking(undefined);
    // The row goes away, and the focus with it.
    void onDelete(number).then((ok) => ok && heading.current?.focus());
  }

  return (
    <section className="orders" aria-labelledby="orders-title">
      <div className="section-head">
        <h2 id="orders-title" ref={heading} tabIndex={-1}>
          Purchase orders
        </h2>
        <span className={open > 0 ? "open-count some" : "open-count"}>{`${open} open`}</span>
      </div>
      {state.orders.length === 0 ? (
        <p className="empty">No orders yet. An order appears here when Runa places it.</p>
      ) : (
        <ul className="order-list">
          {state.orders.map((o) => (
            <Row
              key={o.number}
              order={o}
              model={state.models.find((m) => m.slug === o.model)?.name ?? o.model}
              stock={state.stock}
              busy={busy.has(o.number)}
              landed={o.number === landed}
              onDeliver={onDeliver}
              onAsk={() => setAsking(o.number)}
            />
          ))}
        </ul>
      )}
      {asked && (
        <Confirm
          title={`Delete order ${asked.number}?`}
          text="The order is removed for good. The stock does not change."
          keep="Keep the order"
          go="Delete the order"
          onKeep={() => setAsking(undefined)}
          onGo={() => remove(asked.number)}
        />
      )}
    </section>
  );
}

interface RowProps {
  order: Order;
  model: string;
  stock: Stock;
  busy: boolean;
  landed: boolean;
  onDeliver: (number: string, city: string) => Promise<boolean>;
  onAsk: () => void;
}

/** What customers wrote is text in React, and stays in its own direction. */
function Row({ order: o, model, stock, busy, landed, onDeliver, onAsk }: RowProps) {
  const remove = (
    <button type="button" className="ghost" aria-label={`Delete ${o.number}`} disabled={busy} onClick={onAsk}>
      Delete
    </button>
  );
  return (
    <li className={`order ${o.status}${landed ? " landed" : ""}`} data-order={o.number}>
      <p className="order-what">
        <span className="order-number">{o.number}</span> <span className="order-model">{model}</span>{" "}
        <span className="order-price">{money(o.price_sek)}</span>
      </p>
      <p className="order-who">
        <bdi className="order-name">{o.name}</bdi> <bdi className="order-address">{o.address}</bdi>
      </p>
      {o.status === "open" ? (
        <>
          <p className="order-when">{`Ordered ${when(o.created)} ${place(o.client)}`}</p>
          <div className="order-do">
            <Deliver order={o} stock={stock} busy={busy} onDeliver={onDeliver} />
            {remove}
          </div>
        </>
      ) : (
        <>
          <p className="order-when">
            {`Delivered from ${o.city ?? "stock"} ${when(o.delivered)}`}
            {o.delivered_by && ` by ${o.delivered_by}`}
          </p>
          <div className="order-do">{remove}</div>
        </>
      )}
    </li>
  );
}

function Deliver({ order, stock, busy, onDeliver }: Pick<RowProps, "order" | "stock" | "busy" | "onDeliver">) {
  const options = deliverOptions(stock, order.model);
  const [city, setCity] = useState(options[0]?.city);
  // It starts on the city with the most, and moves only when that city has none left. A city that
  // changed under the pointer would deliver from the wrong place.
  if (options.length > 0 && !options.some((o) => o.city === city)) setCity(options[0].city);
  if (options.length === 0) {
    return (
      <button type="button" className="primary" disabled>
        None in stock
      </button>
    );
  }
  return (
    <>
      <label className="from">
        <span>Deliver from</span>
        <select aria-label={`Deliver ${order.number} from`} value={city} disabled={busy} onChange={(e) => setCity(e.target.value)}>
          {options.map((o) => (
            <option key={o.city} value={o.city}>
              {`${o.city} (${o.count})`}
            </option>
          ))}
        </select>
      </label>
      <button type="button" className="primary" aria-label={`Deliver ${order.number}`} disabled={busy} onClick={() => city && void onDeliver(order.number, city)}>
        Deliver
      </button>
    </>
  );
}
