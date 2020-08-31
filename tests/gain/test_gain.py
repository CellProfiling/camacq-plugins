"""Test gain calculation."""
from unittest.mock import patch, PropertyMock

import numpy as np
import pytest

from camacq import plugins
from camacq.plugins.leica import LeicaImageEvent
from camacqplugins.gain import GAIN_CALC_EVENT
from tests.common import IMAGE_DATA_DIR

# All test coroutines will be treated as marked.
pytestmark = pytest.mark.asyncio  # pylint: disable=invalid-name

PLATE_NAME = "slide"
WELL_X, WELL_Y = 1, 0


@pytest.fixture(name="load_image")
def load_image_fixture():
    """Patch load image and metadata."""
    with patch(
        "camacq.image.ImageData._load_image_data", autospec=True
    ) as load_image, patch(
        "camacq.image.ImageData.metadata", new_callable=PropertyMock
    ) as mock_metadata:
        mock_metadata.return_value = ""
        yield load_image


async def test_gain(center, leica_sample, load_image):
    """Run gain calculation test."""
    config = {
        "gain": {
            "channels": [
                {
                    "channel": "green",
                    "init_gain": [
                        450,
                        495,
                        540,
                        585,
                        630,
                        675,
                        720,
                        765,
                        810,
                        855,
                        900,
                    ],
                },
                {
                    "channel": "blue",
                    "init_gain": [400, 435, 470, 505, 540, 575, 610],
                },
                {
                    "channel": "yellow",
                    "init_gain": [550, 585, 620, 655, 690, 725, 760],
                },
                {
                    "channel": "red",
                    "init_gain": [525, 560, 595, 630, 665, 700, 735],
                },
            ],
        }
    }
    await plugins.setup_module(center, config)
    image_fixture = IMAGE_DATA_DIR / "image_data.npz"
    image_data = await center.add_executor_job(np.load, image_fixture)

    def mock_load_image(image):
        """Mock load image."""
        data = image_data[image.path]
        image._data = data  # pylint: disable=protected-access

    load_image.side_effect = mock_load_image

    events = [LeicaImageEvent({"path": path}) for path in image_data]

    for event in events:
        await center.bus.notify(event)

    calculated = {}

    async def handle_gain_event(center, event):
        """Handle gain event."""
        if (
            event.plate_name != PLATE_NAME
            or event.well_x != WELL_X
            or event.well_y != WELL_Y
        ):
            return
        calculated[event.channel_name] = event.gain

    center.bus.register(GAIN_CALC_EVENT, handle_gain_event)

    images = [event.path for event in events]
    await center.actions.gain.calc_gain(
        plate_name=PLATE_NAME, well_x=WELL_X, well_y=WELL_Y, images=images
    )

    solution = {"blue": 480, "green": 740, "red": 745, "yellow": 805}
    assert calculated == pytest.approx(solution, abs=10)
