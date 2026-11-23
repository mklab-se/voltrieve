// Tiny mock of the Web API's POST /chat, for running and screenshotting the SPA
// without Azure. Emits a scripted turn as server-sent events.
//
//   npm run mock            (listens on :8787)
//   npm run dev:mock        (SPA with fake auth pointing here)
//
// The steps and components are the ones the real API emits (token_validated, backend_selected,
// conversation_created, model_called, server_tool_called, knowledge_base_retrieved,
// server_tool_returned, model_returned). Special messages: "theme" emits client_tool_calls for a
// tool the client has, "tools" for one it does not have, "endless" asks for a tool after every
// result, and "error" emits an error event. The questions on the page and one about the Fjord have
// their own answers, with sources. Everything else gets a plain answer. The X-Correlation-Id of the
// request is echoed back, as the real API does.
//
// POST /attachments takes any photo of at least 200 px. A question with a photo is answered as if it
// showed a broken door mirror. Special messages with a photo: containing "forget" loses the photo
// once, "gone" loses it every time, and "reject" loses it and refuses the next upload.
import { createServer } from "node:http";
import { randomUUID } from "node:crypto";

const PORT = Number(process.env.PORT ?? 8787);
const FAST = process.env.MOCK_FAST === "1";
const sleep = (ms) => new Promise((r) => setTimeout(r, FAST ? 0 : ms));

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, x-agent-backend, x-correlation-id",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Expose-Headers": "x-correlation-id",
};

function source(id, title, kind = "source") {
  // The parts catalogs are internal, so they have no link.
  return { id, title, kind, ...(id.endsWith(".pdf") && { link: `/sources/${id}` }) };
}

const SPARK_SPECS = source("voltrieve-spark-specifications.pdf", "Spark specifications");
const WARRANTY = source("voltrieve-warranty.pdf", "Warranty");
const more = (s) => ({ ...s, kind: "read_more" });

const ANSWERS = [
  {
    about: /fjord/i,
    text:
      "The **Fjord** is the large estate in the range. Here is what it can tow:\n\n" +
      "| Battery | Braked | Unbraked |\n|---|---:|---:|\n" +
      "| 84 kWh | 1,600 kg | 750 kg |\n| 63 kWh | 1,200 kg | 750 kg |\n\n" +
      "Source: *voltrieve-fjord-specifications.pdf*",
    sources: [
      source("voltrieve-fjord-specifications.pdf", "Fjord specifications"),
      source("voltrieve-fjord-brochure.pdf", "Fjord brochure", "read_more"),
    ],
  },
  {
    about: /charg/i,
    text:
      "At home your Spark takes up to **11 kW** from a wall box, which fills an empty battery in about 3.8 hours. " +
      "An ordinary socket with the portable charger also works, but it takes much longer.\n\n" +
      "Source: *voltrieve-spark-specifications.pdf*",
    sources: [
      SPARK_SPECS,
      source("voltrieve-charging-guide.pdf", "Charging guide", "read_more"),
      source("voltrieve-spark-accessories.pdf", "Spark accessories", "read_more"),
    ],
  },
  {
    about: /service/i,
    text:
      "Your Spark is serviced every 24 months or 30,000 km, whichever comes first. You have driven 12,400 km, so the next service is " +
      "in **March 2028 or at 30,000 km**.\n\nSource: *voltrieve-warranty.pdf*",
    sources: [WARRANTY],
  },
  {
    about: /wheel|tyre/i,
    text:
      "The winter wheel set for the Spark is **VA-9101**: four complete 17 inch Pebble wheels with studless Nordic tyres and " +
      "pressure sensors, 12,900 SEK.\n\nSource: *voltrieve-spark-accessories.pdf*",
    sources: [source("voltrieve-spark-accessories.pdf", "Spark accessories"), more(SPARK_SPECS)],
  },
  {
    about: /warranty/i,
    text:
      "- **The whole car:** 3 years or 100,000 km, whichever comes first.\n" +
      "- **The battery:** 8 years or 160,000 km, and at least 70 % of its capacity.\n\n" +
      "Tyres, brake pads and other wear items are not covered.\n\nSource: *voltrieve-warranty.pdf*",
    sources: [WARRANTY],
  },
  { about: /beep/i, text: "I cannot beep here. I can answer questions about your car and change the theme of this page." },
];
const PLAIN = { text: "I can help with charging, service, wheels and the warranty of your Spark. What would you like to know?" };

const BROKEN_PART = {
  text:
    "The photo shows a **broken right door mirror**: the glass is cracked and the cap is gone.\n\n" +
    "You need these parts:\n\n- Mirror glass, right: **VP-14349**\n- Door mirror cap, right: **VP-16689**, primed and ready for paint\n\n" +
    "If the housing is cracked too, the whole mirror is **VP-14258**. Is the housing whole?\n\n" +
    "Source: *voltrieve-parts-body-glass-lighting.md*",
  sources: [source("voltrieve-parts-body-glass-lighting.md", "Parts body glass lighting")],
};

