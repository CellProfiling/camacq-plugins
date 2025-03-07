"""Set up file for camacq-plugins package."""

from pathlib import Path

import setuptools

PROJECT_DIR = Path(__file__).parent.resolve()
VERSION = (
    (PROJECT_DIR / "camacqplugins" / "VERSION").read_text(encoding="utf-8").strip()
)
README_FILE = PROJECT_DIR / "README.md"
LONG_DESCRIPTION = README_FILE.read_text(encoding="utf-8")
REQUIRES = ["camacq>=0.8.0", "matplotlib", "pandas", "scipy"]


setuptools.setup(
    name="camacq-plugins",
    version=VERSION,
    url="https://github.com/CellProfiling/camacq-plugins",
    author="Martin Hjelmare",
    author_email="marhje52@gmail.com",
    description="Plugins for camacq",
    license="Apache License 2.0",
    long_description=LONG_DESCRIPTION,
    long_description_content_type="text/markdown",
    packages=setuptools.find_packages(),
    python_requires=">=3.10",
    install_requires=REQUIRES,
    entry_points={
        "camacq.plugins": [
            "gain = camacqplugins.gain",
            "production = camacqplugins.production",
        ],
    },
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    ],
)
