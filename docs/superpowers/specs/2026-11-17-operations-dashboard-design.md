# Operations dashboard: design

Status: decided with the product owner on 2026-11-17 ("implement and deploy, I'll trust your
judgement"). Implements issue #1.

## 1. Why

A demo needs to show, live and afterwards, who uses the assistant, what they ask, which tools
and knowledge bases answer, how fast, and what it costs. Today the numbers come from scripts run
by hand. The audience should be able to set the same thing up in their own solution, so the
design uses standard parts first: OpenTelemetry, Application Insights and an Azure Workbook.

## 2. What the product owner wants to see

- How many users have used the solution, and how many are using it now.
- The requests (prompts) coming in right now.
- Which tools are used, and the most popular knowledge bases.
- Response times per request, right now and over time.
- What the solution costs to run.
- Where users connect from, if possible.
- It must be clear and interactive, with a wow factor in front of an audience.

## 3. Privacy: store as little as possible

GDPR, the EU AI Act and the EU Data Act, plus cost, and the fact that this is a demo:

- **Prompts are shown live only.** They are never written to Application Insights, logs or
  disk. The API keeps the last 50 in memory and streams them to the dashboard page; a restart or
  newer prompts drop them. Email addresses, phone numbers and Swedish personal identity numbers
  are masked before a prompt leaves the API. Answers are never kept.
  *Note 2026-11-24:* true for what our code writes. Foundry's tracing writes prompts and answers of
  prompt and hosted turns to Application Insights. [The decision is open](../../decisions.md#open-the-product-owner-decides-what-foundrys-tracing-may-record).
- **A "show prompts" switch, off by default**, so the dashboard can be on a big screen. When it is
  off, the API does not send the text at all.
- **People are told.** The chat clients say near the input that prompts may be shown, masked, on
  a live screen during demos.
- **Users are counted with a pseudonym:** a hash of the Entra object id. The raw object id and the
  user name are removed from spans and logs. (A hash is still personal data under GDPR; in a real
  deployment it would get a secret salt and a retention policy.)
- **Location is country and city only.** Application Insights derives it from the client address
  and masks the address itself (the default).
- Data ages out after 30 days (the workspace retention already set).
- In a real deployment: anonymized prompts kept for analysis, a privacy notice, a retention
  policy, and the AI Act transparency notice that users talk to an AI.

## 4. The parts

1. **Turn telemetry** on the existing `chat.turn` span, with OpenTelemetry GenAI semantic
   convention names where one exists (`gen_ai.operation.name`, `gen_ai.request.model`,
   `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.conversation.id`) and a
   `voltrieve.` prefix for the rest: `backend`, `user` (pseudonym), `outcome` (answered, curated,
   client_tool, error), `error_code`, `first_status_ms`, `first_text_ms`, `duration_ms`,
   `model_calls`, `tools` and `knowledge_bases` (comma separated names), `cached_tokens`,
   `search_reasoning_tokens`, `cost_sek` (estimate), `cold_start`. Ids, names, counts and timings
   only. The same numbers go to the live feed.
2. **Application Insights Live Metrics** switched on (`enable_live_metrics=True`): the standard,
   stored-nothing view of requests, response times and failures right now.
3. **A live page in the web client**, `?view=dashboard`, behind a new `Dashboard.View` app role
   (given to the deployer only). It shows users active in the last 5 minutes, turns today, the
   live feed (time, backend, pseudonym, tools, knowledge bases, first text and total time, cost,
   outcome, and the masked prompt when the switch is on), and links to Live Metrics and the
   workbook. Data comes from `GET /admin/live` (Server-Sent Events) on the API.
4. **An Azure Workbook** in Bicep (`infra/modules/dashboard-workbook.bicep`, the workbook JSON in
   `infra/workbooks/operations.json`), deployed by `azd up`, pointed at the Application Insights
   resource by a parameter so it can be copied into another solution. Sections: users (total, per
   day), speed (median and p95 first text per backend, share over 10 s, trend), behavior (tools,
   knowledge bases, model calls per turn, curated share), cost (estimated SEK per day and month
   against the 990 SEK budget, labeled as an estimate, plus the Azure bill from Cost Management
   when the workbook can read it), health (errors, cold starts), location (a map), and a privacy
   check query that shows that no turn span carries free text.
5. **Runbook section**: reading the dashboard, adding it to your own solution, and what to do when
   a number looks wrong.

## 5. Cost estimate

Price per million tokens from a table in the API (`gpt-6.1-terra`: 1.00 input, 0.05 cached,
5.00 output USD; Search reasoning 0.022 USD), converted with a fixed USD to SEK rate kept next to
it. The prompt agent does not report Search reasoning tokens, so its cost is lower than real. The
workbook labels every cost as an estimate. Fixed costs (Search, registry, containers) come from
Cost Management.

## 6. Non-goals

Grafana, alerts beyond the existing budget alert, storing prompts, per-user drill-down, a second
API replica (the live feed is per process, and the API runs one replica).

## 7. Acceptance

- `azd up` deploys the workbook and it shows data for all three backends after a few chats.
- The live page shows turns from all three backends as they happen, prompts only with the switch
  on, masked.
- First text median per backend in the workbook within 10 % of `tests/routing/check.py` on the
  same runs.
- No prompt, answer or user name in any stored telemetry field (the privacy check query).
  *Note 2026-11-24:* not met for Foundry's own `responsesapi` rows, which the query did not look at.
- Runbook section added.
