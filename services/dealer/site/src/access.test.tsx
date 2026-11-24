import { afterEach, describe, expect, it, vi } from "vitest";
import { dealerState, fakeApi, settle } from "./testing/sample";
import { show } from "./testing/react";

vi.mock("react", async () => (await import("./testing/react")).mocked);
const { Desk } = await import("./App");
const stops: (() => void)[] = [];
afterEach(() => stops.splice(0).forEach((s) => s()));

async function page() {
  const f = fakeApi();
  const onSignOut = vi.fn();
  const p = show(<Desk api={f.api} session={{ name: "Kim Berg", username: "kim@voltrieve.example" }} theme="dark" onToggleTheme={() => {}} onSignOut={onSignOut} />);
  stops.push(p.unmount);
  await settle();
  return { ...f, ...p, onSignOut };
}

describe("a 403", () => {
  it("says that the account has no access, and shows nothing else", async () => {
    const p = await page();
    p.reads[0].reject(p.error(403, "This account has no access."));
    await settle();
    expect(p.text()).toContain("This account has no access");
    expect(p.text()).toContain("kim@voltrieve.example");
    expect(p.text()).not.toContain("Inventory");
    expect(p.has("Reset the demo")).toBe(false);
  });

  it("later takes away what was shown", async () => {
    const p = await page();
    p.reads[0].resolve(dealerState());
    await settle();
    expect(p.text()).toContain("Inventory");
    p.streams[0].end(p.error(403, "no"));
    await settle();
    expect(p.text()).toContain("This account has no access");
    expect(p.text()).not.toContain("Inventory");
  });
});

describe("before the first answer", () => {
  it("shows no orders and no stock, and the problem when there is one", async () => {
    const p = await page();
    expect(p.text()).not.toContain("Inventory");
    p.reads[0].reject(p.error(503, "The dealer system is not available right now."));
    await settle();
    expect(p.text()).toContain("The dealer system is not available right now.");
    expect(p.text()).not.toContain("no access");
  });
});
