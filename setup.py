"""Set up file for camacq-plugins package."""
from pathlib import Path

import setuptools

PROJECT_DIR = Path(__file__).parent.resolve()
VERSION = (PROJECT_DIR / "camacqplugins" / "VERSION").read_text().strip()
README_FILE = PROJECT_DIR / "README.md"
LONG_DESCR = README_FILE.read_text(encoding="utf-8")
REQUIRES = ["camacq"]


setuptools.setup(
    name="camacq-plugins",
    version=VERSION,
    url="https://github.com/CellProfiling/camacq-plugins",
    author="Martin Hjelmare",
    author_email="marhje52@kth.se",
    description="Plugins for camacq",
    license="Apache License 2.0",
    long_description=LONG_DESCR,
    long_description_content_type="text/markdown",
    packages=setuptools.find_packages(),
    python_requires=">=3.6",
    install_requires=REQUIRES,
    entry_points={"camacq.plugins": ["production = camacqplugins.production",],},
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
    ],
)
