"""Test gain calculation."""

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

from camacq import plugins
from camacq.control import Center
from camacq.event import Event
from camacq.image import ImageData
from camacq.plugins.leica import LeicaImageEvent
import numpy as np
import pytest

from camacqplugins.gain import GAIN_CALC_EVENT, GainCalcEvent
from tests.common import IMAGE_DATA_DIR

PLATE_NAME = "slide"
WELL_X, WELL_Y = 1, 0


@pytest.fixture(name="load_image")
def load_image_fixture() -> Generator[MagicMock]:
    """Patch load image and metadata."""
    with (
        patch("camacq.image.ImageData._load_image_data", autospec=True) as load_image,
        patch(
            "camacq.image.ImageData.metadata", new_callable=PropertyMock
        ) as mock_metadata,
    ):
        mock_metadata.return_value = ""
        yield load_image


async def test_gain(center: Center, leica_sample: None, load_image: MagicMock) -> None:
    """Run gain calculation test."""
    config: dict[str, Any] = {
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

    def mock_load_image(image: ImageData) -> None:
        """Mock load image."""
        data = image_data[image.path]
        image._data = data

    load_image.side_effect = mock_load_image

    events = [LeicaImageEvent({"path": path}) for path in image_data]

    for event in events:
        await center.bus.notify(event)

    calculated: dict[str, int | None] = {}

    async def handle_gain_event(center: Center, event: Event) -> None:
        """Handle gain event."""
        gain_event = GainCalcEvent(event.data)
        if (
            gain_event.plate_name != PLATE_NAME
            or gain_event.well_x != WELL_X
            or gain_event.well_y != WELL_Y
        ):
            return
        if gain_event.channel_name is not None:
            calculated[gain_event.channel_name] = gain_event.gain

    center.bus.register(GAIN_CALC_EVENT, handle_gain_event)

    images = [event.path for event in events]
    await center.actions.gain.calc_gain(
        plate_name=PLATE_NAME, well_x=WELL_X, well_y=WELL_Y, images=images
    )

    solution = {"blue": 480, "green": 740, "red": 745, "yellow": 805}
    assert calculated == pytest.approx(solution, abs=10)
