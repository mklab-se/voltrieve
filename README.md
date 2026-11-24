# Voltrieve: agentic RAG on Azure, three ways, with the receipts

[![Tests](https://github.com/mklab-se/voltrieve/actions/workflows/tests.yml/badge.svg)](https://github.com/mklab-se/voltrieve/actions/workflows/tests.yml)
[![Documentation site](https://github.com/mklab-se/voltrieve/actions/workflows/pages.yml/badge.svg)](https://mklab.se/voltrieve/)
![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![React 19](https://img.shields.io/badge/React-19-149ECA?logo=react&logoColor=white)
![Microsoft Foundry and Azure AI Search](https://img.shields.io/badge/Azure-Foundry%20%7C%20AI%20Search-0078D4)
![Deploy with azd up](https://img.shields.io/badge/deploy-azd%20up-0078D4)
[![MIT license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[![A dark blue Voltrieve Fjord estate car on a wet road beside a Norwegian fjord](docs/assets/voltrieve-hero.jpg)](https://mklab.se/voltrieve/)

*Meet Voltrieve, a made-up Swedish carmaker with five electric cars, 24 brochures, guides and price lists, and 7 spare parts catalogs. Ask Runa, its AI assistant, which car charges fastest, or which parts a rear brake job needs, and watch three agent architectures find the answer.*

**The same assistant built as an own loop, a Foundry prompt agent and a Foundry hosted agent, deployed side by side and measured with real runs.** See exactly who calls what, where each design wins, and where it fails.

*This repository was called `agentic-rag` until 17 November 2026. Old links redirect here.*

[![Step through a real prompt: a call map of the Web API, Foundry and Azure AI Search with numbered arrows and the real payload of each call](docs/assets/journey-map.png)](https://mklab.se/voltrieve/assets/prompt-journey.html)

Most agent samples stop at "it answers". This one answers the questions you meet in the next meeting:

| The question | The short answer | Read more |
| --- | --- | --- |
| **Why host an agent in Foundry at all?** | For a managed identity, versions and a publishing path. Not for the loop, which is 348 lines of Python. | [Comparison](docs/why-foundry.md) |
| **My client has its own tools. Now what?** | A prompt agent refuses per-request tools with a 400. An own loop or a hosted agent runs them, even one the server has never seen. | [Client tools](docs/why-foundry.md#1-what-happened-with-client-tools) |
| **Can a client send a photo, and where does it go?** | Straight into the model call, as an unstored turn. The conversation keeps the text only. No chat text goes to telemetry since Foundry's tracing was [disconnected](docs/decisions.md#closed-foundrys-tracing-is-disconnected). A new kind of attachment is one handler. | [Photos and attachments](docs/attachments.md) |
| **How does the agent decide to use RAG?** | No classifier exists. The model decides in its first call, from words you control. | [The journey of a prompt](docs/prompt-journey.md) |
| **Can it use several knowledge bases without calling them in vain?** | Yes: all three reached the right bases in every run. Two of them run the searches at the same time. | [Two knowledge bases](docs/why-foundry.md#6-two-knowledge-bases-who-chooses-and-who-runs-them-together) |
| **Which model should I use?** | Newer isn't always better. `gpt-6.1-terra` runs all three backends, with 1 of 48 turns over 10 s to first token on the own loop. `gpt-6-luna` costs about a third of `gpt-5.4-mini`. Search plans its queries with `gpt-5.4-mini`, and `gpt-6-luna` is opt-in (`PLANNING_MODEL_NAME`): the same answers and cheaper planning, but about half a second slower per search. | [Choosing a model](docs/model-comparison.md) |
| **I need curated answers without slowing everything down.** | Pick the answer in the model call you already make, then skip the second one. Curated questions took under 2 s on two of the three, and other questions showed no measurable delay. | [Curated answers](docs/prompt-journey.md#curated-answers) |

## Who it is for: one client for each audience

| Client | Audience | Shows |
| --- | --- | --- |
| [The car](docs/clients.md#the-car) | The driver | The assistant inside the car, with functions you see and hear, and a photo of a road sign |
| [The website](docs/clients.md#the-website) | A signed-in customer | Asking Runa about your own car, with a photo if you have one |
| [The operations dashboard](docs/clients.md#the-operations-dashboard) | Voltrieve employees | What happens in the service: users, times, cost, errors |
| [The CLI](docs/clients.md#the-cli) | Developers | A test tool for the API, on every backend, with the whole trace |

All four clients are built. [The clients](docs/clients.md) says what each one does, and what has not been run live is marked there.

![The car: a dashboard from the driver's seat, a street behind the windscreen, 50 km/h on the driver display, and Runa and the last parking sign on the center screen](docs/images/car-driving.webp)

*The car at 50 km/h. Runa's text comes from a mock server, not from the deployed agents. The car, the street and the sign are made up.*

## See it: the journey of a prompt

**[Open the interactive journey](https://mklab.se/voltrieve/assets/prompt-journey.html)**, or browse [the documentation site](https://mklab.se/voltrieve/). Pick an architecture and a prompt, then step through every call: who makes it, what it carries and what comes back.

- **Three architectures on one map.** The boxes stay in place and you can watch what moves.
- **Seven real prompts,** from a greeting to a two-base spare parts job and a client-side tool.
- **Detailed RAG.** Follow one question from the agent through the knowledge base, knowledge source, index, embedding model and semantic ranker.

Prefer text? [docs/prompt-journey.md](docs/prompt-journey.md) has the same story as Mermaid diagrams.

## Three architectures compared

![Verdict cards for a spare parts question: own loop 7.6 s, prompt agent 12.5 s, hosted agent 16.1 s](docs/assets/journey-verdict.png)

| What you care about | Own loop | Prompt agent | Hosted agent |
| --- | --- | --- | --- |
| Everyday question, total time | **1.5 s** | **1.8 s** | 6.5 s, about 5 s slower on every turn |
| Question for two knowledge bases | **7.8 s**, searches run together | 10.9 s, one at a time | 12.8 s, searches run together |
| A client tool the server has never seen | **Works** | Refused | **Works** |
| Curated answer, word for word | **1.5 s** | **1.7 s** | 6.3 s |
| Control over each search | **Full** | Little, it runs inside Foundry's agent run | **Full** |
| Code you own for searching | A client per base | **None, declare an MCP tool** | A client per base |
| Agent identity and versions in Foundry | No | **Yes** | **Yes** |
| Change instructions without shipping code | No | **Yes** | No |

**Which one should you pick?** The own loop when clients bring tools or speed matters. The prompt agent when the tool list is fixed and you want the least code. The hosted agent when you also need an agent identity or a publishing path. Medians come from live runs, and the [evidence](docs/evidence/) is in the repository.

The diagram below is the whole system. The only thing that changes between backends is who makes the calls.

```mermaid
flowchart LR
    CLI["CLI, for developers<br/>declares client tools"] --> API
    Car["Car, for the driver<br/>set_speed, set_wipers, honk, park"] --> API
    Web["Website, for customers<br/>set_theme, photos"] --> API
    Dash["Dashboard, for employees"] --> API
    Entra["Entra ID"] -. tokens .-> CLI
    Entra -. tokens .-> Car
    Entra -. tokens .-> Web
    Entra -. tokens .-> Dash
    API["Web API<br/>contract, auth, turn state"]
    API -->|"A: agent_reference"| Prompt["Prompt agents<br/>tools in the definition"]
    API -->|"B: own loop, per-request tools"| Model["Model deployment<br/>and Conversations"]
    API -->|"C: Invocations"| Hosted["Hosted agent<br/>same agent core"]
    Prompt --> Model
    Hosted --> Model
    Prompt -->|"MCP, one per base"| KB["AI Search knowledge bases<br/>documents and spare parts"]
    API -->|"B: retrieve, in parallel"| KB
    Hosted -->|"retrieve, in parallel"| KB
    API -->|"A, B: dealer tools"| Dealer["Dealer service"]
    Hosted -->|"C: dealer tools"| Dealer
    API --> AI["Application Insights"]
    Dealer --> AI
    Hosted --> AI
```

Choose per request with `X-Agent-Backend: prompt|loop|hosted`. Clients can't otherwise tell which one answered, so you can compare them on real traffic.

## What you can reuse

- **A shared agent loop** ([`agent_core/`](agent_core/)): parallel tool calls, roles on tools, schema checks, pause and resume for client tools.
- **Client tools end to end**: a turn that pauses, runs on the user's machine and picks up again, with repair when a client disappears.
- **Photos and attachments** ([`api/app/attachments/`](api/app/attachments/)): a handler for each kind, unstored turns that keep the photo out of the conversation and our telemetry (the text is another matter, see [the privacy limits](docs/attachments.md#what-storefalse-does-not-promise)), and an upload that answers `rejected` instead of failing.
- **Curated answers** ([`curated.py`](agent_core/src/voltrieve_agent/curated.py)): approved text, word for word, with no search on every turn.
- **Status events**: tell users what's happening while they wait, on the stream they already have.
- **Agentic retrieval as code** ([`search/definitions/`](search/definitions/)): knowledge bases, sources, indexes and skillsets, on the latest API version.
- **Keyless Azure**: Entra and managed identities everywhere, deployed with one `azd up`.
- **Routing tests** ([`tests/routing/`](tests/routing/)): prove that the model picks the right source, and catch calls in vain.

## Quick start

The story is Voltrieve, a fictional electric car maker, so all data is generated and safe to share. You need `az`, `azd`, `uv`, Node.js and, on Apple Silicon without Rosetta, Docker. See [docs/deploy.md](docs/deploy.md) for rights, regions and hooks.

```sh
az login && azd auth login
azd env new voltrieve-dev
azd env set AZURE_LOCATION swedencentral
azd up                     # watch for the tester password, printed once
scripts/deploy-web.sh      # only where the SWA CLI cannot run, also `dashboard` and `car`
scripts/deploy-agents.sh
```

Then ask something, on any backend:

```sh
# The CLI reads API_URL, TENANT_ID, CLI_CLIENT_ID and API_CLIENT_ID from the azd environment.
uv run --project clients/cli voltrieve ask "Which Voltrieve charges fastest?" --backend loop --az-token
uv run --project clients/cli voltrieve chat --backend hosted --az-token
```

With [`just`](https://github.com/casey/just) installed, `just chat` does the same on the own loop, and `just chat hosted` picks a backend. `just open` opens the deployed web client, and `just web` runs it locally. `just open-dashboard` and `just dashboard` do the same for the operations dashboard. Before a demo, `just warm-up` wakes everything that scales to zero, and `just keep-warm` keeps it awake. Add a gauge in the CLI with `just chat loop --gauge` or `/gauge` in the chat.

The chat shows message bubbles with rendered Markdown and one live status line. Add `--trace` to watch every step, or type `/trace` to see the last turn.

Check it with `uv run scripts/runbook.py` and `uv run tests/grounding/check.py`. Measure routing with `uv run tests/routing/check.py --repeats 1 --no-save`. The walkthrough is in [docs/runbook.md](docs/runbook.md).

Tear down with `azd down --purge`. App registrations and the tester are not removed by azd: [docs/deploy.md](docs/deploy.md#teardown) shows how to delete them.

## Repository layout

| Path | Contents |
| --- | --- |
| `agent_core/` | The shared loop, model client, tools, dealer and knowledge base clients |
| `api/` | The Web API and its three backends |
| `agents/prompt/` | Prompt agent definitions and the rejection demo (backend A) |
| `agents/hosted/` | Hosted agent container and deployment (backend C) |
| `api/app/attachments/` | One handler for each kind of attachment, and the store |
| `clients/car/` | The car, for the driver |
| `clients/web/` | The website, for customers |
| `clients/dashboard/` | The operations dashboard, for employees |
| `clients/cli/` | The CLI, a developer tool |
| `services/dealer/` | The dealer web service |
| `search/` | Definitions and tooling for the Search objects |
| `data/` | The source data file, document generator and generated documents |
| `infra/` | Bicep modules and the Entra setup script |
| `hooks/` | azd hooks |
| `scripts/` | Agent and web deployment, runbook, denied demo, line count |
| `tests/` | Contract tests, the grounding check and the routing suite |
| `docs/` | Principles, comparison, journey of a prompt, runbook, deployment, decisions, evidence, design, screenshots |

## Costs

Idle, about 80 USD a month plus a small amount for Log Analytics: Search Basic is about 75 USD and Container Registry Basic about 5 USD. Container Apps scale to zero, the three Static Web Apps (website, car and dashboard) are on the Free tier, models bill per token, and the hosted agent bills per active session. A Serverless search service can stand next to Basic. It is off by default. In the dev environment it is on, at about 0.11 USD by the diagnostic metric for a night of test runs, not by an invoice ([Deployment](docs/deploy.md#idle-cost)).

## Status

Deployed and run live on 2026-11-04 and 2026-11-05. Photos, the car, the new website and the newer Search versions were deployed and measured on 2026-11-24.

Run live on 2026-11-24:

- The car, the website and the dashboard in a browser against the deployed API, with a token from the az CLI in place of the sign-in page (`docs/evidence/clients-live.txt`).
- A photo on all three backends: 252 parking questions with no wrong verdict, and 36 of 36 part numbers right for a broken lamp ([Photos and attachments](docs/attachments.md#measured)).
- The car's five tools on all three backends, 18 of 18 (`docs/evidence/car-tools.txt`).
- The three sites are deployed and show their sign-in page (looked at by hand, no evidence file).
- Search on the newest data plane version, on Basic and on Serverless, with two planners: 135 of 135 routing turns right on Serverless (`docs/evidence/search-versions.txt`).

Verified live:

- All three backends through the deployed API: every runbook step passes on the final build, including the automated trace check in step 12.
- Grounding: 30 of 30 answers passed.
- The cross-document question "cheapest AWD in Sweden" repeated 6 times per backend: 18 of 18 right. At the default reasoning effort it was 11 to 15 of 18, so the sample uses medium effort.
- A tool the server has never seen, declared only in the request: relayed on loop and hosted, refused on prompt (`docs/evidence/new-client-tool.txt`). The demo declares a car function, `set_seat_heating`.
- Recovery on all three backends: a paused turn nobody answers, and a conversation resumed after the API scaled to zero (`docs/evidence/repair.txt`).
- One trace per turn across API, Foundry, hosted agent and dealer (`docs/evidence/application-insights.txt`), before Foundry's tracing was disconnected. Afterwards one trace joins the API and the hosted agent.
- The prompt agent's 400.
- Where a photo turn leaves its text and its photo, on 2026-11-24: the photo nowhere we looked, the text of prompt and hosted turns in Foundry's tracing (`docs/evidence/photos-privacy.txt`). [Foundry's tracing was then disconnected](docs/decisions.md#closed-foundrys-tracing-is-disconnected), and the marked question and client's line of a turn on each backend left no rows in telemetry (`docs/evidence/tracing-disconnected.txt`).
- The authorization denied path, on the loop backend, against real Foundry with a fake token validator.

Offline tests pass: `agent_core` 167, `api` 459, `agents/hosted` 40, `search` 62, `services/dealer` 42, `clients/cli` 62, `clients/web` 172, `clients/dashboard` 25, `clients/car` 426, `tests/photos` 228, `tests/contract` 74 and `infra/scripts/entra` 41, plus the 123 of `data/generator` in CI (they need a browser), the Bicep and script checks. Every suite runs in [CI](.github/workflows/tests.yml).

Not verified:

- Sign-in to the car, the website and the dashboard.
- The car in Safari or Firefox, on a slow or Windows computer, and with a screen reader. Whether a person hears the horn and the motor.
- A rollback of the Serverless search service or of the Search versions, and what Serverless costs on an invoice.
- The workbook's check of Foundry's tracing rows.
- A trace with the dealer since Foundry's tracing was disconnected, the Agent 365 exporter that the hosted agent's platform turns on, and what Foundry keeps for itself.
- Reading a small sign in a large photo: [a known limit](docs/attachments.md#the-known-limit).
- A real browser sign-in with `voltrieve login` (its MSAL calls are tested against a fake).
- A real sign-in as the tester.
- Publishing to Teams and Microsoft 365.
- Latency and cold-start figures.

## More

- [Principles: what Voltrieve is for](docs/principles.md)
- [The journey of a prompt](docs/prompt-journey.md)
- [The clients](docs/clients.md)
- [Photos and attachments](docs/attachments.md)
- [Why Foundry: the comparison](docs/why-foundry.md)
- [Demo runbook](docs/runbook.md)
- [Deployment](docs/deploy.md)
- [Decisions and live findings](docs/decisions.md)
- [Evidence](docs/evidence/)
- [Design](docs/superpowers/specs/2026-11-03-voltrieve-agentic-rag-design.md)

## License

[MIT](LICENSE). The cars, documents and images are made up for this demo.
