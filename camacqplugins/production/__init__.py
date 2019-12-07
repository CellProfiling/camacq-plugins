"""Provide a plugin for production standard flow."""
import logging
import tempfile
from math import ceil
from pathlib import Path

import voluptuous as vol

from camacq.const import CAMACQ_START_EVENT
from camacq.event import match_event
from camacq.plugins.leica.command import cam_com, del_com, gain_com
from camacq.plugins.sample.helper import next_well_xy
from camacq.util import read_csv

_LOGGER = logging.getLogger(__name__)

PLATE_NAME = "00"
SAMPLE_STATE_FILE = "state_file"

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("gain_pattern_name"): vol.Coerce(str),
        vol.Required("gain_job_id"): vol.Coerce(int),
        vol.Required("gain_job_channels"): vol.Coerce(int),
        vol.Required("exp_pattern_name"): vol.Coerce(str),
        vol.Required("exp_job_ids"): vol.All(
            [vol.Coerce(int)], vol.Length(min=3, max=3)
        ),
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
    flow = WorkFlow(center, conf)
    state_file = conf.get(SAMPLE_STATE_FILE)
    await flow.setup(state_file)


class WorkFlow:
    """Represent the production workflow."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, center, conf):
        """Set up instance."""
        self._center = center
        self.gain_pattern = conf["gain_pattern_name"]
        self.gain_job_id = conf["gain_job_id"]
        self.gain_job_channels = conf["gain_job_channels"]
        self.exp_pattern = conf["exp_pattern_name"]
        self.exp_job_ids = conf["exp_job_ids"]
        self.channels = conf["channels"]
        well_layout = conf["well_layout"]
        self.x_fields = well_layout["x_fields"]
        self.y_fields = well_layout["y_fields"]
        self.plot_save_path = conf.get("plot_save_path")
        self._remove_handle_exp_image = None
        self.wells_left = set()

    async def setup(self, state_file):
        """Set up the flow."""
        if state_file is not None:
            state_data = await self._center.add_executor_job(read_csv, state_file)
        else:
            x_wells = 12
            y_wells = 8
            state_data = [
                {"plate_name": PLATE_NAME, "well_x": well_x, "well_y": well_y}
                for well_x in range(x_wells)
                for well_y in range(y_wells)
            ]

        await self.load_sample(state_data)
        self.image_next_well_on_sample()
        self.analyze_gain()
        self.set_exp_gain()
        self.add_exp_job()
        self._remove_handle_exp_image = self.handle_exp_image()
        self.stop_exp()

    async def load_sample(self, state_data):
        """Load sample state."""
        self.wells_left = {(data["well_x"], data["well_y"]) for data in state_data}
        for data in state_data:
            if data["plate_name"] not in self._center.sample.plates:
                await self._center.actions.sample.set_plate(silent=True, **data)
            well_coord = int(data["well_x"]), int(data["well_y"])
            if well_coord not in self._center.sample.plates[data["plate_name"]].wells:
                await self._center.actions.sample.set_well(silent=True, **data)
            await self._center.actions.sample.set_channel(silent=True, **data)
            await self._center.actions.sample.set_field(silent=True, **data)

    def image_next_well_on_sample(self):
        """Image next well in existing sample."""

        async def send_cam_job(center, event):
            """Run on well event."""
            next_well_x, next_well_y = next_well_xy(center.sample, PLATE_NAME)

            if (
                not match_event(event, event_type=CAMACQ_START_EVENT)
                and not match_event(
                    event,
                    field_x=self.x_fields - 1,
                    field_y=self.y_fields - 1,
                    well_img_ok=True,
                )
                or next_well_x is None
                or (next_well_x, next_well_y) not in self.wells_left
            ):
                return

            if center.sample.images:
                await center.actions.command.stop_imaging()
            await self.send_gain_jobs(
                next_well_x, next_well_y,
            )
            self.wells_left.pop((next_well_x, next_well_y))

        removes = []
        removes.append(self._center.bus.register(CAMACQ_START_EVENT, send_cam_job))
        removes.append(self._center.bus.register("well_event", send_cam_job))

        def remove_callback():
            """Remove all registered listeners of this method."""
            for remove in removes:
                remove()
            removes.clear()

        return remove_callback

    def analyze_gain(self):
        """Analyze gain."""

        async def calc_gain(center, event):
            """Calculate correct gain."""
            field_x, field_y = get_last_gain_coords(self.x_fields, self.y_fields)
            channel_id = self.gain_job_channels - 1
            if not match_event(
                event,
                field_x=field_x,
                field_y=field_y,
                job_id=self.gain_job_id,
                channel_id=channel_id,
            ):
                return

            await center.actions.command.stop_imaging()

            if self.plot_save_path is None:
                save_path = Path(tempfile.gettempdir()) / event.plate_name
            else:
                save_path = Path(self.plot_save_path)
            if not save_path.exists():
                await center.add_executor_job(save_path.mkdir)

            # This should be a path to a base file name, not to a dir or file.
            save_path = save_path / f"{event.well_x}--{event.well_y}"

            await center.actions.gain.calc_gain(
                plate_name=event.plate_name,
                well_x=event.well_x,
                well_y=event.well_y,
                make_plots=True,
                save_path=save_path,
            )

        return self._center.bus.register("image_event", calc_gain)

    def set_exp_gain(self):
        """Set experiment gain."""

        async def set_gain(center, event):
            """Set pmt gain."""
            channel = next(
                (
                    channel
                    for channel in self.channels
                    if event.channel_name == channel["channel"]
                )
            )
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

        return self._center.bus.register("gain_calc_event", set_gain)

    def add_exp_job(self):
        """Add experiment job."""

        async def add_cam_job(center, event):
            """Add an experiment job to the cam list."""
            last_channel = self.channels[-1]
            if not match_event(event, channel_name=last_channel["channel"]) or len(
                event.well.channels
            ) != len(self.channels):
                return

            commands = []
            for field_x in range(self.x_fields):
                for field_y in range(self.y_fields):
                    cmd = cam_com(
                        self.exp_pattern,
                        event.well_x,
                        event.well_y,
                        field_x,
                        field_y,
                        0,
                        0,
                    )
                    commands.append(cmd)

            await center.actions.command.send(command=del_com())
            await center.actions.command.send_many(commands=commands)

            if self._remove_handle_exp_image is None:
                self._remove_handle_exp_image = self.handle_exp_image()

            await center.actions.command.start_imaging()
            await center.actions.command.send(command="/cmd:startcamscan")

        return self._center.bus.register("channel_event", add_cam_job)

    def handle_exp_image(self):
        """Handle experiment image."""

        async def on_exp_image(center, event):
            """Run on experiment image event."""
            await self.rename_image(center, event)
            await self.set_sample_img_ok(center, event)

        return self._center.bus.register("image_event", on_exp_image)

    def stop_exp(self):
        """Trigger to stop experiment."""

        async def stop_imaging(center, event):
            """Run to stop the experiment."""
            match = match_event(
                event,
                field_x=self.x_fields - 1,
                field_y=self.y_fields - 1,
                well_img_ok=True,
            )

            if not match or self.wells_left:
                return

            await center.actions.command.stop_imaging()

            _LOGGER.info("Congratulations, experiment is finished!")

        return self._center.bus.register("well_event", stop_imaging)

    async def send_gain_jobs(self, well_x, well_y):
        """Send gain cam jobs for the center fields of a well."""
        field_x, field_y = get_last_gain_coords(self.x_fields, self.y_fields)
        field_x = field_x - 1  # set the start x field coord

        await self._center.actions.command.send(command=del_com())

        for field_x in range(field_x, field_x + 2):
            command = cam_com(self.gain_pattern, well_x, well_y, field_x, field_y, 0, 0)
            await self._center.actions.command.send(command=command)

        if self._remove_handle_exp_image is not None:
            self._remove_handle_exp_image()
            self._remove_handle_exp_image = None

        await self._center.actions.command.start_imaging()
        await self._center.actions.command.send(command="/cmd:startcamscan")

    async def rename_image(self, center, event):
        """Rename an image."""
        if event.job_id not in self.exp_job_ids or event.channel_id not in (0, 1):
            return

        if event.job_id == self.exp_job_ids[0]:
            channel_id = 0
        elif event.job_id == self.exp_job_ids[1] and event.channel_id == 0:
            channel_id = 1
        elif event.job_id == self.exp_job_ids[1] and event.channel_id == 1:
            channel_id = 2
        elif event.job_id == self.exp_job_ids[2]:
            channel_id = 3

        new_name = (
            f"U{event.well_x:02}--V{event.well_y:02}--E{event.job_id:02}--"
            f"X{event.field_x:02}--Y{event.field_y:02}--"
            f"Z{event.z_slice:02}--C{channel_id:02}.ome.tif"
        )

        await center.actions.rename_image.rename_image(
            old_path=event.path, new_name=new_name
        )

    async def set_sample_img_ok(self, center, event):
        """Set sample field img ok."""
        if not match_event(event, job_id=self.exp_job_ids[-1]):
            return

        await center.actions.sample.set_field(
            plate_name=event.plate_name,
            well_x=event.well_x,
            well_y=event.well_y,
            field_x=event.field_x,
            field_y=event.field_y,
            img_ok=True,
        )


def get_last_gain_coords(x_fields, y_fields):
    """Return a tuple with last gain coordinates x and y.

    The gain coordinates will be the two most centered fields.
    """
    last_x_field = ceil(x_fields / 2)
    last_y_field = ceil(y_fields / 2) - 1
    return last_x_field, last_y_field
