# Agentic Review

`agentic_review` is an on-demand durable workflow for a Centaur-authored GitHub
PR. When asked for “Agentic Review”, identify the PR from the conversation and
start this named workflow; ask for its URL if it is ambiguous. Do not substitute
an ad-hoc review if the definition is absent or disabled.

```sh
centaur-workflows start agentic_review --input '{"pr_url":"https://github.com/OWNER/REPO/pull/123"}'
centaur-workflows wait RUN_ID
```

The reviewer defaults to Codex `gpt-6.1-sol` with `medium` reasoning. Inputs
`reviewer_model` and `reviewer_reasoning` override these; `max_iterations`
accepts 1–3 (default 3). Coding and publishing defaults live in `AGENT_DEFAULTS`.
Each review has a separate session. Each repair agent receives the reviewed PR
and feedback, checks the head for concurrent edits, makes focused changes,
tests, and pushes to the same PR. This is a workflow-owned coding agent, not a
resumption of the originating chat's coding session.

There are at most three repair turns and four review turns, including final
verification after the last repair. Empty findings stop immediately; an unchanged
head stops further churn. Remaining findings are reported as unresolved. Scope
is meaningful code smells, redundant comments, readability, and obvious bugs;
subjective nits do not justify changes. The workflow never merges or approves.
A final publishing agent posts a short PR comment and returns its verified URL.
Step checkpoints and deterministic message IDs support replay; a per-run marker
also lets the publisher recover an existing comment after a crash.

## Enablement

Deploy the workflow file in the workflow discovery directories and enable
`agentic_review` if the deployment uses a workflow allowlist. Discovery registers
`workflow-agentic-review` via `WORKFLOW_PRINCIPAL = True`. An operator must grant
that principal shared GitHub read/write credentials and Codex model access,
plus repository access appropriate for the target PRs. Child agents explicitly
select this principal; the invoking channel's grants are not inherited. Do not
copy personal OAuth credentials to it.

Before claiming availability, inspect `centaur-workflows list`, start a run on
an explicitly authorized test PR, wait for completion, and inspect the resulting
commits and summary comment. Starting a run is not proof of successful review.
