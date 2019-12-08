# Production

## Usage

Add configuration for the `production`, `gain`, `leica` and `rename` plugin, in the `camacq` configuration file.
See the [config_templates](../../config_templates/) directory for example configuration.

```yaml
production:
  ...

gain:
  ...

rename_image:

leica:
  ...
```

Then start `camacq`.

```sh
camacq
```

To allow the user to set up the sample state before starting an
experiment, camacq can load the sample state from a file. In the production
configuration section there is an option to specify a path to a csv
file.

```yaml
production:
  sample_state_file: '/sample_state.csv'
```

Each row in the csv file should represent at least one state of a sample container,
ie well, field or channel. A plate name must also be included. The csv file should have a
header. See below.

```csv
plate_name,well_x,well_y,channel_name,gain
00,1,1,blue,600
```

This example will set a plate '00', a well (1, 1), a blue channel
and set the gain of the blue channel to 600.

```csv
plate_name,well_x,well_y,field_x,field_y
00,1,1,1,1
```

This example will create a plate '00' a well (1, 1) and a field (1, 1)
in the sample state.
