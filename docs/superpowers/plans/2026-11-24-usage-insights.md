# Usage Insights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Say what people use the assistant for, per topic, car model and client, without storing a chat or anything that points at a person, and stop Foundry's tracing from recording chat text.

**Architecture:** The API marks each new conversation and the turn span with the client. Foundry's tracing is disconnected. A nightly Container Apps job reads the last seven settled days from Foundry's conversations, labels and groups them in memory, and saves only totals of five people or more to one Log Analytics table, which the dashboard (through the API) and the workbook read.

**Tech Stack:** Python 3.12, FastAPI, httpx, azure-identity, Bicep, Container Apps jobs, Logs Ingestion API, Azure AI Language, React and TypeScript.

**Spec:** [docs/superpowers/specs/2026-11-24-usage-insights-design.md](../specs/2026-11-24-usage-insights-design.md)

## Global Constraints

- No prompt, answer, intent, conversation id, user id, token or registration in any log, span, saved row, error message or evidence file. Evidence holds counts, labels and made up text only.
- Every total, and every part of it, needs at least **5** different people. Counts are rounded to the nearest 5. The limit is a setting that may be raised and never goes below 5.
- Made up (`seeded`) and real conversations are never grouped or counted together.
- Real conversations are read only when `INSIGHTS_NOTICE_DATE` is set, and only those created on or after it. Empty means none.
- Window: 7 whole UTC days that ended at least 24 hours ago. Cap: 2,000 conversations, then a random sample with its share saved.
- Counted clients: `car` and `web`. Skipped: `cli`, `other`, `test`, unmarked.
- A run that fails saves nothing.
- Models: `gpt-6-luna` for labels and group names, through chat completions, never the Responses or Conversations API. The job never writes to Foundry.
- Group names and intents are in English, at most 60 and 100 characters.
- Keyless. Least privilege in Bicep, role GUIDs or custom roles only. Libraries released at least 168 hours ago.
- All three backends. The routing suite stays within the 10 s limit. Live measuring costs at most 5 USD in all.
- No em-dashes. Plain short sentences. Commit messages: one plain imperative sentence, then the `Co-Authored-By` trailer. Never name a client or hint at one.
- Nothing is deployed and nothing in Azure changes outside the tasks marked **live**. Those are run by the controller, not by a subagent.

## Review Focus

1. A library logs request URLs or bodies by default (httpx logs each URL at INFO, with the conversation id): nothing of the kind reaches stdout (Task 6 test).
2. An empty client id setting matches a token without `azp`: the client becomes `other`, never `car` or `web` (Task 1 test).
3. A conversation with only a photo turn, or with no user text at all: skipped, not an error (Task 6 test).
4. The model gives a group an index twice, or one that does not exist: each conversation counts once, and the run goes on (Task 10 test).
5. A night with nobody over the limit: the run still saves its `run` row, so the workbook does not call it missing (Task 11 test).

## Stops

The controller stops and asks the product owner when: a marked turn still leaves text in a table after the disconnect (Task 4), no role without write access can read conversations (Task 5), the job's own model calls leave text anywhere (Task 9), or personal data detection is not available or costs more than 1 USD a month (Task 9).

## Files

| File | Responsibility |
| --- | --- |
| `api/app/clients.py` | Which client a token belongs to, and the conversation's metadata |
| `agent_core/src/voltrieve_agent/private.py` | `mask_contacts`, shared by the live feed and the job |
| `services/insights/` | The job: one uv project, `voltrieve_insights`, with its Dockerfile and tests |
| `... /conversations.py` | List, select and read |
| `... /masking.py` | Masking and the intent filter |
| `... /labels.py` | The topic list, the schema and the labeling call |
| `... /groups.py` | Grouping, naming and the personal data check |
| `... /totals.py` | The rule of five, rounding and the rows |
| `... /store.py` | Write rows, read the previous group names |
| `... /job.py` | One run, the settings, the exit code |
| `infra/modules/insights.bicep` | Table, rule, job, identity, custom role and assignments |
| `api/app/usage.py` | The query behind `GET /admin/insights` |
| `clients/dashboard/src/insights.ts`, `components/Insights.tsx` | The page |
| `scripts/seed_conversations.py`, `scripts/labels_check.py`, `scripts/tracing_check.py`, `scripts/deploy-insights.sh` | Made up data, the two checks, the job's image |

