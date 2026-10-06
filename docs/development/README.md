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
