# Evidence and Documentation Implementation Plan (D of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy plans A to C, measure them on all three backends, and document them so that someone new understands what was built and why.

**Architecture:** One script draws the signs from `clients/car/src/sim/signs.json` and knows the right answers. It feeds the photo questions in the routing suite and the comparisons from the design.

**Tech Stack:** Python 3.12, Pillow, the existing routing suite, the deployed API.

**Spec:** `docs/superpowers/specs/2026-11-23-car-website-attachments-design.md`, sections 4, 10, 12 and 13.

**Needs:** plans A, B and C merged.

## Global Constraints

- Ask the product owner before deploying, and again after the first 20 photo turns with the measured cost, before running the rest.
- Evidence uses drawn signs and repository pictures only. The real sign photos stay out of the repository.
- Every photo question on three backends, three times, about 300 turns. Comparisons about 550 short model calls.
- The routing suite must not be worse than before for turns without attachments.
- What was not verified is listed as not verified. No em-dashes. Commit message rules as in plan A.

## Review Focus

1. A rate limit in the middle of a run: the run waits and goes on, and times from waits are left out of medians (Task 1).
2. The last day before Christmas and 5 January: the right answer follows the day before a red day (Task 1).
3. Scale to zero during the suite: a lost attachment is uploaded again, not counted as a wrong answer (Task 2).
4. The hosted agent's slower start: measured separately from the photo's own time (Task 2).
5. A reader who opens the docs on the site: new pages are in the navigation and the diagrams render (Task 4).

---

### Task 1: Signs with known answers

**Files:**
- Create: `tests/photos/signs.py`, `tests/photos/pictures.py`, `tests/photos/questions.yaml`, `tests/photos/test_signs.py`, `tests/photos/pyproject.toml`

**Interfaces:**
- Produces: `draw(sign_id: str) -> bytes` (JPEG, rows at least 24 px), `allowed(sign_id: str, when: datetime, minutes: int, charging: bool = False) -> bool`, `fee(sign_id: str, when: datetime) -> bool`, `day_type(date) -> "weekday" | "before_red" | "red"`, `broken_lamp(side: "left" | "right") -> bytes`, `undamaged() -> bytes`

- [ ] **Step 1: Write the failing tests** for the rules, which follow the product owner's reading (each bordered panel is read with the top sign, and does not apply outside its times):

```python
@pytest.mark.parametrize("sign,when,minutes,ok", [
    ("two_hours_fee", "2026-11-24 10:00", 180, False), ("two_hours_fee", "2026-11-24 18:30", 180, True),
    ("two_hours_fee", "2026-11-28 10:00", 180, False), ("two_hours_fee", "2026-11-28 16:00", 180, True),
    ("two_hours_fee", "2026-11-29 12:00", 300, True),  ("two_hours_fee", "2027-01-05 15:30", 240, True),
    ("two_hours_always_fee", "2026-11-24 22:00", 180, False), ("one_panel", "2026-11-24 22:00", 180, True),
    ("fee_always_two_hours", "2026-11-29 09:00", 180, False), ("fee_always_two_hours", "2026-11-24 22:00", 180, True),
    ("friday", "2026-11-27 10:00", 60, False), ("friday", "2026-06-12 10:00", 60, True),
    ("cleaning", "2026-11-30 13:00", 60, False), ("cleaning", "2026-06-01 13:00", 60, True),
    ("no_stopping", "2026-11-29 23:00", 60, False), ("charging", "2026-11-24 10:00", 60, False),
])
def test_allowed(sign, when, minutes, ok): ...

@pytest.mark.parametrize("when,needed", [("2026-11-24 18:15", True), ("2026-11-24 19:30", False), ("2026-11-28 10:00", False),
    ("2026-11-28 15:30", True), ("2026-11-29 12:00", False), ("2027-01-05 17:30", False), ("2027-01-06 12:00", False)])
def test_fee_on_two_hours_fee(when, needed): ...

def test_day_types():  # 2026-12-24 and 2027-01-05 before_red, 2026-12-25 and 2027-01-06 red, 2026-11-28 before_red
```

Red days come from a small list of Swedish public holidays for 2026 and 2027 in `signs.py`, with Easter written out.

- [ ] **Step 2: Implement, run** `cd tests/photos && uv run pytest -q`. Expected: PASS. Add the project to the CI matrix. **Commit** `Add parking signs with known answers for the tests`

### Task 2: Deploy and measure

**Files:**
- Modify: `tests/routing/check.py` (questions may have `photo`, `context` and `verdict` or `article` or `no_article`), `scripts/voltrieve_client.py` (upload, and upload again after `unknown_attachment`)
- Create: `tests/photos/compare.py`, `docs/evidence/photos-routing.json`, `docs/evidence/photos-comparison.txt`, `docs/evidence/photos-privacy.txt`, `docs/evidence/car-tools.txt`

