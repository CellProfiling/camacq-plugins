"""Handle default gain feedback plugin."""
import logging
import os
from collections import defaultdict, namedtuple
from functools import partial
from itertools import groupby
from pathlib import Path

import matplotlib
import pandas as pd
import voluptuous as vol
from scipy.optimize import curve_fit

from camacq.event import Event
from camacq.helper import BASE_ACTION_SCHEMA
from camacq.image import make_proj
from camacq.util import write_csv

matplotlib.use("AGG")  # use noninteractive default backend
# pylint: disable=wrong-import-order, wrong-import-position, ungrouped-imports
import matplotlib.pyplot as plt  # noqa: E402

_LOGGER = logging.getLogger(__name__)
BOX = "box"
COUNT = "count"
VALID = "valid"
CHANNEL_ID = "C{:02d}"
CONF_CHANNEL = "channel"
CONF_CHANNELS = "channels"
CONF_GAIN = "gain"
CONF_INIT_GAIN = "init_gain"
CONF_SAVE_DIR = "save_dir"
COUNT_CLOSE_TO_ZERO = 2
GAIN_CALC_EVENT = "gain_calc_event"
SAVED_GAINS = "saved_gains"
WELL = "well"
WELL_NAME = "U{:02d}--V{:02d}"

ACTION_CALC_GAIN = "calc_gain"
CALC_GAIN_ACTION_SCHEMA = BASE_ACTION_SCHEMA.extend(
    {
        vol.Required("well_x"): vol.Coerce(int),
        vol.Required("well_y"): vol.Coerce(int),
        vol.Required("plate_name"): vol.Coerce(str),
        "images": [vol.Coerce(str)],
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CHANNELS): [
            {
                vol.Required(CONF_CHANNEL): vol.Coerce(str),
                vol.Required(CONF_INIT_GAIN): [vol.Coerce(int)],
            }
        ],
        # pylint: disable=no-value-for-parameter
        vol.Optional(CONF_SAVE_DIR): vol.IsDir(),
    }
)

GAIN = "gain"
Data = namedtuple("Data", [BOX, GAIN, VALID])  # pylint: disable=invalid-name
Channel = namedtuple("Channel", ["name", GAIN])  # pylint: disable=invalid-name


async def setup_module(center, config):
    """Set up gain calculation plugin."""

    async def handle_calc_gain(**kwargs):
        """Handle call to calc_gain action."""
        well_x = kwargs["well_x"]
        well_y = kwargs["well_y"]
        plate_name = kwargs["plate_name"]
        paths = kwargs.get("images")  # list of paths to calculate gain for
        if not paths:
            well = center.samples.leica.get_sample(
                "well", plate_name=plate_name, well_x=well_x, well_y=well_y
            )
            if not well:
                return
            images = {path: image.channel_id for path, image in well.images.items()}
        else:
            images = {
                path: image.channel_id
                for path, image in center.samples.leica.images.items()
                if path in paths
            }
        projs = await center.add_executor_job(make_proj, images)
        await calc_gain(center, config, plate_name, well_x, well_y, projs)

    center.actions.register(
        "gain", ACTION_CALC_GAIN, handle_calc_gain, CALC_GAIN_ACTION_SCHEMA
    )


async def calc_gain(
    center, config, plate_name, well_x, well_y, projs,
):
    """Calculate gain values for the well."""
    # pylint: disable=too-many-arguments, too-many-locals
    gain_conf = config[CONF_GAIN]
    save_dir = gain_conf.get(CONF_SAVE_DIR) or ""
    make_plots = bool(save_dir)
    plot_dir = Path(save_dir) / "plots"
    await center.add_executor_job(ensure_plot_dir, plot_dir)

    init_gain = [
        Channel(channel[CONF_CHANNEL], gain=gain)
        for channel in gain_conf[CONF_CHANNELS]
        for gain in channel[CONF_INIT_GAIN]
    ]

    # This should be a path to a base file name, not to a dir or file.
    plot_path = plot_dir / WELL_NAME.format(well_x, well_y)
    gains = await center.add_executor_job(
        partial(_calc_gain, projs, init_gain, plot=make_plots, save_path=plot_path)
    )
    _LOGGER.info("Calculated gains: %s", gains)
    if SAVED_GAINS not in center.data:
        center.data[SAVED_GAINS] = defaultdict(dict)
    center.data[SAVED_GAINS].update({WELL_NAME.format(well_x, well_y): gains})
    if make_plots:
        await center.add_executor_job(
            save_gain, save_dir, center.data[SAVED_GAINS], [WELL] + list(gains)
        )

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
        await center.bus.notify(event)  # await in sequential order


def _power_func(inp, alpha, beta):
    """Return the value of function of inp, alpha and beta."""
    return alpha * inp ** beta


