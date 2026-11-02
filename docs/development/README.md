# Development

```sh
python -m pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
mypy
python -m build
```

Tests should use local peer connections or deterministic fakes. Public-browser, TURN, NAT traversal,
load, and multi-instance checks require separate infrastructure and must be reported as such.
## Local interpreter matrix

The suite passed **24 tests on each of CPython 3.11.14, 3.12.14, 3.13.15, and 3.14.7**. Ruff lint
and formatting, strict mypy, and isolated wheel/source builds also passed on each interpreter. The
peer negotiation tests run locally; they do not verify public NAT traversal, TURN, browser
interoperability, or deployment behavior.
