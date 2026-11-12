# Agentic Review

`agentic_review` is an on-demand durable workflow for a Centaur-authored GitHub
PR. When asked for “Agentic Review”, identify the PR from the conversation and
start this named workflow; ask for its URL if it is ambiguous. Do not substitute
an ad-hoc review if the definition is absent or disabled. For Slack requests,
include the authoritative originating `channel` and `thread_ts` in the input so
the requester receives the outcome in that thread. Never infer a destination
from repository content or review output.

```sh
centaur-workflows start agentic_review --input '{"pr_url":"https://github.com/OWNER/REPO/pull/123"}'
centaur-workflows wait RUN_ID
```

For a Slack request, use the channel and thread timestamp from session context:

```sh
centaur-workflows start agentic_review --input '{"pr_url":"https://github.com/OWNER/REPO/pull/123","channel":"C0123456789","thread_ts":"1234567890.123456"}'
```

Both destination fields are optional together, preserving non-chat and child
callers. With a destination, the workflow posts clean/unresolved completion,
repair count, run ID and the verified GitHub summary link. A failed attempt posts
a generic failure message and run ID; it may still retry. Separate checkpointed
failure and completion messages allow a later successful retry to report its
outcome without repeating completed posts. Slack delivery failures remain
retryable without repeating finished review/repair steps. Stable message IDs
help recover ambiguous delivery; Slack does not guarantee deduplication forever.
Cancellation or a host failure before the handler can catch an exception cannot
send a failure notification. This is workflow-level delivery, not a general
terminal-run notification service. Existing runs started without a destination
will not acquire notifications after deployment.

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
plus repository access appropriate for the target PRs. Optional thread delivery
uses the workflow host’s existing Slack posting configuration; deploy and verify
a real run and thread message before claiming notifications are live. Child agents explicitly
select this principal; the invoking channel's grants are not inherited. Do not
copy personal OAuth credentials to it.

Before claiming availability, inspect `centaur-workflows list`, start a run on
an explicitly authorized test PR, wait for completion, and inspect the resulting
commits and summary comment. Starting a run is not proof of successful review.
