# Car Cockpit Implementation Plan (B of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A browser cockpit of the Voltrieve Spark that drives down an endless street, obeys Runa, photographs parking signs and shows the chat in a HUD.

**Architecture:** A pure simulation (`src/sim/`) that knows nothing about React or three.js, a three.js scene that draws it, and React for the dashboard, the screen and the HUD. Sign-in, SSE, chat stream, answers and timeline are copied from `clients/web/`.

**Tech Stack:** React 19, TypeScript 7, Vite 8, three.js (an exact version at least two weeks old), vitest. Same pinned versions as `clients/web/` for everything shared.

**Spec:** `docs/superpowers/specs/2026-11-23-car-website-attachments-design.md`, section 8.

**Needs:** plan A merged. Can run at the same time as plan C: this plan touches only `clients/car/`, one test in `tests/contract/`, and its own entries in `azure.yaml`, `hooks/`, `justfile`, `.github/workflows/tests.yml` and `.claude/launch.json`.

## Global Constraints

- The car is the Spark: saffron yellow, registration `SPARK`, 0 to 100 in 9.2 s, top speed 150.
- Blocks of 400 m. One sign at the start of each block, one rule per block, several bays.
- The smallest row of a sign photo is at least 24 px high. Above 70 km/h the photo is blurred enough that the API rejects it.
- The HUD goes back after 12 quiet seconds or on Escape, and stays while the pointer or focus is in it. It fades with reduced motion.
- "Under the hood" is off by default. While off, nothing on screen mentions backends or events.
- Layout from 1280 to 2560 px wide. Smaller gets a message.
- No voice. No shared package with `clients/web/`: copied files say in a comment where the twin lives.
- Images: stop at 5 USD in all, measure the first one before making the rest, and ask the product owner before the first.
- No real brands or logos in pictures. No em-dashes. Commit message rules as in plan A.

## Review Focus

1. The tab is hidden while driving and shown again a minute later: the car does not jump or park in the wrong block (Task 2, step is capped).
2. "Park" while the car stands still in the lane: it creeps to the next bay instead of waiting for ever (Task 2).
3. A new sign passes while Runa is answering about the previous one: the HUD says which block the answer is for (Task 6).
4. The API has lost the photo (422 `unknown_attachment`): it is uploaded again once and the turn goes on, and a second 422 is shown as an error (Task 4).
5. Sound is blocked until the first click: `honk` still returns a truthful result, "The browser blocked the sound" (Task 3).

## File Structure

| File | Responsibility |
| --- | --- |
| `src/sim/car.ts` | Speed, position, parking. Pure |
| `src/sim/street.ts` | Blocks, bays, which sign applies. Pure |
| `src/sim/signs.ts`, `src/sim/signs.json` | Sign definitions and drawing to a canvas |
| `src/scene/Scene.ts`, `src/scene/signCamera.ts` | three.js: the street, rain, the close-up photo |
| `src/tools.ts` | Declarations and running of the five tools |
| `src/attachments.ts` | Upload, and upload again |
| `src/context.ts` | The context sent with each turn |
| `src/components/` | `Cockpit`, `DriverDisplay`, `CenterScreen`, `Hud`, `Pedals`, `Clock`, `UnderTheHood` |
| `src/useChat.ts` | The turn loop: send, run tools, continue |

---

### Task 1: A signed-in shell that can chat

**Files:**
- Create: `clients/car/` with `package.json`, `vite.config.ts` (port 5175), `tsconfig.json`, `index.html`, `.env.example`, `mock/server.mjs`, `src/main.tsx`, `src/App.tsx`, `src/useChat.ts`
- Copy with a twin comment: `src/auth/`, `src/sse.ts`, `src/events.ts`, `src/types.ts`, `src/turns.ts`, `src/format.ts`, `src/chat.ts`, `src/config.ts` (without the portal links), `src/components/{Answer,Recorder,Runa,SourceList,Notices}.tsx`, `src/sources.ts`, `public/runa.png`, and their tests
- Create: `hooks/car-prepackage.sh`
- Modify: `azure.yaml` (service `car`), `justfile` (`car`, `open-car`), `.github/workflows/tests.yml`, `.claude/launch.json` (`car-dev-mock`, `car-mock-api`)

