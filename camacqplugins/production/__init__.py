"""Provide a plugin for production standard flow."""
import asyncio
import logging

import voluptuous as vol

from camacq.plugins.sample import ACTION_TO_METHOD
from camacq.util import read_csv

_LOGGER = logging.getLogger(__name__)

SAMPLE_STATE_FILE = "state_file"


async def setup_module(center, config):
    """Set up Leica api package."""
    print("Production plugin setup!")

    conf = config["production"]
    state_file = conf.get(SAMPLE_STATE_FILE) if conf else None
    if state_file is None:
        return
    state_data = await center.add_executor_job(read_csv, state_file)
    tasks = []
    for data in state_data:
        for action_id, options in ACTION_TO_METHOD.items():
            schema = options["schema"]
            try:
                schema(data)
            except vol.Invalid as exc:
                _LOGGER.debug("Skipping action %s: %s", action_id, exc)
                continue
            tasks.append(
                center.create_task(center.actions.call("sample", action_id, **data))
            )

    if tasks:
        await asyncio.wait(tasks)
