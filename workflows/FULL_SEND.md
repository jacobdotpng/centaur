# Full send

Use the `full-send` skill for the conversational entrypoint. Alignment stays in
the originating Slack thread. Only the requester's explicit **Ship it**, after
the scope summary, starts `full_send`; its input records the agreed scope,
acceptance criteria, deployment target, attribution and confirmation timestamp.
The workflow input is a trusted caller contract, not a Slack authorization
proof. A generic phrase trigger must not start it before alignment.

`full_send` creates/reuses one PR, then uses
`ctx.run_workflow("review_0", "agentic_review", {"pr_url": ...})`. This starts a
real child run through the registry and waits for its persisted output. Review
logic is never imported or copied. Future child runs use the deployed
`agentic_review` implementation; finished review checkpoints remain historical.
The clean result must identify the final reviewed commit and contain no findings.

The CI agent can repair failures on the same PR. A changed head causes a fresh
child review. After at most three review/CI cycles, unresolved issues stop the
run. Merge checks the expected head and repository protections. The shipping
agent follows the agreed repository pipeline and must report the merge commit,
verified deployment revision and CI/CD plus live acceptance evidence. Missing
credentials, pending approvals and unverified deployments fail closed. Progress,
failure and completion messages go to the originating thread.

## Runtime setup

Deploy both workflow files and the new workflow-host/runtime primitive. If an
allowlist is configured, include both `full_send` and `agentic_review`.
Discovery registers `workflow-full-send` and `workflow-agentic-review`; grant
each its own shared GitHub/repository and model permissions. The full-send
principal also needs access to the agreed deployment tools and verification
surface. If the release requires downstream promotion PRs, it also needs
workflow read/write capabilities to call the named review workflow for those
PRs; their exact heads must pass review and CI before merge as well.
Workflow-host Slack delivery needs the deployment's existing Slack
posting configuration. Invoking-channel and personal OAuth grants are not
inherited or copied. Do not provision broad grants merely to make a test pass.

The Python context's `run_workflow(name, workflow_name, input)` starts a child
with an idempotency key scoped to the parent task and named step. Durable
15-second sleeps release the parent worker while the child runs. Completed
output is checkpointed; failed/cancelled children raise and cannot authorize a
merge. Waiting stops after two hours. Parent cancellation or timeout does not
cancel the child; inspect that child before resuming or starting another run.

## Verification

Run workflow and host unit tests, plus the `centaur-workflows` Rust tests.
After deployment, check the live registry for both names and verify their
principal grants. Exercise an explicitly authorized small task: discuss scope,
confirm Ship it, inspect the real parent and child run IDs, review summary and
exact commit, required CI/CD runs, merged PR and live deployed surface. Do not
claim runtime availability based on this document or unit tests alone.
