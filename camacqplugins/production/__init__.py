"""Provide a plugin for production standard flow."""
import asyncio
import logging

from camacq.event import match_event
from camacq.plugins.sample.helper import next_well_xy
from camacq.util import read_csv

_LOGGER = logging.getLogger(__name__)

SAMPLE_STATE_FILE = "state_file"


async def setup_module(center, config):
    """Set up Leica api package."""
    print("Production plugin setup!")

    conf = config["production"]
    state_file = conf.get(SAMPLE_STATE_FILE) if conf else None
    if state_file is not None:
        await load_sample(center, state_file)
    else:
        start_exp(center)

    add_next_well(center)


async def load_sample(center, state_file):
    """Load sample state from file."""
    state_data = await center.add_executor_job(read_csv, state_file)
    tasks = []
    for data in state_data:
        for action in center.actions.sample.values():
            tasks.append(center.create_task(action(silent=True, **data)))

    if tasks:
        await asyncio.wait(tasks)


def start_exp(center):
    """Trigger on start experiment."""

    async def start(center, event):
        """Run on start event."""
        await center.actions.sample.set_well(plate_name="00", well_x=0, well_y=0)

    center.bus.register("camacq_start_event", start)


def add_next_well(center):
    """Add next well."""

    async def well_event(center, event):
        """Run on well event."""
        if not match_event(event, field_x=1, field_y=2, well_img_ok=True):
            return

        # TODO: Make plate and well coordinates configurable.
        plate_name = "00"
        well_x, well_y = next_well_xy(center.sample, plate_name, x_wells=12, y_wells=8)

        await center.actions.sample.set_well(
            plate_name=plate_name, well_x=well_x, well_y=well_y
        )

    center.bus.register("well_event", well_event)
