/** What GET /admin/insights gives. A missing row is under five people, or held back with one that is. */
export type Source = "real" | "seeded";

export interface InsightsRow {
  cut: string; // all, client, topic, topic_model, topic_result, topic_kind, group or group_result
  client: string;
  topic: string;
  car_model: string;
  request_kind: string;
  result: string;
  group: string;
  conversations: number;
  people: number;
}

export interface Insights {
  source: Source;
  run: string | null;
  window: { start: string; end: string } | null; // both days are included
  sample_share: number | null;
  rows: InsightsRow[];
  weeks: { end: string; topic: string; conversations: number; people: number }[];
}

// The two tabs, kept in the address.

export type Tab = "live" | "ask";
export const TABS: [Tab, string][] = [["live", "Live"], ["ask", "What people ask"]];

export function tabFromHash(hash: string): Tab {
  return hash === "#ask" ? "ask" : "live";
}

/** The tab a key moves to, or null for a key that moves nowhere. */
export function nextTab(tab: Tab, key: string): Tab | null {
  if (key === "ArrowLeft" || key === "ArrowRight") return tab === "live" ? "ask" : "live";
  if (key === "Home") return "live";
  return key === "End" ? "ask" : null;
}

// The request.

export function buildInsightsRequest(apiUrl: string, token: string, source: Source): { url: string; init: RequestInit } {
  return {
    url: `${apiUrl.replace(/\/+$/, "")}/admin/insights?source=${source}`,
    init: { headers: { Accept: "application/json", Authorization: `Bearer ${token}` }, cache: "no-store" },
  };
}

/** An answer for another source is a failure: made up data must never be shown as real. */
export async function loadInsights(apiUrl: string, auth: { getToken(): Promise<string> }, source: Source, signal: AbortSignal): Promise<Insights> {
  const token = await auth.getToken();
  signal.throwIfAborted();
  const { url, init } = buildInsightsRequest(apiUrl, token, source);
  const response = await fetch(url, { ...init, signal });
  if (!response.ok) throw new Error(`status ${response.status}`);
  const data = (await response.json()) as Insights | null;
  if (data?.source !== source || !Array.isArray(data.rows) || !Array.isArray(data.weeks)) throw new Error("not an answer");
  return { ...data, rows: data.rows.filter(counted), weeks: data.weeks.filter(counted) };
}

/** A row without a number says nothing. */
function counted(row: { people: unknown; conversations: unknown }): boolean {
  return Number.isFinite(row.people) && Number.isFinite(row.conversations);
}

// Words.

const LABELS = {
  topic: {
    charging: "Charging",
    range_battery: "Range and battery",
    price_purchase: "Price and purchase",
    compare_models: "Comparing models",
    features_specs: "Features and specifications",
    service_warranty: "Service and warranty",
    parts_accessories: "Parts and accessories",
    test_drive_dealer: "Test drives and dealers",
    car_controls: "Controls in the car",
    fault: "Faults and damage",
    signs_parking: "Signs and parking",
    app_account: "App and account",
    other: "Other",
  },
  result: { answered: "Answered", partly: "Partly answered", not_found: "Not found", handed_off: "Sent on to someone else", refused: "Refused" },
  kind: { how_to: "How to", compare: "Compare", buy: "Buy", problem: "Problem", command: "Command", smalltalk: "Small talk" },
  model: { spark: "Spark", lumen: "Lumen", ragn: "Ragn", fjord: "Fjord", atlas: "Atlas" },
  client: { car: "Car", web: "Website" },
  group: { other: "Smaller groups together", "unnamed group": "Unnamed group" },
} satisfies Record<string, Record<string, string>>;

/** The friendly name of a label, or the label as it is. */
export function label(of: keyof typeof LABELS, value: string): string {
  const names: Record<string, string> = LABELS[of];
  return Object.hasOwn(names, value) ? names[value] : value;
}

const LONGEST = 80;

export function cut(text: string): string {
  const characters = [...text];
  return characters.length > LONGEST ? `${characters.slice(0, LONGEST - 1).join("")}…` : text;
}