const SEARCH = {
  documents: { tool: "search_voltrieve_docs", status: ["searching_documents", "Searching the documents"] },
  parts: { tool: "search_spare_parts", status: ["searching_parts", "Searching the parts catalog"] },
};

/** The answer, and the steps before it. The traces and statuses are those of the real API. */
function script(message, backend, photos) {
  const answer = photos.length ? BROKEN_PART : (ANSWERS.find((a) => a.about.test(message)) ?? PLAIN);
  const search = answer.sources && (photos.length ? SEARCH.parts : SEARCH.documents);
  const steps = [
    ["token_validated", "auth", "roles: Chat.User, TestDrive.Book", 4],
    ["backend_selected", "api", backend, 6],
    ["status", ...(backend === "hosted" ? ["starting_agent", "Getting ready"] : ["thinking", "Thinking"])],
    ["conversation_created", "api", "conv_mock", 21],
    ...photos.map((p) => ["attachment_used", "api", `${p.kind} ${p.detail}`, 22]),
    ["model_called", "model", "iteration 1", 38],
    ...(search
      ? [
          ["model_returned", "model", "0 text deltas, 1 tool calls", 910],
          ["status", ...search.status],
          ["server_tool_called", "tools", search.tool, 920],
          ["knowledge_base_retrieved", "search", "4 chunks from 2 documents", 1384],
          ["server_tool_returned", "tools", search.tool, 1390],
          ["status", "reviewing_results", "Reading the results"],
          ["model_called", "model", "iteration 2", 1402],
        ]
      : []),
  ];
  return { steps, answer };
}

// Attachments. Only their ids and sizes are kept.
const attachments = new Map();
const forgotten = new Set(); // correlation ids
let rejectNext = false;

function rejected(code, message) {
  return { id: null, kind: "photo", status: "rejected", code, message };
}

/** Width and height from the first SOF segment of a JPEG, which is what the SPA sends. */
function jpegSize(bytes) {
  for (let at = bytes.indexOf(Buffer.from([0xff, 0xd8, 0xff])) + 2; at > 1 && at + 9 < bytes.length; ) {
    const marker = bytes[at + 1];
    if (marker === 0xc0 || marker === 0xc2) return { height: bytes.readUInt16BE(at + 5), width: bytes.readUInt16BE(at + 7) };
    at += 2 + bytes.readUInt16BE(at + 2);
  }
  return undefined;
}

function upload(bytes) {
  const size = jpegSize(bytes);
  if (!size) return [415, { detail: "Unknown kind of attachment" }];
  if (Math.min(size.width, size.height) < 200) {
    return [201, rejected("too_small", "The photo is too small. Move closer and take a new one.")];
  }
  if (rejectNext) {
    rejectNext = false;
    return [201, rejected("too_blurry", "The photo is blurred. Hold still or slow down and take a new one.")];
  }
  const id = `att-${randomUUID().slice(0, 8)}`;
  attachments.set(id, { kind: "photo", size: bytes.length, detail: `${size.width}x${size.height}` });
  return [201, { id, kind: "photo", status: "ready", expires_at: new Date(Date.now() + 900_000).toISOString() }];
}

/** Whether the API still has what the turn names, after what the message asks the mock to do. */
function hasAttachments(body, correlationId) {
  const ids = body.attachments ?? [];
  if (ids.length === 0) return true;
  const msg = String(body.message ?? "");
  const once = /forget|reject/i.test(msg) && !forgotten.has(correlationId);
  if (once) forgotten.add(correlationId);
  if (once && /reject/i.test(msg)) rejectNext = true;
  if (once || /gone/i.test(msg)) for (const id of ids) attachments.delete(id);
  return ids.every((id) => attachments.has(id));
}

