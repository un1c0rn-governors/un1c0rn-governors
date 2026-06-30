Design Governor Testing

This package keeps its proof story in the public repo.
The PyPI install is the runtime tool.
The repo is where the full confidence suite lives.

Run the full suite with:

```bash
python -m playwright install --with-deps chromium
python -m unittest discover -s tests -p "test_*.py"
```

The browser install is required for the visual proof tests.

What the suite proves:

- Bootstrap and setup:
  `tests/test_bootstrap.py`
  Proves `init` writes the full starter set.

- Request scope and named contract law:
  `tests/test_release_hardening.py`
  `tests/test_ci_workflow.py`
  Proves empty-touch requests block, unknown contracts block, and installable `ci-check` behavior is enforced.

- Policy checks:
  `tests/test_directive_02_policy_rules.py`
  Proves selector-style rules, file-contains rules, and html-count rules fail and pass correctly.

- Visual proof and stale lock blocking:
  `tests/test_directive_03_visual_proofs.py`
  Proves governed screenshots are captured and later drift marks locked proof stale.

- Surface adapters:
  `tests/test_directive_05_surface_adapters.py`
  Proves TSX, Vue, and Svelte surface extraction works for the supported cases, and strict mode blocks unsupported files.

- Release hardening:
  `tests/test_release_hardening.py`
  Proves the shipped workflow and project template match the release contract.

Why the filenames still mention directives:

The suite was built in governed stages.
The public test map above is the release-facing view.
The filenames keep the implementation history, while this document tells you what each test proves about the product.
