# My Car CLI (`my-car`)

A fast command-line dashboard for MB EQB stats.

## Features

- Battery level and remaining electric range
- Odometer reading
- Tyre pressures in KPA or PSI
- Next-service distance
- OS Credential Manager token storage
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

Configuration, cache, browser data, and any fallback token file are stored under `~/.my-car-cli/`. Authentication tokens are stored in the OS Credential Manager when available.

Set `MY_CAR_AUTH_TOKEN` to provide a bearer token without using stored credentials.

## Tests

```bash
uv run pytest tests/ -v
```

## Disclaimer

This is an unofficial personal project. It is not affiliated with or endorsed by any vehicle manufacturer.
