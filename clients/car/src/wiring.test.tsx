import { createRef, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

// There is no DOM here. Effects run at once, so that what they connect can be used.
const cleanups: (() => void)[] = [];
vi.mock("react", async (original) => ({
  ...(await original<typeof import("react")>()),
  useEffect: (effect: () => void | (() => void)) => {
    const cleanup = effect();
    if (cleanup) cleanups.push(cleanup);
  },
}));

const { useKeys } = await import("./components/Cockpit");
const { Hud } = await import("./components/Hud");
const { Answer } = await import("./components/Answer");
const { ErrorBoundary } = await import("./components/ErrorBoundary");
const { App } = await import("./App");
const { Pedals } = await import("./pedals");

/** What a component returns, with its hooks run as in a page. */
function returned(render: () => ReactNode): ReactNode {
  let result: ReactNode;
  const Probe = () => {
    result = render();
    return null;
  };
  renderToStaticMarkup(<Probe />);
  return result;
}

afterEach(() => {
  cleanups.splice(0).forEach((c) => c());
  vi.unstubAllGlobals();
});

describe("the keys", () => {
  function keys() {
    const page = new EventTarget();
    const doc = { activeElement: null as unknown };
    vi.stubGlobal("window", page);
    vi.stubGlobal("document", doc);
    const car = { speed: 0, target: 0, speedKmh: () => car.speed, setSpeed: (kmh: number) => void (car.target = kmh) };
    const field = createRef<HTMLInputElement>();
    const input = { tagName: "INPUT", closest: () => null, focus: vi.fn(), blur: vi.fn() };
    field.current = input as unknown as HTMLInputElement;
    const onHud = vi.fn();
    returned(() => (useKeys(field, new Pedals(car), onHud), null));
    const send = (type: string, key: string) => {
      const e = Object.assign(new Event(type, { cancelable: true }), { key, ctrlKey: false, metaKey: false, altKey: false, isComposing: false });
      page.dispatchEvent(e);
      return e;
    };
    return { page, doc, car, input, onHud, send };
  }

  it("drive, and nothing scrolls", () => {
    const { car, send } = keys();
    expect(send("keydown", "ArrowUp").defaultPrevented).toBe(true);
    expect(car.target).toBe(150);
    car.speed = 30;
    send("keyup", "ArrowUp");
    expect(car.target).toBeGreaterThan(0);
    expect(car.target).toBeLessThan(150);
  });

  it("let go of the pedals when the window loses the keyboard", () => {
    const { car, page, send } = keys();
    send("keydown", "ArrowDown");
    car.speed = 50;
    page.dispatchEvent(new Event("blur"));
    expect(car.target).toBeGreaterThan(0);
  });

  it("scroll where there is something to scroll", () => {
    const { car, doc, send } = keys();
    doc.activeElement = { tagName: "DIV", closest: () => ({ scrollHeight: 900, clientHeight: 300 }) };
    expect(send("keydown", "ArrowUp").defaultPrevented).toBe(false);
    expect(car.target).toBe(0);
  });

  it("start a message, and do not while the driver is in the field", () => {
    const { doc, input, send } = keys();
    send("keydown", "a");
    expect(input.focus).toHaveBeenCalledTimes(1);
    doc.activeElement = input;
    send("keydown", "b");
    send("keydown", "ArrowUp");
    expect(input.focus).toHaveBeenCalledTimes(1);
  });

  it("put the HUD back, and leave the field", () => {
    const { doc, input, onHud, send } = keys();
    doc.activeElement = input;
    send("keydown", "Escape");
    expect(input.blur).toHaveBeenCalled();
    expect(onHud).toHaveBeenCalledWith("escape");
  });

  it("do nothing any more when the car is gone", () => {
    const { car, send } = keys();
    cleanups.splice(0).forEach((c) => c());
    send("keydown", "ArrowUp");
    expect(car.target).toBe(0);
  });
});

describe("the HUD", () => {
  const hud = (onEvent: (e: string) => void) =>
    returned(() => Hud({ open: true, pending: "Hi", onEvent, onOpenSource: () => {} })) as ReactElement<any>;

  it("tells of the pointer and of the focus, each for itself", () => {
    const onEvent = vi.fn();
    const { props } = hud(onEvent);
    props.onPointerEnter();
    props.onPointerLeave();
    props.onFocus();
    const inside = {};
    props.onBlur({ currentTarget: { contains: (e: unknown) => e === inside }, relatedTarget: inside });
    expect(onEvent.mock.calls.flat()).toEqual(["pointerenter", "pointerleave", "focus"]); // from one part to another
    props.onBlur({ currentTarget: { contains: () => false }, relatedTarget: null });
    expect(onEvent).toHaveBeenLastCalledWith("blur");
  });

  it("marks what scrolls", () => {
    const html = renderToStaticMarkup(hud(() => {}));
    expect(html).toMatch(/class="hud-scroll"[^>]*data-scroll/);
  });
});

describe("an answer", () => {
  it("is not read again when nothing about it has changed", () => {
    expect((Answer as unknown as { $$typeof: symbol }).$$typeof).toBe(Symbol.for("react.memo"));
  });
});

describe("a car that breaks", () => {
  it("says so in plain words, and can be reloaded", () => {
    const reload = vi.fn();
    vi.stubGlobal("location", { reload });
    const boundary = new ErrorBoundary({ children: "the car" });
    expect(boundary.render()).toBe("the car");
    boundary.state = ErrorBoundary.getDerivedStateFromError();
    const shown = boundary.render() as ReactElement;
    const html = renderToStaticMarkup(shown);
    expect(html).toContain("Something went wrong in the car.");
    expect(html).toMatch(/<button[^>]*>Reload<\/button>/);
    const button = findButton(shown)!;
    button.props.onClick();
    expect(reload).toHaveBeenCalled();
  });

  it("is around the car", () => {
    vi.stubEnv("VITE_API_URL", "http://api");
    vi.stubEnv("VITE_DEV_FAKE_AUTH", "true");
    expect((App() as ReactElement).type).toBe(ErrorBoundary);
    vi.unstubAllEnvs();
  });
});

function findButton(node: ReactNode): ReactElement<any> | undefined {
  if (Array.isArray(node)) return node.map(findButton).find(Boolean);
  if (!node || typeof node !== "object" || !("props" in node)) return undefined;
  const element = node as ReactElement<any>;
  return element.type === "button" ? element : findButton(element.props.children);
}
