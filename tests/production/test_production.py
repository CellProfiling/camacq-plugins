"""Test the production plugin."""
import tempfile
from pathlib import Path
from unittest.mock import call

from asynctest import patch
import pytest
from ruamel.yaml import YAML

from camacq import plugins
from camacq.plugins.api import ImageEvent

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
gain:
  channels:
  - channel: green
    init_gain: [450, 495, 540, 585, 630, 675, 720, 765, 810, 855, 900]
  - channel: blue
    # 63x
    #init_gain: [750, 730, 765, 800, 835, 870, 905]
    # 10x
    init_gain: [700, 735, 770, 805, 840, 875, 910]
  - channel: yellow
    # 63x
    #init_gain: [550, 585, 620, 655, 690, 725, 760]
    # 10x
    init_gain: [700, 735, 770, 805, 840, 875, 910]
  - channel: red
    # 63x
    #init_gain: [525, 560, 595, 630, 665, 700, 735]
    # 10x
    init_gain: [600, 635, 670, 705, 740, 775, 810]
"""


@pytest.fixture(name="calc_gain")
def calc_gain_fixture():
    """Mock calc_gain plugin function."""
    with patch("camacq.plugins.gain.calc_gain") as mock_gain:
        yield mock_gain


@pytest.fixture(name="make_proj")
def make_proj_fixture():
    """Mock make_proj image function."""
    with patch("camacq.plugins.gain.make_proj") as mock_proj:
        yield mock_proj


class WorkflowImageEvent(ImageEvent):
    """Represent a test image event."""

    event_type = "workflow_image_event"

    @property
    def job_id(self):
        """:int: Return job id of the image."""
        return self.data.get("job_id")


async def test_duplicate_image_events(center, calc_gain, make_proj):
    """Test duplicate image events."""
    config = YAML(typ="safe").load(CONFIG)
    await plugins.setup_module(center, config)
    plate_name = "00"
    well_x = 0
    well_y = 0
    field_x = 1
    field_y = 1
    job_id = 3
    channel_id = 31
    save_path = Path(tempfile.gettempdir()) / plate_name
    save_path = save_path / f"{well_x}--{well_y}"
    test_projs = ["test"]
    make_proj.return_value = test_projs

    event = WorkflowImageEvent(
        {
            "path": "test_path",
            "plate_name": plate_name,
            "well_x": well_x,
            "well_y": well_y,
            "field_x": field_x,
            "field_y": field_y,
            "job_id": job_id,
            "channel_id": channel_id,
        }
    )
    await center.bus.notify(event)
    await center.wait_for()

    assert calc_gain.call_count == 1
    assert calc_gain.call_args == call(
        center, config, plate_name, well_x, well_y, test_projs, True, str(save_path),
    )
