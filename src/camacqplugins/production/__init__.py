"""Provide a plugin for production standard flow."""

from collections.abc import Callable
import logging
from math import ceil
from typing import Any, TypedDict

from camacq.const import CAMACQ_START_EVENT, IMAGE_EVENT
from camacq.control import Center
from camacq.event import Event, match_event
from camacq.plugins.leica.command import cam_com, del_com, gain_com
from camacq.plugins.leica.sample import (
    CHANNEL_EVENT,
    SET_SAMPLE_SCHEMA,
    WELL_EVENT,
    next_well_xy,
)
from camacq.plugins.sample import get_matched_samples
import pandas as pd
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

CONF_GAIN_PATTERN_NAME = "gain_pattern_name"
CONF_GAIN_JOB_ID = "gain_job_id"
CONF_GAIN_JOB_CHANNELS = "gain_job_channels"
CONF_EXP_PATTERN_NAME = "exp_pattern_name"
CONF_EXP_JOB_IDS = "exp_job_ids"
CONF_CHANNELS = "channels"
CONF_CHANNEL = "channel"
CONF_JOB_NAME = "job_name"
CONF_DETECTOR_NUM = "detector_num"
CONF_DEFAULT_GAIN = "default_gain"
CONF_MAX_GAIN = "max_gain"
CONF_WELL_LAYOUT = "well_layout"
CONF_X_FIELDS = "x_fields"
CONF_Y_FIELDS = "y_fields"
CONF_SAMPLE_STATE_FILE = "sample_state_file"

PLATE_NAME = "00"
SAMPLE_PLATE_NAME = "plate_name"
SAMPLE_WELL_X = "well_x"
SAMPLE_WELL_Y = "well_y"


def read_csv(path: str) -> list[dict[str, str]]:
    """Return a list where each item is a row and dict."""
    try:
        data = pd.read_csv(path, dtype=str)
        data = data.fillna(value="")
    except Exception as exc:  # pylint: disable=broad-except
        _LOGGER.error("Failed to read csv file: %s", exc)
        raise vol.Invalid("Failed to read csv file") from exc
    return data.to_dict(orient="records")  # type: ignore[return-value]


@vol.truth
def is_csv(value: str) -> bool:
    """Return true if value ends with .csv."""
    return value.endswith(".csv")