---

# Step 1: tracing and history

### Task 1: The API marks the conversation and the turn

**Files:**
- Create: `api/app/clients.py`
- Modify: `api/app/auth.py`, `api/app/settings.py`, `api/app/backends/base.py`, `api/app/turns.py`, `api/app/routes.py`, `api/app/insights.py`, `infra/modules/api-app.bicep`, `infra/main.bicep`, `infra/main.parameters.json`, `clients/dashboard/src/dashboard.ts`, `clients/dashboard/src/components/Dashboard.tsx`, `clients/dashboard/mock/server.mjs`
- Test: `api/tests/test_clients.py`, `api/tests/test_auth.py`, `api/tests/test_chat.py`, `api/tests/test_insights.py`, `api/tests/conftest.py`, `clients/dashboard/src/dashboard.test.ts`

**Interfaces:**
- Produces: `Claims.azp: str = ""`. `client_name(settings: Settings, azp: str, test: bool) -> str` gives `car`, `web`, `cli`, `other` or `test`. `conversation_metadata(owner_oid: str, client: str, backend: str, car_model: str | None) -> dict[str, str]`. `Backend.create_conversation(metadata: dict[str, str]) -> str`. `Job.client: str`. `TurnRecorder.client` and `TurnSummary.client`, on the span as `voltrieve.client` and on the feed as `client`. Settings `car_client_id`, `web_client_id`, `cli_client_id`. Header `X-Voltrieve-Test: 1`.

- [ ] **Step 1: Write the failing tests** in `api/tests/test_clients.py`

```python
S = Settings(car_client_id="car-app", web_client_id="web-app", cli_client_id="cli-app")

@pytest.mark.parametrize("azp, test, name", [
    ("car-app", False, "car"), ("web-app", False, "web"), ("cli-app", False, "cli"),
    ("04b07795-8ddb-461a-bbee-02f9e1bf7b46", False, "other"), ("", False, "other"),
    ("car-app", True, "test"), ("someone-else", True, "test"),
])
def test_client_name(azp, test, name):
    assert client_name(S, azp, test) == name

def test_an_empty_setting_never_matches_an_empty_azp():
    assert client_name(Settings(car_client_id="", web_client_id="", cli_client_id=""), "", False) == "other"

def test_metadata_has_the_model_only_when_the_catalog_has_it():
    assert conversation_metadata("o", "car", "loop", "Voltrieve Spark") == {
        "owner_oid": "o", "client": "car", "backend": "loop", "car_model": "spark"}
    assert "car_model" not in conversation_metadata("o", "car", "loop", "Volvo EX30")
    assert "car_model" not in conversation_metadata("o", "car", "loop", None)
```

And in the existing files: `test_auth.py` checks that `azp` is read and that a token without it gives `""`. `test_chat.py` checks that a first message with the header `X-Voltrieve-Test: 1` creates a conversation with `client == "test"` on each of the three backends, that one without it takes the client from `azp`, that a later message does not create metadata again, and that any other value of the header is ignored. `test_insights.py` checks `voltrieve.client` in `span_attributes()` and `client` on the feed. The registration and the odometer are in neither.

- [ ] **Step 2: Run** `cd api && uv sync --locked --reinstall -q && uv run --locked pytest -q`. Expected: the new tests fail.
- [ ] **Step 3: Implement.** `car_model` is matched by the last word, lower case, against `MODEL_SLUGS` from `voltrieve_agent.server_tools`, so the API has no second list. `TurnService` builds the metadata where it calls `create_conversation`. The header is read in `/chat` and `/chat/tool-results`, not added to `allow_headers`, so no browser page can send it.
- [ ] **Step 4: Bicep.** Parameters `carClientId`, `webClientId`, `cliClientId` (default `''`, from `CAR_CLIENT_ID`, `WEB_CLIENT_ID`, `CLI_CLIENT_ID`) become `CAR_CLIENT_ID`, `WEB_CLIENT_ID` and `CLI_CLIENT_ID` in the API's environment. Extend `infra/tests/test_bicep.sh` to check the three.
- [ ] **Step 5: The live page** shows the client on each card. A turn without one shows nothing.
- [ ] **Step 6: Run** the api, agents/hosted, tests/contract and dashboard suites and `sh infra/tests/test_bicep.sh`. Expected: all pass.
- [ ] **Step 7: Commit** `Mark each conversation and turn with the client`

