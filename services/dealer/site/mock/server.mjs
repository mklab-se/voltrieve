// Tiny mock of the dealer service, for running and screenshotting the page without Azure.
// All of it is made up, and kept in memory.
//
//   npm run mock            (listens on :8790)
//   npm run dev:mock        (the page with fake auth, where Vite sends /staff and /config.json here)
//
//   curl -X POST localhost:8790/mock/order     an order arrives (?count=3 gives three at once)
//   curl -X POST localhost:8790/mock/calls     Runa asks for a few things
//   curl -X POST localhost:8790/mock/drop      the streams end
//   curl -X POST localhost:8790/mock/fail      503 from now on, and again: no longer
//   MOCK_ROLE=none npm run mock                403 for everybody
//
// After `npm run build` it also serves the built page, with the same policy as the service.
import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";

const PORT = Number(process.env.PORT ?? 8790);
const REP = "Dev Tester";

const MODELS = [
  { slug: "spark", name: "Spark", price: 289000 },
  { slug: "lumen", name: "Lumen", price: 379000 },
  { slug: "ragn", name: "Ragn", price: 549000 },
  { slug: "fjord", name: "Fjord", price: 529000 },
  { slug: "atlas", name: "Atlas", price: 749000 },
];
const CITIES = ["Stockholm", "Göteborg", "Malmö", "Uppsala"];
const START = { spark: [4, 2, 5, 0], lumen: [3, 1, 2, 4], ragn: [0, 0, 0, 0], fjord: [1, 4, 3, 2], atlas: [2, 3, 1, 5] };
const BUYERS = [
  ["Kim Berg", "Storgatan 1, 753 10 Uppsala", "fjord", "web"],
  ["Anna Lind", "Hamngatan 12, 411 14 Göteborg", "ragn", "cli"],
  ["Ödvar Ek", "<b>Kungsgatan</b> 8, 211 49 Malmö", "spark", "web"],
  ["Sam Holm", `${"Långa Vägen 1234 ".repeat(11)}Kiruna`, "atlas", "web"],
];

let next = 1001;
let stock, orders, log;
let failing = false;
const streams = new Set();

const now = () => new Date().toISOString();
const startStock = () => Object.fromEntries(Object.entries(START).map(([m, n]) => [m, Object.fromEntries(CITIES.map((c, i) => [c, n[i]]))]));
function send(event, data) {
  for (const res of streams) res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}
function add(kind, who, over = {}) {
  const entry = { id: randomUUID(), at: now(), kind, who, client: null, model: null, city: null, number: null, result: null, correlation_id: randomUUID(), ...over };
  log = [...log, entry].slice(-200);
  send("log", entry);
}
function place() {
  const [name, address, model, client] = BUYERS[(next - 1001) % BUYERS.length];
  const number = `VO-${next++}`;
  orders.unshift({ number, model, price_sek: MODELS.find((m) => m.slug === model).price, name, address, client, status: "open", created: now(), city: null, delivered: null, delivered_by: null });
  add("terms", "Runa", { client, model });
  add("offer", "Runa", { client, model });
  add("order", "Runa", { client, model, number });
}
function calls() {
  add("stock", "Runa", { model: "atlas", city: "Göteborg", client: "web", result: "3" });
  add("part_stock", "Runa", { city: "Malmö", client: "car" });
  add("prices", "Runa", { client: "cli" });
  add("test_drive", "Runa", { model: "lumen", city: "Uppsala", client: "web" });
  add("refused", "Kim Berg");
}
function reset() {
  stock = startStock();
  orders = [];
  log = [];
}
reset();
place();
place();
Object.assign(orders[1], { status: "delivered", city: "Göteborg", delivered: now(), delivered_by: REP });
add("delivered", REP, { model: "fjord", number: "VO-1001", city: "Göteborg" });
calls();

async function read(req) {
  let text = "";
  for await (const part of req) text += part;
  return text ? JSON.parse(text) : {};
}

