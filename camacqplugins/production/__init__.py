"""Provide a plugin for production standard flow."""
import asyncio
import logging
import tempfile
from pathlib import Path

from camacq.event import match_event
from camacq.plugins.leica.command import cam_com, del_com, gain_com
from camacq.plugins.sample.helper import next_well_xy
from camacq.util import read_csv

_LOGGER = logging.getLogger(__name__)

SAMPLE_STATE_FILE = "state_file"
START_STOP_DELAY = 2.0


async def setup_module(center, config):
    """Set up production plugin."""
    print("Production plugin setup!")

    conf = config["production"]
    state_file = conf.get(SAMPLE_STATE_FILE) if conf else None
    if state_file is not None:
        await load_sample(center, state_file)
        image_next_well_on_sample(center)
    else:
        start_exp(center)
        add_next_well(center)
        image_next_well_on_event(center)

    analyze_gain(center)
    set_exp_gain(center)
    add_exp_job(center)
    set_img_ok(center)
    rename_exp_image(center)
    stop_exp(center)


async def load_sample(center, state_file):
    """Load sample state from file."""
    state_data = await center.add_executor_job(read_csv, state_file)
    for data in state_data:
        await center.actions.sample.set_plate(silent=True, **data)
        await center.actions.sample.set_well(silent=True, **data)
        await center.actions.sample.set_channel(silent=True, **data)
        await center.actions.sample.set_field(silent=True, **data)


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
        next_well_x, _ = next_well_xy(center.sample, plate_name, x_wells, y_wells)

        if (
            not match_event(event, field_x=1, field_y=2, well_img_ok=True)
            or next_well_x is None
        ):
            return

        await asyncio.sleep(START_STOP_DELAY)
        await center.actions.command.stop_imaging()
        await asyncio.sleep(2 * START_STOP_DELAY)

        well_x, well_y = next_well_xy(center.sample, plate_name, x_wells, y_wells)

        await center.actions.sample.set_well(
            plate_name=plate_name, well_x=well_x, well_y=well_y
        )

    center.bus.register("well_event", set_next_well)


def image_next_well_on_sample(center):
    """Image next well in existing sample."""

    async def send_cam_job(center, event):
        """Run on well event."""
        # TODO: Make stop field coordinates configurable.
        plate_name = "00"
        next_well_x, next_well_y = next_well_xy(center.sample, plate_name)

        if (
            not match_event(event, event_type="camacq_start_event")
            and not match_event(event, field_x=1, field_y=2, well_img_ok=True)
            or next_well_x is None
        ):
            return

        await center.actions.command.send(command=del_com())
        # TODO: Make exp job and field coordinates configurable.
        command = cam_com("p10xgain", next_well_x, next_well_y, 0, 1, 0, 0)
        await center.actions.command.send(command=command)
        command = cam_com("p10xgain", next_well_x, next_well_y, 1, 1, 0, 0)
        await center.actions.command.send(command=command)

        # TODO: Unregister rename image and set img ok.

        await center.actions.command.start_imaging()
        await asyncio.sleep(START_STOP_DELAY)
        await center.actions.command.send(command="/cmd:startcamscan")

    center.bus.register("camacq_start_event", send_cam_job)
    center.bus.register("well_event", send_cam_job)


def image_next_well_on_event(center):
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
        await asyncio.sleep(START_STOP_DELAY)
        await center.actions.command.send(command="/cmd:startcamscan")

    center.bus.register("well_event", send_cam_job)


def analyze_gain(center):
    """Analyze gain."""

    async def calc_gain(center, event):
        """Calculate correct gain."""
        # TODO: Make event field coordinates, job id and save_path configurable.
        field_x = 1
        field_y = 1
        job_id = 3
        channel_id = 31
        if not match_event(
            event,
            field_x=field_x,
            field_y=field_y,
            job_id=job_id,
            channel_id=channel_id,
        ):
            return

        await asyncio.sleep(START_STOP_DELAY)
        await center.actions.command.stop_imaging()
        await asyncio.sleep(START_STOP_DELAY)

        # This should be a path to a base file name, not to an actual dir or file.
        save_path = (
            Path(tempfile.gettempdir())
            / event.plate_name
            / f"{event.well_x}--{event.well_y}"
        )

        # FIXME: Adjust the action type for plugins.gain to avoid period in the name.
        await center.actions.plugins.gain.calc_gain(
            plate_name=event.plate_name,
            well_x=event.well_x,
            well_y=event.well_y,
            make_plots=True,
            save_path=save_path,
        )

    center.bus.register("image_event", calc_gain)


