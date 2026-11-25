// Tiny mock of the Web API's GET /admin/live and GET /admin/insights, for running and screenshotting
// the dashboard without Azure. It sends a made-up stream of turns, and made-up totals for both sources.
//
//   npm run mock            (listens on :8788)
//   MOCK_ASK=empty npm run mock     (no totals yet; MOCK_ASK=fail answers 503)
//   npm run dev:mock        (the app with fake auth pointing here)
import { createServer } from "node:http";
import { randomUUID } from "node:crypto";

const PORT = Number(process.env.PORT ?? 8788);

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};

// GET /admin/live: a made-up stream of turns, so the dashboard can be seen without Azure.
const SAMPLE = [
  ["loop", "What does the Fjord Plus AWD cost in Heath?", ["search_voltrieve_docs"], ["voltrieve-kb"]],
  ["prompt", "I've broken my right side mirror on my Spark. In stock in Stockholm?", ["search_spare_parts", "check_part_stock"], ["voltrieve-parts-kb"]],
  ["hosted", "Which winter tyres fit my Fjord Plus?", ["search_spare_parts"], ["voltrieve-parts-kb"]],
  ["loop", "What are your opening hours?", ["get_curated_answer"], []],
  ["hosted", "Call me on [phone] about a test drive of the Atlas", ["book_test_drive"], []],
];
function liveFeed(req, res, prompts) {
  res.writeHead(200, { ...CORS, "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
  const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  let n = 0;
  send("summary", { active_users: 3, turns: 41 });
  const timer = setInterval(() => {
    const [backend, prompt, tools, kbs] = SAMPLE[n % SAMPLE.length];
    const turn_id = `mock-${n++}`;
    const at = Date.now() / 1000;
    send("turn", { turn_id, at, user: randomUUID().slice(0, 12), backend, client: ["car", "web", "cli"][n % 3], prompt: prompts ? prompt : null, kind: "message", outcome: "running" });
    setTimeout(() => {
      const first = backend === "hosted" ? 7000 + Math.random() * 3000 : 1500 + Math.random() * 2500;
      send("turn", {
        turn_id, outcome: tools[0] === "get_curated_answer" ? "curated" : "answered", first_text_ms: Math.round(first),
        duration_ms: Math.round(first + 1500), tools, knowledge_bases: kbs, cost_sek: 0.02 + Math.random() * 0.08,
      });
      send("summary", { active_users: 2 + (n % 3), turns: 41 + n });
      const [door, tool] = [["customer", "search_parts"], ["car", "honk"], ["operations", "service_health"], ["runa", "ask_runa"]][n % 4];
      send("door", { id: n, at: Date.now() / 1000, door, tool, app: ["ChatGPT", "Claude", "VS Code"][n % 3], result: n % 5 ? "done" : "refused", elapsed_ms: 300 + n * 37 });
    }, 1800);
  }, 2500);
  req.on("close", () => clearInterval(timer));
}

// GET /admin/insights: made-up totals, rounded to five. The real source has fewer people, a sample and gaps.
const TOPICS = {
  charging: [["Charging at home without a wall box", 0.4], ["Public chargers that are slow or busy", 0.25], ["other", 0.2]],
  range_battery: [["How far the car goes in the cold", 0.5], ["Keeping the battery healthy", 0.3]],
  price_purchase: [["What leasing costs a month", 0.4], ["Trading in a car", 0.2]],
  fault: [["A warning light for the tyre pressure", 0.4], ["Wipers that streak, skip, squeak or stop halfway up the windscreen when it has rained for a long while", 0.25], ["unnamed group", 0.2]],
  compare_models: [["Fjord or Atlas for a family", 0.5]],
  parts_accessories: [["Winter wheels that fit", 0.5], ["<b>Mirror</b> glass to get", 0.3]],
  service_warranty: [["What the warranty covers", 0.5]],
  signs_parking: [["Reading a parking sign with two times", 0.6]],
  new_topic_without_a_label: [],
  other: [],
};
const WANT = { charging: 60, range_battery: 45, price_purchase: 40, fault: 35, compare_models: 25, parts_accessories: 25, service_warranty: 20, signs_parking: 15, new_topic_without_a_label: 10, other: 15 };
const five = (n) => Math.round(n / 5) * 5;
const daysAgo = (n) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);
function insights(source) {
  const real = source === "real";
  const size = real ? 0.6 : 1.5;
  const rows = [];
  const add = (cut, labels, people) => {
    if (five(people) < 5) return; // the rule of five
    rows.push({ cut, client: "", topic: "", car_model: "", request_kind: "", result: "", group: "", ...labels, conversations: five(people * 1.5), people: five(people) });
  };
  add("all", {}, 210 * size);
  add("client", { client: "car" }, 130 * size);
  add("client", { client: "web" }, 95 * size);
  Object.entries(TOPICS).forEach(([topic, groups], t) => {
    const people = WANT[topic] * size;
    add("topic", { topic }, people);
    if (five(people) < 5) return;
    ["spark", "lumen", "ragn", "fjord", "atlas"].forEach((car_model, m) => add("topic_model", { topic, car_model }, people * (((t * 3 + m * 2) % 5) / 10)));
    [["answered", 0.6], ["partly", 0.15], ["not_found", 0.12], ["handed_off", t === 3 ? 0.4 : 0.05], ["refused", 0.02]].forEach(([result, share]) => add("topic_result", { topic, result }, people * share));
    [["how_to", 0.5], ["problem", 0.2], ["buy", 0.2]].forEach(([request_kind, share]) => add("topic_kind", { topic, request_kind }, people * share));
    for (const [group, share] of groups) {
      if (five(people * share) < 5) continue;
      add("group", { topic, group }, people * share);
      add("group_result", { topic, group, result: "answered" }, people * share * 0.7);
      add("group_result", { topic, group, result: "partly" }, people * share * 0.3);
    }
  });
  const weeks = [];
  for (let w = 0; w < 12; w++) {
    Object.keys(TOPICS).forEach((topic, t) => {
      if (real && (w === 4 || (t + w) % 7 === 5)) return; // a missing night, and weeks under five
      const people = five(WANT[topic] * size * (1 - w * 0.04 + (((w * 7 + t * 5) % 5) - 2) * 0.1));
      if (people >= 5) weeks.push({ end: daysAgo(2 + w * 7), topic, conversations: five(people * 1.5), people });
    });
  }
  const run = `${daysAgo(0)}T02:15:00Z`;
  const window = { start: daysAgo(8), end: daysAgo(2) };
  const empty = process.env.MOCK_ASK === "empty";
  return { source, run: empty ? null : run, window: empty ? null : window, sample_share: real && !empty ? 0.8 : 1, rows: empty ? [] : rows, weeks: empty ? [] : weeks };
}

createServer((req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, CORS).end();
    return;
  }
  if (req.method === "GET" && req.url?.startsWith("/admin/live")) {
    if (!req.headers.authorization?.startsWith("Bearer ")) {
      res.writeHead(401, CORS).end();
      return;
    }
    liveFeed(req, res, req.url.includes("prompts=true"));
    return;
  }
  if (req.method === "GET" && req.url?.startsWith("/admin/insights")) {
    const source = new URL(req.url, "http://mock").searchParams.get("source") ?? "real";
    const status = !req.headers.authorization?.startsWith("Bearer ") ? 401 : !["real", "seeded"].includes(source) ? 422 : process.env.MOCK_ASK === "fail" ? 503 : 200;
    res.writeHead(status, { ...CORS, "Content-Type": "application/json", "Cache-Control": "no-store" });
    res.end(status === 200 ? JSON.stringify(insights(source)) : "{}");
    return;
  }
  res.writeHead(404, CORS).end();
}).listen(PORT, "127.0.0.1", () => console.log(`mock Web API (live feed and totals) on http://127.0.0.1:${PORT}`));
