# Working on Voltrieve

Instructions for AI agents (and people) who change this repository.

## Read the principles first

[docs/principles.md](docs/principles.md) says what Voltrieve is for: the pillars every feature serves and the rules every feature follows. Read it before you design or build anything new.

## Before you build: the fit check

For every new feature, before any design or code:

1. Name the pillar or pillars it serves.
2. Say whether it enhances something that exists or adds something new.
3. Check it against every rule in the principles.

If the answer is not clearly "fits", stop and ask the maintainer. Do not quietly bend a rule. The outcome is one of:

- it fits, and you go on to the design write-up,
- a rule is overruled on purpose, and the reason goes in [docs/decisions.md](docs/decisions.md),
- the principles change, in the same commit as the work that needed it,
- the feature is done another way, or not at all.

Small fixes and changes to something that already exists do not need a written fit check, but the rules still apply.

## How work is done here

- **Design first.** A short design write-up in `docs/superpowers/specs/`, approved before any code.
- **All three backends.** Agent features work on the own loop, the prompt agent and the hosted agent, or the design says why not.
- **Measure it.** Run the routing suite against the 10 s time-to-first-token limit, and keep live results in `docs/evidence/`. List what you could not verify as not verified.
- **Document it.** Update the docs, add a decision for each real choice, add at least one diagram, screenshot or interactive view, and update the status in the README.
- **Keyless and least privilege.** No secrets in source, roles in Bicep, checks that fail closed.
- **Private by default.** No prompts, answers, photos or signed links in telemetry or logs. Not met yet for prompt and hosted turns: [the decision is open](docs/decisions.md#open-the-product-owner-decides-what-foundrys-tracing-may-record). One job may read stored chats. Only groups shared by at least five people may leave it.
- **Made up data only.** Never name a real client or put a real client situation in the repository. Use a neutral stand-in.
- **Cost first.** State any new idle cost, and get bulk generation approved before it runs.

## Writing style

Plain, short sentences, in English. No em-dashes: use a comma, a colon, parentheses or a new sentence. Commit messages are one plain sentence in the imperative ("Add source links beside the answer").
