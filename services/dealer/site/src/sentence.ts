import type { LogEntry } from "./state";

/** One sentence for a log entry. `names` are the models' names by slug. */
export function sentence(e: LogEntry, names: Record<string, string>): string {
  const model = e.model && (Object.hasOwn(names, e.model) ? names[e.model] : e.model);
  const of = model ? ` of ${model}` : "";
  const forModel = model ? ` for ${model}` : "";
  const inCity = e.city ? ` in ${e.city}` : "";
  const number = e.number ? ` ${e.number}` : "";
  const text = (): string => {
    switch (e.kind) {
      case "stock": return `Looked up the stock${of}${inCity}`;
      case "part_stock": return `Looked up spare parts${inCity}`;
      case "prices": return `Looked up the prices${of}`;
      case "terms": return `Looked up the terms${forModel}`;
      case "offer": return `Asked for an offer${forModel}`;
      // Without a number there is no order.
      case "order": return e.number ? `Placed order${number}${forModel}` : `Asked to order ${model ?? "a car"}`;
      case "test_drive": return `Asked for a test drive${of}${inCity}`;
      case "delivered": return `Delivered${number || " an order"}${model ? `, ${model},` : ""}${e.city ? ` from ${e.city}` : ""}`;
      // Through the door of the dealers: what was asked for and not done has the reason in the result.
      case "open_orders": return `Looked up the open orders${e.city ? ` for ${e.city}` : ""}`;
      case "change_asked": return `Asked to change the stock${of}${inCity}`;
      case "deliver_asked": return `Asked to deliver${number || " an order"}${model ? `, ${model},` : ""}${e.city ? ` from ${e.city}` : ""}`;
      case "deleted": return `Deleted order${number}`;
      case "stock_changed": return `Changed the stock${of}${inCity}`;
      case "reset": return "Reset the demo";
      case "refused": return "Was refused: no access";
      default: return "Did something this page does not know";
    }
  };
  const result = e.result === null || e.result === undefined || e.result === "" ? "" : `: ${e.result}`;
  return `${text()}${result}`.replace(/[.,]?$/, ".");
}