def is_sample_state(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate state data.

    At least one sample action must validate per sample data item.
    """
    schemas = list(SET_SAMPLE_SCHEMA.validators)
    for idx, data in enumerate(value):
        valid = False
        error = None
        sample_name = data.get("name")
        for schema in schemas:
            if (
                schema.schema["name"] in ("plate", "image")
                or schema.schema["name"] != sample_name
            ):
                continue
            try:
                data.update(schema(data))
            except vol.Invalid as exc:
                error = exc
                continue
            valid = True
            break

        if not valid:
            _LOGGER.error(
                "The sample state file contains invalid data at row %s: %s",
                idx + 2,
                error,
            )
            if error:
                raise error
            raise vol.Invalid("Invalid sample state file")

    return value


CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_GAIN_PATTERN_NAME): vol.Coerce(str),
        vol.Required(CONF_GAIN_JOB_ID): vol.Coerce(int),
        vol.Required(CONF_GAIN_JOB_CHANNELS): vol.Coerce(int),
        vol.Required(CONF_EXP_PATTERN_NAME): vol.Coerce(str),
        vol.Required(CONF_EXP_JOB_IDS): vol.All(
            [vol.Coerce(int)], vol.Length(min=3, max=3)
        ),
        vol.Required(CONF_CHANNELS): [
            {
                vol.Required(CONF_CHANNEL): vol.Coerce(str),
                vol.Required(CONF_JOB_NAME): vol.Coerce(str),
                vol.Required(CONF_DETECTOR_NUM): vol.Coerce(int),
                vol.Required(CONF_DEFAULT_GAIN): vol.Coerce(int),
                vol.Required(CONF_MAX_GAIN): vol.Coerce(int),
            }
        ],
        vol.Required(CONF_WELL_LAYOUT): {
            vol.Required(CONF_X_FIELDS): vol.Coerce(int),
            vol.Required(CONF_Y_FIELDS): vol.Coerce(int),
        },
        # pylint: disable=no-value-for-parameter
        CONF_SAMPLE_STATE_FILE: vol.All(
            vol.IsFile(), is_csv, read_csv, is_sample_state
        ),
    },
)


class SampleData(TypedDict, total=False):
    """Type for sample data."""

    name: str
    plate_name: str
    well_x: int
    well_y: int
    field_x: int
    field_y: int


async def setup_module(center: Center, config: dict[str, Any]) -> None:
    """Set up production plugin."""
    conf = config["production"]
    flow = WorkFlow(center, conf)
    state_data: list[SampleData] | None = conf.get(CONF_SAMPLE_STATE_FILE)
    if state_data is None:
        x_wells = 12
        y_wells = 8
        state_data = [
            SampleData(
                plate_name=PLATE_NAME,
                well_x=well_x,
                well_y=well_y,
            )
            for well_x in range(x_wells)
            for well_y in range(y_wells)
        ]
    await flow.setup(state_data)


# Channel configuration dictionary type
ChannelConfig = dict[str, str | int]


class WorkFlow:
    """Represent the production workflow."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, center: Center, conf: dict[str, Any]) -> None:
        """Set up instance."""
        self._center = center
        self.gain_pattern: str = conf[CONF_GAIN_PATTERN_NAME]
        self.gain_job_id: int = conf[CONF_GAIN_JOB_ID]
        self.gain_job_channels: int = conf[CONF_GAIN_JOB_CHANNELS]
        self.exp_pattern: str = conf[CONF_EXP_PATTERN_NAME]
        self.exp_job_ids: list[int] = conf[CONF_EXP_JOB_IDS]
        self.channels: list[ChannelConfig] = conf[CONF_CHANNELS]
        well_layout: dict[str, int] = conf[CONF_WELL_LAYOUT]
        self.x_fields: int = well_layout[CONF_X_FIELDS]
        self.y_fields: int = well_layout[CONF_Y_FIELDS]
        self._remove_handle_exp_image: Callable[[], None] | None = None
        self.wells_left: set[tuple[int, int]] = set()

    async def setup(self, state_data: list[SampleData]) -> None:
        """Set up the flow."""
        await self.load_sample(state_data)
        self.image_next_well_on_sample()
        self.analyze_gain()
        self.set_exp_gain()
        self.add_exp_job()
        self._remove_handle_exp_image = self.handle_exp_image()
        self.stop_exp()

    async def load_sample(self, state_data: list[SampleData]) -> None:
        """Load sample state."""
        for data in state_data:
            well_coord = data["well_x"], data["well_y"]
            self.wells_left.add(well_coord)
            await self._center.actions.sample.set_sample(silent=True, **data)

    def image_next_well_on_sample(self) -> Callable[[], None]:
        """Image next well in existing sample."""

        async def send_cam_job(center: Center, event: Event) -> None:
            """Run on well event."""
            next_well_x, next_well_y = next_well_xy(
                center.samples.leica,  # type: ignore[arg-type]
                PLATE_NAME,
            )

            if (
                (
                    not match_event(event, event_type=CAMACQ_START_EVENT)
                    and not match_event(
                        event,
                        field_x=self.x_fields - 1,
                        field_y=self.y_fields - 1,
                        well_img_ok=True,
                    )
                )
                or next_well_x is None
                or next_well_y is None
                or (next_well_x, next_well_y) not in self.wells_left
            ):
                return

            if center.samples.leica.images:
                await center.actions.command.stop_imaging()
            await self.send_gain_jobs(
                next_well_x,
                next_well_y,
            )
            self.wells_left.remove((next_well_x, next_well_y))

        removes = []
        removes.append(self._center.bus.register(CAMACQ_START_EVENT, send_cam_job))
        removes.append(self._center.bus.register(WELL_EVENT, send_cam_job))

        def remove_callback() -> None:
            """Remove all registered listeners of this method."""
            for remove in removes:
                remove()
            removes.clear()

        return remove_callback

    def analyze_gain(self) -> Callable[[], None]:
        """Analyze gain."""

        async def calc_gain(center: Center, event: Event) -> None:
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
            await center.actions.gain.calc_gain(
                plate_name=event.plate_name,  # type: ignore[attr-defined]
                well_x=event.well_x,  # type: ignore[attr-defined]
                well_y=event.well_y,  # type: ignore[attr-defined]
            )

        return self._center.bus.register(IMAGE_EVENT, calc_gain)

    def set_exp_gain(self) -> Callable[[], None]:
        """Set experiment gain."""

        async def set_gain(center: Center, event: Event) -> None:
            """Set pmt gain."""
            channel_id, channel = next(
                (
                    (channel_id, channel)
                    for channel_id, channel in enumerate(self.channels)
                    if event.channel_name == channel[CONF_CHANNEL]  # type: ignore[attr-defined]
                )
            )
            exp = str(channel[CONF_JOB_NAME])
            num = int(channel[CONF_DETECTOR_NUM])
            event_gain: int = event.gain or int(channel[CONF_DEFAULT_GAIN])  # type: ignore[attr-defined]
            gain = min(event_gain, int(channel[CONF_MAX_GAIN]))

            command = gain_com(exp=exp, num=num, value=gain)

            # Set the gain at the microscope.
            await center.actions.command.send(command=command)
            # Set the gain in the sample state.
            await center.actions.sample.set_sample(
                name="channel",
                plate_name=event.plate_name,  # type: ignore[attr-defined]
                well_x=event.well_x,  # type: ignore[attr-defined]
                well_y=event.well_y,  # type: ignore[attr-defined]
                channel_id=channel_id,
                values={"channel_name": event.channel_name, "gain": gain},  # type: ignore[attr-defined]
            )

        return self._center.bus.register("gain_calc_event", set_gain)

    def add_exp_job(self) -> Callable[[], None]:
        """Add experiment job."""

        async def add_cam_job(center: Center, event: Event) -> None:
            """Add an experiment job to the cam list."""
            last_channel = self.channels[-1]
            channels = get_matched_samples(
                center.samples.leica,
                "channel",
                {
                    "plate_name": event.plate_name,  # type: ignore[attr-defined]
                    "well_x": event.well_x,  # type: ignore[attr-defined]
                    "well_y": event.well_y,  # type: ignore[attr-defined]
                },
            )
            if not match_event(event, channel_name=last_channel[CONF_CHANNEL]) or len(
                channels
            ) != len(self.channels):
                return

            commands = []
            for field_x in range(self.x_fields):
                for field_y in range(self.y_fields):
                    cmd = cam_com(
                        self.exp_pattern,
                        event.well_x,  # type: ignore[attr-defined]
                        event.well_y,  # type: ignore[attr-defined]
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

        return self._center.bus.register(CHANNEL_EVENT, add_cam_job)

    def handle_exp_image(self) -> Callable[[], None]:
        """Handle experiment image."""

        async def on_exp_image(center: Center, event: Event) -> None:
            """Run on experiment image event."""
            await self.rename_image(center, event)
            await self.set_sample_img_ok(center, event)

        return self._center.bus.register(IMAGE_EVENT, on_exp_image)

    def stop_exp(self) -> Callable[[], None]:
        """Trigger to stop experiment."""

        async def stop_imaging(center: Center, event: Event) -> None:
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

        return self._center.bus.register(WELL_EVENT, stop_imaging)

    async def send_gain_jobs(self, well_x: int, well_y: int) -> None:
        """Send gain cam jobs for the center fields of a well."""
        field_x, field_y = get_last_gain_coords(self.x_fields, self.y_fields)
        field_x = field_x - 1  # set the start x field coord

        await self._center.actions.command.send(command=del_com())

        for field_x_ in range(field_x, field_x + 2):
            command = cam_com(
                self.gain_pattern, well_x, well_y, field_x_, field_y, 0, 0
            )
            await self._center.actions.command.send(command=command)

        if self._remove_handle_exp_image is not None:
            self._remove_handle_exp_image()
            self._remove_handle_exp_image = None

        await self._center.actions.command.start_imaging()
        await self._center.actions.command.send(command="/cmd:startcamscan")

    async def rename_image(self, center: Center, event: Event) -> None:
        """Rename an image."""
        if (
            event.job_id not in self.exp_job_ids  # type: ignore[attr-defined]
            or event.channel_id not in (0, 1)  # type: ignore[attr-defined]
        ):
            return

        channel_id: int
        if event.job_id == self.exp_job_ids[0]:  # type: ignore[attr-defined]
            channel_id = 0
        elif (
            event.job_id == self.exp_job_ids[1]  # type: ignore[attr-defined]
            and event.channel_id == 0  # type: ignore[attr-defined]
        ):
            channel_id = 1
        elif (
            event.job_id == self.exp_job_ids[1]  # type: ignore[attr-defined]
            and event.channel_id == 1  # type: ignore[attr-defined]
        ):
            channel_id = 2
        elif event.job_id == self.exp_job_ids[2]:  # type: ignore[attr-defined]
            channel_id = 3
        else:
            return

        new_name = (
            f"U{event.well_x:02}--V{event.well_y:02}--E{event.job_id:02}--"  # type: ignore[attr-defined]
            f"X{event.field_x:02}--Y{event.field_y:02}--"  # type: ignore[attr-defined]
            f"Z{event.z_slice:02}--C{channel_id:02}.ome.tif"  # type: ignore[attr-defined]
        )

        await center.actions.rename_image.rename_image(
            old_path=event.path,  # type: ignore[attr-defined]
            new_name=new_name,
        )

    async def set_sample_img_ok(self, center: Center, event: Event) -> None:
        """Set sample field img ok."""
        if not match_event(event, job_id=self.exp_job_ids[-1]):
            return

        await center.actions.sample.set_sample(
            name="field",
            plate_name=event.plate_name,  # type: ignore[attr-defined]
            well_x=event.well_x,  # type: ignore[attr-defined]
            well_y=event.well_y,  # type: ignore[attr-defined]
            field_x=event.field_x,  # type: ignore[attr-defined]
            field_y=event.field_y,  # type: ignore[attr-defined]
            values={"img_ok": True},
        )


def get_last_gain_coords(x_fields: int, y_fields: int) -> tuple[int, int]:
    """Return a tuple with last gain coordinates x and y.

    The gain coordinates will be the two most centered fields.
    """
    last_x_field = ceil(x_fields / 2)
    last_y_field = ceil(y_fields / 2) - 1
    return last_x_field, last_y_field
