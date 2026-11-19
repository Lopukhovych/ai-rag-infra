import os

import pytest

os.environ.setdefault("CDK_DEFAULT_ACCOUNT", "123456789012")

from app import build_app


@pytest.fixture(scope="session")
def app():
    return build_app(
        {"githubUser": "octo", "alertEmail": "me@example.com", "nag": "false"}
    )


@pytest.fixture(scope="session")
def stacks(app):
    return {c.stack_name: c for c in app.node.children if hasattr(c, "stack_name")}
