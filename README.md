# camacq-plugins

Plugins for camacq:

- [production](camacqplugins/production/)

## Installation

- Clone and install the package.

    ```sh
    # Clone the repo.
    git clone https://github.com/CellProfiling/camacq-plugins.git
    # Enter directory.
    cd camacq-plugins
    # Install package.
    pip install .
    # Test that program is callable and show help.
    camacq -h
    ```

### Requirements

- Python version 3.7+.
- camacq >= 0.6.0

## Usage

Add configuration for the plugin you want to run.
See the [config_templates](config_templates/) directory for example configuration.

Then start `camacq`.

```sh
camacq
```

## Development

### Release

See the [release instructions](RELEASE.md).

## Licence

- Apache-2.0.

## Authors

- Martin Hjelmare
