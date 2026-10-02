# AGENTS.md — My Car CLI Codebase Reference

> Read this first in every session. It covers architecture, auth flow, gotchas, and dev commands.

> **IMPORTANT** — Before making any changes, read `BUGS.md`. It is the canonical log of every bug, attempted fix, successful fix, reverted change, and open question in this codebase. Do not repeat fixes that have already been tried. Add new entries for anything you attempt.

---

## Tech Stack

| Layer | Choice | Notes |
|:---|:---|:---|
| Language | Python 3.11+ | |
| Package manager | **uv** | Not pip/poetry. `uv sync`, `uv run`, `uv add` |
| CLI framework | **typer** | Type-hinted commands, no argparse |
| Terminal UI | **rich** | Panels, tables, progress bars, colors |
| HTTP | **httpx** | Synchronous client (not async) |
| Secrets | Token file | `~/.my-car-cli/token`. `MY_CAR_AUTH_TOKEN` overrides it. |
| Browser auto | **playwright** | Optional, installed via `login` extra. Chrome default; do not spoof a Firefox user agent |
| Testing | **unittest** + **pytest** | Both work. Pytest is dev dep |
| Build | **hatchling** | Config in `pyproject.toml` |
| Linting | None configured | No ruff/flake8/mypy yet |

---

## File Map (read order for understanding)

```
src/my_car_cli/
├── __init__.py          # version = "0.1.0"
├── main.py              # CLI entry: "login", "status", "logout", "config"
├── auth.py              # Token file + Playwright login flow
├── api.py               # HTTP calls to OneWeb API (status, next-service, JWE refresh)
├── config.py            # ~/.my-car-cli/config.json (VIN, unit, TTL)
├── cache.py             # ~/.my-car-cli/cache.json (15min TTL)
└── display.py           # Rich dashboard rendering
tests/
├── test_cli.py          # Config, dashboard, token file, status refresh wiring
└── test_api.py          # Mocked refresh and callback-UUID exchange
BUGS.md                  # Bug log — add entries here after every fix
.har files               # HAR captures from real Firefox login sessions (DO NOT COMMIT)
```

---

## Auth Flow (the hardest part — read carefully)

### The full login chain (from HAR analysis)

```
STEP  WHAT                                                   RESULT
────  ─────────────────────────────────────────────────────  ──────────────────────────────────
  1   GET  www.mercedes-benz.co.uk/                          200 (Akamai JS executes)
  2   GET  api.oneweb.mercedes-benz.com/cias/v1/
         authentication?prompt=DEFAULT&locale=en_GB
         &tenantId=oneweb&redirectUrl=...                    302 → id.mercedes-benz.com/ciam/auth/login
  3   User enters email/password on CIAM login page
  4   POST id.mercedes-benz.com/as/<tenantId>/resume/
         as/authorization.ping                               302 (JWE in POST body)
      Set-Cookie: CIAM.DEVICE, ciamMB, PF
  5   GET  api.oneweb.mercedes-benz.com/cias/v1/
         authentication/ciam-callback?code=...&state=...     303
      Location: mercedes-benz.co.uk/...?b2xProvider=CIAS
                &b2xFlow=LOGIN&token=<token>
  6   GET  mercedes-benz.co.uk/...?token=<token>             200 (page renders, token in URL)
```

### Our browser automation flow (`auth.py:login_interactive_playwright`)

```
  Phase 1 — Landing page
    page.goto("https://www.mercedes-benz.co.uk/")
    wait 6 seconds for Akamai JS (sets _abck, bm_sz cookies)

  Phase 2 — CIAS auth entry
    page.goto(CIAS_AUTH_URL)  →  redirects to id.mercedes-benz.com/ciam/auth/login

  Phase 3 — User logs in manually in the browser window

  Phase 4 — Token capture (three mechanisms)
    a) Request interceptor: catches Authorization: Bearer <jwe> on api.oneweb.* calls
    b) Response watcher: parses token= from redirect Location headers and page URLs
    c) Callback UUID: if the page URL has ?token=<uuid>, exchange it once via GET /cias/v1/jwe/{uuid}
```

### Key facts about the token

