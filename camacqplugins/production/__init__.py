"""Provide a plugin for production standard flow."""
import asyncio
import logging
import tempfile
from math import ceil
from pathlib import Path

import voluptuous as vol

from camacq.event import match_event
from camacq.plugins.leica.command import cam_com, del_com, gain_com
from camacq.plugins.sample.helper import next_well_xy
from camacq.util import dotdict, read_csv

_LOGGER = logging.getLogger(__name__)

SAMPLE_STATE_FILE = "state_file"

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("gain_pattern_name"): vol.Coerce(str),
        vol.Required("exp_pattern_name"): vol.Coerce(str),
        vol.Required("channels"): [
            {
                vol.Required("channel"): vol.Coerce(str),
                vol.Required("job_name"): vol.Coerce(str),
                vol.Required("detector_num"): vol.Coerce(int),
                vol.Required("default_gain"): vol.Coerce(int),
                vol.Required("max_gain"): vol.Coerce(int),
            }
        ],
        vol.Required("well_layout"): {
            vol.Required("x_fields"): vol.Coerce(int),
            vol.Required("y_fields"): vol.Coerce(int),
        },
        # pylint: disable=no-value-for-parameter
        "plot_save_path": vol.IsDir(),
        SAMPLE_STATE_FILE: vol.IsFile(),
    },
)


async def setup_module(center, config):
    """Set up production plugin."""
    conf = config["production"]
    gain_pattern = conf["gain_pattern_name"]
    exp_pattern = conf["exp_pattern_name"]
    channels = conf["channels"]
    well_layout = conf["well_layout"]
    x_fields = well_layout["x_fields"]
    y_fields = well_layout["y_fields"]
    plot_save_path = conf.get("plot_save_path")
    subscriptions = dotdict()

    state_file = conf.get(SAMPLE_STATE_FILE)
    if state_file is not None:
        x_wells = None
        y_wells = None
        await load_sample(center, state_file)
        image_next_well_on_sample(
            center, gain_pattern, subscriptions, x_fields, y_fields
        )
    else:
        x_wells = 12
        y_wells = 8
        start_exp(center)
        add_next_well(center, x_wells, y_wells, x_fields, y_fields)
        image_next_well_on_event(
            center, gain_pattern, subscriptions, x_fields, y_fields
        )

    analyze_gain(center, plot_save_path, asyncio.Lock(), x_fields, y_fields)
    set_exp_gain(center, channels)
    add_exp_job(center, channels, exp_pattern, subscriptions, x_fields, y_fields)
    subscriptions.set_img_ok = set_img_ok(center)
    subscriptions.rename_exp_image = rename_exp_image(center)
    stop_exp(center, x_wells, y_wells, x_fields, y_fields)


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


def add_next_well(center, x_wells, y_wells, x_fields, y_fields):
    """Add next well."""

    async def set_next_well(center, event):
        """Run on well event."""
        plate_name = "00"
        next_well_x, _ = next_well_xy(center.sample, plate_name, x_wells, y_wells)

        if (
            not match_event(
                event, field_x=x_fields - 1, field_y=y_fields - 1, well_img_ok=True,
            )
            or next_well_x is None
        ):
            return

        await center.actions.command.stop_imaging()

        well_x, well_y = next_well_xy(center.sample, plate_name, x_wells, y_wells)

        await center.actions.sample.set_well(
            plate_name=plate_name, well_x=well_x, well_y=well_y
        )

    center.bus.register("well_event", set_next_well)


def image_next_well_on_sample(center, gain_pattern, subscriptions, x_fields, y_fields):
    """Image next well in existing sample."""

    async def send_cam_job(center, event):
        """Run on well event."""
        plate_name = "00"
        next_well_x, next_well_y = next_well_xy(center.sample, plate_name)

        if (
            not match_event(event, event_type="camacq_start_event")
            and not match_event(
                event, field_x=x_fields - 1, field_y=y_fields - 1, well_img_ok=True,
            )
            or next_well_x is None
        ):
            return

        await send_gain_jobs(
            center,
            gain_pattern,
            next_well_x,
            next_well_y,
            x_fields,
            y_fields,
            subscriptions,
        )

    center.bus.register("camacq_start_event", send_cam_job)
    center.bus.register("well_event", send_cam_job)


def image_next_well_on_event(center, gain_pattern, subscriptions, x_fields, y_fields):
    """Image next well."""

    async def send_cam_job(center, event):
        """Run on well event."""
        if event.well.images:
            return

        await send_gain_jobs(
            center,
            gain_pattern,
            event.well.x,
            event.well.y,
            x_fields,
            y_fields,
            subscriptions,
        )

    center.bus.register("well_event", send_cam_job)