**Interfaces:**
- Produces: `useChat({ auth, config, backend, tools, runTool, context }): { state, busy, send(text: string, attachments?: string[]), reset() }`, where `runTool(call: ClientToolCall): Promise<{ output: string; attachments?: string[] }>` and `context(): ClientContext`

- [ ] **Step 1: Write the failing test** `src/useChat.test.ts` (a plain function `runTurn` behind the hook): a turn with two tool rounds sends the results with their attachments, at most 5 rounds run, `context()` is read once per message and sent as `context`.
- [ ] **Step 2: Run** `cd clients/car && npm install && npm test`. Expected: FAIL, then implement until PASS.
- [ ] **Step 3: The mock server** answers `/chat`, `/chat/tool-results` and `/attachments`. Messages: "wipers" calls `set_wipers`, "honk" `honk`, a number with "speed" `set_speed`, "park here" `get_sign_photo` and then a scripted parking answer, "park" `park`. `/attachments` answers `rejected` `too_blurry` when the file name is `blurred.jpg`.
- [ ] **Step 4: Run** `npm run build`. Expected: no type errors. **Commit** `Add the car client shell with sign-in and chat`

### Task 2: The simulation

**Files:**
- Create: `src/sim/car.ts`, `src/sim/street.ts`, `src/sim/car.test.ts`, `src/sim/street.test.ts`

**Interfaces:**
- Produces:
  - `type Mode = "driving" | "parking" | "parked" | "leaving"`
  - `interface Car { distanceM: number; speedKmh: number; targetKmh: number; mode: Mode; lateral: number; bayM?: number }` (`lateral` 0 in the lane, 1 in the bay)
  - `newCar(): Car`, `step(car: Car, dtSeconds: number): Car`, `setSpeed(car: Car, kmh: number): Car`, `startParking(car: Car): Car`
  - `BLOCK_M = 400`, `SIGN_AT_M = 10`, `BAYS_AT_M = [60, 130, 200, 270, 340]`, `blockOf(distanceM): number`, `signOf(block: number): SignId`, `nextBay(distanceM: number, needM: number): number`
  - `passedSign(beforeM: number, afterM: number): number | undefined` (the block whose sign was passed)

- [ ] **Step 1: Write the failing tests.**

```ts
expect(seconds(0, 100)).toBeCloseTo(9.2, 1);                 // at 60 steps a second
expect(run(setSpeed(newCar(), 200), 60).speedKmh).toBe(150);
expect(run(setSpeed(car(90), 0), 10).speedKmh).toBe(0);
expect(step(car(50), 60).distanceM - car(50).distanceM).toBeLessThan(2);  // dt capped at 0.1 s
expect(run(startParking(car(0)), 60).mode).toBe("parked");   // from a standstill it creeps
expect(run(startParking(car(90)), 60)).toMatchObject({ mode: "parked", speedKmh: 0, lateral: 1 });
expect(run(setSpeed(parked(), 30), 10)).toMatchObject({ mode: "driving", lateral: 0 });
expect(setSpeed(parked(), 0).mode).toBe("parked");
expect(nextBay(335, 20)).toBe(400 + 60);                     // not enough room: the next block
expect(signOf(7)).toBe(signOf(7)); expect(signOf(3)).not.toBe(signOf(4));   // fixed order, never the same twice in a row
expect(passedSign(409, 411)).toBe(1); expect(passedSign(411, 420)).toBeUndefined();
```

- [ ] **Step 2: Run** `npm test -- sim`, expected FAIL. Implement: constant acceleration 100 / 9.2 km/h per second, braking 25 km/h per second, creep speed 15 km/h for parking. Run again, expected PASS.
- [ ] **Step 3: Commit** `Simulate the car and the street`

### Task 3: The tools

**Files:**
- Create: `src/tools.ts`, `src/horn.ts`, `src/tools.test.ts`
- Modify: `tests/contract/test_contract.py`

