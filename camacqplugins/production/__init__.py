"""Provide a plugin for production standard flow."""
import asyncio
import logging

from camacq.event import match_event
from camacq.plugins.leica.command import cam_com, del_com
from camacq.plugins.sample.helper import next_well_xy
from camacq.util import read_csv

_LOGGER = logging.getLogger(__name__)

SAMPLE_STATE_FILE = "state_file"


async def setup_module(center, config):
    """Set up production plugin."""
    print("Production plugin setup!")

    conf = config["production"]
    state_file = conf.get(SAMPLE_STATE_FILE) if conf else None
    if state_file is not None:
        await load_sample(center, state_file)
    else:
        start_exp(center)

    add_next_well(center)
    image_next_well(center)
    stop_exp(center)


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

    async def set_start_well(center, event):
        """Run on start event."""
        await center.actions.sample.set_well(plate_name="00", well_x=0, well_y=0)

    center.bus.register("camacq_start_event", set_start_well)


def add_next_well(center):
    """Add next well."""

    async def set_next_well(center, event):
        """Run on well event."""
        # TODO: Make well layout and stop field coordinates configurable.
        plate_name = "00"
        x_wells = 12
        y_wells = 8
        next_well_x, _ = next_well_xy(plate_name, x_wells, y_wells)

        if (
            not match_event(event, field_x=1, field_y=2, well_img_ok=True)
            or next_well_x is None
        ):
            return

        await asyncio.sleep(2.0)
        await center.actions.command.stop_imaging()
        await asyncio.sleep(4.0)

        well_x, well_y = next_well_xy(
            center.sample, plate_name, x_wells=x_wells, y_wells=y_wells
        )

        await center.actions.sample.set_well(
            plate_name=plate_name, well_x=well_x, well_y=well_y
        )

    center.bus.register("well_event", set_next_well)


def image_next_well(center):
    """Image next well."""

    async def send_cam_job(center, event):
        """Run on well event."""
        if event.well.images:
            return

        await center.actions.command.send(command=del_com())
        # TODO: Make exp job and field coordinates configurable.
        command = cam_com("p10xgain", event.well.x, event.well.y, 0, 1, 0, 0)
        await center.actions.command.send(command=command)
        command = cam_com("p10xgain", event.well.x, event.well.y, 1, 1, 0, 0)
        await center.actions.command.send(command=command)

        # TODO: Unregister rename image and set img ok.

        await center.actions.command.start_imaging()
        await asyncio.sleep(2.0)
        await center.actions.command.send(command="/cmd:startcamscan")

    center.bus.register("well_event", send_cam_job)


def stop_exp(center):
    """Trigger to stop experiment."""

    async def stop_imaging(center, event):
        """Run to stop the experiment."""
        # TODO: Make well layout and stop field coordinates configurable.
        next_well_x, _ = next_well_xy("00", 12, 8)

        if (
            not match_event(event, field_x=1, field_y=2, well_img_ok=True)
            or next_well_x is not None
        ):
            return

        # Sleep to let images be completely scanned before stopping.
        await asyncio.sleep(2.0)
        await center.actions.api.stop_imaging()

    center.bus.register("well_event", stop_imaging)
