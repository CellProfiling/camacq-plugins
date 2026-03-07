#!/usr/bin/env python3
"""Tool to generate image fixture for tests from real image data."""

import argparse
import fnmatch
import gzip
import os
from pathlib import Path
import shutil
from typing import Any

import numpy as np
import tifffile

IMAGE_DATA_DIR = os.path.join(os.path.dirname(__file__), "../tests/fixtures/image_data")


def _find_files(root_dir: str, search: str) -> list[str]:
    """Search for files in root directory."""
    matches = []
    for root, _, filenames in os.walk(os.path.normpath(root_dir)):
        for filename in fnmatch.filter(filenames, search):
            matches.append(os.path.join(root, filename))
    return matches


def pack_image_fixture(root_dir: str | None = None) -> None:
    """Gunzip tif images for image tests."""
    if root_dir is None:
        root_dir = IMAGE_DATA_DIR
    matches = _find_files(root_dir, "*.tif")
    print("Gzipping the images, this will take some time...")
    for path in matches:
        gz_path = f"{path}.gz"
        with open(path, "rb") as f_in:
            with gzip.open(gz_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(path)


def unpack_image_fixture(root_dir: str | None = None) -> None:
    """Unzip gunzipped tif images for image tests."""
    if root_dir is None:
        root_dir = IMAGE_DATA_DIR
    matches = _find_files(root_dir, "*.gz")
    for gz_path in matches:
        path, _ = os.path.splitext(gz_path)
        with gzip.open(gz_path, "rb") as f_in:
            with open(path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)


def read_image_data(root_dir: str | None = None) -> list[dict[str, Any]]:
    """Return a list of dicts with path and image numpy array data."""
    if root_dir is None:
        root_dir = IMAGE_DATA_DIR
    matches = _find_files(root_dir, "*.tif")
    image_data = []
    for path in matches:
        try:
            data = tifffile.imread(path, key=0)
        except OSError as exc:
            print("Failed reading image:", exc)
            raise

        image_data.append({"path": path, "data": data})

    return image_data


def save_images_to_npz(path: str) -> None:
    """Save image data as compressed npz."""
    resolved_path = Path(path).resolve()
    image_data = read_image_data()
    np.savez_compressed(
        resolved_path, **{data["path"]: data["data"] for data in image_data}
    )


def get_arguments(args: list[str] | None = None) -> argparse.Namespace:
    """Get parsed arguments."""
    parser = argparse.ArgumentParser(description="Unpack or pack fixture files.")
    parser.add_argument("--pack", action="store_true", help="Pack fixture files.")
    parser.add_argument("--npz", help="Save image fixture data in a npz file.")
    return parser.parse_args(args=args)


def main(args: list[str] | None = None) -> None:
    """Pack or unpack the images for test fixtures."""
    parsed_args = get_arguments(args=args)

    if parsed_args.npz:
        save_images_to_npz(parsed_args.npz)
        return
    if parsed_args.pack:
        pack_image_fixture()
    else:
        unpack_image_fixture()


if __name__ == "__main__":
    main()
