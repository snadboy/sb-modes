"""Integration tests run inside a real Home Assistant core (pytest-homeassistant-custom-component).

    uv venv .venv --python 3.14 && uv pip install --python .venv/bin/python pytest-homeassistant-custom-component
    .venv/bin/pytest

The UI dependencies are stood in for by empty modules: SB UI Kit is not part of the harness,
and frontend needs the hass_frontend package, which it does not ship. The panel's
registration is wrapped so the modes run without it; the panel itself is checked live.
"""

import pytest
from pytest_homeassistant_custom_component.common import MockModule, mock_integration


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def sb_ui_kit_stub(hass):
    for domain in ("sb_ui_kit", "frontend", "panel_custom"):
        mock_integration(hass, MockModule(domain))
    yield
