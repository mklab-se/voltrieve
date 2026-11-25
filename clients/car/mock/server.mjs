// Tiny mock of the Web API, for running and screenshotting the car without Azure.
// It answers /chat, /chat/tool-results, /attachments and /car/link.
//
//   npm run mock            (listens on :8789)
//   npm run dev:mock        (the car with fake auth pointing here)
//
// "wipers" calls set_wipers, "honk" honk, "speed" with a number set_speed, "park here" get_sign_photo
// and then a scripted parking answer, "park" park, "error" an error, "how far" a long answer with
// sources. Everything else gets a scripted answer. As in the real API: a body over 64 KB is too large,
// an id that was not given out here is an unknown attachment, and a file named blurred.jpg is rejected.
//
// With remote control on in the menu, this plays someone's AI:
//   curl -X POST localhost:8789/mock/car-command -d '{"name":"flash_lights","arguments":{"times":2},"app":"Claude"}'
import { randomUUID } from "node:crypto";
import { createServer } from "node:http";

const PORT = Number(process.env.PORT ?? 8789);
const FAST = process.env.MOCK_FAST === "1";
const sleep = (ms) => new Promise((r) => setTimeout(r, FAST ? 0 : ms));

const MAX_BODY = 64 * 1024;
const MAX_UPLOAD = 8 * 1024 * 1024 + 64 * 1024;
const TOO_BLURRY = "The photo is blurred. Hold still or slow down and take a new one.";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, x-agent-backend, x-correlation-id",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Expose-Headers": "x-correlation-id",
};

const attachments = new Set(); // the ids given out
const paused = new Map(); // turn id -> { tool, conversation_id, backend }

const links = new Map(); // link id -> { send, parked }, the oldest first
const commands = new Map(); // command id -> what to do with the answer of the car

const PARKING =
  "Yes. You can park here for **2 hours**, and there is a fee between 9 and 18.\n\n" +
  "The sign applies to the whole block.";

const ANSWER = "I am Runa, and this is the **Spark**. Ask me to turn on the wipers, to honk or to park.";

const RANGE =
  "With the Spark in the **Plus** trim you can count on these distances:\n\n" +
  "- In the city, in summer: **310 km**\n- On the motorway: **220 km**\n- In the city, in winter: **240 km**\n\n" +
  "Warm the cabin while the car still charges, then the battery is there for the road.";
const SOURCES = [
  { id: "spark_owners_manual", title: "Spark owner's manual", kind: "source", link: "/sources/spark_owners_manual" },
  { id: "winter_driving", title: "Driving in winter", kind: "read_more", link: "/sources/winter_driving" },
];

const sse = (res) => (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);

function json(res, status, body) {
  res.writeHead(status, { ...CORS, "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(body));
}

async function readBody(req, limit) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size <= limit) chunks.push(chunk); // the rest is read and dropped, so the answer arrives
  }
  return size > limit ? undefined : Buffer.concat(chunks);
}

/** The tool a message asks for, or undefined. */
function toolFor(message, hasPhoto) {
  const m = message.toLowerCase();
  const number = m.match(/\d+/)?.[0];
  if (m.includes("wiper")) {
    const mode = /\boff\b|stop/.test(m) ? "off" : /fast|full/.test(m) ? "fast" : "slow";
    return ["set_wipers", { mode }];
  }
  if (m.includes("honk")) return ["honk", { times: Number(number ?? (m.includes("twice") ? 2 : 1)) }];
  if (m.includes("speed") && number) return ["set_speed", { kmh: Number(number) }];
  if (/\bpark here/.test(m)) return hasPhoto ? undefined : ["get_sign_photo", {}];
  if (/\bpark\b/.test(m)) return ["park", {}];
  return undefined;
}

async function say(send, text) {
  for (const word of text.split(/(?<=\s)/)) {
    await sleep(35);
    send("delta", { text: word });
  }
}

async function chat(res, body, backend) {
  const send = sse(res);
  const t0 = Date.now();
  const trace = async (step, component, detail) => {
    await sleep(160);
    send("trace", { step, component, detail, elapsed_ms: Date.now() - t0 });
  };
  const message = String(body.message ?? "");
  const conversation_id = body.conversation_id ?? `conv-${randomUUID().slice(0, 8)}`;
  const turn_id = `turn-${randomUUID().slice(0, 8)}`;
  const named = body.attachments ?? [];

  await trace("token_validated", "auth", "roles: Chat.User");
  await trace("backend_selected", "api", backend);
  if (/error/i.test(message)) return send("error", { code: "model_error", message: "The model call failed." });
  for (const _ of named) await trace("attachment_used", "api", "photo, 1024x1024");
  await trace("model_called", "model", "iteration 1");

  const tool = toolFor(message, named.length > 0);
  if (tool) {
    const [name, args] = tool;
    await trace("client_tool_requested", "tools", name);
    paused.set(turn_id, { tool: name, conversation_id, backend });
    return send("client_tool_calls", {
      turn_id,
      conversation_id,
      calls: [{ call_id: `call-${randomUUID().slice(0, 8)}`, name, arguments: JSON.stringify(args) }],
    });
  }
  const range = /how far|range/i.test(message);
  if (range) send("status", { code: "searching", message: "Reading the owner's manual" });
  if (range) await sleep(1200);
  await say(send, /park here/i.test(message) ? PARKING : range ? RANGE : ANSWER);
  if (range) send("sources", { items: SOURCES });
  send("done", { conversation_id, turn_id, backend, usage: { input_tokens: 1840, output_tokens: 62 } });
}