def _check_upward(points):
    """Return a function that checks if points move upward."""

    def wrapped(point):
        """Return True if trend is upward.

        The calculation is done for a point with neighbouring points.
        """
        idx, item = point
        valid = item.valid and item.box <= 600
        prev = next_ = True
        if idx > 0:
            prev = item.box >= points[idx - 1].box
        if idx < len(points) - 1:
            next_ = item.box <= points[idx + 1].box
        return valid and (prev or next_)

    return wrapped


def _create_plot(path, x_data, y_data, coeffs, label):
    """Plot and save plot to path."""
    plt.ioff()
    plt.clf()
    plt.yscale("log")
    plt.xscale("log")
    plt.plot(
        x_data, y_data, "bo", x_data, _power_func(x_data, *coeffs), "g-", label=label
    )
    plt.savefig(path)


def _calc_gain(projs, init_gain, plot=True, save_path=""):
    """Calculate gain values for the well.

    Do the actual math.
    """
    # pylint: disable=too-many-locals
    box_vs_gain = {}

    for c_id, proj in projs.items():
        channel = init_gain[c_id]
        if channel.name not in box_vs_gain:
            box_vs_gain[channel.name] = []
        hist_data = pd.DataFrame(
            {BOX: list(range(len(proj.histogram[0]))), COUNT: proj.histogram[0]}
        )
        # Handle all zero pixels
        non_zero_hist_data = hist_data[(hist_data[COUNT] > 0) & (hist_data[BOX] > 0)]
        if non_zero_hist_data.empty:
            continue
        # Find the max box holding pixels
        box_max_count = non_zero_hist_data[BOX].iloc[-1]
        # Select only histo data where count is > 0 and 255 > box > 0.
        # Only use values in interval 10-100 and
        # > (max box holding pixels - 175).
        roi = hist_data[
            (hist_data[COUNT] > 0)
            & (hist_data[BOX] > 0)
            & (hist_data[BOX] < 255)
            & (hist_data[COUNT] >= 10)
            & (hist_data[COUNT] <= 100)
            & (hist_data[BOX] > (box_max_count - 175))
        ]
        if roi.shape[0] < 3:
            continue
        x_data = roi[COUNT].astype(float).values
        y_data = roi[BOX].astype(float).values
        coeffs, _ = curve_fit(_power_func, x_data, y_data, p0=(1000, -1))
        if plot:
            _save_path = "{}_{}.ome.png".format(save_path, CHANNEL_ID.format(c_id))
            _create_plot(
                _save_path, hist_data[COUNT], hist_data[BOX], coeffs, "count-box"
            )
        # Find box value where count is close to zero.
        # Store that box value and it's corresponding gain value.
        # Store boolean saying if second slope coefficient is negative.
        box_vs_gain[channel.name].append(
            Data._make(
                (_power_func(COUNT_CLOSE_TO_ZERO, *coeffs), channel.gain, coeffs[1] < 0)
            )
        )

    gains = {}
    for channel, points in box_vs_gain.items():
        # Sort points with ascending gain, to allow grouping.
        points = sorted(points, key=lambda item: item.gain)
        long_group = []
        for key, group in groupby(enumerate(points), _check_upward(points)):
            # Find the group with the most points and use that below.
            stored_group = list(group)
            if key and len(stored_group) > len(long_group):
                long_group = stored_group

        # Curve fit the longest group with power function.
        # Plot the points and the fit.
        # Return the calculated gains at bin 255, using fit function.
        if len(long_group) < 3:
            gains[channel] = None
            continue
        coeffs, _ = curve_fit(
            _power_func,
            [p[1].box for p in long_group],
            [p[1].gain for p in long_group],
            p0=(1, 1),
        )
        if plot:
            _save_path = "{}_{}.png".format(save_path, channel)
            _create_plot(
                _save_path,
                [p.box for p in points],
                [p.gain for p in points],
                coeffs,
                "box-gain",
            )
        gains[channel] = round(_power_func(255, *coeffs))

    return gains


def save_gain(save_dir, saved_gains, header):
    """Save a csv file with gain values per image channel."""
    path = os.path.normpath(os.path.join(save_dir, "output_gains.csv"))
    write_csv(path, saved_gains, header)


def ensure_plot_dir(plot_dir):
    """Make sure that plot dir exists."""
    if not plot_dir.exists():
        plot_dir.mkdir()


class GainCalcEvent(Event):
    """An event produced by a sample channel change event."""

    __slots__ = ()

    event_type = GAIN_CALC_EVENT

    @property
    def channel_name(self):
        """:str: Return the channel name of the event."""
        return self.data.get("channel_name")

    @property
    def gain(self):
        """:str: Return the channel gain of the event."""
        return self.data.get("gain")

    @property
    def plate_name(self):
        """:str: Return the name of the plate."""
        return self.data.get("plate_name")

    @property
    def well_x(self):
        """:int: Return the well x coordinate of the event."""
        return self.data.get("well_x")

    @property
    def well_y(self):
        """:int: Return the well y coordinate of the event."""
        return self.data.get("well_y")

    def __repr__(self):
        """Return the representation."""
        return "<{}: {}>".format(type(self).__name__, self.gain)