async function turn(res, body, backend) {
  const t0 = Date.now();
  const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  const el = () => Date.now() - t0;
  const msg = String(body.message ?? "");
  const conversation_id = body.conversation_id ?? `conv-${randomUUID().slice(0, 8)}`;
  const turn_id = `turn-${randomUUID().slice(0, 8)}`;

  const photos = (body.attachments ?? []).map((id) => attachments.get(id));
  const { steps, answer } = script(msg, backend, photos);
  if (/error/i.test(msg)) {
    send("trace", { step: "token_validated", component: "auth", detail: "roles: Chat.User", elapsed_ms: el() });
    await sleep(300);
    send(
      "error",
      backend === "hosted"
        ? { code: "agent_unavailable", message: "The hosted agent could not complete the answer." }
        : { code: "model_error", message: "The model call failed." },
    );
    return;
  }
  for (const [step, component, detail, at] of steps) {
    if (step === "status") {
      send("status", { code: component, message: detail });
      continue;
    }
    await sleep(220);
    send("trace", { step, component, detail, elapsed_ms: at });
  }
  if (/endless/i.test(msg)) {
    send("client_tool_calls", { turn_id: "endless", conversation_id, calls: [THEME_CALL] });
    return;
  }
  if (/theme/i.test(msg)) {
    send("client_tool_calls", {
      turn_id,
      conversation_id,
      calls: [{ call_id: "t1", name: "set_theme", arguments: JSON.stringify({ theme: /light/i.test(msg) ? "light" : "dark" }) }],
    });
    return;
  }
  if (/tools/i.test(msg)) {
    send("trace", { step: "client_tool_requested", component: "tools", detail: "beep", elapsed_ms: el() });
    send("client_tool_calls", {
      turn_id,
      conversation_id,
      calls: [{ call_id: "c1", name: "beep", arguments: JSON.stringify({ times: 3 }) }],
    });
    return;
  }
  for (const word of answer.text.split(/(?<=\s)/)) {
    await sleep(35);
    send("delta", { text: word });
  }
  if (answer.sources) {
    send("trace", { step: "sources_resolved", component: "api", detail: `${answer.sources.length} documents`, elapsed_ms: el() });
    send("sources", { turn_id, items: answer.sources });
  }
  send("done", { conversation_id, turn_id, backend, usage: { prompt_tokens: 1840, completion_tokens: 62, total_tokens: 1902 } });
}

const THEME_CALL = { call_id: "t1", name: "set_theme", arguments: JSON.stringify({ theme: "light" }) };
const KNOWN_SOURCES = [...ANSWERS, BROKEN_PART].flatMap((a) => a.sources ?? []);

createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, CORS).end();
    return;
  }
  if (req.method === "POST" && req.url === "/chat/tool-results") {
    let raw = "";
    for await (const chunk of req) raw += chunk;
    res.writeHead(200, { ...CORS, "Content-Type": "text/event-stream" });
    if (raw.includes('"turn_id":"endless"')) {
      const again = { turn_id: "endless", conversation_id: "conv_mock", calls: [THEME_CALL] };
      res.end(`event: client_tool_calls\ndata: ${JSON.stringify(again)}\n\n`);
      return;
    }
    res.write(`event: delta\ndata: ${JSON.stringify({ text: "Done." })}\n\n`);
    res.end(`event: done\ndata: ${JSON.stringify({ conversation_id: "conv_mock", turn_id: "t", backend: "loop" })}\n\n`);
    return;
  }
  if (req.method === "POST" && req.url === "/attachments") {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    await sleep(900);
    const [status, body] = req.headers.authorization?.startsWith("Bearer ") ? upload(Buffer.concat(chunks)) : [401, {}];
    res.writeHead(status, { ...CORS, "Content-Type": "application/json" }).end(JSON.stringify(body));
    return;
  }
  // GET /sources/{id}: a made-up signed link, so the click can be seen without Azure.
  if (req.method === "GET" && req.url?.startsWith("/sources/")) {
    const id = decodeURIComponent(req.url.slice("/sources/".length));
    const known = KNOWN_SOURCES.find((s) => s.id === id);
    if (!known) return void res.writeHead(404, CORS).end();
    if (!known.link) return void res.writeHead(403, CORS).end();
    res.writeHead(200, { ...CORS, "Content-Type": "application/json", "Cache-Control": "no-store" });
    res.end(JSON.stringify({ url: `https://mockstorage.blob.core.windows.net/voltrieve-docs/${id}?sp=r&sig=mock`, expires_in: 300 }));
    return;
  }
  if (req.method !== "POST" || req.url !== "/chat") {
    res.writeHead(404, CORS).end();
    return;
  }
  if (!req.headers.authorization?.startsWith("Bearer ")) {
    res.writeHead(401, CORS).end();
    return;
  }
  let raw = "";
  for await (const chunk of req) raw += chunk;
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    res.writeHead(400, CORS).end();
    return;
  }
  const backend = String(req.headers["x-agent-backend"] ?? "loop");
  const correlationId = String(req.headers["x-correlation-id"] ?? randomUUID());
  if (!hasAttachments(body, correlationId)) {
    res.writeHead(422, { ...CORS, "Content-Type": "application/json" });
    res.end(JSON.stringify({ detail: { code: "unknown_attachment" } }));
    return;
  }
  res.writeHead(200, {
    ...CORS,
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "X-Correlation-Id": correlationId,
  });
  try {
    await turn(res, body, backend);
  } finally {
    res.end();
  }
}).listen(PORT, "127.0.0.1", () => console.log(`mock Web API on http://127.0.0.1:${PORT}`));
