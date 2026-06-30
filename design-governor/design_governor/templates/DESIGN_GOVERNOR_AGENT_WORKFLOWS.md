Design Governor Agent Workflows

Codex

Read `AGENTS.md` first.
Then read `HOW_IT_WORKS.md`.
Before changing design code, make or update the active change request file and run `design-governor ci-check`.
Plain English is the default. Explain what happened in simple language first, and put technical details last unless expert mode is turned on.
If the project uses visual proof in CI, make sure the app-start step and `DESIGN_GOVERNOR_VISUAL_BASE_URL` are set before the check runs.

Cursor

Start each design task by reading `AGENTS.md` and `HOW_IT_WORKS.md`.
Keep the active change request file current while you work.
Run `design-governor ci-check` before you stop.
Default to plain-English reporting, then add technical detail only when it helps.
If visual proof is enabled, make sure CI knows how to start the app and where the base URL lives.

Claude

Read the repo rules first.
Use the active change request file as the named design boundary.
Run `design-governor ci-check` before handing work back.
Explain the result simply first, then list exact contract or rule details after that if needed.
If visual proof is enabled, verify the workflow starts the app and exports the base URL before CI checks.

Plain terminal flow

1. Create or update `design_change_request.active.json`.
2. Run `design-governor ci-check`.
3. Make only the allowed design change.
4. Run `design-governor ci-check` again before commit.
5. If visual proof is part of the project, keep the app-start command and base URL in sync with the workflow template.
6. Use `--expert` when you want the raw contract, rule, selector, and file detail.
7. Use `--json` when another tool or automation needs machine output.