async function staff(req, url) {
  const [, , what, number, verb] = url.pathname.split("/").map(decodeURIComponent);
  const route = `${req.method} ${what}`;
  if (route === "GET state") return [200, { models: MODELS, cities: CITIES, stock, orders, log }];
  if (route === "POST stock") {
    const { model, city, change } = await read(req);
    if (stock[model][city] + change < 0) return [409, { detail: `There is none in stock in ${city}.` }];
    stock[model][city] += change;
    add("stock_changed", REP, { model, city, result: String(stock[model][city]) });
    send("changed", {});
    return [200, { in_stock: stock[model][city] }];
  }
  if (route === "POST reset") {
    reset();
    send("clear", {});
    add("reset", REP);
    send("changed", {});
    return [204];
  }
  const order = what === "orders" && orders.find((o) => o.number === number);
  if (!order) return [404, { detail: "Not found." }];
  if (req.method === "DELETE") {
    orders = orders.filter((o) => o !== order);
    add("deleted", REP, { number, model: order.model });
  } else if (verb === "deliver") {
    const { city } = await read(req);
    if (order.status === "delivered") return [409, { detail: "This order is already delivered." }];
    if (!stock[order.model][city]) return [409, { detail: `There is none in stock in ${city}.` }];
    stock[order.model][city]--;
    Object.assign(order, { status: "delivered", city, delivered: now(), delivered_by: REP });
    add("delivered", REP, { number, model: order.model, city });
  }
  send("changed", {});
  return [req.method === "DELETE" ? 204 : 200, order];
}

const POLICY = "default-src 'self'; connect-src 'self' https://login.microsoftonline.com; frame-src https://login.microsoftonline.com; frame-ancestors 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'";
const TYPES = { html: "text/html; charset=utf-8", js: "text/javascript", css: "text/css" };
async function file(path, res) {
  const name = path === "/" ? "index.html" : /^\/assets\/[\w.-]+$/.test(path) ? path.slice(1) : undefined;
  const body = name && (await readFile(new URL(`../dist/${name}`, import.meta.url)).catch(() => undefined));
  if (!body) return res.writeHead(404).end();
  res.writeHead(200, { "Content-Type": TYPES[name.split(".").pop()] ?? "application/octet-stream", "Content-Security-Policy": POLICY, "X-Content-Type-Options": "nosniff" }).end(body);
}

createServer(async (req, res) => {
  const url = new URL(req.url, "http://mock");
  const json = (status, body) => res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" }).end(status === 204 ? undefined : JSON.stringify(body));
  try {
    if (url.pathname === "/config.json") return json(200, { tenantId: "mock-tenant", clientId: "mock-client", scope: "api://mock/manage_as_user" });
    if (req.method === "POST" && url.pathname.startsWith("/mock/")) {
      const what = url.pathname.slice("/mock/".length);
      if (what === "order") {
        for (let n = 0; n < Number(url.searchParams.get("count") ?? 1); n++) place();
        send("changed", {});
      } else if (what === "calls") calls();
      else if (what === "drop") streams.forEach((s) => s.end());
      else if (what === "fail") failing = !failing;
      else return json(404, {});
      return json(200, { orders: orders.length, failing });
    }
    if (!url.pathname.startsWith("/staff/")) return await file(url.pathname, res);
    if (!req.headers.authorization?.startsWith("Bearer ")) return json(401, { detail: "No token." });
    if (process.env.MOCK_ROLE === "none") return json(403, { detail: "No role." });
    if (failing) return json(503, { detail: "The dealer system is not available right now." });
    if (url.pathname === "/staff/events") {
      res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store" });
      res.write(": keepalive\n\n");
      streams.add(res);
      const timer = setInterval(() => res.write(": keepalive\n\n"), 15000);
      res.on("close", () => (clearInterval(timer), streams.delete(res)));
      return;
    }
    return json(...(await staff(req, url)));
  } catch {
    return json(500, { detail: "The mock failed." });
  }
}).listen(PORT, "127.0.0.1", () => console.log(`mock dealer service on http://127.0.0.1:${PORT}`));
