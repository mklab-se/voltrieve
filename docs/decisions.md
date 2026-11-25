# Decisions

What was decided, why, and what was found on the live service. Dated facts are from 2026-11-04 unless noted. The comparison is in [why-foundry.md](why-foundry.md).

## API versions

### Latest versions everywhere, including preview

The user decided to use the latest API versions, preview included. This replaced an earlier plan to stay on GA.

| Surface | Version |
| --- | --- |
| Search data plane (REST) | `2026-11-01-preview` |
| Knowledge base MCP endpoint | `2026-11-01-preview` |
| Search management (Bicep) | `2026-11-01` |
| `Microsoft.CognitiveServices/*` | `2026-11-15-preview` |
| `Microsoft.App/*` | `2026-07-01` |
| `Microsoft.Storage/*` | `2026-09-01` |
| `Microsoft.ContainerRegistry/registries` | `2026-11-01` |
| `Microsoft.OperationalInsights/workspaces` | `2026-03-01` |
| `Microsoft.Web/staticSites` | `2025-05-01` |
| `Microsoft.Resources/resourceGroups` | `2026-06-01` |
| `Microsoft.ManagedIdentity/userAssignedIdentities` | `2024-11-30` |
| `Microsoft.Insights/components` | `2020-02-02` |

### Search management stepped back from the newest listed version

On 2026-11-04 `2026-11-01` was listed in the docs but not live in France Central, so Bicep used `2026-09-01-preview`, the newest the service accepted then. By 2026-11-24 it was live, and Bicep uses it: see the next section.

### Search moved to the Foundry IQ versions

On 2026-11-24 the product owner asked for the newest search APIs. The data plane and the MCP endpoint use `2026-11-01-preview`, not the stable `2026-10-01`, because only the preview accepts GPT-6 models for planning. Bicep uses the stable `2026-11-01`, which is live in France Central now, so the section above is history. No field we send or read changed.

- **Planning stays on `gpt-5.4-mini`:** `gpt-6.1-terra` is not among the models Search lists, and `gpt-6-luna` is the cheapest that is. Measured on 2026-11-24, GPT-6 planning gave the same answers for an eighth of the planning cost, but took about half a second longer on every search, and speed decides. Both models are deployed, and `PLANNING_MODEL_NAME` is the one value that chooses. Deploy the code before choosing a GPT-6 model: older clients are not documented to work with such a base.
- **Serverless is on in the dev environment, and off by default:** it stands in Sweden Central, next to the API. Warm, a search took 0.3 s less than on Basic (median 1.3 against 1.6 s). Routing was right in 135 of 135 turns, and the first call after 14 idle minutes took 2.9 s. Filling it and all the test runs used at least 0.42 CU hours by the diagnostic metric, about 0.11 USD, which is not an invoice. `SEARCH_SERVERLESS` is `off`, `standby` or `active`. The Basic service stays in all three, and costs the same as before until the product owner decides to delete it. `active` is refused before `standby` has been provisioned, and pruning documents prunes every service.
- **The Serverless identity has one Foundry role,** Cognitive Services OpenAI User, as the identity page asks, and planning works with it. Basic keeps its two.