def set_exp_gain(center):
    """Set experiment gain."""

    async def set_gain(center, event):
        """Set pmt gain."""
        # TODO:  Make exp job names configurable.
        exp_job_1 = "exp_job_1"
        exp_job_2 = "exp_job_2"
        exp_job_3 = "exp_job_3"

        if event.channel_name == "green":
            exp = exp_job_1
            num = 1
            gain = min(event.gain or 800, 800)
        elif event.channel_name == "blue":
            exp = exp_job_2
            num = 1
            gain = min(event.gain or 505, 610)
        elif event.channel_name == "yellow":
            exp = exp_job_2
            num = 2
            gain = min(event.gain or 655, 760)
        elif event.channel_name == "red":
            exp = exp_job_3
            num = 2
            gain = event.gain or 630
            gain = min(gain + 25, 735)

        command = gain_com(exp=exp, num=num, value=gain)

        # Set the gain at the microscope.
        await center.actions.command.send(command=command)
        # Set the gain in the sample state.
        await center.actions.sample.set_channel(
            plate_name=event.plate_name,
            well_x=event.well_x,
            well_y=event.well_y,
            channel_name=event.channel_name,
            gain=gain,
        )

    center.bus.register("gain_calc_event", set_gain)


def add_exp_job(center):
    """Add experiment job."""

    async def add_cam_job(center, event):
        """Add an experiment job to the cam list."""
        # TODO: Make channels layout configurable.
        if not match_event(event, channel_name="red") or len(event.well.channels) != 4:
            return

        # TODO: Make well layout configurable.
        commands = []
        for field_x in range(2):
            for field_y in range(3):
                cmd = cam_com(
                    "p10xexp", event.well_x, event.well_y, field_x, field_y, 0, 0
                )
                commands.append(cmd)

        await center.actions.command.send(command=del_com())
        await center.actions.command.send_many(commands=commands)

        # TODO: Turn on rename image and set_img_ok during experiment job phase.

        await center.actions.command.start_imaging()
        await center.actions.command.send(command="/cmd:startcamscan")

    center.bus.register("channel_event", add_cam_job)


def set_img_ok(center):
    """Set field as imaged ok."""

    async def set_sample_img_ok(center, event):
        """Set sample field img ok."""
        if not match_event(event, job_id=5):
            return

        await center.actions.sample.set_field(
            plate_name=event.plate_name,
            well_x=event.well_x,
            well_y=event.well_y,
            field_x=event.field_x,
            field_y=event.field_y,
            img_ok=True,
        )

    return center.bus.register("image_event", set_sample_img_ok)


def rename_exp_image(center):
    """Rename an experiment image."""

    async def rename_image(center, event):
        """Rename an image."""
        if event.job_id not in (3, 4, 6):
            return

        if event.job_id == 3:
            channel_id = event.channel_id
        elif event.job_id == 4 and event.channel_id == 0:
            channel_id = 1
        elif event.job_id == 4 and event.channel_id == 1:
            channel_id = 2
        elif event.job_id == 6:
            channel_id = 3

        new_name = (
            f"U{event.well_x:03}--V{event.well_y}--E{event.job_id}--X{event.field_x}"
            f"--Y{event.field_y}--Z{event.z_slice}--C{channel_id}.ome.tif"
        )

        center.actions.rename(old_path=event.path, new_name=new_name)

    center.bus.register("image_event", rename_image)


def stop_exp(center):
    """Trigger to stop experiment."""

    async def stop_imaging(center, event):
        """Run to stop the experiment."""
        # TODO: Make well layout and stop field coordinates configurable.
        next_well_x, _ = next_well_xy(center.sample, "00", 12, 8)

        if (
            not match_event(event, field_x=1, field_y=2, well_img_ok=True)
            or next_well_x is not None
        ):
            return

        # Sleep to let images be completely scanned before stopping.
        await asyncio.sleep(START_STOP_DELAY)
        await center.actions.api.stop_imaging()

    center.bus.register("well_event", stop_imaging)
