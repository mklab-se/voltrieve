# Deployment

How to deploy Voltrieve to Azure, in the order that worked on 2026-11-04. Nothing here uses API keys: every call uses Entra tokens.

## Prerequisites

Tools:

- Azure CLI (`az`) and Azure Developer CLI (`azd`)
- `uv` (Python 3.12 is resolved per project)
- Node.js and npm, for the website build
- Docker, where the Static Web Apps CLI cannot run (see below)

Model quota in `AZURE_LOCATION`, GlobalStandard, in thousands of tokens a minute: 150 each for `gpt-6.1-terra`, `gpt-5.4-mini` and `gpt-6-luna`, and 10 for `text-embedding-3-large`. `gpt-6-luna` is always deployed, also when it does not plan anything, so a subscription without that quota cannot provision. It costs nothing until it is used.

Rights, for one user account. A service principal does not work, because the deployer role assignments use `principalType: 'User'`:

- enough on the subscription to create resources and role assignments;
- in Entra: create app registrations, service principals, a user, and app role assignments.

Sign in to both tools as the same user. The post-provision hook checks this and stops if they differ:

```sh
az login
azd auth login
```

## Regions and why

| Resource | Region | Why |
| --- | --- | --- |
| Foundry, storage, Container Apps, registry, monitoring | Sweden Central (`AZURE_LOCATION`) | The primary region |
| Azure AI Search | France Central (`AZURE_SEARCH_LOCATION`, the default) | Sweden Central and West Europe were flagged as not accepting new Search services. France Central is the nearest EU region that does. |
| Three Static Web Apps: web, car and dashboard | East US 2 (`webLocation` in Bicep) | West Europe rejected new Static Web Apps. This region holds the metadata; the content is global. |

Search reaches Foundry and storage over public endpoints with Entra tokens.

All three backends run `gpt-6.1-terra` version `2026-10-26`, set with `chatModelName`. `gpt-5.4-mini` plans the Search queries. `gpt-6-luna` is opt-in, set with `PLANNING_MODEL_NAME` (`planningModelName` in Bicep). Do not move a model without checking availability: all three are deployed as GlobalStandard with capacity 150. Capacity 10 caused rate limit failures in multi-question runs. On pay-per-token, capacity is a cap, not a cost.

## Environment

```sh
azd env new voltrieve-dev
azd env set AZURE_LOCATION swedencentral
```

The name is limited to 22 characters and appears in resource names. Optional values:

