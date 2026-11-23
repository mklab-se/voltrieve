# Website and Operations Implementation Plan (C of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The website shows a customer asking about their own car, with a photo if they want, and the operations dashboard is an app of its own.

**Architecture:** The dashboard moves out of `clients/web/` first (issue #12), so the website work starts from a smaller app. The website then gets a made-up customer and car, sent as `context`, and an attach button on the API from plan A.

**Tech Stack:** React 19, TypeScript 7, Vite 8, vitest, the same pinned versions as today.

**Spec:** `docs/superpowers/specs/2026-11-23-car-website-attachments-design.md`, section 9, and GitHub issue #12.

**Needs:** plan A merged. Can run at the same time as plan B: this plan touches `clients/web/`, `clients/dashboard/`, and its own entries in `azure.yaml`, `hooks/`, `justfile`, `.github/workflows/tests.yml` and `.claude/launch.json`.

## Global Constraints

- Issue #12 is the specification for the dashboard, except that the registration, the Static Web App, `deploy-web.sh` and CORS are already done in plan A Task 7.
- "Under the hood" (timeline, backend picker, correlation id) is off by default.
- No gauge, no `set_gauge`, no "Flight Recorder" and no dashboard code left in `clients/web/`.
- The car is the same Spark as in the cockpit, registration `SPARK`. The customer is made up.
- The client shrinks a photo to 1,600 px before the upload. The server checks again.
- A photo stays attached to the chat until the customer removes it or it is refused.
- No em-dashes. Commit message rules as in plan A.

## Review Focus

1. The photo expired while the customer was away: the next message uploads it again by itself and is answered (Task 4).
2. A HEIC photo from a phone, or any file the browser cannot decode: a clear message, nothing is sent (Task 4).
3. The photo is rejected after the customer pressed send: the text stays in the field and the message says what to do (Task 4).
4. A user without `Dashboard.View` opens the dashboard: the notice, and no request to `/admin/live` (Task 1).
5. An old bookmark `?page=dashboard` on the website: the chat opens, no error (Task 1).

---

### Task 1: Move the dashboard to its own app

**Files:**
- Create: `clients/dashboard/` as in issue #12 sections 1 and 5, port 5174, `hooks/dashboard-prepackage.sh`
- Modify: `clients/web/src/App.tsx`, `clients/web/src/components/Header.tsx`, `clients/web/src/config.ts`, `clients/web/src/styles.css`, `clients/web/mock/server.mjs`, `hooks/web-prepackage.sh`, `azure.yaml`, `justfile`, `.github/workflows/tests.yml`, `.claude/launch.json`, `infra/tests/test_web_env.sh`
- Move: `Dashboard.tsx`, `dashboard.ts`, `dashboard.test.ts`

- [ ] **Step 1: Write the failing tests** in the new app: without the role the notice is shown and `fetch` is not called. In the web app: `?page=dashboard` renders the chat.
- [ ] **Step 2: Move the code,** with the duplicated files and their twin comments as issue #12 says.
- [ ] **Step 3: Check.** Run: `cd clients/dashboard && npm ci && npm test && npm run build`, `cd clients/web && npm test && npm run build`, `grep -ri "dashboard\|admin/live" clients/web/src clients/web/mock`, `sh infra/tests/test_web_env.sh`. Expected: PASS, and the grep finds nothing.
- [ ] **Step 4: Commit** `Move the operations dashboard to its own app`

### Task 2: Remove the gauge and hide the machinery

**Files:**
- Delete: `clients/web/src/components/Speedometer.tsx`
- Create: `clients/web/src/components/UnderTheHood.tsx`, `clients/web/src/underTheHood.test.tsx`
- Modify: `clients/web/src/tools.ts`, `clients/web/src/tools.test.ts`, `clients/web/src/App.tsx`, `clients/web/src/components/{Header,Chat,Recorder,Notices}.tsx`, `clients/web/src/styles.css`, `clients/web/index.html`

- [ ] **Step 1: Write the failing tests:** `CLIENT_TOOLS` is `set_theme` alone. The switch is off by default, remembered, and works when storage throws. While off, the page has no timeline, no backend picker, no correlation id and no tab for the timeline.
- [ ] **Step 2: Implement.** The name in the header is `My Voltrieve`. Sign-in page: heading `Welcome to My Voltrieve`, text `Sign in to see your car and ask Runa about it.`
- [ ] **Step 3: Check.** Run: `npm test && npm run build && grep -rni "gauge\|flight recorder" src mock index.html`. Expected: PASS, and the grep finds nothing. **Commit** `Make the website a customer page without a gauge`

### Task 3: The customer's car

**Files:**
- Create: `clients/web/src/garage.ts`, `clients/web/src/components/MyCar.tsx`, `clients/web/src/garage.test.ts`, `clients/web/public/spark.webp` (from `data/design/images/spark/master_front.jpg`, at most 200 KB)
- Modify: `clients/web/src/chat.ts`, `clients/web/src/App.tsx`, `clients/web/src/components/Chat.tsx`, `clients/web/src/chat.test.ts`, `clients/web/src/styles.css`

**Interfaces:**
- Produces: `MY_CAR: { model: "Voltrieve Spark"; trim: "Prime"; drive: "RWD"; model_year: 2026; registration: "SPARK"; odometer_km: 12400; color: "Saffron"; nextService: "March 2028 or 30,000 km" }`, `webContext(now: Date): ClientContext`, `ChatRequestInput.context` and `ChatRequestInput.attachments`

- [ ] **Step 1: Write the failing tests:** the request body has `context` without `speed_kmh`, `color` and `nextService`, and with `local_time` and its offset. Every fact on the card exists for the Spark in `data/voltrieve.yaml` (trim, color, service interval).
- [ ] **Step 2: Implement** the card with the picture, and `Ask Runa about your car`. Suggested questions: `How fast can I charge at home?`, `When is my next service and what is done?`, `Which winter wheels fit my car?`, `What does my warranty cover?`
- [ ] **Step 3: Run** `npm test && npm run build`, look at it in the preview at 375, 768 and 1440 px, light and dark. **Commit** `Show the customer's own car on the website`

### Task 4: Attach a photo

**Files:**
- Create: `clients/web/src/attachments.ts`, `clients/web/src/components/PhotoButton.tsx`, `clients/web/src/attachments.test.ts`
- Modify: `clients/web/src/App.tsx`, `clients/web/src/components/Chat.tsx`, `clients/web/src/chat.ts`, `clients/web/src/turns.ts`, `clients/web/mock/server.mjs`

**Interfaces:**
- Produces: `uploadPhoto(...)` with the same signature as in plan B Task 4, `shrink(file: File, maxSide = 1600): Promise<Blob>`, `attachedReducer`, `httpError(status, code?)` that keeps the API's `detail.code`

- [ ] **Step 1: Write the failing tests:** shrinking keeps the aspect ratio and never enlarges; a file that cannot be decoded gives `This file is not a photo the browser can read. Use JPEG, PNG or WebP.`; the photo is named on the next message too until it is removed; 422 `unknown_attachment` uploads once more and sends again, and the second time shows an error; a rejected photo keeps the text in the field and shows the server's message; `New chat` removes the photo; the photo's thumbnail is in the user's bubble.
- [ ] **Step 2: Implement** with a button, drag and drop on the composer, a preview with a remove button, and `Photos are not stored in the conversation.` beside it.
- [ ] **Step 3: Check** against the mock and `npm test && npm run build`. Expected: PASS. **Commit** `Let customers attach a photo`