### Task 2: Our own tools say that they are tests

**Files:**
- Modify: `scripts/voltrieve_client.py`, `scripts/denied_demo.py`, `scripts/warm_up.py` if it starts a conversation, and every other script that posts to `/chat` without `Api`
- Test: `tests/photos/test_client.py`, `tests/contract/test_scripts.py`

**Interfaces:**
- Consumes: the header of Task 1.
- Produces: every request from `Api` carries `X-Voltrieve-Test: 1`.

- [ ] **Step 1: Write the failing test:** `test_every_request_says_it_is_a_test` sends a turn, a tool result and an upload through `httpx.MockTransport` and asserts the header on each. A second test greps `scripts/` and `tests/` for `"/chat"` and asserts that each file uses `Api` or sends the header.
- [ ] **Step 2: Run it.** Expected: FAIL.
- [ ] **Step 3: Set the header** as a default of the `httpx.Client` in `Api`, and in the scripts that do not use it. The voltrieve CLI is a real client and is not changed.
- [ ] **Step 4: Run** `tests/photos` and `tests/contract`. Expected: PASS.
- [ ] **Step 5: Commit** `Say in our own test tools that they are tests`

### Task 3: The hosted agent sends its own telemetry, and Bicep stops connecting Application Insights

**Files:**
- Modify: `agents/hosted/main.py`, `agents/hosted/deploy.py`, `infra/modules/foundry.bicep`, `infra/main.bicep`, `infra/tests/test_bicep.sh`
- Test: `agents/hosted/tests/`

**Interfaces:**
- Produces: the hosted agent reads `VOLTRIEVE_TELEMETRY_CONNECTION_STRING` (the platform reserves `APPLICATIONINSIGHTS_CONNECTION_STRING`). `deploy.py` fills it from the azd value `APPLICATIONINSIGHTS_CONNECTION_STRING`.

The agent server takes `applicationinsights_connection_string` in its constructor (`azure-ai-agentserver-core` 2.3.0, `_base.py`), so our code needs no exporter of its own. `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false` is already set, and it also turns off the server's own sensitive data.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_host_gets_our_connection_string(monkeypatch):
    monkeypatch.setenv("VOLTRIEVE_TELEMETRY_CONNECTION_STRING", "InstrumentationKey=x")
    seen = host_arguments(monkeypatch)            # patches InvocationAgentServerHost
    assert seen["applicationinsights_connection_string"] == "InstrumentationKey=x"

def test_without_it_the_host_starts_without_telemetry(monkeypatch):
    monkeypatch.delenv("VOLTRIEVE_TELEMETRY_CONNECTION_STRING", raising=False)
    assert host_arguments(monkeypatch)["applicationinsights_connection_string"] is None

