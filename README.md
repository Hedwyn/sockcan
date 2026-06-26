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
Usage: python -m sockcan.benchmarks [OPTIONS] COMMAND [ARGS]...

Options:
  --help  Show this message and exit.

Commands:
  daemon  Benchmarks all 4 scenarios: direct kernel vs userspace daemon,...
  kernel  Benchmarks python-can vs sockcan with direct kernel...
```

### Kernel benchmark
Compares python-can and sockcan on direct kernel communications:
```shell
python -m sockcan.benchmarks kernel --rounds 200 --batch-size 100
```

### Daemon benchmark
Compares all 4 scenarios: python-can + sockcan on both direct kernel and userspace daemon.
Requires vcan0 and uses a virtual bus (no hardware needed):
```shell
python -m sockcan.benchmarks daemon --rounds 100 --batch-size 50 --port 18765
```

## Running tests

Tests are based on `pytest` and `hypothesis`. Make sure to install this package with `test` extra (`pip install .\[test\`).<br>
You can show hypothesis stats with `--hypothesis-show-statistics`:

```shell
python -m pytest -vv --hypothesis-show-statistics
```
