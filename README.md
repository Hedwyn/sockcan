# sockcan

Provides a socketcan-alike interface other arbitrary CAN drivers.

## Installing

```
pip install sockcan
```

## Documentation

Documentation is available [here](https://Hedwyn.github.io/sockcan/).

## Running benchmarks

Benchmarks can be run with `python -m sockcan.benchmarks`:

```shell
python -m sockcan.benchmarks --help
Usage: python -m sockcan.benchmarks [OPTIONS]

  Runs the benchmarks interactively

Options:
  -r, --rounds INTEGER
  -b, --batch-size INTEGER
  -v, --verbose
  --help                    Show this message and exit.
```

## Running tests

Tests are based on `pytest` and `hypothesis`. Make sure to install this package with `test` extra (`pip install .\[test\`).<br>
You can show hypothesis stats with `--hypothesis-show-statistics`:

```shell
python -m pytest -vv --hypothesis-show-statistics
```