- [ ] **Step 1: Run the routing suite before** on the deployed version and keep it. Run: `uv run tests/routing/check.py --repeats 1 --label before-photos`.
- [ ] **Step 2: Ask, then deploy in this order.** The hosted agent goes before the API: with a new API and an old agent, photo turns answer without the photo and the log protection is missing.
  1. A provision preview, then `azd provision` (registrations, two sites, CORS, `PROMPT_AGENT_CAR`). Not `azd up`.
  2. `scripts/deploy-agents.sh` (three prompt agents, then the hosted agent).
  3. `azd deploy api`. The new revision drops sessions bound to the old hosted version.
  4. Smoke, with one car-tools turn on the prompt agent and one upload per backend.
  5. On the hosted agent: 3 photos, a pause and a continuation, before anything else is measured (large `kept` through the gateway is not proven). Note the API's memory peak.
  6. `web` and `dashboard` together, then `car`.
  Rollback in reverse, API first. On an empty environment `azd up` comes first, then `deploy-agents.sh`, then an API restart; photos count as unavailable until then. `docs/deploy.md` gets this order.
- [ ] **Step 3: The first 20 photo turns,** report the cost and the times, and wait for a yes.
- [ ] **Step 4: The rest:** the sign questions, the broken lamp left and right (`VP-14375`, `VP-14440`, taken from `parts-index.json`), the undamaged car, a drawn sign asked about as a broken part, a blurred photo, "which part is this?". Then `compare.py` (three approaches and four places for the rules). Then the routing suite again as `--label after-photos`.
- [ ] **Step 5: The car's tools** with a script on all three backends: wipers, horn, speed, park, sign photo through the tool. "honk" after a sign question is stored as usual.
- [ ] **Step 6: Privacy:** after a photo turn, list the conversation's items: text only. Query Application Insights (traces, exceptions and dependencies) and the live feed for `SPARK`, `base64` and `From the client` in the run's time span, also after a photo turn that fails on purpose. Expected: nothing. The runbook says that failures of private turns arrive as traces, not exceptions.
- [ ] **Step 7: Sign in** to the three sites by hand with the product owner, and note what was seen.
- [ ] **Step 8: Commit** `Measure photos, the car tools and routing on the three backends`

### Task 3: Replace the old device example

**Files:**
- Modify: `scripts/new_client_tool_demo.py`, `docs/why-foundry.md`, `docs/runbook.md`, `docs/principles.md`, `README.md`, `docs/evidence/new-client-tool.txt`, `docs/superpowers/specs/2026-11-03-voltrieve-agentic-rag-design.md`, `tests/contract/test_scripts.py`

- [ ] **Step 1: Change the demo** to `set_seat_heating(level)` 0 to 3, "a car with a function the server has never seen". Rewrite the section "A device that brings its own tools" for a car. Run it live and keep the output.
- [ ] **Step 2: Check.** Run: `git grep -niE "emissiv|thermal camera|android"`. Expected: nothing. **Commit** `Use a car function in the new client tool demo`

### Task 4: Documentation

**Files:**
- Create: `docs/attachments.md`, `docs/clients.md`, `docs/assets/car-*.png`, `docs/assets/website-*.png`, `docs/assets/car.gif` or `.mp4`
- Modify: `README.md`, `docs/index.md`, `docs/decisions.md`, `docs/principles.md` (pillar table), `docs/runbook.md`, `docs/deploy.md`, `docs/why-foundry.md`, `docs/prompt-journey.md` (client tools), `docs/evidence/README.md`, `scripts/build_site.py` if it lists pages

- [ ] **Step 1: `docs/clients.md`:** one audience per client, with a diagram and pictures.
- [ ] **Step 2: `docs/attachments.md`:** how a photo travels (diagram), the measurements, why it is not stored, what `store=False` does not promise, the known limit, the extension table, the warning light example, why no skills (issue #17).
- [ ] **Step 3: Decisions,** one entry each: photo straight to the model, unstored photo turns, no built-in parking rules, the sign exception, no skills, browser instead of native, one client per audience, `set_speed` does what it is told.
- [ ] **Step 4: Deploy guide:** the two registrations and sites, the third prompt agent, teardown, cost. README: the table of audiences with the CLI as a developer tool, layout, status, verified and not verified, test counts.
- [ ] **Step 5: Check.** Run: `uv run scripts/build_site.py`, open the new pages, `git diff --name-only <base> | xargs grep -n $'\u2014'`. Expected: no dashes, pages in the navigation, diagrams render.
- [ ] **Step 6: Commit** `Document the car, the website and attachments`

### Task 5: Acceptance

- [ ] **Step 1: Go through section 13 of the spec** line by line, with a link to the evidence or the test for each. What cannot be shown is listed as not verified in the README.
- [ ] **Step 2: Update** issues #4 (what is done of it), #10 and #12 with the result. Ask the product owner before closing any.
