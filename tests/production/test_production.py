"""Test the production plugin."""
import tempfile
from pathlib import Path
from unittest.mock import call

import pytest
import voluptuous as vol
from asynctest import CoroutineMock
from ruamel.yaml import YAML

from camacq import plugins
from camacq.plugins.api import ImageEvent
from camacq.plugins.gain import GainCalcEvent

# All test coroutines will be treated as marked.
pytestmark = pytest.mark.asyncio  # pylint: disable=invalid-name

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
plate_name,well_x,well_y,channel_name,gain
00,0,0
00,0,1
00,1,0
00,1,1
""".strip()


class WorkflowImageEvent(ImageEvent):
    """Represent a test image event."""

    event_type = "workflow_image_event"

    @property
    def job_id(self):
        """:int: Return job id of the image."""
        return self.data.get("job_id")


async def test_duplicate_image_events(center):
    """Test duplicate image events."""
    config = YAML(typ="safe").load(CONFIG)
    await plugins.setup_module(center, config)
    plate_name = "00"
    well_x = 0
    well_y = 0
    save_path = Path(tempfile.gettempdir()) / plate_name
    save_path = save_path / f"{well_x}--{well_y}"
    calc_gain = CoroutineMock()
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
            "channel_id": 31,
        }
    )
    center.create_task(center.bus.notify(event))
    center.create_task(center.bus.notify(event))
    await center.wait_for()

    assert calc_gain.call_count == 1
    assert calc_gain.call_args == call(
        action_id="calc_gain",
        plate_name=plate_name,
        well_x=well_x,
        well_y=well_y,
        make_plots=True,
        save_path=save_path,
    )
    for channel_name, gain in gains.items():
        channel = center.sample.get_channel(plate_name, well_x, well_y, channel_name)
        assert channel.gain == gain


async def test_load_sample(center, tmp_path):
    """Test loading sample state from file."""
    state_file = tmp_path / "state_file.csv"
    state_file.write_text(SAMPLE_STATE)
    config = YAML(typ="safe").load(CONFIG)
    config["production"]["state_file"] = str(state_file)
    plate_name = "00"
    await plugins.setup_module(center, config)
    await center.wait_for()

    plate = center.sample.get_plate(plate_name)
    assert len(plate.wells) == 4
