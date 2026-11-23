import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import catalog from "../../../data/voltrieve.yaml?raw";
import { buildChatRequest } from "./chat";
import { MyCar } from "./components/MyCar";
import { MY_CAR, webContext } from "./garage";

/** A moment, as a browser with this offset from UTC sees it. */
function at(utc: string, offsetMinutes: number): Date {
  const date = new Date(utc);
  vi.spyOn(date, "getTimezoneOffset").mockReturnValue(-offsetMinutes);
  return date;
}

describe("the context", () => {
  const base = { apiUrl: "https://api", token: "t", backend: "loop" as const, message: "hi", correlationId: "x" };

  it("goes with the question, as the car's facts and the local time", () => {
    const { init } = buildChatRequest({ ...base, context: webContext(at("2026-11-24T08:05:09.123Z", 60)) });
    expect(JSON.parse(init.body as string).context).toEqual({
      car: {
        model: "Voltrieve Spark",
        trim: "Plus",
        drive: "RWD",
        model_year: 2026,
        registration: "SPARK",
        odometer_km: 12400,
      },
      local_time: "2026-11-24T09:05:09+01:00",
    });
  });

  it.each([
    [0, "2026-11-24T22:30:00+00:00"],
    [120, "2026-11-25T00:30:00+02:00"],
    [-210, "2026-11-24T19:00:00-03:30"],
    [345, "2026-11-25T04:15:00+05:45"],
  ])("has the time of the browser, %i minutes from UTC", (offset, expected) => {
    expect(webContext(at("2026-11-24T22:30:00Z", offset)).local_time).toBe(expected);
  });

  it("is left out when there is none", () => {
    expect(JSON.parse(buildChatRequest(base).init.body as string)).not.toHaveProperty("context");
  });
});

/** The lines of one list item: from "  - name: X" to the next item or the next top level key. */
function model(name: string): string {
  const lines = catalog.split("\n");
  const start = lines.indexOf(`  - name: ${name}`);
  expect(start).toBeGreaterThan(-1);
  const end = lines.findIndex((line, i) => i > start && /^( {2}- |\S)/.test(line));
  return lines.slice(start, end === -1 ? undefined : end).join("\n");
}

const number = (key: string) => Number(catalog.match(new RegExp(`^ {2}${key}: (\\d+)$`, "m"))?.[1]);

describe("the car in the catalog", () => {
  const spark = model(MY_CAR.model);

  it("has the trim, the drive and the color", () => {
    expect(spark).toMatch(new RegExp(`^ {6}- name: ${MY_CAR.trim}$`, "m"));
    expect(spark).toMatch(new RegExp(`^ {4}drive: \\[.*\\b${MY_CAR.drive}\\b.*\\]$`, "m"));
    // The picture has white wheel covers.
    expect(spark).toMatch(new RegExp(`^ {6}- name: ${MY_CAR.trim}$(?:\\n {8}.*)*?\\n {8}wheel: pebble_white$`, "m"));
    const color = spark.match(new RegExp(`^ {6}- \\{ key: \\w+, name: ${MY_CAR.color}, .*hex: "(#\\w+)"`, "m"));
    expect(color?.[1]).toBeDefined();
    expect(renderToStaticMarkup(createElement(MyCar, { owner: "Dev" }))).toContain(`background:${color![1]}`);
  });

  it("is due for its first service by the catalog's interval", () => {
    const [, month, year, km] = MY_CAR.nextService.match(/^(\w+) (\d{4}) or ([\d,]+) km$/)!;
    expect(month).toBe("March");
    expect((Number(year) - MY_CAR.model_year) * 12).toBe(number("service_interval_months"));
    expect(Number(km!.replace(",", ""))).toBe(number("service_interval_km"));
    expect(MY_CAR.odometer_km).toBeLessThan(number("service_interval_km"));
  });
});

describe("the card", () => {
  const html = renderToStaticMarkup(createElement(MyCar, { owner: "Dev Tester" }));
  const facts = [...html.matchAll(/<dt>([^<]+)<\/dt><dd>(.*?)<\/dd>/g)].map(([, key, value]) => [key, value!.replace(/<[^>]+>/g, "")]);

  it("shows the owner, the picture and the facts", () => {
    expect(html).toContain("Dev Tester");
    expect(html).toMatch(/<img[^>]*src="\/spark\.webp"/);
    expect(html).toMatch(/<img[^>]*width="1200" height="800"/);
    expect(html).toContain("Voltrieve Spark");
    expect(facts).toEqual([
      ["Registration", "SPARK"],
      ["Model year", "2026"],
      ["Trim", "Plus, RWD"],
      ["Color", "Saffron"],
      ["Odometer", "12,400 km"],
      ["Next service", "March 2028 or 30,000 km"],
    ]);
  });

  it("folds to a bar with a named button on a small screen", () => {
    expect(html).toMatch(/<button[^>]*aria-expanded="false"[^>]*>/);
  });
});