The numbers are in [`search-versions.txt`](https://github.com/mklab-se/voltrieve/blob/main/docs/evidence/search-versions.txt). The design has what breaks, the order and the way back: [design](https://github.com/mklab-se/voltrieve/blob/main/docs/superpowers/specs/2026-11-24-search-foundry-iq-design.md). The commands are in [Deployment](deploy.md#order-for-an-update-with-the-new-search-values).

### The Basic search service is gone

On 2026-11-24 the Basic service had cost 517 SEK of the month to date, about 22 SEK a day. A full month of that is about one third of the 2000 SEK budget. It has been unused since Serverless is `active`. Serverless had cost 1.25 SEK so far. So the product owner asked to have Basic removed, and it was deleted in the dev environment on 2026-11-24 (`docs/evidence/search-basic-removed.txt`).

- **A value, not a deletion.** `SEARCH_BASIC` is `on` or `off`. `off` leaves the service, its roles and every reference out of the template. A provision never deletes it: the owner does, by name, after a check ([the steps](deploy.md#removing-the-basic-service)).
- **The guard is double.** `off` needs `SEARCH_SERVERLESS` at `active`. The preprovision hook refuses anything else, and so does the template itself, before it creates anything.
- **Basic stays on by default.** A new environment has nothing to fall back on until Serverless is filled, and one `azd up` has to be enough. A new environment therefore still pays for Basic until someone moves it to Serverless.
- **The way back** is a new, empty service that is filled again from storage: 22 SEK a day from then on, a round of embeddings under 0.01 USD, two provisions, and a region that takes a new service. The fallback of switching to Basic in one provision is gone.

Not verified: the removal and the way back have not been run.

### Bicep BCP081 warnings are expected

The lint reports BCP081 ("does not have types available") for `Microsoft.CognitiveServices/*@2026-11-15-preview`, `Microsoft.ContainerRegistry/registries@2026-11-01`, `Microsoft.Web/staticSites@2025-05-01`, `Microsoft.Resources/resourceGroups@2026-06-01` and `Microsoft.Storage/*@2026-09-01`. These versions are newer than Bicep's type data. The warnings do not block deployment, but Bicep cannot validate those resources' properties before deploying, so errors there appear at deployment time. `infra/tests/test_bicep.sh` does not fail on them.

## Search

### Query planning on

Each knowledge base has the planning model in `models` with `retrievalReasoningEffort` low. The planning model is `gpt-5.4-mini`, with a deployment of its own, and `gpt-6-luna` is opt-in. On `2026-08-01-preview`, with the chat model of that time, this produced `modelQueryPlanning` activity and three sub-queries for one question. GA `2026-04-01` cannot plan with a model for `searchIndex` sources. The search service identity needs a role on Foundry for this: Cognitive Services OpenAI User, and Basic keeps Cognitive Services User as well. Output mode is `extractiveData`: the agent writes the answer.

### Retrieve request shape

The retrieve body uses `messages` and `maxOutputSize`. Live testing settled this: the docs also list `maxOutputSizeInTokens`, but the service wanted `maxOutputSize`.

### No page or section field in the chunk index

The skillset splits extracted text into character-based chunks, and extraction gives no reliable page or section for Markdown, DOCX and PDF alike. A field would be wrong or empty for some formats. Citations use the document title and the chunk content.

## Regions

- Sweden Central holds everything except the two below.
- Azure AI Search is in France Central (`AZURE_SEARCH_LOCATION`). Sweden Central and West Europe were flagged as not accepting new Search services. The cross-region placement costs a little latency.
- The Static Web App metadata is in East US 2. West Europe rejected new Static Web Apps. The content is global, so users are not affected.

## Models

- Chat: `gpt-5.4-mini` version `2026-03-17`, GlobalStandard, capacity 50 at first. The agents later moved to `gpt-6.1-terra`, and the capacity to 150: see below. `gpt-5.4-mini` is still deployed, for planning Search queries.
- Embeddings: `text-embedding-3-large` version 1, GlobalStandard, capacity 10.
- Capacity 10 on chat broke multi-question runs with rate limits. On pay-per-token, capacity is a cap, not a cost, so it was raised to 50.

## Agent core

### Plain Responses loop instead of Agent Framework

The design preferred Microsoft Agent Framework, with the plain OpenAI SDK as the stated fallback. `agent_core` uses the fallback: a plain loop over the Responses API. Pausing for client tools and emitting trace events are first class in the loop, and the comparison needs an honest line count. Agent Framework was not tried, so this does not show that it cannot do the job. The choice is reversible.

### Server tools are listed whatever the role

`ToolSet.definitions` offers every server tool, and `execute` enforces roles. A denied call must be a visible `authorization_denied` event, and a prompt agent cannot hide tools per user anyway. This overrides the plan text.

### Hosts emit `done`

The core never emits `done`, because only the host knows the backend. `TurnOutcome` carries `failed` status and `client_call_ids`, and terminal events stay single.

### The assistant is called Runa

From runes: written, hidden knowledge, which is what a retrieval assistant brings back. It is a short Swedish first name that fits a Swedish carmaker. Three rules come with the name (issue #15). Runa always says it is an AI and never claims to be a person. Questions about Runa itself need no search, and the routing suite checks that with three questions whose answers must name Runa or say AI. Nothing else changes: no persona, backstory or catchphrases. The AI rule and "questions about you need no search" sit in a bullet of their own at the end of the instructions, not in the routing list: put in the list, they moved parts questions such as "the mirror on my Spark is broken" to both sources, and the own loop dropped from 8 of 8 right to 2 of 6. Its avatar is a glowing rune-shaped V emblem, not a face, for the same reason; `data/design/images/runa/README.md` has the prompt. The code, packages and Azure resources keep the name `voltrieve`.

### Client tool calls carry the conversation id

`client_tool_calls` includes `conversation_id`, amending design section 8.1. A first-turn client has no other way to learn it.

## Turn state

### In-process turn state and `maxReplicas` 1

Paused turns live in a dict in the API process, so the API runs with `maxReplicas` 1. A restart loses paused turns and the client gets `turn_expired`. This is acceptable for a reference implementation at low cost. Running more replicas needs a shared store.

The pause limit is 15 minutes. A turn survives a client disconnect, and SSE sends a keep-alive every 15 seconds, because Container Apps closes connections idle for 240 seconds.

### Conversation owner stored in Foundry metadata

The owner `oid` goes into the Foundry conversation's metadata when the conversation is created. The in-process map is a bounded cache (1000 conversations). Otherwise scale to zero would break every conversation. Reading it back after a real restart is not in the recorded evidence.

A foreign conversation gets the same 403 as an unknown one, so ids cannot be probed. A foreign paused turn gets the same `turn_expired` answer as an unknown one, and is left untouched for its owner. If Foundry cannot be read at all, the lookup answers 503: an outage is not an unknown conversation.

### Stateless repair

A conversation touched first by a process, or left dirty by a failure, is repaired before use: function calls without an output get a synthetic one. This costs one extra list call per conversation per process. It covers lost pauses, failed closes and restarts.

### Concurrent turns

A second message while a turn is running gets 409 `turn_in_progress`. It is the simplest correct behaviour, and the client must retry.

## Backends

### A: three agents

Tools are fixed in the definition, so there are three agents: `voltrieve-prompt-cli`, `voltrieve-prompt-web` and `voltrieve-prompt-car`. The API picks the first, in the order web, CLI, car, whose tools include every declared one, and refers to it by name, which gives the latest version.

### C: Invocations protocol

Measured: the hosted Responses protocol drops caller `tools` and `metadata` (they arrive as `None`). Invocations forwards arbitrary JSON, SSE and `traceparent`.

### C: caller check fails closed

The agent accepts only the API's managed identity, read from `x-ms-original-subject`. With `ALLOWED_CALLER_OID` empty, every request is refused. `ALLOW_ANY_CALLER=true` is for local use only. The operator must keep `API_PRINCIPAL_ID` correct.

### C: lost stream grace

When the stream to the hosted agent breaks, the conversation stays blocked for 120 seconds and is then repaired. This avoids racing a detached turn. The user can wait up to two minutes after a dropped connection.

### Car images: JPEG originals in git

The 75 car images are stored as 1536 x 1024 JPEG at quality 90 (26 MB in all), not as the PNGs the image model returns (176 MB), and not in Git LFS. The PDFs embed these JPEGs as they are, so no second copy is needed; the spec's WebP copy is dropped, because Chromium would re-encode WebP losslessly into the PDF. Plain git keeps the repository working for anyone who clones it without `git-lfs`, and a public repository's clones would otherwise use the owner's monthly LFS bandwidth.

### Sales folder PDFs: default parsing

The sales folder PDFs are indexed with the default PDF text extraction, not the Document Layout skill. Measured on 2026-11-10: in all four test questions (a trim price plus a color price, which trims include a pack, the financing example, the boot with the seats folded) each table row came back with its model, its columns and its values on one line, and the chunk named the right document. Evidence: `docs/evidence/sales-table-test.txt`. Document Layout would add per-page cost and indexing time for no measured gain. Revisit if a later document puts tables across pages or in several columns.

## Clients and observability

### Backends are indistinguishable to clients

The prompt backend rewrites Foundry citation markers (`【5:0†file】` becomes ` (file)`, `【4:0†source】` is dropped) before text reaches a client, holding a marker back across deltas until it closes. The fact checks in `scripts/voltrieve_facts.py` no longer strip markers, so they judge what a client sees. The error code for a failed hosted call is `agent_unavailable`, not a name that gives the backend away.

### Client tool arguments are validated, and schemas may not use `$ref`

A client declares the schema of its tools, and the model is not bound to it. `ToolSet.validate_client` checks each call before it is forwarded; a failing call is answered with the error and never reaches the client. The API rejects `$ref` and `$dynamicRef` anywhere in a client schema (422), so the validator can never be made to fetch a URL.

### One correlation id per turn

The CLI and the website generate an id for each turn and send it as `X-Correlation-Id` on every request of the turn, and show it. A client tool turn is two requests under one id. Every hop of a turn is a log line and a span event, with arguments and result previews at debug level only.

### The CLI reads the azd environment

The CLI maps the azd keys (`API_URL`, `TENANT_ID`, `CLI_CLIENT_ID`, `API_CLIENT_ID`) itself; `VOLTRIEVE_*` variables override them. `voltrieve login` shows the account picker every time and `voltrieve logout` forgets the cache. Control characters in model text are removed before they reach the terminal.

### Dates follow the dealer

`get_current_date` and the dealer's "future date" rule use Europe/Stockholm, not the host's clock: containers run in UTC, which is a day behind for the first hours after midnight in Sweden.

## Identity

### Azure CLI pre-authorized on the API

`voltrieve-api` lists the Azure CLI as a pre-authorized application for `access_as_user`, so scripts can get tokens without an interactive login. Tokens still need the `Chat.User` role. This slightly widens who can mint tokens for the API in the tenant.

### Roles live in Bicep

Direct `az role assignment` was blocked by a safety check in this environment. Every Azure role is declared in Bicep, by GUID, at the target resource. App roles such as `Dealer.Access` go through Graph in the hook and scripts.

### Application Insights connection string

It is an ingestion identifier, not a secret: it only allows sending telemetry. It is set as a plain value. Everything else uses Entra tokens.

## Deployment

- Provision runs before package, because the web build needs provision outputs.
- The website upload runs in Docker on Apple Silicon. The SWA CLI's client is x86, and installing Rosetta is a system change.
- The root `.dockerignore` is an allow-list, because `azd` tar failed on files changing during the archive.
- The search smoke test runs in `postup`, so a miss cannot block a deployment. `scripts/smoke.py` runs at the end of `scripts/deploy-agents.sh` and covers the API, the dealer, the indexer and one turn per backend.
- The dealer client timeout is 45 seconds, because the dealer scales to zero. The earlier 15 seconds caused one grounding miss.

## Photos, the car and the website

Dated facts in this section are from the spike of 2026-11-23. The numbers are in [attachments.md](attachments.md).

### The photo goes straight to the model

Three ways to read a photo were tried: straight into the model call, through a photo reading tool, and through a fixed reading form. The first was right 54 of 54 times at a median of 1.6 s. The tool took 5.2 s and the form got 53 of 54. Straight into the call is faster and loses no detail. The price is that the photo must be kept out of the stored conversation, which the next entry deals with.

### Photo turns are unstored, and the text is recorded by us

A model call with an attachment is made with `store=False`, for every kind of attachment, so a new kind never touches a backend. The loop keeps the turn's items in memory and records only the text and the answer at the end. This keeps the photo out of the conversation. It did not keep the text out of Foundry's tracing, which is [disconnected](#closed-foundrys-tracing-is-disconnected) for that reason. The alternatives kept the photo: a stored conversation item still showed in the stored response after it was deleted, and a model still answered about a deleted file. The cost is that later turns do not see that turn's search results. `store=False` is not a promise of zero retention, and the documentation says so (links in [attachments.md](attachments.md#what-storefalse-does-not-promise)).

### No built-in parking rules

Public parking rules went four ways in 96 questions each: the model's own knowledge, the rules in the prompt, a tool that returns them, and a specialist tool that gets the photo. All four were right 96 of 96. The cheapest and fastest part is to build nothing, so nothing is built. What is not public cannot be known by a model, and for that [the extension table](attachments.md#how-it-extends) says where it goes.

### Reading a road sign is the one exception to answering only from documents

Runa answers only from retrieved documents and tool results. Reading a public road sign in the user's photo needs the traffic rules, which are in no document of ours. The instructions allow general knowledge for that, and for nothing else. The three new bullets sit outside the routing list, because text inside that list has moved the routing before. The order of a parking answer is in the instructions, for parking only. How to read a sign's panels is not, since the model knows it and the tests check it.

### No skills, for now

Agent Skills would let someone change what Runa does without deploying code. Whoever runs this architecture owns the backend, and a rule or a routine can be a server tool with a schema, a role and tests. A skill needs its own harness on the server and gives nothing more while every change is a deployment. Foundry's own skills are in preview and are described only with the Copilot harness, which does not accept `store: false`. This is issue #17, and [attachments.md](attachments.md#why-no-skills) says what would change it.

### The car is a browser app, not a native one

A browser cockpit can be opened by anyone who watches a demo, signs in the same way as the website, and costs nothing more than a Static Web App on the Free tier. A native app would need a build and a device for each audience, and the car has no hardware to show. The street is a 3D scene (three.js) behind a picture of the cockpit, which costs one more dependency (the scene is 163 kB compressed). The cockpit and five house fronts are generated pictures, which cost 0.32 USD in all and are provisional until the product owner has picked them. The rest of the street, the signs and their photos are drawn by code, so the text on a sign is right and the answer is known. Sprites would have made the sign photo fake, and a looped video could not vary the signs or let the car park. The price: the cars on the street are simple when they are close, the steering wheel does not turn, and very wide windows lose part of the picture.

### One client for each audience

The web client had become a customer chat, an event viewer, a backend picker and an operations dashboard in one, and the CLI did not look like a car. Now the car is for the driver, the website for a signed-in customer, the dashboard for employees and the CLI for developers. Each has its own app registration and Static Web App, so a role or a redirect is changed for one audience at a time. The cost is two more registrations and two more sites on the Free tier. See [clients.md](clients.md).

### The car has its own prompt agent

A prompt agent's tools are fixed in its definition, so the car needs its own, `voltrieve-prompt-car`. The API now picks the first agent, in the order web, CLI, car, whose tools include every declared one. No match still gives `unsupported_client_tool`. The old rule picked the CLI agent whenever any declared tool was a CLI tool, which breaks when the car also has `honk`.

### `set_speed` does what it is told

The car is a simulation, so `set_speed` accepts any speed from 0 to 150 whenever the model asks. A real car must decide for itself what it accepts, whatever an assistant requests, and must never leave a safety decision to a model. The documentation says so, so nobody takes the demo for a design to copy.

### The hosted agent is deployed before the API

With a new API and an old hosted agent, photo turns answer without the photo and the protection of the logs is missing. So the order is the provision, then `scripts/deploy-agents.sh`, then the API, and a rollback goes the other way round. See [deploy.md](deploy.md#order-for-an-update-with-attachments).

### Closed: Foundry's tracing is disconnected

<a id="open-the-product-owner-decides-what-foundrys-tracing-may-record"></a>

This was open as *The product owner decides what Foundry's tracing may record*. The product owner chose on 2026-11-24 to **disconnect Application Insights from the Foundry project**, because Voltrieve is not interested in stored chat text.

**Why it was asked.** Rule 4 of the [principles](principles.md#4-secure-by-default) says that no prompts or answers may be in telemetry. For turns on the prompt agent and the hosted agent that was not met (`docs/evidence/photos-privacy.txt`): Foundry's server-side tracing wrote the question, the line from the client and the answer to the Application Insights resource, in `genAIContent` and `dependencies`. It did so since 2026-11-03, and it starts when Application Insights is [connected to the project](https://learn.microsoft.com/azure/foundry/observability/concepts/trace-data). The own loop left no such rows, and our own spans were clean. `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false` is set for the API and the hosted agent as defence in depth. It does not control the server-side tracing.

| Choice | What it gives | What it costs |
| --- | --- | --- |
| **Disconnect Application Insights from the project (chosen)** | The documented way to turn the tracing off | The traces in the Foundry portal, and the `responsesapi` and `agentsv2` rows of the end to end trace. The hosted agent loses the connection string that Foundry injects. Rows already written stay until retention removes them |
| **Protect the table** | Only those with the role Privileged Monitoring Data Reader can read [`AppGenAIContent`](https://learn.microsoft.com/azure/foundry/observability/how-to/traces-sensitive-content). The traces stay | The text is still recorded, so rule 4 must be overruled on purpose. It is a preview. Our rows were in `dependencies` too |
| **Accept it** | Nothing to build, and full traces | Anyone who can read the Application Insights resource can read the chats. Rule 4 must be overruled on purpose, and people who chat must be told |

**What was done.** The connection is gone from Bicep, and a check of the compiled template (`infra/tests/check_insights.py`, run by `test_bicep.sh`) keeps it out of every module. Bicep is incremental and does not delete, so the deployed connection `appinsights` was deleted by hand at 14:25 UTC, and nothing else was. An older deployment of your own needs the same step, and so does any deployment after a provision from before 2026-11-24, which [recreates it](deploy.md#order-for-an-update-with-the-usage-insights).

**What was found.** The hosted agent went silent after the disconnect. The platform leaves a value that does not parse in `APPLICATIONINSIGHTS_CONNECTION_STRING`, and the exporter also parses that variable. So the first searches proved little for the hosted agent. It is fixed in `agents/hosted/main.py`, in version 35 of the agent.

**The proof** (`docs/evidence/tracing-disconnected.txt`). One marked turn per backend, with the marker in the question and in the client's line, then a search of every table of the workspace.

| Run | Own loop | Prompt agent | Hosted agent |
| --- | --- | --- | --- |
| Before, connected | No rows | `AppDependencies` 3, `AppGenAIContent` 3 | `AppDependencies` 4, `AppGenAIContent` 4 |
| After, hosted version 35, 16 and 47 minutes later | No rows | No rows | No rows |

The hosted agent's own logs and spans arrive from version 35, and one trace id joins the API and the hosted agent. The smoke test passed 10 of 10. First text on hosted had a p95 of 13.6 s, over the 10 s limit as before (12.6 s over the days before). The own loop and the prompt agent are under it.

**Not verified.** Whether a text found only in the answer would show (the marker was not put there), the Agent 365 exporter that the hosted agent's platform turns on (it reported that it had nothing to export, nobody at Voltrieve asked for it, and it is not ours to change here), and what Foundry keeps for itself. Rows from before 14:25 UTC stay until [retention removes them](runbook.md#the-operations-dashboard).

### The hosted agent gets our own connection string through the host's argument

Without the connection to the project, the platform gives the hosted agent no usable connection string, and it reserves the usual name of the variable. So `deploy.py` sets `VOLTRIEVE_TELEMETRY_CONNECTION_STRING`, and `main.py` hands it to the agent server host as `applicationinsights_connection_string`. The host still configures Azure Monitor itself. The other ways were to start the Azure Monitor exporter in our own code, which is more code to own, or to log to the console only, which loses the agent's spans. In the process our string also replaces the broken one in `APPLICATIONINSIGHTS_CONNECTION_STRING`, because the exporter parses that too. Without our variable the broken value is removed and the host starts without telemetry. The string is an ingestion identifier, as [above](#application-insights-connection-string).

### A header can only take a conversation out of the counting

Conversations and turns are marked with the client (`car`, `web`, `cli`, `other` or `test`). The client comes from the `azp` claim of the validated token, not from a name that the client sends, so a caller cannot choose to be counted as another client. Our own test tools sign in like anyone else and had to be told apart. They send `X-Voltrieve-Test: 1`, which turns the client into `test`. The header can take a conversation out of the counting and never put one in. The price is that any caller can take its own conversation out. The car model is the one the client states, kept only as a slug from the catalog (`api/app/clients.py`).

Seen live: the newest conversation per backend carried `test`, its backend and the car model `spark`, and 137 turns carried `voltrieve.client`, all `test` (`docs/evidence/tracing-disconnected.txt`). Not verified: a conversation marked with `car`, `web` or `cli`, because every marked turn was our own run.

## Usage insights

Decided on 2026-11-24. The [design](https://github.com/mklab-se/voltrieve/blob/main/docs/superpowers/specs/2026-11-24-usage-insights-design.md) is approved by the product owner, and its last section says what changed while building. The evidence is in `docs/evidence/insights-*.txt`. The job has only run on made up conversations.

### A nightly job instead of labeling each turn

The product owner chose a job that reads Foundry's stored conversations at night, over labeling each turn in the API right after the answer. The job sees a whole conversation, so it can judge whether the person got what they needed, and nothing is added to a turn. All three backends keep their conversations in the same store, so one job covers them. The price: one identity can read everyone's chats, and nothing shows until the next night. Foundry's own cluster analysis needs samples with the full text, and an agent that labels itself can slow or disturb answers.

### No stored intents and no tokens for people

The product owner chose labels and a short intent sentence, not labels only. The job reads the same seven days again each night, so it does not need to keep either between nights. Intents and owners are in its memory only. What is saved is groups and counts. A second store of chat text was named as a failure in the design.

### Log Analytics instead of a database

The totals are a custom table, `VoltrieveInsights_CL`, kept for 12 months while the workspace stays at 30 days. It adds no service to run and close to no cost (a few MB a year), and the workbook reads it directly. A run is one upload, so it is saved whole or not at all. The price: rows cannot be changed, so a new run for a week adds rows, and each reader takes the newest week and then the newest run of it (an older week can be run later, for a demo). And a query waits a few minutes after ingestion.

### The rule of five, and what it still leaves readable

A row is saved only if five different people are in it, and counts are rounded to five. The limit is a setting that can be raised and never lowered. Two more rules came while building:

- **Suppression.** Where the labels that are missing are few and known, the saved parts of a row never leave a rest of one to four people. Then the smallest part is held back too. This holds for client, car model, result and kind, and for the results of groups.
- **At most three conversations of one person count in a row,** so one person with many chats does not make a topic large.

A script over 3,000 random inputs (24,884 saved rows) found no row of fewer than five people, and no such rest where the rule applies. What a reader can still work out, on purpose:

- Topics and groups hold nothing back, since one person with another question would empty the page. So a reader can tell that one to four people asked about something else, but no row says what.
- A missing row means under the limit, held back, or that nobody asked.
- Rounding hides exact counts, not that a rest exists when the whole is clearly larger than its saved parts.
- A rest of five or more can be several small parts, and a reader who knows that each is under the limit can guess how they split.
- Runs overlap, so two nights can be subtracted. Closed weeks without overlap would remove that, at the price of fresh numbers.

None of it names a person. No more ways to cut the numbers are added, since each one would add a way to subtract.

### Only names that can be saved are checked for personal data

Patterns do not catch the names of people, and street addresses and postal codes are masked only as a best effort (see [the dealer decisions](#dealer-orders-and-inventory)), so a group name passes Azure AI Language's detection of personal data, and one that fails becomes "unnamed group". Only the names of groups with five people are sent to it. A smaller group becomes "other", so its name goes nowhere and there is nothing to check. That keeps the calls few. The run fails if the detection does not answer. Four categories pass, since they are ordinary in a car question: quantity, date and time, type of person, and organization. So the name of a company could pass the detection, and only the prompt forbids it. Live, two forced names were replaced and two plain ones kept. The planted details never reached a name, so this does not say how the detection handles a real one. Its price was not found for the text meter. Microsoft's page says about 1 USD per 1,000 texts.

### A cap and a random sample

Above 1,000 real or 500 made up conversations, a week is a random sample and the page shows the share, so a busy week cannot become a large bill or an empty page. The sample is of conversations, not of people. Each source has its own cap (`INSIGHTS_CAP` and `INSIGHTS_SEEDED_CAP`), so made up conversations never take the room of real ones.

The approved design said 2,000 for each. Both full would take about 31 minutes at the measured speed, over the deadline of 25, and the run would fail. 1,500 in all take about 12. Labeling faster is not the way out: `gpt-6-luna` has a capacity of 150 thousand tokens a minute, shared with Search query planning when `PLANNING_MODEL_NAME=gpt-6-luna`, and the measured run used about 146 thousand (132 of them input). So the number of labels at once stays at eight. If a 429 remains after the retries, the run fails closed. The price is a sampled share sooner than planned.

### The job and the API may read one table

Both identities read through Log Analytics Data Reader with a condition on the name of the table, because the same role without it opens every log. The job may also read conversations, call models in the Foundry account and analyze text (a custom role with three data actions), pull its image, and write to its own rule. The role cannot be held to one model. Live, the job's identity got 0 rows from `AppTraces`, `AppGenAIContent`, `ContainerAppConsoleLogs_CL` and `search *`, and 403 when it tried to create, add or delete. Deleting a conversation that exists was not tried.

### Real chats are read from the first full day after the notice

The car and the website say that a nightly job counts what people ask about. Real conversations are read only if they started on or after `INSIGHTS_NOTICE_DATE`, the first full UTC day after the notice was live. A date and not a time, because the job counts whole days and a chat from the day the notice appeared may be older than it. Empty means that no real conversation is read, and so does a run with `--window-end`. The date and the schedule stay off until the product owner says yes to both.

### Foundry's tracing

Disconnected, with its own entry: [Closed: Foundry's tracing is disconnected](#closed-foundrys-tracing-is-disconnected).

## Dealer orders and inventory

Decided on 2026-11-24. The [design](https://github.com/mklab-se/voltrieve/blob/main/docs/superpowers/specs/2026-11-24-dealer-orders-design.md) is approved by the product owner. It adds a system that people can look at beside Runa, and a second lesson: what Runa may do depends on where it is used. Run live on 2026-11-24 ([`dealer-orders.txt`](https://github.com/mklab-se/voltrieve/blob/main/docs/evidence/dealer-orders.txt)).

### The dealer serves its own page

The page for the representative is built into the dealer image and served by the dealer service itself, from `GET /`. Runa then calls the same system that you look at. The other ways were a separate static site, which needs cross-site setup and one more site, a page that goes through the Web API, which does not look like a system of its own, and a page in the operations dashboard, which mixes sales with the people who run the service. The price is that the page shares the cold start of the service, and that the image also builds the page. Only the page, `/assets/*` and `/config.json` (tenant and client ids) are open to anyone.

### Two doors with two roles

The machine routes need `Dealer.Access`, which only applications can hold. The staff routes need a signed-in user with the scope `manage_as_user` and the role `Dealer.Manage`. Neither opens the other: Runa can read stock and place orders, and cannot deliver, delete or change stock. The page has its own registration, `voltrieve-dealer-site`, which may use that scope and nothing on the API. Ordering needs a third role, `Order.Place`, on the API, so that nobody types an address only to be refused. The deployer gets `Order.Place` and `Dealer.Manage`. The test user gets neither. The Azure CLI is pre-authorized for the dealer's scope, as it is for the API, so that our scripts can read the stock and delete the orders they made. Calls without a valid token are not in the log, since anyone on the internet could flood it. A signed-in caller without the right role is.

### The car rule is in code, by the token's `azp`

A server tool may name the clients it is for. Both order tools name `web` and `cli`. The client is read from the `azp` claim of the validated token, never from a header, the model or the conversation, so a car that calls itself something else is still a car. An unknown client is refused too. The check is in `ToolSet.execute`, before the role check, so it is the same code on all three backends. The refusal is a fixed text that tells Runa what to say, and the trace event is `client_not_allowed`. The tools are listed for the car too, so the refusal is ours and not something the model makes up, and the attempt can be seen. A refused order never reaches the dealer service, so it is not in its log.

- The Azure CLI counts as `cli` for this rule only, not for the usage insights, where it stays `other`. A person who holds `Order.Place` could use the Voltrieve CLI in the same terminal anyway.
- To test the car's refusal on the deployed service, a script sends `X-Voltrieve-Client: car`. The header can only turn a client into the car: it takes away and never gives. `X-Voltrieve-Test` does not change who may order, so an order in a test turn is a real row, and the script deletes it.

### An order needs a later message

Runa saves an offer with `prepare_car_order` (customer, model, price, address and the turn it was made in) and reads it back. `place_car_order` places it only for the same customer, in a later turn, within 30 minutes. In the same turn the service refuses and Runa must ask. So text in a document, or an eager model, cannot order in one step. That the later message really says yes is left to the model, and is measured by `scripts/orders_check.py`. An offer becomes one order, so placing it again returns the same order: a lost reply is answered by placing the same offer again. The name is the `name` claim of the token, or the username when there is none. It never comes from the model.

### Table Storage and one replica

Stock, orders and offers live in one table in the existing storage account, in one partition, so that a delivery is one transaction: the order changes and the stock goes down, or neither. ETags stop two clicks from both winning. The dealer's identity may read and write that table and nothing else in the account. No new resource or plan is added. Without a working store the calls answer 503 and `/healthz` still answers. The memory store is only for tests and local work.

The service runs as at most one replica, so every open screen gets every event, and it can still scale to zero. An open page reads the state once a minute. That is meant to keep the service awake, and it repairs a missed event. `scripts/dealer_awake.py` measures it. During a deployment two copies may run for a moment: the store is safe then, but a screen can miss an event until its next read.

### The log is in memory

The log holds the latest 200 entries and is lost at a restart. Orders and stock are not. An entry has time, what happened, who (Runa, or the representative's name), the client when known, the result and the correlation id. It names the model, city and order number, and never an address. A database for the log would have been one more thing to keep clean of personal data, for a demo screen.

### Personal data, and the masking

For the first time Voltrieve stores a name and an address itself. The name is the signed-in account's real name, and the address is what the customer types: **use a made up address in a demo.** Both are shown to staff with `Dealer.Manage`, and are deleted with the order, by a reset, or at the first use after 30 days (a service that nobody uses deletes nothing, so this is no firm limit). They are never in a log line, span or metric, and a validation error does not repeat the input. The chat where the address was typed is stored in Foundry like every chat. Masking of Swedish street addresses and postal codes was added to the live dashboard and the nightly job as a best effort: a pattern cannot find every address, and the names of people are still not caught. The website's notice has one more sentence about orders.

A marker in an address was found in no table of the workspace ([`dealer-orders.txt`](https://github.com/mklab-se/voltrieve/blob/main/docs/evidence/dealer-orders.txt)). Not verified: the masking on real addresses.

## Bring your own AI: four MCP doors

Decided on 2026-11-25. The product owner ordered the build of [the design](https://github.com/mklab-se/voltrieve/blob/main/docs/superpowers/specs/2026-11-25-mcp-doors-design.md) without reading it first, and the choices below were made by the builder. How to connect is in [Bring your own AI](bring-your-own-ai.md).

### One door per audience

Operations, car, dealer and customer are four OAuth resources with four registrations. A token for the car is then useless at the dealer. One server that filters its tools by role would be less to run, but a mistake in the filter would open everything. Inside a door the rules are still checked on every call, since a tool that is not listed can be called all the same (tried, and it could).

### A door stands in the service that owns the data

The API has three and the dealer service one. A gateway would need to exchange the token, because MCP forbids passing on a token that was issued for something else, and the API would then see the gateway as the caller. Azure API Management would give limits and monitoring ready made, and a standing cost. The price is that a door shares the one replica with Runa: at most four calls at a time, short timeouts, and no model call except in `ask_runa`.

### A guard in front of the SDK

The official SDK does the protocol. Used as it comes, it wrote the caller's own words into span names, span attributes and logs. So its tracing middleware is taken out, its logger is silent, and what we record has only texts from our own lists.

### Clients are registered by hand, without secrets

Each harness has a public client with PKCE. Entra ID has no dynamic registration, and client ID metadata documents are a preview without a service level. A calling app that is not on the list is refused, so a new harness is a decision. The Azure CLI may call only where `MCP_ALLOW_CLI` is true, for scripts.

### The car decides last

The car page opens a stream to the API when the driver switches remote control on. Web PubSub would be right with more than one replica. The door has four functions, and driving, parking and the camera are not among them. The API checks that the car is parked, and the car checks it again.

### A dealer is a list from caller to city

The city never comes from an argument. An unattended agent can change stock and read orders, and cannot deliver, since there is nobody to ask. Names and addresses are not given: an order belongs to whoever delivers it, and the address is on the staff page.

### Two ways for a customer, in two doors

The tools and `ask_runa` first shared a door, with a scope each. Entra ID puts every scope that was consented to in the token, so a harness would have had both, and nothing could be compared. So `ask_runa` has a door of its own, with its own places, since a turn may take long. The nightly job does not read such questions: whoever asks has not seen the notice. No signed links are given, because they would live on in someone else's chat. Ordering a car is left out, since nothing in an outside harness is a later message in our sense.

### The newer sign-in server of Entra ID

Live on 2026-11-26, Entra ID's ordinary authorization server publishes no `code_challenge_methods_supported`, and a client that follows MCP must then refuse it. The versioned server `oauth21/2026-08` publishes S256, the `iss` parameter and client ID metadata documents. It is a preview without a service level, and it is what a harness needs today. ChatGPT then chose its own metadata document, which asks for `private_key_jwt`, and Entra ID refused that.

### Remote control is off by default, and a door is awake only when used

A replica that always runs would avoid the half minute that a first call takes, and would be a standing cost. It was not chosen.

## Live findings

- Prompt agent with `agent_reference` plus `tools`: `400 Not allowed when agent is specified.`
- Responses with `conversation` plus per-request `tools` works. `function_call_output` works with a model and with `agent_reference`.
- Prompt agent: the knowledge base attaches by MCP through a `RemoteTool` connection with `ProjectManagedIdentity`. The result has no references or activity, and citations appear as `【5:0†file】` markers, which the API rewrites before they reach a client. Roles can only be enforced in the relay in the API.
- Hosted: the gateway replaces `x-ms-original-subject`. A forged request as the deployer got 403 from the handler.
- Hosted: each agent has an Entra identity (`instance_identity.principal_id`), stable across versions.
- Hosted: the first call on a new session is slow, a broken image gives `session_not_ready`, sessions bind to one version, the idle timeout minimum is 120 seconds, and billing is for active sessions only.
- Hosted deployment needs a `linux/amd64` image, Foundry Project Manager, and registry pull roles for the project identity. Search and dealer roles for the agent identity come after that identity exists, so a second provision is needed.
- Grounding, 30 questions over 3 backends: 30 passed on the final build (`docs/evidence/grounding.txt`). An early run had 28: a dealer cold-start timeout, fixed with a 45 second timeout, and one wrong answer.
- "Cheapest AWD in Sweden" repeated 6 times per backend: 18 of 18 right at reasoning effort medium (`docs/evidence/cross-document-repeat.txt`). At the default effort it was 13, 11 and 15 of 18 on three builds, with wrong answers treating AWD-only models as the only AWD models. An added instruction did not help and was removed. Effort is set in `voltrieve_agent.model.REASONING_EFFORT`, for the Responses call and for the prompt agents' definitions alike.
- Rate limits: Foundry reports them mid-stream, so SDK retries never see them. `ResponsesModelClient` retries up to 3 times before output starts. The chat deployment cap is 150 (150k tokens a minute); it is a limit, not a cost. Lower caps caused `model_error` in burst runs.
- Paused turn abandoned, and conversation resumed after 420 seconds idle so the API scaled to zero: passed on all three backends, with `turn_abandoned` and `conversation_repaired` in the trace. `docs/evidence/repair.txt`.
- While Application Insights was connected to the project, one trace id spanned the API, Foundry, the hosted agent and the dealer. Foundry added its own `responsesapi` spans for prompt and hosted agents, and an `agentsv2` request for hosted. The `responsesapi` spans held the question and the answer (found on 2026-11-24, `docs/evidence/photos-privacy.txt`), so it is [disconnected](#closed-foundrys-tracing-is-disconnected). The direct loop got none of these. `docs/evidence/application-insights.txt`.
- `scripts/denied_demo.py` on `loop` gave `authorization_denied` and a polite refusal (`docs/evidence/authorization-denied.txt`).

### Second knowledge base

Spare parts are a separate knowledge base with its own container, index, indexer and knowledge source, not a second source in `voltrieve-kb`. A knowledge base with both sources let the search planner route, and it used only the parts source in 3 of 3 runs.

The service guide sits in the documents base and has no article numbers. The catalogs have no procedures. This makes the question that needs both real, instead of a test written to pass.

Every catalog row names its vehicle, because search returns chunks and a row must make sense without its heading. Parts search uses an overlap of 200 characters instead of 500, since rows stand alone.

### Parallel tools

Reads of one model response run together. A tool with `concurrent=False` waits for them and runs alone, so a booking never races a read. Outputs reach the model in call order, whichever finished first.

### No router step

A separate planning call would add a model round trip to every turn, to do what the first model call does anyway. The routing check found no call in vain without one.

### Follow-up search for what the first search could not know

After a job search, the agent checks each component against the catalog results and searches again for the rest. It costs a third step on some turns. It does not always fire: in the final run brake fluid was still missing from 4 of 36 job answers on the own loop and hosted agent.

### Search retries on 429

A retrieve spends model tokens on query planning, so a busy deployment answers `429`. `KnowledgeBase` retries twice, after 10 and 20 seconds. Other failures go straight to the model as a tool error.

### The deployment script refreshes search as well

`scripts/deploy-agents.sh` reinstalls `voltrieve-search` next to `voltrieve-agent-core`. Without it the prompt agents failed on a stale copy, and the agents stayed on the previous version.

### Curated answers in the model's first call

The tool list costs a few hundred tokens and no call. A check in front of the model, or a search on every turn, would make every question pay for answers that a few need. An embedding match in the API would be faster for a curated question but cannot see the conversation, and it could match "the phone number to the Stockholm dealer". A handler returns `FinalAnswer`, a `str` subclass, so the loop can skip the second model call without any change to the tool contract. Errors stay plain text and go to the model. The short cut only fires when the tool is the only call of the first model response. The first live run showed why: the prompt agent called the tool in 11 of 12 two-base job questions on its way to the searches, the same habit `prompt-extra-steps.txt` shows with dealer tools, and one brake question ended with the phone number. Genuine curated questions chose it in the first response in 81 of 81 runs. After the fix the prompt agent still made the needless call in 7 of 12 job questions, but none ended early: 117 of 117 article numbers were found, and curated questions stayed at 81 of 81. If writing the answer to the conversation fails, the turn fails with `model_error` before any text is sent, and the conversation is repaired on next use.

### Prompt agents get their own sentence on searching

The instruction "call them all in the same step" made the prompt agent emit two parallel function calls before searching, because it cannot run MCP searches in parallel: 11 of 12 first responses. `parallel_tool_calls=False` on the request also stopped it, in 4 of 4 runs, but it would make a mixed curated question end early, so the instructions differ instead. Search, then catalog, and no other tool first: 0 of 7 first responses, then 1 of 27 job runs in the live checks.

### All backends run gpt-6.1-terra

Speed decides as long as quality holds. `gpt-6.1-terra` had the fewest turns over 10 seconds to the first token, or tied for the fewest, on every backend, at about the cost of `gpt-5.4-mini`. Search planning stayed on `gpt-5.4-mini`, because Search refused GPT-6 models then. The newer Search versions accept `gpt-6-luna` (not `gpt-6.1-terra`), and it is deployed but opt-in: see [above](#search-moved-to-the-foundry-iq-versions). The agent and the planner have deployments of their own, so they do not share a token limit. The figures are in `docs/model-comparison.md`.

### Brake fluid is listed in the brake catalog too

All remaining article misses were brake fluid, which only sat among the service items, so a search for brake parts did not reach it. The brake catalog now has a section for consumables, and all 111 article numbers were found on all three backends. Regenerating also brought the catalog in line with the generator: 429 parts, and new article numbers from the old 433.

### Source links cite titles, not numbered markers

Issue #2 proposed `[1]` markers mapped to retrieval references. The model already cites document
titles, and the prompt agent searches inside Foundry, so the API never sees its references.
Reading the titles from the answer works the same on every backend. The first live run showed
that the model often wrote a readable name ("Voltrieve buyer's guide") instead of the file name,
so the instructions now ask for the file name exactly as the results give it, with an example.
After that, all three backends cited file names in every test question. The API looks the titles
up in the indexes, so an invented title gets no link. Other choices:

- `read_more` is decided by rule, not by the model: the brochure and specifications of every
  model a sales answer names. It is predictable, costs no model call, and gives every car the
  same two documents.
- `GET /sources/{id}` answers with JSON (`url`, `expires_in`) instead of a redirect. A browser
  cannot send the bearer token on a link it opens, so the client fetches the link and then opens
  it in a window it opened during the click.
- `linkable` is a string field, "true" or "false": custom blob metadata reach the index as text.
  Anything but "true" is not linkable.
- The plain-text citation stays in the answer, so clients that ignore the event still name the
  sources.
- Curated answers carry no sources: they cite nothing.

## Not recorded or not verified

- Interactive website sign-in, and a real browser sign-in with `voltrieve login` (tested against a fake MSAL application).
- A real sign-in as the tester. The denied case ran with a fake validator.
- Publishing to Teams and Microsoft 365.
- Cold-start figures. Whole turn times are in `docs/why-foundry.md` section 6, measured from one laptop.
- Whether `alwaysQuerySource` would make one knowledge base cover both sources.
- A trace with the dealer since Foundry's tracing was disconnected, and the Agent 365 exporter of the hosted agent's platform.
- The usage insights on real conversations and on a schedule, the job at its cap, group names over several nights of real use, the price of the detection. The queries that take the newest week first, for the page and for the group names, ran live, and the deployed model calls accept `store: false`.
- A 403 from `GET /admin/insights` for a user without `Dashboard.View`, live.
- The 10 minute run expiry from older documentation.
- Whether an MCP tool with a project connection works outside an agent definition.
- Whether Foundry keeps a photo that was sent in an unstored turn, for abuse monitoring or anything else.
- A browser sign-in to the car and the new website, and an unstored turn inside the hosted agent, until the evidence runs say otherwise.
- Indexer duration, final item counts, how long role propagation retries waited, and smoke test scores. The first deployment did not keep these.
- The dealer system and the orders: the sign-in to the page, other browsers than Chromium, a token of Runa on the staff routes (tests only), the `name` claim in a token of the website or the CLI. The full routing suite after the new instructions reached the right sources in 139 of 141 turns (`docs/evidence/routing.txt`).