**Interfaces:**
- Consumes: `setSpeed`, `startParking`, the five tools in plan A Task 4
- Produces: `CAR_TOOLS`, `runCarTool(call: ClientToolCall, car: CarControls): Promise<{ output: string; attachments?: string[] }>`, `interface CarControls { setSpeed(kmh: number): void; setWipers(mode: WiperMode): void; honk(times: number): Promise<boolean>; park(): Promise<{ block: number; askedBlock?: number }>; signPhoto(): Promise<{ id?: string; message: string }> }`

- [ ] **Step 1: Write the failing tests:** valid calls reach the controls and give these outputs. `The car is accelerating to 90 km/h.` / `The car is braking to 30 km/h.` / `The wipers are slow.` / `Honked 2 time(s).` / `The browser blocked the sound. Click in the car first.` / `Parked, in the block you asked about.` / `Parked in another block. Another sign applies here: ask again before you leave the car.` / `Parked.` (nothing asked) / `The photo is attached.` / the rejection message / `No parking sign has been passed yet.` Bad arguments and unknown names give `Error: ...` and change nothing.
- [ ] **Step 2: Contract test:** `CAR_TOOLS` in `clients/car/src/tools.ts` and in `agents/prompt/create_agents.py` have the same names, descriptions and parameters (read the TypeScript file the same way the CLI comparison reads its file; if that is not possible, export the list to `clients/car/src/tools.json` and import it in both tests and code).
- [ ] **Step 3: Implement, run** `npm test -- tools` and `cd tests/contract && uv run pytest -q`. Expected: PASS. **Commit** `Let Runa use the wipers, the horn, the speed and parking`

### Task 4: Signs, the photo and the upload

**Files:**
- Create: `src/sim/signs.json`, `src/sim/signs.ts`, `src/attachments.ts`, `src/sim/signs.test.ts`, `src/attachments.test.ts`

**Interfaces:**
- Produces:
  - `interface SignDef { id: string; top: "P" | "no_parking" | "no_stopping"; panels: { color: "blue" | "yellow"; rows: { text: string; red?: boolean }[] }[] }`
  - `drawSign(def: SignDef, rowPx: number): HTMLCanvasElement`
  - `uploadPhoto(input: { apiUrl; token; blob: Blob; label: string }, fetchImpl?): Promise<{ status: "ready"; id: string } | { status: "rejected"; message: string } | { status: "error"; message: string }>`
  - `class SignPhotos { add(block: number, blob: Blob): void; last(): { block: number; blob: Blob; url: string } | undefined; idForLast(upload): Promise<...> }` (uploads once, keeps the id, uploads again after `unknown_attachment`)

- [ ] **Step 1: The definitions,** in this order in `signs.json`. Rows in parentheses are for days before a red day, `red` rows for red days:

| id | Top | Panels |
| --- | --- | --- |
| `two_hours_fee` | P | `2 tim`, `9-18`, `(9-15)` / `Avgift`, `7-19`, `(11-17)` |
| `fee_always_two_hours` | P | `Avgift` / `2 tim`, `8-18`, `(8-11)`, red `8-15` |
| `two_hours_always_fee` | P | `2 tim` / `Avgift`, `8-18` |
| `one_panel` | P | `2 tim`, `Avgift`, `8-18` |
| `friday` | no_parking | yellow `Fredag`, `8-16`, `1/11-15/5` |
| `cleaning` | P | `Avgift`, `8-18` / yellow with the no parking symbol `Måndag`, `12-15`, `1 okt-30 april` |
| `no_stopping` | no_stopping | none |
| `charging` | P | `Endast`, `laddning` |

- [ ] **Step 2: Write the failing tests:** every definition draws, with the canvas height equal to its panels, and rows at `rowPx`. The upload: ready, rejected with the server's message, network error, one new upload after 422 `unknown_attachment`, an error after the second. A photo is uploaded once for two questions.
- [ ] **Step 3: Implement, run, commit** `Draw Swedish parking signs and upload their photos`

### Task 5: The street in 3D

**Files:**
- Create: `src/scene/Scene.ts`, `src/scene/signCamera.ts`, `src/scene/Scene.test.ts`, `scripts/` none
- Create: `public/cockpit.webp`, `public/facades/*.webp`, `assets/README.md` (prompts, model, cost)