async function toolResults(res, body, turn) {
  const send = sse(res);
  send("trace", { step: "model_called", component: "model", detail: "iteration 2", elapsed_ms: 400 });
  const outputs = body.results.map((r) => String(r.output));
  const hasPhoto = body.results.some((r) => r.attachments?.length > 0);
  await say(send, turn.tool === "get_sign_photo" && hasPhoto ? PARKING : outputs.join(" "));
  send("done", { conversation_id: turn.conversation_id, turn_id: body.turn_id, backend: turn.backend, usage: {} });
}

function upload(res, raw) {
  const form = raw.toString("latin1");
  if (!/name="file"/.test(form)) return json(res, 422, { detail: "The file is missing" });
  if (/filename="blurred\.jpg"/.test(form)) {
    return json(res, 201, { id: null, kind: "photo", status: "rejected", code: "too_blurry", message: TOO_BLURRY });
  }
  const id = `att_${randomUUID()}`;
  attachments.add(id);
  const expires_at = new Date(Date.now() + 15 * 60_000).toISOString();
  return json(res, 201, { id, kind: "photo", status: "ready", expires_at });
}

function carLink(req, res) {
  const id = randomUUID();
  res.writeHead(200, { ...CORS, "Content-Type": "text/event-stream", "Cache-Control": "no-store" });
  const send = sse(res);
  links.set(id, { send, parked: false });
  send("link", { id });
  const alive = setInterval(() => res.write(": keep-alive\n\n"), 10_000);
  res.on("close", () => {
    clearInterval(alive);
    links.delete(id);
  });
}

/** The state of a car, a command that it takes, or what it did. */
function fromCar(res, path, body) {
  const [, , , id, what] = path.split("/");
  const link = links.get(id);
  if (!link) return json(res, 404, { detail: "Unknown car link" });
  const command = commands.get(body.id);
  if (what === "state") link.parked = body.parked;
  if (what === "results" && command?.taken) command.resolve({ ok: body.ok, message: body.message });
  if (what === "take") {
    if (!command || command.taken) return json(res, 409, { detail: "Nobody waits for this command" });
    command.taken = true;
    setTimeout(command.resolve, 2000, { error: "unsure" });
  }
  return res.writeHead(204, CORS).end();
}

/** Only here: a command as the API would send it, or not at all when the car is not linked or not parked. */
async function carCommand(res, { name = "honk", arguments: args = { times: 1 }, app = "Claude" }) {
  const link = [...links.values()].at(-1);
  if (!link) return json(res, 409, { error: "not_linked" });
  if (!link.parked) return json(res, 409, { error: "moving" });
  const id = randomUUID();
  const answer = new Promise((resolve) => {
    const command = { resolve, taken: false };
    commands.set(id, command);
    setTimeout(() => command.taken || resolve({ error: "no_answer" }), 5000);
  });
  link.send("command", { id, name, arguments: args, app });
  const result = await answer;
  commands.delete(id);
  json(res, 200, result);
}

const CAR_POST = /^\/car\/link\/[^/]+\/(state|take|results)$/;
const PATHS = ["/chat", "/chat/tool-results", "/attachments", "/car/link", "/mock/car-command"];

async function handle(req, res) {
  if (req.method === "OPTIONS") return res.writeHead(204, CORS).end();
  if (req.method !== "POST" || !(PATHS.includes(req.url) || CAR_POST.test(req.url))) {
    return res.writeHead(404, CORS).end();
  }
  const raw = await readBody(req, req.url === "/attachments" ? MAX_UPLOAD : MAX_BODY);
  if (!raw) return json(res, 413, { detail: "Request body too large" });
  const byHand = req.url === "/mock/car-command"; // from a terminal, without a token
  if (!byHand && !req.headers.authorization?.startsWith("Bearer ")) return json(res, 401, { detail: "Missing token" });
  if (req.url === "/attachments") return upload(res, raw);
  if (req.url === "/car/link") return carLink(req, res);

  let body;
  try {
    body = JSON.parse(byHand && raw.length === 0 ? "{}" : raw);
  } catch {
    return json(res, 422, { detail: "Invalid JSON" });
  }
  if (byHand) return carCommand(res, body);
  if (CAR_POST.test(req.url)) return fromCar(res, req.url, body);
  const isChat = req.url === "/chat";
  if (!isChat && !Array.isArray(body.results)) return json(res, 422, { detail: "results are missing" });
  const turn = isChat ? undefined : paused.get(body.turn_id);
  if (!isChat && !turn) return json(res, 422, { detail: "The turn is not known here" });
  const ids = isChat ? (body.attachments ?? []) : body.results.flatMap((r) => r.attachments ?? []);
  if (ids.some((id) => !attachments.has(id))) return json(res, 422, { detail: { code: "unknown_attachment" } });
  if (!isChat) paused.delete(body.turn_id); // after the check, so it can be answered with another id

  res.writeHead(200, {
    ...CORS,
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-store",
    "X-Correlation-Id": String(req.headers["x-correlation-id"] ?? randomUUID()),
  });
  if (isChat) await chat(res, body, String(req.headers["x-agent-backend"] ?? "loop"));
  else await toolResults(res, body, turn);
  res.end();
}

createServer((req, res) => {
  handle(req, res).catch((e) => {
    console.error(e);
    res.destroy();
  });
}).listen(PORT, "127.0.0.1", () => console.log(`mock Web API on http://127.0.0.1:${PORT}`));
