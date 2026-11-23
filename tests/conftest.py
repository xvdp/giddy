"""No VS Code command line in tests: gaffyview.installed() returns None unless a test sets GIDDY_CODE."""
import pytest


@pytest.fixture(autouse=True)
def _no_code_cli(monkeypatch):
    monkeypatch.setenv("GIDDY_CODE", "giddy-test-no-code-cli")
