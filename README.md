# My Car CLI (`my-car`)

A fast command-line dashboard for MB EQB stats.

## Features

- Battery level and remaining electric range
- Odometer reading
- Tyre pressures in KPA or PSI
- Next-service distance
- Local token file under `~/.my-car-cli/`
- Configurable local response caching
- Optional browser-assisted login

## Setup

This project uses Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

For browser-assisted login:

```bash
uv sync --extra login
uv run playwright install chromium
```

## Usage

Authenticate:

```bash
uv run my-car login
```

Use `--manual` to paste a bearer token instead, or `--fresh` to clear the saved browser profile before logging in.

Set your VIN and preferred pressure unit:

```bash
uv run my-car config --vin <YOUR_VIN>
uv run my-car config --unit PSI
```

View vehicle status:

```bash
uv run my-car status
```

Bypass the local cache:

```bash
uv run my-car status --refresh
```

Other commands:

```bash
uv run my-car config --show
uv run my-car logout
uv run my-car --version
```

## Local Data

Configuration, cache, browser data, and the authentication token are stored under `~/.my-car-cli/`. The token file is `~/.my-car-cli/token`.

Set `MY_CAR_AUTH_TOKEN` to provide a bearer token without using the token file.

## Tests

```bash
uv run pytest tests/ -v
```

## Disclaimer

This is an unofficial personal project. It is not affiliated with or endorsed by any vehicle manufacturer.
