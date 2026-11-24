import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Orders } from "./components/Orders";
import { dealerState, order } from "./testing/sample";

// The real renderer, and not the test one: what reaches the page.
it("an address with markup is text in the page", () => {
  const state = dealerState({ orders: [order("VO-1002", { address: '<b>Storgatan</b> "1" <img src=x onerror=alert(1)>' })] });
  const html = renderToStaticMarkup(<Orders state={state} busy={new Set()} onDeliver={async () => true} onDelete={async () => true} />);
  expect(html).toContain("&lt;b&gt;Storgatan&lt;/b&gt;");
  expect(html).not.toContain("<b>");
  expect(html).not.toContain("<img");
});

describe("a button", () => {
  it("in the order list never sends a form", () => {
    const html = renderToStaticMarkup(<Orders state={dealerState({ orders: [order("VO-1")] })} busy={new Set()} onDeliver={async () => true} onDelete={async () => true} />);
    for (const b of html.match(/<button[^>]*>/g)!) expect(b).toContain('type="button"');
  });
});