**Interfaces:**
- Consumes: `Car`, `signOf`, `drawSign`, `passedSign`
- Produces: `class StreetScene { constructor(canvas: HTMLCanvasElement); render(car: Car, wipers: WiperMode): void; photo(car: Car, block: number): Promise<Blob>; dispose(): void }`, `blurPx(speedKmh: number): number`

- [ ] **Step 1: Ask the product owner,** then make one cockpit picture from the driver's seat with `data/design/images/spark/interior.jpg` as the reference, screens dark, no people. Measure the cost, report it, then up to 3 variants. The product owner picks one. Cut the windscreen out to transparency, save as WebP.
- [ ] **Step 2: Up to 6 facade textures** in the colors of the Spark pictures. If the cap is reached, use plain colored facades drawn by code.
- [ ] **Step 3: Write the failing tests:** `blurPx(70) === 0`, `blurPx(71) >= 8`, `blurPx(150) <= 24`; objects behind the car are reused, so the number of meshes is the same after 5 km as after 500 m (count them with a fake renderer).
- [ ] **Step 4: Implement.** Street width, sidewalk, parked cars outside the bays, trees, a lamp every 40 m. Rain with the wipers on. `photo()` renders the block's sign with a second camera, 1024 x 1024, straight on and a little from below, rows at least 24 px, then applies the blur with a 2D canvas filter and saves JPEG quality 85.
- [ ] **Step 5: Check the photo against the real handler:** a small script saves one photo per sign at 50 km/h and one at 90 km/h, and `api/app/attachments/photo.py` accepts all the first and rejects all the second as `too_blurry`. Run: `npm run sign-photos && uv run --project api python scripts/spikes/check_sign_photos.py clients/car/out`. Expected: `8 ready, 8 too_blurry`.
- [ ] **Step 6: Commit** `Drive the Spark down an endless street`

### Task 6: The dashboard, the screen and the HUD

**Files:**
- Create: `src/components/{Cockpit,DriverDisplay,CenterScreen,Hud,Pedals,Clock}.tsx`, `src/context.ts`, `src/hud.ts`, `src/hud.test.ts`, `src/context.test.ts`, `src/styles.css`

**Interfaces:**
- Produces: `hudReducer(state: HudState, event: "send" | "delta" | "done" | "tick" | "escape" | "enter" | "leave", nowMs: number): HudState` with `HudState { open: boolean; hold: boolean; quietSince?: number; askedBlock?: number; currentBlock: number }`, `carContext(car: Car, clock: Date): ClientContext`

- [ ] **Step 1: Write the failing tests:** opens on send, stays open while streaming, closes 12,000 ms after the last delta and not at 11,999, does not close while held, closes on Escape, opens again on the next message. `carContext` gives `Voltrieve Spark`, `Prime`, `RWD`, 2026, `SPARK`, the odometer, the clock with its offset and the rounded speed. When `currentBlock !== askedBlock` while an answer is open, `notice` is `This answer is for the previous block.`
- [ ] **Step 2: Implement.** The picture keeps its aspect ratio, and the displays are placed in percent of it. The HUD animates `transform` and `opacity` from the center screen's rectangle. Keyboard: Enter sends, Escape closes the HUD, up and down drive when no field has focus. The last sign photo with "Park here?" sends the message `Can I park here?` with the photo attached. The clock sets day and time.
- [ ] **Step 3: See it run** with `car-dev-mock` and `car-mock-api`: at 1280, 1920 and 2560 px wide, at 900 px (message), with reduced motion, "turn on the wipers", "honk twice", "set the speed to 90", "park", "can I park here?". No console errors. Keep screenshots for plan D.
- [ ] **Step 4: Commit** `Add the Spark dashboard and the HUD`

### Task 7: Under the hood

**Files:**
- Create: `src/components/UnderTheHood.tsx`, `src/underTheHood.test.tsx`

- [ ] **Step 1: Write the failing test:** off by default, remembered in `localStorage` and working when that throws. While off, the rendered text holds none of `loop`, `prompt`, `hosted`, `trace`, `event`.
- [ ] **Step 2: Implement** as a panel beside the car with the timeline and the backend picker. Run `npm test && npm run build`. Expected: PASS.
- [ ] **Step 3: Commit** `Hide the events and the backend picker behind a switch in the car`