| Name | Default | Purpose |
| --- | --- | --- |
| `AZURE_SEARCH_LOCATION` | `francecentral` | Search region |
| `PLANNING_MODEL_NAME` | `gpt-5.4-mini` | The model that plans Search queries. The only other value is `gpt-6-luna`, which is opt-in and follows [its own order](#order-for-an-update-with-the-new-search-values) |
| `SEARCH_SERVERLESS` | `off` | A Serverless search service next to the Basic one. `off`: not deployed. `standby`: deployed, nothing uses it. `active`: the API and agents use it. Basic stays in all three, and going back to `off` does not delete a deployed service |
| `VOLTRIEVE_VERIFIED_DOMAIN` | the tenant's default domain | Domain for the tester account |

The Basic service is in `AZURE_SEARCH_LOCATION`. A Serverless service is in `serverlessSearchLocation`, which defaults to Sweden Central.

The hooks and scripts fill in the rest, such as `TENANT_ID`, `API_CLIENT_ID`, `WEB_URL`, `CAR_URL`, `DASHBOARD_URL`, `CAR_CLIENT_ID`, `DASHBOARD_CLIENT_ID`, `TEST_USER_UPN` and `HOSTED_AGENT_PRINCIPAL_ID`. All of them are identifiers. The Application Insights connection string is an ingestion identifier, not a secret.

## Order

### 1. `azd up`

```sh
azd up
```

Stay at the terminal: the preprovision hook prints the tester's password once, the first time the account is created, and it is not stored anywhere. Do not paste it into docs or tickets.

`azure.yaml` replaces the default order with provision, `package --all`, `deploy --all`. The default packages first, which is too early: the web build needs values that provision creates.

What each hook does:

| Hook | When | What |
| --- | --- | --- |
| `hooks/preprovision.sh` | Before each provision | Creates or updates the app registrations (`voltrieve-api`, `voltrieve-cli`, `voltrieve-web`, `voltrieve-car`, `voltrieve-dashboard`, `voltrieve-dealer`), the tester, roles and consent, and stores the ids in the azd environment. Idempotent. Runs interactively so the password is visible. |
| `hooks/postprovision.sh` | After each provision | Checks that `az` and `azd` are the same user. Uploads the documents to their two containers, applies the search objects of both knowledge bases, runs both indexers, gives the API identity `Dealer.Access`, and adds the redirect URI of each of the three sites (`set-spa-redirect --app web|car|dashboard`). Each step retries up to 10 times, 60 seconds apart, for role propagation. |
| `hooks/web-prepackage.sh` | Before the web package | Writes `clients/web/.env.production` with public values only. |
| `hooks/car-prepackage.sh` | Before the car package | Writes `clients/car/.env.production` with public values only. |
| `hooks/dashboard-prepackage.sh` | Before the dashboard package | Writes `clients/dashboard/.env.production` with public values only. |
| `hooks/postup.sh` | After `azd up` | Runs the search smoke test. It runs last so that a retrieval miss cannot block a deployment. |

Provision keeps deployed images: azd sets `SERVICE_API_RESOURCE_EXISTS` and `SERVICE_DEALER_RESOURCE_EXISTS`, so Bicep reuses the current image instead of the placeholder.

### 2. Apple Silicon without Rosetta: `scripts/deploy-web.sh`

`azd` deploys each Static Web App with the Static Web Apps CLI, whose upload client is an x86 binary. On Apple Silicon without Rosetta that step fails, and on macOS 27 without Rosetta so do `azd deploy web`, `azd deploy car` and `azd deploy dashboard`. `dealer` and `api` deploy first, so only the Static Web Apps are missing. Installing Rosetta is a system change, so this repository uses a container instead. This worked on macOS 27 on 2026-11-24:

```sh
azd package web && scripts/deploy-web.sh web
azd package car && scripts/deploy-web.sh car
azd package dashboard && scripts/deploy-web.sh dashboard
```

`azd package` runs the hook of the site and builds it. Without an argument, `scripts/deploy-web.sh` uploads `web`.

It runs the Linux `StaticSitesClient` in Docker against `clients/<service>/dist`, and keeps the deployment token in memory. It finds the site by its `azd-service-name` tag, never by its position in a list, and stops if none or more than one matches. If `azd up` stopped at this step, run the smoke test by hand, since the `postup` hook may not have run:

```sh
SEARCH_ENDPOINT="$(azd env get-value SEARCH_ENDPOINT)" PLANNING_MODEL="$(azd env get-value PLANNING_MODEL)" \
  uv run --project search voltrieve-search smoke
```

Without `PLANNING_MODEL` the check does not look at which model planned.

### 3. `scripts/deploy-agents.sh`

Run this after `azd up`, from the repository root, as the deployer:

```sh
scripts/deploy-agents.sh
```

1. `agents/prompt/create_agents.py` creates a new version of each of three prompt agents: `voltrieve-prompt-cli`, `voltrieve-prompt-web` and `voltrieve-prompt-car`. The third has the car's tools (`set_speed`, `set_wipers`, `honk`, `park`, `get_sign_photo`). The API picks the first agent whose tools include every tool the client declared.
2. `agents/hosted/deploy.py` builds the `linux/amd64` image with `az acr build`, creates a hosted agent version, waits until it is active, and stores `HOSTED_AGENT_PRINCIPAL_ID`.
3. `azd provision --no-prompt` runs again, because the agent identity did not exist before step 2. Bicep then gives it its Foundry and Search roles. All provision hooks run again too.
4. `voltrieve-entra assign-app-role` gives the agent identity `Dealer.Access`, with the same retry.

Until step 4 has propagated, hosted dealer tools fail. Sessions are bound to the agent version they started on, so conversations in flight stay on the old version until the API restarts or a call fails and the session is dropped.

### 4. Check it

```sh
uv run scripts/runbook.py
uv run tests/grounding/check.py
```

See [runbook.md](runbook.md).

## Order for an update with attachments

When attachments and the new Search values ship in the same update, this order applies. The planner stays on `gpt-5.4-mini` and `SEARCH_SERVERLESS` on `off` for all of it. Steps 2 and 3 of [the new search values](#order-for-an-update-with-the-new-search-values) come afterwards, in their own provisions.

When an environment already runs an older version, deploy the hosted agent before the API. With a new API and an old agent, a photo turn answers without the photo and the protection of the logs is missing.

1. Preview, then `azd provision`: the three registrations and sites, the CORS origins, and `PROMPT_AGENT_CAR`. Not `azd up`, which would deploy the API before the agents.
2. `scripts/deploy-agents.sh` at once after the provision, with nothing in between, since a provision moves the MCP connections and the prompt agents keep the old address until it has run. It deploys the three prompt agents, then the hosted agent, then `azd provision` again (the agent identity did not exist before, so Bicep gives it its roles then) and the `Dealer.Access` role. Its final check is `scripts/smoke.py --basic`, because the API is still older.
3. `azd deploy api`. The new revision drops the sessions that are bound to the old hosted version.
4. `uv run scripts/smoke.py`, the full check. It adds one turn with the car's tools on the prompt agent and one upload on each backend.
5. `web` and `dashboard` together, then `car`. On macOS 27 without Rosetta, package and upload each one [with the container](#2-apple-silicon-without-rosetta-scriptsdeploy-websh).

**Rollback** goes in reverse, the API first, then the agents, so a new API never meets an older agent.

**An empty environment** starts with `azd up`, then `scripts/deploy-agents.sh`, then an API restart. Photos count as unavailable until then.

## Redeploying

| Change | Command |
| --- | --- |
| API or dealer code, `agent_core` | `azd deploy api`, `azd deploy dealer` |
| Website, car, dashboard | `azd deploy web`, `azd deploy car`, `azd deploy dashboard`, or `azd package <service> && scripts/deploy-web.sh <service>` where the SWA client cannot run (macOS 27 without Rosetta) |
| Agents, including `agent_core` as used by the hosted agent | `scripts/deploy-agents.sh` |
| Infrastructure | `azd provision` |
| Documents | `azd provision` (the post-provision hook uploads and indexes) |

## Idle cost

Approximate:

| Resource | Idle cost |
| --- | --- |
| Azure AI Search Basic | About 75 USD a month |
| Azure AI Search Serverless, `off` (the default) | 0 |
| Azure AI Search Serverless, `standby` or `active` | Storage under 0.01 USD a month, and 0.255 USD per CU hour while it works, on top of Basic. It sleeps after 10 idle minutes |
| Container Registry Basic | About 5 USD a month |
| Log Analytics | Small, by ingestion |
| Container Apps | None when scaled to zero |
| Three Static Web Apps, Free tier | None |
| Models | None: per token |
| Hosted agent | None: per active session |

Search is the cost that matters. Tear down when you are done.

The car and the dashboard add no idle cost: two more Static Web Apps on the Free tier, one more prompt agent, and two more registrations. What they add is use:

- A photo adds about 950 input tokens to each turn while it lives.
- The pictures for the car cost 0.32 USD once: the cockpit in its variants and five house fronts ([how they were made](https://github.com/mklab-se/voltrieve/blob/main/clients/car/assets/README.md)).
- The first 20 photo turns cost about 0.11 USD, and the whole photo run of 351 turns about 1.10 USD, at list prices ([Photos and attachments](attachments.md#measured)).

## Before a demo

The API and the dealer scale to zero after 5 idle minutes. A cold start took 34 seconds for the API and 30 for the dealer in a measured run, which is far over the 10 second limit for the first token. `just warm-up` wakes everything and asks a real question on each backend. `just keep-warm` does the same, then pings `/healthz` every 2 minutes until Ctrl-C, which costs no tokens.

## Budget alert

The resource group has a budget named `voltrieve-monthly` of 990 SEK a month, which is about USD 100, because the subscription bills in SEK. It sends an email when actual cost goes over the budget. It was created by hand, so it is not part of `azd up`. An alert does not stop any spending, and cost data can lag by up to a day.

## Teardown

```sh
azd down --purge
```

`--purge` also removes the soft-deleted resources that would otherwise keep their names reserved.

**Entra objects survive teardown.** `azd` does not remove the app registrations or the tester, because hooks created them, not Bicep. To delete them:

```sh
UPN="$(azd env get-value TEST_USER_UPN)"
az ad user delete --id "$UPN"

for name in voltrieve-api voltrieve-cli voltrieve-web voltrieve-car voltrieve-dashboard voltrieve-dealer; do
  az ad app delete --id "$(az ad app list --display-name "$name" --query '[0].id' -o tsv)"
done
```

Deleted applications stay in the Entra recycle bin for a while. These commands were not run in this project, so check the names first if the tenant has anything else called `voltrieve-*`. Whether the hosted agent's Entra identity goes away with the project was also not checked.

If you deploy again into the same azd environment after teardown, clear the stale agent identity first, or provision will try to assign roles to a principal that no longer exists:

```sh
azd env set HOSTED_AGENT_PRINCIPAL_ID ""
```

The tester password is printed again only if the account was deleted and is recreated.

## Order for an update with the new search values

The default stays as it was: `PLANNING_MODEL_NAME` is `gpt-5.4-mini` and `SEARCH_SERVERLESS` is `off`, so a plain `azd provision` changes neither. A GPT-6 planning model needs every caller of Search to use `2026-11-01-preview` or later, so the code goes first. The reasons are in [Decisions](decisions.md#search-moved-to-the-foundry-iq-versions).

Each step is an `azd provision`, with `scripts/deploy-agents.sh` at once after it. A provision moves the MCP connections, and the prompt agents keep the old address until `deploy-agents.sh` has run.

1. **The versions, with the old planner.** Leave both values alone. `azd provision`, then `scripts/deploy-agents.sh`, then `azd deploy api`. The provision writes the search definitions with the new version. Check with `uv run scripts/smoke.py`, the prompt agent first.
2. **The planner, if wanted.** `azd env set PLANNING_MODEL_NAME gpt-6-luna`, then `azd provision`, then `scripts/deploy-agents.sh`. The next retrieve plans with it, with no new API image. Never do this in the same provision as step 1: an old client would meet a base that plans with GPT-6.
3. **Serverless, if wanted, and last.** `active` is refused before `standby` has been provisioned. Set `standby`, provision and fill the service:

```sh
azd env set SEARCH_SERVERLESS standby
azd provision
scripts/deploy-agents.sh

set -a; eval "$(azd env get-values)"; set +a
export SEARCH_ENDPOINT="$SEARCH_SERVERLESS_ENDPOINT"   # this shell only, the API still uses Basic
uv run --project search voltrieve-search apply         # the definitions
uv run --project search voltrieve-search index         # the documents, already in storage
uv run --project search voltrieve-search smoke
unset SEARCH_ENDPOINT
```

Then switch. Use a new shell, so that the changed `SEARCH_ENDPOINT` is gone:

```sh
azd env set SEARCH_SERVERLESS active
azd provision
scripts/deploy-agents.sh
```

**Rollback** goes the other way: Serverless, planner, version. The commands are in [the design, section 8](https://github.com/mklab-se/voltrieve/blob/main/docs/superpowers/specs/2026-11-24-search-foundry-iq-design.md#8-rollback).

When attachments ship in the same update, use [that order](#order-for-an-update-with-attachments) for step 1, since it deploys the agents before the API too.

Measured on 2026-11-24, with the commands above ([`search-versions.txt`](https://github.com/mklab-se/voltrieve/blob/main/docs/evidence/search-versions.txt)): `gpt-6-luna` plans about half a second slower per search. Serverless is about 0.3 s faster per search than Basic when warm, and took 2.9 s for the first call after 14 idle minutes. The rollback commands were not run.
