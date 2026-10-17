"""Integration tests run inside a real Home Assistant core (pytest-homeassistant-custom-component).

    uv venv .venv --python 3.14 && uv pip install --python .venv/bin/python pytest-homeassistant-custom-component
    .venv/bin/pytest
"""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
