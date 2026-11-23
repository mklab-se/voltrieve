// Tiny mock of the Web API's GET /admin/live, for running and screenshotting the dashboard
// without Azure. It sends a made-up stream of turns.
//
//   npm run mock            (listens on :8788)
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
    send("turn", { turn_id, at, user: randomUUID().slice(0, 12), backend, prompt: prompts ? prompt : null, kind: "message", outcome: "running" });
    setTimeout(() => {
      const first = backend === "hosted" ? 7000 + Math.random() * 3000 : 1500 + Math.random() * 2500;
      send("turn", {
        turn_id, outcome: tools[0] === "get_curated_answer" ? "curated" : "answered", first_text_ms: Math.round(first),
        duration_ms: Math.round(first + 1500), tools, knowledge_bases: kbs, cost_sek: 0.02 + Math.random() * 0.08,
      });
      send("summary", { active_users: 2 + (n % 3), turns: 41 + n });
    }, 1800);
  }, 2500);
  req.on("close", () => clearInterval(timer));
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
  res.writeHead(404, CORS).end();
}).listen(PORT, "127.0.0.1", () => console.log(`mock Web API (live feed only) on http://127.0.0.1:${PORT}`));
