import asyncio
from collections.abc import Sequence

import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Create an isolated event loop for async unit/e2e tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration/e2e tests that require real runtime dependencies.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "integration: requires runtime services or user configuration"
    )
    config.addinivalue_line("markers", "e2e: end-to-end runtime tests")
    config.addinivalue_line("markers", "property: property-based deterministic tests")


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: Sequence[pytest.Item],
) -> None:
    run_integration = config.getoption("--run-integration")
    skip_integration = pytest.mark.skip(
        reason="integration/e2e is opt-in; rerun with --run-integration"
    )

    for item in items:
        if "tests/e2e/" in item.nodeid:
            item.add_marker("integration")
            item.add_marker("e2e")
        if (
            item.get_closest_marker("integration") or item.get_closest_marker("e2e")
        ) and not run_integration:
            item.add_marker(skip_integration)