- Format: **JWE** (JSON Web Encryption), not standard JWT — it is opaque
- Looks like: `eyJlbmMiOiJBMjU2Q0JDLUhTNTEy...` (about 4800 characters; too large for Windows Credential Manager)
- Issued by: CIAS (CIAM Authentication Service)
- Used as: `Authorization: Bearer {jwe_token}` on all `api.oneweb.mercedes-benz.com` requests
- Expiry check: call the API and see if you get 401 (current). A proactive `GET /cias/v1/validation` endpoint also exists — see BUGS.md #14 and #18 (not yet wired).
- Token refresh: on status 401, `POST /cias/v1/jwe/refresh` runs once and the status call is retried once. See BUGS.md #18. Full re-login remains the fallback when refresh fails.

---

## Bot Detection (Akamai)

Mercedes uses **Akamai Bot Manager**. Key cookies:

| Cookie | Domain | Set By | Purpose |
|:---|:---|:---|:---|
| `_abck` | `.mercedes-benz.co.uk` | Akamai JS | Primary bot detection cookie |
| `bm_sz` | `.mercedes-benz.co.uk` | Akamai JS | Bot sensor data |
| `ak_bmsc` | `.mercedes-benz.co.uk` | Akamai | Bot Manager session cookie |
| `bm_mi` | `.mercedes-benz.co.uk` | Akamai JS | Monitor interval |
| `bm_sv` | `.mercedes-benz.co.uk` | Server | Server verification |
| `INGRESSCOOKIE` | `id.mercedes-benz.com` | CIAM LB | Load balancer affinity |

**Critical**: These cookies must exist before hitting `id.mercedes-benz.com`. They're set by JS on `mercedes-benz.co.uk`. Going directly to the login page = blocked.

**Persistence**: By using `launch_persistent_context()` at `~/.my-car-cli/browser-profile/`, these cookies survive between sessions and Akamai recognizes the browser on subsequent runs.

---

## Development Commands

```bash
# Install everything
uv sync

# Add playwright login support
uv add ".[login]"
uv run playwright install chromium

# Run the CLI
uv run my-car login                    # auto (playwright)
uv run my-car login --manual           # manual token paste
uv run my-car login --fresh            # clear browser profile, start new
uv run my-car status                   # show dashboard
uv run my-car status --refresh         # bypass cache
uv run my-car config --show            # view config
uv run my-car config --vin <VIN>       # set VIN
uv run my-car config --unit PSI        # set tyre unit (PSI or KPA)
uv run my-car config --ttl 15          # set cache TTL in minutes
uv run my-car logout                   # delete token file + clear cache

# Testing
uv run pytest tests/ -v
uv run python -m unittest tests/test_cli.py

# Syntax check (quick)
uv run python -c "import py_compile; py_compile.compile('src/my_car_cli/auth.py', doraise=True)"
```

---

## API Endpoints

