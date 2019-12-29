"""Provide package level pytest fixtures."""
import pytest

from camacq.control import Center
from camacq.plugins.leica import sample as leica_sample_mod


@pytest.fixture(name="center")
def center_fixture(event_loop):
    """Give access to center via fixture."""
    _center = Center(loop=event_loop)
    _center._track_tasks = True  # pylint: disable=protected-access
    yield _center


@pytest.fixture(name="leica_sample")
async def leica_sample_fixture(center):
    """Mock leica sample."""
    await leica_sample_mod.setup_module(center, {})
