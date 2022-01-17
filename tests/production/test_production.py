"""Test the production plugin."""
from unittest.mock import AsyncMock, call

import voluptuous as vol
from ruamel.yaml import YAML

from camacq import plugins
from camacq.plugins.api import ImageEvent
from camacq.plugins.sample import get_matched_samples
from camacqplugins.gain import GainCalcEvent

CONFIG = """
production:
  gain_pattern_name: p10xgain
  gain_job_id: 3
  gain_job_channels: 32
  exp_pattern_name: p10xexp
  exp_job_ids:
   - 3
   - 4
   - 6
  channels:
  - channel: green
    job_name: green10x
    detector_num: 1
    default_gain: 800
    max_gain: 800
  - channel: blue
    job_name: blue10x
    detector_num: 1
    default_gain: 800
    max_gain: 800
  - channel: yellow
    job_name: blue10x
    detector_num: 2
    default_gain: 695
    max_gain: 800
  - channel: red
    job_name: red10x
    detector_num: 2
    default_gain: 700
    max_gain: 800
  well_layout:
    x_fields: 2
    y_fields: 3
"""

SAMPLE_STATE = """
name,plate_name,well_x,well_y,field_x,field_y
field,00,0,0,0,0
field,00,0,0,0,1
well,00,0,1
well,00,1,0
well,00,1,1
""".strip()


class WorkflowImageEvent(ImageEvent):
    """Represent a test image event."""

    event_type = "workflow_image_event"

    @property
    def job_id(self):
        """:int: Return job id of the image."""
        return self.data.get("job_id")


async def test_image_events(center, leica_sample):
    """Test image events."""
    config = YAML(typ="safe").load(CONFIG)
    plate_name = "00"
    well_x = 0
    well_y = 0
    await plugins.setup_module(center, config)
    calc_gain = AsyncMock()
    gains = {
        "green": 800,
        "blue": 700,
        "yellow": 600,
        "red": 500,
    }

    async def fire_gain_event(**kwargs):
        """Fire gain event."""
        well_x = kwargs.get("well_x")
        well_y = kwargs.get("well_y")
        plate_name = kwargs.get("plate_name")

        for channel_name, gain in gains.items():
            event = GainCalcEvent(
                {
                    "plate_name": plate_name,
                    "well_x": well_x,
                    "well_y": well_y,
                    "channel_name": channel_name,
                    "gain": gain,
                }
            )
            await center.bus.notify(event)

    calc_gain.side_effect = fire_gain_event

    center.actions.register(
        "gain", "calc_gain", calc_gain, vol.Schema({}, extra=vol.ALLOW_EXTRA)
    )

    event = WorkflowImageEvent(
        {
            "path": "test_path",
            "plate_name": plate_name,
            "well_x": well_x,
            "well_y": well_y,
            "field_x": 1,
            "field_y": 1,
            "job_id": 3,
            "z_slice_id": 0,
            "channel_id": 31,
        }
    )
    center.create_task(center.bus.notify(event))
    await center.wait_for()

    assert calc_gain.call_count == 1
    assert calc_gain.call_args == call(
        action_id="calc_gain",
        plate_name=plate_name,
        well_x=well_x,
        well_y=well_y,
    )

    channels = {
        channel["channel"]: channel_id
        for channel_id, channel in enumerate(config["production"]["channels"])
    }

    for channel_name, gain in gains.items():
        channel = center.samples.leica.get_sample(
            "channel",
            plate_name=plate_name,
            well_x=well_x,
            well_y=well_y,
            channel_id=channels[channel_name],
        )
        assert channel.values["gain"] == gain


async def test_load_sample(center, leica_sample, tmp_path):
    """Test loading sample state from file."""
    state_file = tmp_path / "state_file.csv"
    state_file.write_text(SAMPLE_STATE)
    config = YAML(typ="safe").load(CONFIG)
    config["production"]["sample_state_file"] = str(state_file)
    plate_name = "00"
    await plugins.setup_module(center, config)
    await center.wait_for()

    wells = get_matched_samples(
        center.samples.leica, "well", {"plate_name": plate_name}
    )
    assert len(wells) == 4
    fields = get_matched_samples(
        center.samples.leica,
        "field",
        {"plate_name": plate_name, "well_x": 0, "well_y": 0},
    )
    assert len(fields) == 2