export function about(count: number): string {
  return `about ${count.toLocaleString("en")}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A day such as 22 Nov 2026. The windows are whole days in UTC. */
export function day(iso: string): string {
  const [year, month, date] = iso.slice(0, 10).split("-").map(Number);
  return MONTHS[month - 1] && date && year ? `${date} ${MONTHS[month - 1]} ${year}` : iso;
}

/** A moment such as 24 Nov 2026, 02:15 UTC. */
export function moment(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  const utc = at.toISOString();
  return `${day(utc)}, ${utc.slice(11, 16)} UTC`;
}

export function sampleNote(share: number | null): string | null {
  // Rounded down, so that a sample never says 100 %.
  return share !== null && share < 1 ? `A sample of ${Math.floor(share * 100 + 1e-9)} % of the conversations` : null;
}

// What the page shows.

/** Answered, in percent of the conversations with a saved result. Nothing when that cannot be said:
 *  no row for answered means that it is not saved, not that it is none. */
export function answeredShare(results: InsightsRow[]): number | null {
  const answered = results.find((r) => r.result === "answered");
  const all = results.reduce((sum, r) => sum + r.conversations, 0);
  return answered && all > 0 ? Math.round((answered.conversations / all) * 100) : null;
}

export interface GroupView {
  name: string;
  people: number;
  conversations: number;
  answered: number | null;
}

export interface TopicView {
  topic: string;
  people: number;
  conversations: number;
  answered: number | null;
  groups: GroupView[];
  results: InsightsRow[];
  kinds: InsightsRow[];
  models: Record<string, number>; // people per car model, only the saved ones
}

/** Most people first, then by name, and `other` last. */
function ordered<T extends { people: number }>(items: T[], name: (item: T) => string): T[] {
  return [...items].sort(
    (a, b) => Number(name(a) === "other") - Number(name(b) === "other") || b.people - a.people || (name(a) < name(b) ? -1 : 1),
  );
}

export function askView(data: Insights): { total?: InsightsRow; clients: InsightsRow[]; topics: TopicView[]; models: string[] } {
  const of = (cutName: string) => data.rows.filter((r) => r.cut === cutName);
  const results = of("topic_result");
  const kinds = of("topic_kind");
  const models = of("topic_model");
  const groups = of("group");
  const groupResults = of("group_result");
  const topics = of("topic").map(({ topic, people, conversations }): TopicView => {
    const own = results.filter((r) => r.topic === topic);
    return {
      topic,
      people,
      conversations,
      answered: answeredShare(own),
      groups: ordered(
        groups.filter((g) => g.topic === topic).map((g) => ({
          name: g.group,
          people: g.people,
          conversations: g.conversations,
          answered: answeredShare(groupResults.filter((r) => r.topic === topic && r.group === g.group)),
        })),
        (g) => g.name,
      ),
      results: ordered(own, (r) => r.result),
      kinds: ordered(kinds.filter((k) => k.topic === topic), (k) => k.request_kind),
      models: Object.fromEntries(models.filter((m) => m.topic === topic).map((m) => [m.car_model, m.people])),
    };
  });
  return {
    total: of("all")[0],
    clients: ordered(of("client"), (c) => c.client),
    topics: ordered(topics, (t) => t.topic),
    models: [...new Set(models.map((m) => m.car_model))].sort(),
  };
}

const WEEKS = 12;
const DAY_MS = 86_400_000;

function dayNumber(iso: string): number {
  return Date.parse(`${iso.slice(0, 10)}T00:00:00Z`) / DAY_MS;
}

export interface Trends {
  ends: string[]; // the last day of each week, oldest first
  series: { topic: string; points: (number | null)[] }[];
  max: number;
}

/** People per topic and week, counted back from the last window.
 *
 * Every night has a window of seven days, so there can be several in a week. One night stands for its
 * week, and the same one for all topics: the latest. A topic without a row that night has no number.
 */
export function trends(data: Insights): Trends {
  const weeks = data.weeks.filter((w) => !Number.isNaN(dayNumber(w.end)));
  const last = Math.max(...weeks.map((w) => dayNumber(w.end)), data.window ? dayNumber(data.window.end) : -Infinity);
  if (!Number.isFinite(last)) return { ends: [], series: [], max: 0 };

  const ends = Array.from({ length: WEEKS }, (_, i) => {
    const nominal = last - (WEEKS - 1 - i) * 7;
    const nights = weeks.map((w) => dayNumber(w.end)).filter((n) => n > nominal - 7 && n <= nominal);
    return nights.length ? Math.max(...nights) : nominal;
  });
  const shown = weeks.filter((w) => ends.includes(dayNumber(w.end)));
  const listed = askView(data).topics.map((t) => t.topic);
  const rest = [...new Set(shown.map((w) => w.topic))].filter((t) => !listed.includes(t)).sort();
  const series = [...listed, ...rest]
    .map((topic) => ({
      topic,
      points: ends.map((end) => shown.find((w) => w.topic === topic && dayNumber(w.end) === end)?.people ?? null),
    }))
    .filter((s) => s.points.some((p) => p !== null));
  return {
    ends: ends.map((n) => new Date(n * DAY_MS).toISOString().slice(0, 10)),
    series,
    max: Math.max(0, ...series.flatMap((s) => s.points.map((p) => p ?? 0))),
  };
}

/** The stretches without a missing week, as [week, people]. */
export function runs(points: (number | null)[]): [number, number][][] {
  const found: [number, number][][] = [];
  points.forEach((p, i) => {
    if (p === null) return;
    if (i > 0 && points[i - 1] !== null) found[found.length - 1].push([i, p]);
    else found.push([[i, p]]);
  });
  return found;
}