def test_deploy_passes_it_and_keeps_message_content_off():
    variables = environment_variables({**ENV, "APPLICATIONINSIGHTS_CONNECTION_STRING": "c"})
    assert variables["VOLTRIEVE_TELEMETRY_CONNECTION_STRING"] == "c"
    assert variables["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] == "false"
```

In `test_bicep.sh`: `if grep -q "AppInsights" modules/foundry.bicep; then echo "Foundry is connected to Application Insights"; exit 1; fi`

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement**, and remove `appInsightsConnection`, the `appInsights` resource and the `appInsightsName` parameter from `foundry.bicep`. Update the docstrings that say the platform injects the string.
- [ ] **Step 4: Run** agents/hosted, `sh infra/tests/test_bicep.sh` and `uvx pytest -q infra/tests`. Expected: PASS.
- [ ] **Step 5: Commit** `Send the hosted agent's telemetry with our own connection string`

### Task 4 (live): Disconnect the tracing and prove it

**Files:**
- Create: `scripts/tracing_check.py`, `docs/evidence/tracing-disconnected.txt`
- Test: `tests/contract/test_scripts.py`

- [ ] **Step 1: Write `tracing_check.py`.** `--mark` sends one turn per backend with a random marker in the question and in the client's line (`trim`, which is text). `--search MARKER` queries every table of the workspace (`search in (*) "MARKER" | summarize count() by $table`, 4 hours) and prints counts only. A test asserts that the query holds no `project` and no `take`, and that the output is counts.
- [ ] **Step 2: Baseline.** Run it before the change. Expected: the marker is found for prompt and hosted, not for loop. Keep the counts.
- [ ] **Step 3: Deploy** in the order of `docs/deploy.md`: `azd provision --preview` (read it, only the connection, API settings and tags may differ), `azd provision`, `scripts/deploy-agents.sh`, `azd deploy api`, `azd deploy dashboard` with `scripts/deploy-web.sh dashboard`.
- [ ] **Step 4: Delete the deployed connection.** Bicep is incremental and does not. Print it first, check that its name is `appinsights` and its category `AppInsights`, then `az rest --method delete` on that one resource. Nothing else is deleted.
- [ ] **Step 5: Prove it.** Mark, wait 15 minutes, search, wait 30 more, search again. Expected: 0 in every table, on all three backends. Anything else is a stop.
- [ ] **Step 6: The hosted agent.** Check that its logs and spans arrive (`cloud_RoleName` of the hosted agent, newer than the deploy), and that one trace id still joins the API, the hosted agent and the dealer. If not, the loss is written down.
- [ ] **Step 7: The marks.** Read the metadata of the newest conversation per backend: `client` is `test`, `backend` is right. Check `voltrieve.client` on the spans.
- [ ] **Step 8: Run** `uv run scripts/smoke.py` and the routing suite, one question at a time. Expected: first text within 10 s, as before.
- [ ] **Step 9: Commit** the evidence: `Prove that no chat text reaches telemetry`
- [ ] **Step 10: Document it.** Close the open decision with the proof, remove "not met yet" from rule 4 of `docs/principles.md`, from `AGENTS.md` and from every page that `grep -rn "Foundry's tracing" docs README.md AGENTS.md` finds, correct the docstrings in `agents/hosted/main.py`, `api/app/insights.py` and `api/app/context.py`, and reduce the runbook's end to end trace to what is left. The workbook's count of Foundry's rows stays, as a check that should be empty. Run `uvx pytest -q infra/tests`. Commit `Document that Foundry's tracing is disconnected`.

# Step 2: the job, as far as labels

### Task 5 (live): A role that can read conversations and nothing more

**Files:**
- Create: `infra/modules/insights-role.bicep` (the custom role, scoped to the resource group), `docs/evidence/insights-role.txt`
- Modify: `infra/main.bicep`, `infra/tests/test_bicep.sh`

**Interfaces:**
- Produces: custom role `Voltrieve insights reader`, data actions `Microsoft.CognitiveServices/accounts/AIServices/agents/read`, `Microsoft.CognitiveServices/accounts/OpenAI/deployments/chat/completions/action` and `Microsoft.CognitiveServices/accounts/Language/analyze-text/action`. User assigned identity `id-insights-<env>` with it on the Foundry account.

The test runs as a temporary Container Apps job with that identity, so the deployer's own roles cannot help. The job prints HTTP statuses only.

- [ ] **Step 1: Deploy the role and the identity** and wait for propagation.
- [ ] **Step 2: Probe** with the identity: list conversations (200), list items of one (200), a conversation created by the API on each backend is seen, create a conversation (403), add an item (403), delete a conversation (403, on an id that does not exist), one chat completion (200), one `analyze-text` (200 or the reason). If reading is refused, try adding `Microsoft.CognitiveServices/accounts/AIServices/responses/read`. Nothing with write or delete is tried. No working role is a stop.
- [ ] **Step 3: Delete the temporary job** and keep the statuses as evidence.
- [ ] **Step 4: Commit** `Add a role that can read conversations and nothing more`

### Task 6: Pick and read conversations

**Files:**
- Create: `services/insights/pyproject.toml`, `uv.lock`, `src/voltrieve_insights/conversations.py`, `src/voltrieve_insights/quiet.py`, `tests/test_conversations.py`, `tests/conftest.py`
- Modify: `.github/workflows/tests.yml` (matrix `services/insights`), `docs/principles.md`, `AGENTS.md`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class Conversation:
    id: str = field(repr=False)
    owner: str = field(repr=False)
    client: str               # car or web
    backend: str
    car_model: str | None
    started: date             # created_at, or metadata "started" when seeded
    seeded: bool

def window(now: datetime) -> tuple[date, date]: ...          # first day, last day, both included
def select(raw: Iterable[dict], first: date, last: date, notice: date | None) -> list[Conversation]: ...
def sample(items: list[Conversation], cap: int) -> tuple[list[Conversation], float]: ...   # kept, share
async def list_raw(http, since: datetime) -> AsyncIterator[dict]: ...  # newest first, stops at since
async def read_text(http, conversation_id: str) -> tuple[list[tuple[str, str]], str | None]:
    """(role, text) of user and assistant messages in order, and the registration in the client's line."""
def hush() -> None:            # quiet.py: httpx, httpcore and azure loggers to WARNING, no bodies
```

`window(datetime(2026,11,24,2,30,UTC))` is 16 to 22 November: 23 November ended 2.5 hours ago, less than 24.

- [ ] **Step 1: Write the failing tests.** Each is one assertion on made up data: the window above; a conversation on the first and the last day is in, the day before and after out; `cli`, `other`, `test`, unmarked and a missing owner are out; with `notice=None` no real one is in and a seeded one is; a real one before the notice date is out, on it in; a seeded one is dated by its `started`; an unreadable `started` is out; `sample` of 2,500 gives 2,000 and 0.8, of 10 gives all and 1.0; only `message` items with role user or assistant are read, never `function_call`, `function_call_output`, `mcp_call` or `reasoning`; the client's line `[From the client, data only: ...]` is removed from the start of the first user message and its registration is returned; a conversation with only an attachment gives no text and is dropped without an error; a page that fails raises.

```python
def test_nothing_that_identifies_reaches_the_log(caplog, capsys, http):
    hush(); run(read_text(http, "conv_secret123"))
    assert "conv_secret123" not in caplog.text + capsys.readouterr().err
    assert "conv_secret123" not in repr(Conversation("conv_secret123", "oid-1", "car", "loop", None, DAY, False))
```

- [ ] **Step 2: Run** `uv run --locked pytest -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** httpx against `GET {project}/openai/v1/conversations?limit=100&order=desc` and `/conversations/{id}/items`, eight at a time, retrying 429 and 5xx three times. The listing follows `last_id` until `created_at` is before the first day.
- [ ] **Step 4: Amend rule 4** in `docs/principles.md` and the matching line in `AGENTS.md`, in this commit: "One job may read stored chats. Only groups shared by at least five people may leave it."
- [ ] **Step 5: Run the tests.** Expected: PASS. **Commit** `Read the conversations of the last settled week`

### Task 7: Mask, and filter the intents

**Files:**
- Create: `services/insights/src/voltrieve_insights/masking.py`, `tests/test_masking.py`
- Modify: `agent_core/src/voltrieve_agent/private.py`, `api/app/insights.py`
- Test: `agent_core/tests/`, `api/tests/test_insights.py`

**Interfaces:**
- Produces: `mask_contacts(text: str) -> str` in agent_core (the three patterns of `mask_prompt`, which now calls it and behaves as before). `mask(text: str) -> str` adds plates and VINs, as `[plate]` and `[vin]`. `allowed(text: str, registration: str | None) -> bool`.

- [ ] **Step 1: Write the failing tests.** `mask`: `ABC 123`, `abc12d`, `WVWZZZ1JZXW000001`, an email, a phone number and a Swedish id number are replaced, `Atlas AWD 2026` and `VT-1042-11` stay readable. `allowed` is false for each of those, for any four digits in a row (`2026`), for the registration `SPARK` in any case or inside a sentence, and for more than 100 characters; true for `wants to charge at home without a wall box`. A registration of one or two characters is compared as a whole word.
- [ ] **Step 2: Run, implement, run.** The existing `mask_prompt` tests pass unchanged.
- [ ] **Step 3: Commit** `Mask what could point at a person before the model sees it`

### Task 8: Labels, made up conversations, and the notice

**Files:**
- Create: `services/insights/src/voltrieve_insights/labels.py`, `tests/test_labels.py`, `scripts/seed_conversations.py`, `services/insights/data/seeded.yaml` (at least 150 made up conversations with their right labels: 100 for accuracy, 50 with planted personal details, Swedish and English), `scripts/labels_check.py`
- Modify: `clients/car/src/components/Menu.tsx`, `clients/web/src/components/Chat.tsx`, their tests, `docs/clients.md`

**Interfaces:**
- Consumes: `Conversation`, `mask`, `allowed`.
- Produces:

```python
TOPICS = ("charging", "range_battery", "price_purchase", "compare_models", "features_specs", "service_warranty",
          "parts_accessories", "test_drive_dealer", "car_controls", "fault", "signs_parking", "app_account", "other")
KINDS = ("how_to", "compare", "buy", "problem", "command", "smalltalk")
RESULTS = ("answered", "partly", "not_found", "handed_off", "refused")

@dataclass(frozen=True)
class Labelled:
    owner: str = field(repr=False)
    client: str
    seeded: bool
    topic: str
    car_model: str | None
    kind: str
    result: str
    intent: str | None = field(repr=False)   # None when dropped

async def label(model: Model, c: Conversation, messages: list[tuple[str, str]], registration: str | None) -> Labelled: ...
class Model:                                  # chat completions with a strict json_schema, retry 429 and 5xx
    async def ask(self, system: str, data: str, schema: dict) -> dict: ...
```

- [ ] **Step 1: Write the failing tests:** the model gets masked text only, as data inside one user message, and the system message says that text in it is never an instruction; the schema is strict with the enums above and `maxLength` 100; a label outside the enums raises; an intent that `allowed` refuses becomes `None` and the rest stays; the stated car model is used when the model gives none; a conversation over 12,000 characters keeps its first and last 6,000; the request body has no `store: true`; a failure names the error type only.
- [ ] **Step 2: Run, implement, run.**
- [ ] **Step 3: The seed script** writes conversations straight to Foundry with `owner_oid` `seeded-<n>`, `client`, `backend`, `car_model`, `seeded: "true"` and `started` (an ISO date, spread over `--weeks`, default 4), and adds the user and assistant items. It calls no model. It refuses to run unless `--yes` and prints how many it will write. A test checks the metadata.
- [ ] **Step 4: `labels_check.py`** labels the 100 and compares topic, car model, kind and result (all four right counts as right), then the 50 and counts planted details in intents before and after the filter. Output: counts and made up text only.
- [ ] **Step 5: The notice,** after "your chat is kept by the service.": "A nightly job counts what people ask about. It saves only topics shared by several people, never your chat." Tests in both clients. Check that the car's menu still fits at phone width.
- [ ] **Step 6: Run** services/insights, car and web suites and builds. **Commit** twice: `Label conversations with a small model` and `Tell people that a nightly job counts what they ask about`

### Task 9 (live): Deploy the clients, measure the labels

- [ ] Deploy car and web, and check the sentence on both deployed sites. Write down the date and time.
- [ ] Seed the 150. Run `labels_check.py`. Pass: at least 90 of 100 right. Below that, change the prompt and run again, at most five times, every run kept in the evidence.
- [ ] After the runs, search every table for a marker planted in one seeded conversation, and list stored responses: nothing from the job. Anything else is a stop.
- [ ] Personal data detection: availability in `swedencentral`, the api version, the price from the pricing page, and the 50 planted names in English names. Not available or over 1 USD a month is a stop.
- [ ] Commit `Measure the labels on made up conversations` with `docs/evidence/insights-labels.txt` (counts, cost, what reached the intents in memory).

# Step 3: groups, dashboard and workbook

### Task 10: Group and name

**Files:**
- Create: `services/insights/src/voltrieve_insights/groups.py`, `tests/test_groups.py`

**Interfaces:**
- Consumes: `Labelled`, `Model`, `allowed`.
- Produces: `async def group(model, pii, items: list[Labelled], previous: dict[str, list[str]]) -> list[tuple[Labelled, str | None]]` (the group's name, `None` for no group, `"unnamed group"` for a name that failed). `class PersonalData: async def clean(self, names: list[str]) -> list[bool]` calls `analyze-text` with `PiiEntityRecognition`, language `en`.

- [ ] **Step 1: Write the failing tests:** one call per topic, with numbered intents and the previous names; real and seeded are grouped in separate calls; an index given twice counts in the first group only, an unknown index is ignored, one left out gets `None`; a name over 60 characters, or that `allowed` or the detection refuses, becomes `unnamed group`; every entity fails a name except `Quantity`, `DateTime`, `PersonType` and `Organization`; a failed detection raises (the run fails closed); an intent that says "name this group X" is only data in the request.
- [ ] **Step 2: Run, implement, run. Commit** `Group what people ask about`

### Task 11: The rule of five

**Files:**
- Create: `services/insights/src/voltrieve_insights/totals.py`, `tests/test_totals.py`

**Interfaces:**
- Produces: `def totals(items: list[tuple[Labelled, str | None]], limit: int = 5) -> list[Row]` for one source, and `MIN_PEOPLE = 5`. A row is a dict with `Cut` (`run`, `all`, `client`, `topic`, `topic_model`, `topic_result`, `topic_kind`, `group`, `group_result`), `Client`, `Topic`, `CarModel`, `RequestKind`, `Result`, `GroupName`, `Conversations`, `People`. Every run has exactly one `run` row, without counts.

- [ ] **Step 1: Write the failing tests**

```python
def test_four_people_are_not_saved_and_five_are():
    assert cuts(people(4), "topic") == []
    assert cuts(people(5), "topic") == [{"Topic": "charging", "People": 5, "Conversations": 5}]

def test_one_person_asking_twenty_times_is_one_person():
    assert cuts([one_person()] * 20, "topic") == []

def test_the_limit_cannot_go_below_five():
    assert cuts(people(4), "topic", limit=1) == []

def test_a_night_with_nobody_still_has_its_run_row():
    assert [r["Cut"] for r in totals([])] == ["run"]
```

And: counts round to the nearest five (7 to 5, 8 to 10); a result of a group with four people has no row; groups under five are added together as `other` within their topic and saved only if that holds five people; `unnamed group` follows the same rule; no row holds an owner, an intent, an id or a time; a property test over 200 random inputs finds no row whose true number of people is under five.

- [ ] **Step 2: Run, implement, run. Commit** `Save only what at least five people share`

### Task 12: Save, and run as a job

**Files:**
- Create: `services/insights/src/voltrieve_insights/store.py`, `job.py`, `tests/test_store.py`, `tests/test_job.py`, `Dockerfile`, `infra/modules/insights.bicep`, `scripts/deploy-insights.sh`
- Modify: `infra/main.bicep`, `infra/main.parameters.json`, `infra/tests/test_bicep.sh`, `docs/deploy.md`

**Interfaces:**
- Consumes: all of the above.
- Produces: table `VoltrieveInsights_CL`, the columns of Task 11 plus `TimeGenerated`, `Run` (datetime), `WindowStart`, `WindowEnd`, `Source` (`real` or `seeded`), `SampleShare` (real). Outputs `INSIGHTS_JOB_NAME`, `LOG_ANALYTICS_WORKSPACE_ID`. `python -m voltrieve_insights.job [--window-end YYYY-MM-DD]`, where the argument is for seeded data and reads no real chat.

- [ ] **Step 1: Write the failing tests:** one upload per run holds both sources and is under 1 MB; a failure in any step (list, read, label, group, detection, previous names) saves nothing and exits 1, with an error type and no message on stdout; an unset notice date reads no real conversation; `--window-end` gives no `real` rows; the logs of a whole run with planted text hold counts only; four seeded people and one real person asking the same give no row in either source; the previous names are read per source and topic from the latest `Run`; a limit below 5 is raised to 5.
- [ ] **Step 2: Implement.** httpx and azure-identity only: `POST {ingestion}/dataCollectionRules/{id}/streams/Custom-VoltrieveInsights_CL?api-version=2023-01-01` and `POST https://api.loganalytics.azure.com/v1/workspaces/{id}/query`.
- [ ] **Step 3: Bicep.** The table with `retentionInDays: 365` and `totalRetentionInDays: 365` (the workspace stays at 30), a data collection rule of kind `Direct`, the job (0.5 CPU, 1 GiB, timeout 1,800 s, `replicaRetryLimit: 0`), Monitoring Metrics Publisher (`3913510d-42f4-4e42-8a64-420c390055eb`) on the rule, Log Analytics Data Reader (`3b03c2da-16b3-4a49-8834-0f8130efdd3b`) on the workspace with a condition that allows only this table, for the job and for the API, AcrPull, and the role of Task 5. `insightsSchedule` (`INSIGHTS_SCHEDULE`, default `off`): `off` is a manual trigger, else cron `30 2 * * *`. `insightsNoticeDate` (`INSIGHTS_NOTICE_DATE`, default empty). `test_bicep.sh` checks both defaults, the condition and that the job has no other role.
- [ ] **Step 4: `deploy-insights.sh`** builds the image with `az acr build` and updates the job, as `deploy-agents.sh` does for the hosted agent.
- [ ] **Step 5: Run** services/insights and infra tests. **Commit** `Run the insights as a job that fails closed`

### Task 13: `GET /admin/insights`

**Files:**
- Create: `api/app/usage.py`, `api/tests/test_usage.py`
- Modify: `api/app/routes.py`, `api/app/settings.py`, `api/app/main.py`, `infra/modules/api-app.bicep`, `infra/main.bicep`

**Interfaces:**
- Produces: `GET /admin/insights?source=real|seeded` (default `real`) gives `{"source", "run", "window": {"start", "end"} | null, "sample_share", "rows": [...], "weeks": [{"end", "topic", "conversations", "people"}]}`. `rows` are the latest run's, `weeks` one point per `WindowEnd` in the last 12 weeks from the latest run of each.

- [ ] **Step 1: Write the failing tests:** no token gives 401, no `Dashboard.View` gives 403 and nothing is queried, `source=other` gives 422, the query text holds no client input, no run gives `window: null` and empty lists, a failed query gives 503 without its text, the answer is cached for five minutes per source and sent with `Cache-Control: no-store`.
- [ ] **Step 2: Run, implement, run. Commit** `Give the dashboard what people ask about`

### Task 14: The dashboard page and the workbook

**Files:**
- Create: `clients/dashboard/src/insights.ts`, `insights.test.ts`, `components/Insights.tsx`
- Modify: `clients/dashboard/src/App.tsx`, `components/Gate.tsx`, `components/Header.tsx`, `styles.css`, `mock/server.mjs`, `infra/workbooks/build_operations.py`, `operations.json`, `infra/tests/test_workbook.py`

**Interfaces:**
- Consumes: the answer of Task 13.

- [ ] **Step 1: Write the failing tests:** the page is asked for only with the role; two tabs, **Live** and **What people ask**, kept in the address as `#ask`; a switch **Real** and **Made up**, and the page always says which, when the last run was and the share sampled when under 1; the two are never added; empty real data says "Not enough people yet. Only what at least five people share is shown."; top groups with the share answered, topics per car model, topics by client, and the trend per week; a count is shown as "about 10".
- [ ] **Step 2: Workbook.** A group **What people ask** with the same data and a parameter **Source**, and **Missing nights**: the days of the last 30 without a `run` row for the source. The queries run on the workspace (`microsoft.operationalinsights/workspaces`, `__WORKSPACE_ID__` replaced by Bicep). No column named `kind`. Extend `test_workbook.py`.
- [ ] **Step 3: Run** dashboard tests and build, `uvx pytest -q infra/tests`, and look at the page in the browser against the mock, light and dark, desktop and phone. **Commit** `Show what people ask about`

### Task 15 (live): Accept it

- [ ] Deploy in this order: provision, the job's image, the API, the dashboard. Seed four weeks, run the job for each `--window-end`, then once without.
- [ ] Section 9, step 3 of the design, one line each in `docs/evidence/insights-acceptance.txt`: four people and five, twenty phrasings of one need, the steering conversation, 403 without the role, a failed run that saves nothing (a job run with a wrong deployment name) and shows as missing.
- [ ] Four made up people plus one real is shown by a test in Task 12, not live: no real chat is read before the product owner's yes. So the first run on real chats is listed as not verified.
- [ ] Group names on two runs over the same data: the share that kept their name.
- [ ] Planted details in everything saved: export the table and count. Expected: 0.
- [ ] What a run costs and how long it takes, next to section 10 of the design.
- [ ] The job's and the API's identities cannot read another table (a query of `AppTraces` is refused).
- [ ] The routing suite, within 10 s.
- [ ] **Commit** `Accept the usage insights on made up data`

### Task 16: Document it

- [ ] README status, `docs/runbook.md`, `docs/clients.md` with a screenshot, `docs/deploy.md`, `docs/evidence/README.md`, `docs/index.md` if it lists features. A decision for each real choice: the nightly job instead of labeling a turn, no stored intents, Log Analytics instead of a database, overlapping windows and what is left to hint at. Not verified is listed as not verified.
- [ ] Comment on issue #5 with what it takes over. Do not close it.
- [ ] **Commit** `Document the usage insights`

### After the plan

The schedule stays `off` and the notice date empty until the product owner says yes to both. Turning them on is `azd env set INSIGHTS_NOTICE_DATE`, `azd env set INSIGHTS_SCHEDULE on` and a provision.