| Endpoint | Method | Purpose |
|:---|:---|:---|
| `api.oneweb.mercedes-benz.com/cias/v1/authentication?...` | GET | Start login flow (entry point) |
| `api.oneweb.mercedes-benz.com/cias/v1/authentication/ciam-callback?...` | GET | CIAM post-login callback |
| `api.oneweb.mercedes-benz.com/me/vsc/v1/user/vehicle/status-information?locale=en-GB` | GET | Vehicle status (battery, tyres, mileage) |
| `api.oneweb.mercedes-benz.com/domains/vehicles/maintenances/next-service` | GET | Next service info |
| `id.mercedes-benz.com/as/<tenant>/resume/as/authorization.ping` | POST | CIAM auth resume (JWT in body) |
| `id.mercedes-benz.com/ciam/auth/login` | GET | CIAM login form (landing after redirect) |
| `api.oneweb.mercedes-benz.com/cias/v1/validation` | GET | Check if current JWE is still valid (see BUGS.md #18) |
| `api.oneweb.mercedes-benz.com/cias/v1/jwe/refresh` | POST | Rotate to a fresh JWE |
| `api.oneweb.mercedes-benz.com/cias/v1/claims?scope=FULL_NO_CIAM_ID` | PUT | Fetch user claims |
| `api.oneweb.mercedes-benz.com/cias/v1/jwe/{intermediate_token}` | GET | Post-login exchange: CIAS callback UUID → real JWE (raw text body) |

### API Headers (status-information)

```
Authorization: Bearer {jwe_token}
X-Application-Name: mmv-vehicle-stage
X-me-FinOrVin: {vin}
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36
Origin: https://www.mercedes-benz.co.uk
Referer: https://www.mercedes-benz.co.uk/
```

### API Headers (next-service)

```
Authorization: Bearer {jwe_token}
x-application-name: MMA
x-language: en
x-market: GB
x-vehicle-id: {vin}
Accept: application/json;version=2
```

---

## Known Pitfalls & Design Decisions

1. **Never go directly to `id.mercedes-benz.com/ciam/auth/login`** — Akamai blocks it. Always visit `mercedes-benz.co.uk` first to establish fingerprint.

2. **Don't navigate to `my-mercedes-benz.html` for login** — that's the post-auth destination, not the entry point. Use the CIAS authentication URL.

3. **Persistent context is essential** — `browser.new_context()` (incognito) triggers "new device" Akamai challenge every time.

4. **No browser UA override** — Playwright Chromium uses its default Chrome UA. (Previously spoofed Firefox 153; removed in BUGS.md #11 because Mercedes' JS served Firefox-specific code paths that hung under Chromium. The `USER_AGENT` constant was deleted from `auth.py`.) The HTTP client (`api.py:DEFAULT_HEADERS`) still sends a hardcoded Chrome UA string for API calls — update it if Mercedes ever rejects it.

5. **JWE tokens are opaque** — the token looks like a JWT but is encrypted. You cannot decode it to check expiry. Handle 401s gracefully via `api.py:TokenExpiredException`.

6. **Token refresh uses `POST /cias/v1/jwe/refresh`** — not `bias-oidc`. See BUGS.md #18. On status 401 the CLI refreshes once, saves, and retries once. A 401 from refresh still means re-login.

7. **Next-service API is non-critical** — if it fails, we silently return `{}` and show no service data. The status API failure is fatal.

8. **The session token lives only in `~/.my-car-cli/token`** — Windows Credential Manager cannot store a JWE this large (`CredWrite` 1783). See BUGS.md #19. `MY_CAR_AUTH_TOKEN` still overrides the file when it is a JWE.

9. **Dual `sys.stdout.reconfigure`** — happens in both `main.py` and `display.py`. Redundant but harmless.

10. **VIN default is `None`** — the hardcoded real VIN was scrubbed from `config.py`, `api.py`, and `README.md` before the first git commit. Users set their own VIN via `~/.my-car-cli/config.json` (created on first run) or `my-car config --vin <VIN>`.

11. **HAR files in root** — 2 files (~14MB each) with sensitive session tokens. Must not be committed.

12. **`set_stored_token` saves the rotated JWE** on the status 401 path before the single retry.

---

## Config & Storage Locations

```
~/.my-car-cli/
├── config.json           # {"vin": "...", "locale": "en-GB", "pressure_unit": "PSI", "cache_ttl_minutes": 15}
├── cache.json            # {"cached_at": <timestamp>, "vin": "...", "data": {...}}
├── token                 # JWE bearer. The only token store.
└── browser-profile/      # Playwright persistent context (Chrome profile, cookies, localStorage)

Environment:
  MY_CAR_AUTH_TOKEN       # Overrides the token file if set to a JWE
```

---

## Test Data Shapes

### Status API Response (`liveData`)
```json
{
  "liveData": {
    "lastUpdateTimestamp": 1704067200000,
    "levels": [{"value": 50, "type": "ELECTRIC", "unit": "PERCENTAGE", "displayLowFuelWarning": false}],
    "mileage": {"value": 12345, "unit": "MILES"},
    "ranges": [{"value": 100, "type": "ELECTRIC", "unit": "MILES", "displayLowRangeWarning": false}],
    "tires": [
      {"value": 280.0, "type": "FRONT_LEFT", "warning": "OK", "unit": "KPA"},
      {"value": 280.0, "type": "FRONT_RIGHT", "warning": "OK", "unit": "KPA"},
      {"value": 280.0, "type": "REAR_LEFT", "warning": "OK", "unit": "KPA"},
      {"value": 280.0, "type": "REAR_RIGHT", "warning": "OK", "unit": "KPA"}
    ],
    "brakeFluid": {"fluidLevelWarning": false}
  }
}
```

### Next Service API Response
```json
{
  "days": {},
  "mileage": {"remaining": 5000, "status": "OK", "limit": 60000, "unit": "KM"},
  "extent": "UNKNOWN",
  "status": {"value": "OK", "references": ["MILEAGE"]}
}
```
