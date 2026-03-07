"""Provide package level pytest fixtures."""

import asyncio

from camacq.control import Center
from camacq.plugins.leica import sample as leica_sample_mod
import pytest


@pytest.fixture(name="center")
async def center_fixture() -> Center:
    """Give access to center via fixture."""
    _center = Center(loop=asyncio.get_running_loop())
    _center._track_tasks = True
    return _center


@pytest.fixture(name="leica_sample")
async def leica_sample_fixture(center: Center) -> None:
    """Mock leica sample."""
    await leica_sample_mod.setup_module(center, {})
