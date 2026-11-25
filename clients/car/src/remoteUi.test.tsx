import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CenterScreen } from "./components/CenterScreen";
import { DriverDisplay } from "./components/DriverDisplay";
import { Menu } from "./components/Menu";
import { readGauges } from "./gauges";
import { Pedals } from "./pedals";
import { newCar } from "./sim/car";
import { initialChatState } from "./turns";
import type { Remote } from "./useRemote";
import { Workspace } from "./Workspace";

const nothing = () => {};
const gauges = readGauges(newCar(), "off");

const screen = (remote: Partial<Remote>) =>
  renderToStaticMarkup(
    <CenterScreen
      style={{}}
      state={initialChatState}
      busy={false}
      lifted={false}
      gauges={gauges}
      pedals={new Pedals({ speedKmh: () => 0, setSpeed: nothing })}
      field={createRef<HTMLInputElement>()}
      now={() => new Date()}
      onSetClock={nothing}
      motorOn={false}
      onMotor={nothing}
      onSend={nothing}
      onPark={nothing}
      onOpenSource={nothing}
      remote={{ on: false, setOn: nothing, status: "connecting", notes: [], flashing: false, ...remote }}
    />,
  );

describe("remote control in the menu", () => {
  it.each([false, true])("is a switch that says whether it is on (%s)", (on) => {
    const html = renderToStaticMarkup(
      <Menu openAtFirst onNewChat={nothing} onSignOut={nothing} username="dev@x" underTheHood={false} onUnderTheHood={nothing} remote={on} onRemote={nothing} />,
    );
    expect(html).toMatch(new RegExp(`role="switch" aria-checked="${on}">Remote control`));
  });

  it("is off when the page loads", () => {
    const auth = { kind: "fake" as const, init: async () => null, signIn: async () => {}, signOut: async () => {}, getToken: async () => "token" };
    const config = { apiUrl: "http://api", tenantId: "", clientId: "", scope: "", fakeAuth: true };
    const html = renderToStaticMarkup(<Workspace config={config} auth={auth} session={{ name: "Dev", username: "dev@x" }} />);
    expect(html).not.toContain("Remote control");
    expect(html).not.toContain("flashing");
  });
});

describe("the center screen", () => {
  it("says while remote control is on", () => {
    expect(screen({})).not.toContain("Remote control");
    expect(screen({ on: true })).toContain("Remote control is connecting");
    expect(screen({ on: true, status: "linked" })).toContain("Remote control is on");
  });

  it("says what was done, in a live region that is there before", () => {
    expect(screen({})).toContain('<div role="status" aria-live="polite"></div>');
    const html = screen({ on: true, notes: [{ id: 1, text: "Claude honked the horn." }] });
    expect(html).toMatch(/<div role="status" aria-live="polite"><p[^>]*>Claude honked the horn\.<\/p><\/div>/);
  });

  it("shows text, whatever the app is called", () => {
    const html = screen({ on: true, notes: [{ id: 1, text: "<img src=x onerror=alert(1)> honked the horn." }] });
    expect(html).not.toContain("<img src=x");
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt; honked the horn.");
  });
});

describe("the driver display", () => {
  it("has both arrows while the lights flash", () => {
    expect(renderToStaticMarkup(<DriverDisplay gauges={gauges} style={{}} />)).not.toContain("dd-turn");
    const html = renderToStaticMarkup(<DriverDisplay gauges={gauges} flashing style={{}} />);
    expect(html).toMatch(/class="dd-turn flashing" aria-label="The lights flash">(<svg.*<\/svg>){2}<\/p>/);
  });
});
