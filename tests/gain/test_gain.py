"""Test gain calculation."""
from collections import defaultdict

import numpy as np
import pytest

from camacq.plugins.leica import LeicaImageEvent
from camacq.image import ImageData
from camacqplugins.gain import GAIN_CALC_EVENT, calc_gain
from tests.common import IMAGE_DATA_DIR

# All test coroutines will be treated as marked.
pytestmark = pytest.mark.asyncio  # pylint: disable=invalid-name

PLATE_NAME = "slide"
WELL_X, WELL_Y = 1, 0


def make_proj(images):
    """Mock make proj."""
    sorted_images = defaultdict(list)
    max_imgs = {}
    for path, (channel, data) in images.items():
        image = ImageData(path=path, data=data)
        # Exclude images with 0, 16 or 256 pixel side.
        # pylint: disable=len-as-condition
        if len(image.data) == 0 or len(image.data) == 16 or len(image.data) == 256:
            continue
        sorted_images[channel].append(image)
        proj = np.max([img.data for img in sorted_images[channel]], axis=0)
        max_imgs[channel] = ImageData(path=path, data=proj)
    return max_imgs


async def test_gain(center):
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
                {"channel": "blue", "init_gain": [400, 435, 470, 505, 540, 575, 610],},
                {
                    "channel": "yellow",
                    "init_gain": [550, 585, 620, 655, 690, 725, 760],
                },
                {"channel": "red", "init_gain": [525, 560, 595, 630, 665, 700, 735],},
            ],
        }
    }
    image_fixture = IMAGE_DATA_DIR / "image_data.npz"
    image_data = await center.add_executor_job(np.load, image_fixture)
    events = [LeicaImageEvent({"path": path}) for path in image_data]
    images = {
        event.path: (event.channel_id, image_data[event.path]) for event in events
    }
    projs = await center.add_executor_job(make_proj, images)
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

    await calc_gain(
        center, config, PLATE_NAME, WELL_X, WELL_Y, projs,
    )

    solution = {"blue": 480, "green": 740, "red": 745, "yellow": 805}
    assert calculated == pytest.approx(solution, abs=10)