def analyze_gain(center, save_path, gain_lock, x_fields, y_fields):
    """Analyze gain."""

    async def calc_gain(center, event):
        """Calculate correct gain."""
        # TODO: Make job id and channel id configurable.
        field_x, field_y = get_last_gain_coords(x_fields, y_fields)
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

        # Guard against duplicate image events.
        async with gain_lock:
            well = center.sample.get_well(
                plate_name=event.plate_name, well_x=event.well_x, well_y=event.well_y
            )
            gain_set = any(
                channel.gain is not None for channel in well.channels.values()
            )
            if gain_set:
                return

            await center.actions.command.stop_imaging()

            nonlocal save_path
            if save_path is None:
                save_path = Path(tempfile.gettempdir()) / event.plate_name
            else:
                save_path = Path(save_path)
            if not save_path.exists():
                await center.add_executor_job(save_path.mkdir)

            # This should be a path to a base file name, not to an actual dir or file.
            save_path = save_path / f"{event.well_x}--{event.well_y}"

            await center.actions.gain.calc_gain(
                plate_name=event.plate_name,
                well_x=event.well_x,
                well_y=event.well_y,
                make_plots=True,
                save_path=save_path,
            )

    center.bus.register("image_event", calc_gain)


def set_exp_gain(center, channels):
    """Set experiment gain."""

    async def set_gain(center, event):
        """Set pmt gain."""
        for channel in channels:
            if event.channel_name != channel["channel"]:
                continue
            exp = channel["job_name"]
            num = channel["detector_num"]
            gain = min(event.gain or channel["default_gain"], channel["max_gain"])

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


def add_exp_job(center, channels, exp_pattern, subscriptions, x_fields, y_fields):
    """Add experiment job."""

    async def add_cam_job(center, event):
        """Add an experiment job to the cam list."""
        last_channel = channels[-1]
        if not match_event(event, channel_name=last_channel["channel"]) or len(
            event.well.channels
        ) != len(channels):
            return

        commands = []
        for field_x in range(x_fields):
            for field_y in range(y_fields):
                cmd = cam_com(
                    exp_pattern, event.well_x, event.well_y, field_x, field_y, 0, 0
                )
                commands.append(cmd)

        await center.actions.command.send(command=del_com())
        await center.actions.command.send_many(commands=commands)

        if subscriptions.set_img_ok is None:
            subscriptions.set_img_ok = set_img_ok(center)
        if subscriptions.rename_exp_image is None:
            subscriptions.rename_exp_image = rename_exp_image(center)

        await center.actions.command.start_imaging()
        await center.actions.command.send(command="/cmd:startcamscan")

    center.bus.register("channel_event", add_cam_job)


def set_img_ok(center):
    """Set field as imaged ok."""

    async def set_sample_img_ok(center, event):
        """Set sample field img ok."""
        if not match_event(event, job_id=6):
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
    # TODO: Make experiment pattern job_ids configurable.

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

        await center.actions.rename_image.rename_image(
            old_path=event.path, new_name=new_name
        )

    return center.bus.register("image_event", rename_image)


def stop_exp(center, x_wells, y_wells, x_fields, y_fields):
    """Trigger to stop experiment."""

    async def stop_imaging(center, event):
        """Run to stop the experiment."""
        next_well_x, _ = next_well_xy(center.sample, "00", x_wells, y_wells)
        match = match_event(
            event, field_x=x_fields - 1, field_y=y_fields - 1, well_img_ok=True,
        )

        if not match or next_well_x is not None:
            return

        await center.actions.command.stop_imaging()

    center.bus.register("well_event", stop_imaging)


def get_last_gain_coords(x_fields, y_fields):
    """Return a tuple with last gain coordinates x and y.

    The gain coordinates will be the two most centered fields.
    """
    last_x_field = ceil(x_fields / 2)
    last_y_field = ceil(y_fields / 2) - 1
    return last_x_field, last_y_field


async def send_gain_jobs(
    center, gain_job, well_x, well_y, x_fields, y_fields, subscriptions
):
    """Send gain cam jobs for the center fields of a well."""
    field_x, field_y = get_last_gain_coords(x_fields, y_fields)
    field_x = field_x - 1  # set the start x field coord

    await center.actions.command.send(command=del_com())

    for field_x in range(field_x, field_x + 2):
        command = cam_com(gain_job, well_x, well_y, field_x, field_y, 0, 0)
        await center.actions.command.send(command=command)

    if subscriptions.set_img_ok is not None:
        subscriptions.set_img_ok()
        subscriptions.set_img_ok = None
    if subscriptions.rename_exp_image is not None:
        subscriptions.rename_exp_image()
        subscriptions.rename_exp_image = None

    await center.actions.command.start_imaging()
    await center.actions.command.send(command="/cmd:startcamscan")
