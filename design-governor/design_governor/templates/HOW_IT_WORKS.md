How It Works

You ask for a change.
That request gets turned into a small request file.
The request names the design contracts that are allowed to move.
It also lists the files, selectors, tokens, or named surfaces that are likely to be touched.

The governor reads that request and compares it to the contract registry.
It rebuilds the impact map from the contracts.
It looks up which contracts are tied to those files, selectors, tokens, and surfaces.
It also checks the real locked files on disk against their approved proof.
It only accepts file paths that stay inside the project folder.
It hashes the real file state and the real page copy it finds, not just labels.
For CSS, HTML, Vue, and Svelte, it reads governed structure so spacing-only cleanup does not count as drift.
JSX or TSX support is basic and best-effort while that adapter path is still being hardened.
If a contract includes visual proof specs, it can also open the route, wait for the named state, capture a governed screenshot, and compare it to the last approved image.
If that visual proof runs in CI, the app still has to be running and the base URL still has to be known.
The generated workflow gives you a starter place to set both of those.
If strict surface mode is on and the contract points at an unsupported file kind, it blocks instead of pretending the proof is real.

If the request only touches named contracts and the locked proof is still clean, it passes.
If it would touch a locked contract that was not named, it blocks and reports.
If a locked area no longer matches its approved proof, it marks that lock stale and blocks.

The default way it explains those results is plain English first.
If you want the raw rule ids, selectors, and file details, run it in expert mode.

That means approved design choices become stored law.
They are not left to memory.

The flow is:

1. Request.
2. Bot check.
3. Gate start.
4. Edit only if allowed.
5. Gate finish.
6. Approval.
7. Lock or snapshot with real proof.
8. Save the approved visual proof artifacts with the snapshot when that contract requires them.

Glossary

Design contract
A named part of the site, like a hero section or pricing card.

Lock
A rule that says an approved part should not change unless you name it first.

Proof
A saved receipt of what the approved design looked like or contained.

Change request
A small file that says what you want to change before the AI starts editing.

Gate
A checkpoint before and after edits that makes sure the AI stayed inside the allowed area.

Chromium
The browser engine Design Governor uses to take screenshots for visual proof. Think of it as the camera.

CI
An automated check that runs before code is merged. Think of it as a robot reviewer.
