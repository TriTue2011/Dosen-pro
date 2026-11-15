import pytest


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield
