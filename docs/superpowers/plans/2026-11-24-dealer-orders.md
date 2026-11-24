# Dealer Orders and Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The dealer service gets stored stock, purchase orders and a page for representatives, and Runa can order a car on the website and in the CLI, never in the car.

**Architecture:** The dealer service keeps its machine API, gains a staff API, live events and a page in the same container, and stores everything in one Table Storage partition. Runa gets two server tools in `agent_core`, guarded by the client from the token and a role, so all three backends behave the same.

**Tech Stack:** Python 3.12, FastAPI, `azure-data-tables` (async), React 19, Vite 8, MSAL, Bicep, Microsoft Graph setup in Python.

**Spec:** `docs/superpowers/specs/2026-11-24-dealer-orders-design.md`

## Global Constraints

- No em-dash characters anywhere. Plain, short sentences in English. Runa is an AI, never "she" or "he".
- Commit messages: one plain imperative sentence, a blank line, `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No real client is named or hinted at, in code, docs, commits or evidence. All data is made up.
- No keys or secrets. Entra ID and managed identities, least privilege, roles by GUID in Bicep. Checks fail closed.
- An address, a customer's name, a prompt or an answer never reaches a log line, span, metric, debug payload or error text. Log entries of the service name model, city and order number, and staff by name, never an address.
- A new library version must be at least 168 hours old. `azure-data-tables` is 12.7.0.
- Order numbers: `VO-1001` and up, never reused, also after a reset. Address: 5 to 200 characters in one line. Offer: 30 minutes. Orders: 30 days. Log: latest 200 entries, in memory. Dealer: at most one replica, may scale to zero.
- Ordering clients: `web` and `cli`. Roles: `Order.Place` (API, users), `Dealer.Manage` (dealer, users), `Dealer.Access` (dealer, applications, exists). Scope on the dealer: `manage_as_user`.
- The car's refusal, word for word: "It is not possible to order a car from within the car. You are welcome to talk with Runa about buying a car on the Voltrieve website."
- The existing routes, tools and their answers do not change, except that `/stock` reads the store.
- Nothing is pushed, and no bulk model run starts, without the product owner's yes. Hard stop 2000 SEK for November 2026.

## Review Focus

1. Two representatives, or a double click, deliver the same order or take the last car at once: one wins, the other gets 409, stock never below 0 (Task 2).
2. Text in an address such as `<script>`, control characters, newlines or 5,000 characters: stored as one clean line or refused, and shown as text (Tasks 3 and 5).
3. The store is unreachable: 503 with a fixed text on every route that needs it, the page says so and keeps what it shows, `/healthz` answers 200 (Tasks 3 and 5).
4. The connection drops while an order arrives, or several arrive at once: each order is signed exactly once, in order (Task 6).
5. A car's token with `X-Voltrieve-Client: web`, `X-Voltrieve-Test: 1`, no declared tools, or a resumed conversation that began on the website: still refused (Task 8).

## Files

| File | Responsibility |
| --- | --- |
| `services/dealer/app/model.py` | Order, Offer, errors, the rules that need no store |
| `services/dealer/app/store.py`, `table_store.py` | The store protocol with the memory store, and Table Storage |
| `services/dealer/app/hub.py` | The log and the live subscribers |
| `services/dealer/app/auth.py` | Machine and staff token validation |
| `services/dealer/app/routes.py`, `staff.py`, `site.py` | Machine routes, staff routes, the page's files and headers |
| `services/dealer/site/` | The page |
| `agent_core/.../tools.py`, `server_tools.py`, `dealer.py`, `instructions.py`, `private.py` | The tools, the car rule, masking |
| `api/app/clients.py`, `auth.py`, `routes.py`, `turns.py`, `backends/hosted.py`, `agents/hosted/main.py` | Name and client reach the tools |
| `infra/modules/storage.bicep`, `role-storage-table.bicep`, `dealer-app.bicep`, `main.bicep` | Table, role, settings, one replica |
| `infra/scripts/entra/.../apps.py`, `cli.py`, `hooks/*.sh` | Scope, roles, the page's registration |

---

### Task 1: Roles, registrations and infrastructure

**Files:** Modify `infra/scripts/entra/src/voltrieve_entra/{apps,cli}.py`, `hooks/{preprovision,postprovision}.sh`, `infra/main.bicep`, `infra/main.parameters.json`, `infra/modules/{storage,dealer-app}.bicep`, `infra/tests/test_bicep.sh`, `infra/tests/test_preprovision.py`. Create `infra/modules/role-storage-table.bicep`. Test: `infra/scripts/entra/tests/test_apps.py`, `infra/tests/test_bicep.sh`.

**Interfaces:**
- Produces azd values `DEALER_SITE_CLIENT_ID`, `DEALER_MANAGE_ROLE_ID`, `DEALER_SCOPE_ID`, `ORDER_PLACE_ROLE_ID`. Dealer container settings `TABLE_ENDPOINT`, `TABLE_NAME` (`dealer`), `SITE_CLIENT_ID`. `SPA_APPS["dealer"] = "voltrieve-dealer-site"`.

- [ ] **Step 1: Write the failing Entra tests.** `test_dealer_body_has_scope_and_manage_role`: roles are `Dealer.Access` (`["Application"]`) and `Dealer.Manage` (`["User"]`), scope `manage_as_user` enabled, `requestedAccessTokenVersion` 2. `test_azure_cli_is_pre_authorized_for_the_dealer_scope` and that the pre-authorization survives a second setup. `test_setup_creates_the_dealer_site_once`: localhost `http://localhost:5176`, required access is the dealer scope only, not the API. `test_only_the_deployer_gets_order_place_and_dealer_manage`: the test user has `Chat.User` only. `test_set_spa_redirect` covers `dealer`.
- [ ] **Step 2: Run them.** `cd infra/scripts/entra && uv run pytest -q`. Expected: the new ones fail.
- [ ] **Step 3: Implement** in `apps.py`: `dealer_application_body(access_role_id, manage_role_id, scope_id)`, `dealer_site_application_body(dealer_client_id, scope_id, redirect_uris)`, `pre_authorized_body` made usable for both registrations, `Order.Place` ("Can order a car.") in `api_application_body`, stable ids `role:Order.Place`, `role:Dealer.Manage`, `scope:manage_as_user`. Grant `manage_as_user` to the site's principal on the dealer's, and nothing on the API. `--dealer-redirect-uri` in `cli.py`.
- [ ] **Step 4: Hooks.** `preprovision.sh` passes `${DEALER_URL:+--dealer-redirect-uri "$DEALER_URL"}`. `postprovision.sh` runs `set-spa-redirect --app dealer --uri "$DEALER_URL"`.
- [ ] **Step 5: Bicep.** Table `dealer` in `storage.bicep`. `role-storage-table.bicep` assigns one role on one table, as `role-storage-container.bicep` does for a container. The dealer's system-assigned identity gets Storage Table Data Contributor (`0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3`) on that table. `maxReplicas: 1`. Env `TABLE_ENDPOINT`, `TABLE_NAME`, `SITE_CLIENT_ID`.
- [ ] **Step 6: Assert it** in `test_bicep.sh`: the dealer has `maxReplicas: 1` and `minReplicas: 0`, the role is scoped to a table, no other storage role names the dealer. Run `sh infra/tests/test_bicep.sh && uvx pytest -q infra/tests`. Expected: `bicep ok`, all pass.
- [ ] **Step 7: Commit** "Add the dealer roles, its registration for the page and its table".

### Task 2: The store

**Files:** Create `services/dealer/app/{model,store,table_store}.py`, `services/dealer/tests/{test_store,test_table_store}.py`. Modify `services/dealer/pyproject.toml`, `uv.lock`, `app/settings.py`.

**Interfaces:**
- Produces, in `model.py`: `Offer(id, customer, name, model, price_sek, address, turn, client, created, order: str | None)`, `Order(number, model, price_sek, name, address, client, status, created, city, delivered, delivered_by)` with `status` `"open"` or `"delivered"`; errors `NotFound`, `SameTurn`, `OfferExpired`, `AlreadyDelivered`, `OutOfStock`, `StoreUnavailable`; `clean_address(text: str) -> str` (raises `ValueError`); `OFFER_MINUTES = 30`, `ORDER_DAYS = 30`, `FIRST_NUMBER = 1001`.
- Produces, in `store.py`: `class Store(Protocol)` with async `seed(stock)`, `stock() -> dict[str, dict[str, int]]`, `change_stock(model, city, change) -> int`, `save_offer(offer)`, `place(offer_id, customer, turn, now) -> tuple[Order, bool]` (the bool is true when it is new), `orders() -> list[Order]` newest first, `deliver(number, city, by, now) -> Order`, `delete(number) -> Order`, `reset(stock)`, `purge(now)`, `close()`; `MemoryStore`; `build_store(settings) -> Store`.
- `Settings` gains `table_endpoint`, `table_name`, `site_client_id`, `store` (`"table"` default or `"memory"`). `table` without an endpoint raises at start: it never falls back to memory.

- [ ] **Step 1: Write the failing tests**, one parametrized suite that runs on both stores. The table store runs on a fake table client in the test file that keeps ETags and applies a transaction whole or not at all. Names and assertions:
  - `test_seed_only_when_there_is_no_stock`
  - `test_stock_never_goes_below_zero`: `change_stock(.., -1)` at 0 raises `OutOfStock`
  - `test_an_offer_becomes_one_order`: two `place` calls in a later turn give `VO-1001` and the same order, flags `True` then `False`
  - `test_an_offer_cannot_be_placed_in_its_own_turn`: `SameTurn`
  - `test_an_offer_is_for_its_customer`: another customer gets `NotFound`, and so does an unknown id
  - `test_an_offer_expires_after_30_minutes`: placed at 29:59, `OfferExpired` at 30:00, and an offer that already became an order still returns it
  - `test_delivery_takes_one_car_from_the_city`, `test_delivery_twice_is_refused` (`AlreadyDelivered`), `test_delivery_without_stock_changes_nothing` (`OutOfStock`, order still open)
  - `test_two_deliveries_of_the_last_car`: run together with `asyncio.gather`, one succeeds, stock 0
  - `test_two_placements_at_once_get_different_numbers`
  - `test_delete_leaves_the_stock_alone`, for an open and a delivered order
  - `test_reset_restores_the_stock_and_keeps_the_numbering`: the next order is not `VO-1001` again
  - `test_purge_removes_orders_after_30_days_and_offers_after_30_minutes`
  - `test_more_than_100_rows_can_be_reset` (table only: a transaction holds at most 100)
  - `test_azure_errors_become_store_unavailable`, and that the error has no row in its text or its chain (table only)
  - `test_clean_address`: newlines, tabs and control characters become one space, 4 and 201 characters are refused, `<script>` is kept as text
- [ ] **Step 2: Run them.** `cd services/dealer && uv run pytest -q tests/test_store.py tests/test_table_store.py`. Expected: fail on import.
- [ ] **Step 3: Implement.** One partition `voltrieve`. Row keys `stock|<model>|<city>`, `offer|<id>`, `order|<number>`, `counter`. `place` and `deliver` are one transaction with ETags, read again and tried again up to 5 times on 409 or 412, then `StoreUnavailable`. Offer ids are `secrets.token_urlsafe(9)`. `ManagedIdentityCredential` in Azure, through `DefaultAzureCredential` with the system-assigned identity. The SDK's loggers are set to WARNING.
- [ ] **Step 4: Run them.** Expected: pass. Then the whole suite of the service.
- [ ] **Step 5: Commit** "Store the dealer's stock, offers and orders".

### Task 3: Orders, the log and two kinds of callers

**Files:** Create `services/dealer/app/hub.py`, `services/dealer/tests/{test_hub,test_orders}.py`. Modify `services/dealer/app/{auth,routes,main,data}.py`, `tests/{conftest,test_auth,test_routes}.py`.

**Interfaces:**
- Produces: `Hub.add(kind, who, *, client=None, model=None, city=None, number=None, result=None, correlation_id=None)`, `Hub.latest() -> list[dict]`, `Hub.subscribe() -> asyncio.Queue`, `Hub.unsubscribe(q)`, `Hub.changed()`, `Hub.clear()`. A queue item is `("log", entry)`, `("changed", {})` or `("clear", {})`. Entry kinds: `stock`, `part_stock`, `prices`, `terms`, `offer`, `order`, `test_drive`, `delivered`, `deleted`, `stock_changed`, `reset`, `refused`.
- Produces: `TokenValidator` takes `required_scope: str | None`. `app.state.validator` (machine: role `Dealer.Access` and no `scp` claim) and `app.state.staff_validator` (scope `manage_as_user` and role `Dealer.Manage`). `Claims` gains `name`.
- Routes: `GET /orders/terms?model=` gives `{"model", "price", "currency": "SEK", "stock": {city: n}}`. `POST /orders/offers` takes `{"model", "address", "customer", "name", "turn", "client"}` and gives 201 `{"offer", "model", "price", "currency", "name", "address"}`. `POST /orders` takes `{"offer", "customer", "turn"}` and gives 201 (200 the second time) `{"order_number", "model", "price", "currency", "name", "address", "ordered", "message"}` with `message` "Thank you for your order. Voltrieve will be in touch about delivery." `SameTurn` is 409 "The customer has not answered yet. Read back the car, the price and the address, and ask.", `OfferExpired` 410 "The offer has expired. Make a new one.", `NotFound` 404 "Unknown offer.", `StoreUnavailable` 503 "The dealer system is not available right now."

- [ ] **Step 1: Write the failing tests.** The flow terms, offer, order with two turns and its numbers. Each error above by status and text. `customer` and `name` over 200 characters, an unknown model, a bad address: 422, and the body does not hold the value. A logged line and the spans of a whole flow (in-memory exporter, as in `test_telemetry.py`) do not hold a marker address or name. Entries: each kind once, no address in any, `who` is `Runa`, `client` from the offer. A token without the role (403) makes one `refused` entry, a missing or bad token (401) none. `/healthz` is 200 while the store raises, and every stock or order route then is 503. Machine routes: a user token with `Dealer.Manage` and the scope gets 403. `test_hub`: keeps 200, a slow subscriber with a full queue is dropped and does not stop the others.
- [ ] **Step 2: Run them.** Expected: fail.
- [ ] **Step 3: Implement.** The routes get the store and the hub from `app.state`. `DealerData` keeps prices, cities and parts, and loses the car stock to the store. Startup seeds and purges. An unreachable store at startup is logged by type, and seeding is tried again on first use. Custom handler for `RequestValidationError`: `loc` and `msg` only.
- [ ] **Step 4: Run the whole suite.** Expected: pass, with the old route tests unchanged except for the fixture that gives a memory store.
- [ ] **Step 5: Commit** "Take orders in the dealer service and log every call".

### Task 4: Staff routes, live events and the page's files

**Files:** Create `services/dealer/app/{staff,site}.py`, `services/dealer/tests/{test_staff,test_site}.py`. Modify `app/main.py`, `Dockerfile`, `.dockerignore`.

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces: `GET /staff/state` `{"models": [{"slug", "name", "price"}], "cities": [...], "stock": {model: {city: n}}, "orders": [...], "log": [...]}`. `POST /staff/stock` `{"model", "city", "change": 1 | -1}` gives `{"in_stock": n}`. `POST /staff/orders/{number}/deliver` `{"city"}`. `DELETE /staff/orders/{number}` 204. `POST /staff/reset` 204. `GET /staff/events`: events `log`, `changed`, `clear`, a keepalive comment every 15 s, and the stream ends after 30 minutes so that the page comes back with a fresh token. `GET /config.json` `{"tenantId", "clientId", "scope"}`.

- [ ] **Step 1: Write the failing tests.** A machine token gets 403 on every staff route, no token 401, a staff token without the role 403. Delivery, second delivery (409 "This order is already delivered."), no stock (409 "There is none in stock in Malmö."), unknown number (404). Stock at 0 minus one is 409. Each change makes one log entry with the representative's `name` (else `preferred_username`, else "Representative") and one `changed`. State purges orders over 30 days. The event stream sends what happens after it opened, ends at 30 minutes (clock injected), and frees its queue when the client leaves. Site: `/` and `/assets/x.js` are served without a token, with `Content-Security-Policy` (`default-src 'self'`, `connect-src 'self' https://login.microsoftonline.com`, `frame-src https://login.microsoftonline.com`, `frame-ancestors 'self'`, `img-src 'self' data:`, `style-src 'self' 'unsafe-inline'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-cache` for `/`. `..` in a path is 404. Staff answers are `Cache-Control: no-store`. Without built files `/` is 404 and the API works.
- [ ] **Step 2: Run them.** Expected: fail.
- [ ] **Step 3: Implement.** The Dockerfile gets a first stage on `node:24-slim` that runs `npm ci && npm run build` in `site/` and copies `dist` to `/srv/site`. Until Task 5 the stage builds a one-line page.
- [ ] **Step 4: Run the suite, and build the image** with `docker build services/dealer`. Expected: pass, and `curl /` in the container gives the page.
- [ ] **Step 5: Commit** "Add the staff routes and serve the dealer page".
- [ ] **Step 6: Deploy early and measure that an open page keeps the service awake.** Provision and `azd deploy dealer`. With the deployer's Azure CLI token, a script `scripts/dealer_awake.py` holds `/staff/events` and reads `/staff/state` every 60 s for 12 minutes, and prints the replica's start time before and after and every lost connection. Then 12 minutes with only the stream. Keep the output in `docs/evidence/dealer-awake.txt`. If the minute read does not keep it awake, change the read to every 20 s and measure again before Task 5. Also check here that the Azure CLI token has the `name` claim.

### Task 5: The page

**Files:** Create `services/dealer/site/` (`package.json`, `package-lock.json`, `index.html`, `vite.config.ts` on port 5176, `tsconfig.json`, `mock/server.mjs` on 8790, `src/{main,App}.tsx`, `src/{state,api,sse,config}.ts`, `src/auth/*`, `src/components/{Header,Orders,Inventory,Log,Notices,Confirm}.tsx`, `src/styles.css`, tests). Modify `.github/workflows/tests.yml`.

**Interfaces:**
- Consumes: the routes of Task 4.
- Produces, for Task 6: `useDealer(api)` returning `{state, arrived: Order[], dismiss(number), live: boolean, problem?: string}`. `arrived` holds open or delivered orders whose number was not in the first state read and has not been dismissed, oldest first.

- [ ] **Step 1: Copy** the auth, `sse.ts` and theme code from `clients/dashboard`, with the comment that names its twin. Configuration comes from `/config.json`, not from the build. Same dependency versions as the dashboard.
- [ ] **Step 2: Write the failing tests.** `state.test.ts`: the first read gives no `arrived`, a later unknown number gives one, the same number never again, also after a lost connection, after `clear` with no orders nothing is left, two at once keep their order. `deliverOptions(stock, model)` lists cities with stock, most first, and is empty at none. `orders.test.tsx`: the Deliver button is off with "None in stock" and on after a car is added, Delete asks first, an address with `<b>` is shown as text. `inventory.test.tsx`: minus is off at 0, plus sends one change, a failed change puts the number back and shows the notice. `log.test.tsx`: each kind has a sentence, the newest is on top. `access.test.tsx`: a 403 says that the account has no access. `api.test.ts`: 503 gives "The dealer system is not available right now.", and the shown state stays.
- [ ] **Step 3: Run them.** `npm test`. Expected: fail.
- [ ] **Step 4: Implement** the layout of section 8 of the spec: orders and inventory on the left, the log on the right, below 1000 pixels the log goes under. It works from 720 pixels with no sideways scroll. The state is read at start, at each `changed` (at most one read at a time, one more after it), at each reconnect and every 60 s. The stream reconnects after 1, 2, 5 and then 10 s. Numbers that change flash, not with reduced motion. Buttons and confirmations work with the keyboard.
- [ ] **Step 5: Run `npm test && npm run build`**, then the page against the mock server (which places an order by `POST /mock/order`) in the browser: both themes, 720 and 1440 pixels, no console errors.
- [ ] **Step 6: CI.** A job `dealer-site` like `dashboard`, in `services/dealer/site`.
- [ ] **Step 7: Commit** "Add the dealer page with orders, inventory and the live log".

### Task 6: The signing

**Files:** Create `services/dealer/site/src/{signature.ts,signature.test.ts}`, `src/components/{Signing.tsx,Signing.test.tsx}`. Modify `App.tsx`, `styles.css`.

**Interfaces:**
- Consumes: `arrived`, `dismiss`.
- Produces: `signaturePath(name: string): string` (an SVG path, the same for the same name).

- [ ] **Step 1: Write the failing tests.** `signaturePath` is the same for the same name, differs for "Kim Berg" and "Anna Lind", has a path for an empty name and for "Ö", and holds only path commands and numbers. The paper shows number, date, car, price with a space as thousands separator, name, address and "Demo order, not a real purchase". Two arrived orders play one after the other. A click and Escape both dismiss exactly the one shown. After the last one the page below is usable and the focus is back where it was. With reduced motion there is no drawing and it closes after 3 s.
- [ ] **Step 2: Run them.** Expected: fail.
- [ ] **Step 3: Implement.** A dimmed page, the paper slides in (0.6 s), the pen draws the path with `stroke-dashoffset` (2 s), the stamp with the number lands (0.4 s), it rests (1.8 s), and flies to its row (0.7 s). The overlay is a dialog with a label, announced once to a screen reader.
- [ ] **Step 4: Run the tests, then look at it** against the mock: one order, three at once, skipped, reduced motion, both themes. Keep a picture of the paper for the docs.
- [ ] **Step 5: Commit** "Sign a purchase order on the screen when it arrives".

### Task 7: Runa's tools, the car rule and masking

**Files:** Modify `agent_core/src/voltrieve_agent/{tools,server_tools,dealer,loop,private}.py`. Test: `agent_core/tests/{test_tools,test_server_tools,test_loop,test_private}.py`, `services/insights/tests` (masking).

**Interfaces:**
- Produces: `UserContext(oid, username, roles, name="", client="")`. `ServerTool` gains `clients: frozenset[str] | None = None`, `client_refusal: str = ""`, `private: bool = False`. `current_turn_id: ContextVar[str | None]`, set by `run_turn` from `turn.turn_id`. `CAR_REFUSAL` (the fixed text) and `ORDER_CLIENTS = frozenset({"web", "cli"})`, `ORDER_ROLE = "Order.Place"` in `server_tools.py`. `DealerClient.terms(model)`, `.offer(model, address, customer, name, turn, client)`, `.place(offer, customer, turn)`.

- [ ] **Step 1: Write the failing tests.**
  - `test_tools`: a tool with `clients` refuses `car`, `other`, `test` and `""` with `client_refusal`, emits `client_not_allowed`, calls no handler, and does it before the role check (a car user without the role gets the car text). A tool without `clients` runs for `""`. A private tool's validation error names the field and not the value. The log record of a refusal and of a failing private handler has no argument.
  - `test_loop`: for a private tool the `server_tool_called` and `server_tool_returned` events have no `arguments` and no `output_preview`.
  - `test_server_tools`: `prepare_car_order` without an address calls `/orders/terms` only and tells Runa to ask for the address and say that it is saved with the order. With one it posts name, oid, client and turn from the context, never from the arguments. `place_car_order` posts offer, customer and turn. `httpx.ReadTimeout` on placing gives "The order may have been received. Call place_car_order again with the same offer." and on the rest the text of today. The tools are private, not concurrent, need `Order.Place`. `test_only_the_booking_tool_runs_alone` is renamed and covers the three.
  - `test_private`: `mask_contacts("Storgatan 1, 753 20 Uppsala")` has no "Storgatan 1" and no "753 20". Also "Västra Hamngatan 12B", "Kungsvägen 3", "Ringvägen 100 lgh 1102", "Torget 5". Not masked: "529 000 SEK", "VP-10007", "Fjord 2", "100 km", "model year 2026". No slowdown on 100,000 characters (under 1 s).
- [ ] **Step 2: Run them.** `cd agent_core && uv run pytest -q`. Expected: the new ones fail.
- [ ] **Step 3: Implement.** Schemas: `prepare_car_order` `{model: enum, address: {type: string, minLength: 5, maxLength: 200}}`, required `model`. `place_car_order` `{offer: {type: string, maxLength: 40}}`, required. Both have `additionalProperties: false`. Statuses `("preparing_order", "Preparing your order")`, `("placing_order", "Placing your order")`. Description of the first: "Call this first when the user wants to buy or order a car, before you ask for anything. Without an address it gives the price and the stock. With the address the user gave, it makes an offer to read back." Of the second: "Order the car of an offer. Only when the user has said yes to the offer in a later message."
- [ ] **Step 4: Run** `agent_core`, `services/insights` and `api` suites. Expected: pass.
- [ ] **Step 5: Commit** "Let Runa order a car on the website and in the CLI only".

### Task 8: The client and the name reach the tools

**Files:** Modify `api/app/{auth,clients,routes,turns}.py`, `api/app/backends/hosted.py`, `agents/hosted/main.py`, `scripts/{denied_demo,voltrieve_client}.py`. Test: `api/tests/{test_auth,test_clients,test_chat,test_hosted_backend}.py`, `agents/hosted/tests/test_handler.py`, `api/tests/test_prompt_backend.py`.

**Interfaces:**
- Consumes: `UserContext.name`, `.client`.
- Produces: `Claims.name`. `ordering_client(settings, azp: str, header: str | None) -> str` in `clients.py`: `car`, `web`, `cli` from `azp`, the Azure CLI's id (`04b07795-8ddb-461a-bbee-02f9e1bf7b46`) is `cli`, all else `other`, and the header value `car` turns any of them into `car`. It ignores `X-Voltrieve-Test`. `Job.ordering_client`, `PausedTurn.ordering_client`. The hosted body's `user` gains `name` and `client`, both optional when read.

- [ ] **Step 1: Write the failing tests.** The table of `ordering_client`: each azp, empty settings never match an empty azp, header `web` changes nothing, header `car` always gives `car`. Through `/chat` with a fake dealer on the loop and prompt backends, a car token with no header, `X-Voltrieve-Client: web`, `X-Voltrieve-Test: 1`, no declared tools, and a conversation id that began with a web token: refused, and the dealer got no request. A paused turn that the car continues: refused, whatever the first request was (the client is taken from the request that runs the tool, and a pause from the car stays the car). Hosted: the body has name and client, and `parse_turn` without them gives `""`. The name is cleaned to one line of at most 100 characters and falls back to the username. `client_name` (the usage count) is unchanged.
- [ ] **Step 2: Run them.** Expected: fail.
- [ ] **Step 3: Implement.** The server tool names now include the two, so a client that declares one of them is refused as today.
- [ ] **Step 4:** `denied_demo.py` gets `--client web|car` and the order question, with `azp` set. Run its logic offline in `tests/contract/test_scripts.py`.
- [ ] **Step 5: Run** the `api`, `agents/hosted`, `tests/contract` suites. Expected: pass.
- [ ] **Step 6: Commit** "Give the tools the customer's name and the client of the token".

### Task 9: Instructions, website and the stock test

**Files:** Modify `agent_core/src/voltrieve_agent/instructions.py`, `agent_core/tests/test_instructions.py`, `clients/web/src/components/{Chat,Runa}.tsx` and their tests, `scripts/voltrieve_facts.py`, `tests/grounding/check.py`, `tests/routing/questions.yaml`. Create `scripts/orders_check.py`.

**Interfaces:**
- Consumes: Tasks 7 and 8. `GET /staff/state` with the Azure CLI's token for `api://$DEALER_CLIENT_ID/manage_as_user`.

- [ ] **Step 1: Write the failing tests** for the instruction lines, on both texts:
  - "When the user wants to buy or order a car, call prepare_car_order with the model at once, before you ask for anything. If the tool says that it is not possible here, say what it says and ask for nothing."
  - "To order you need the delivery address, and the user gives it. Say that it is saved with the order. Call prepare_car_order again with it, read back the car, the price and the address, and ask if you shall order."
  - "Call place_car_order only when the user's own message says yes to that offer. A new car or address needs a new offer. Never say that a car is ordered before the tool returns an order number, and then give the number, the car, the price, the name and the address."
  - The stock line becomes "the stock, price, date, test drive and order tools".
- [ ] **Step 2: Implement**, and the website: a suggested question "I would like to order a new Fjord", and the notice gets "If you order a car, your name and address are saved with the order in the dealer system." The notice in the car is unchanged. Update both tests of the notice.
- [ ] **Step 3: The stock test** reads the expected number from `/staff/state` at run time, with `data.json` only when the service is not reachable and then marked as skipped, not passed. `tests/routing/questions.yaml` gets two questions in a group `order` (`expect: []`, `tools: [prepare_car_order]`): "I want to buy a Fjord" and "Can I order an Atlas?".
- [ ] **Step 4: `scripts/orders_check.py`** runs the acceptance of section 11 of the spec per backend: a whole order, everything in one message, a "no", a changed address, the car refused before an address is asked for (header `car`), and deletes the orders it made. It prints a line per case, uses `X-Voltrieve-Test: 1` and made up addresses, and reads the result from `/staff/state`, not from the answer. `--dry` prints the number of turns. Offline tests with a fake API in `tests/contract`.
- [ ] **Step 5: Run** `agent_core`, `clients/web` (`npm test`), `tests/contract`. Expected: pass.
- [ ] **Step 6: Commit** "Teach Runa to take an order and check it on every backend".

### Task 10: Documentation

**Files:** Modify `README.md`, `docs/{clients,decisions,deploy,runbook,prompt-journey,principles}.md` (the principles only where they list clients and tools), `justfile` (`dealer` recipe that prints the page's address). Create `docs/images/{dealer,dealer-signing}.webp`.

- [ ] **Step 1: Decisions**, one entry each: the dealer serves its own page, two doors with two roles, the car rule in code by the token's `azp`, an order needs a later message, Table Storage and one replica, the log in memory.
- [ ] **Step 2: Clients** gets the fifth client with both pictures (marked as mock data) and the diagram. **Deploy** gets the new values, roles, redirect, deploy order (dealer, agents, API) and idle cost. **Runbook** gets the demo: warm up, open the page, reset, order on the website, deliver, add a car, try in the car, and what to do when the page is cold. Both say to use a made up address.
- [ ] **Step 3: README** status, with a "not verified" list.
- [ ] **Step 4: Build the site** as CI does, and grep the changed files for the em-dash and the client words. Expected: none.
- [ ] **Step 5: Commit** "Document the dealer system and the orders".

### Task 11: Deploy and accept

- [ ] **Step 1: Provision and deploy** in this order: `azd provision`, `azd deploy dealer`, `scripts/deploy-agents.sh`, `azd deploy api`, the website with `azd package web` and `scripts/deploy-web.sh web`. Read the month's cost first.
- [ ] **Step 2: `uv run scripts/smoke.py`.** Expected: all checks pass.
- [ ] **Step 3: `uv run scripts/orders_check.py`** on the three backends. Expected: every case right. Output in `docs/evidence/dealer-orders.txt`, without names or addresses that are not made up.
- [ ] **Step 4: By script with the deployer's token:** deliver, twice (409), at zero (409), delete, a staff token on the machine routes (403), a restart that keeps stock and orders, a marker address in no table of the workspace after 15 minutes. `denied_demo.py` for a user without `Order.Place`. Same evidence file.
- [ ] **Step 5: The page in a browser** against the deployed service, with a token from the Azure CLI in place of the sign-in page: an order placed by script is signed on the screen. The sign-in page itself is listed as not verified until the product owner signs in.
- [ ] **Step 6: Ask the product owner for the routing suite** (15 to 20 SEK), with the cost so far. Only after a yes: run it on the three backends and compare with the limits of today.
- [ ] **Step 7: Reset** the demo data, update the README status, commit "Record the live checks of the dealer orders".
