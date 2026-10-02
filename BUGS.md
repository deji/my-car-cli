# Debug Log — Bugs & Fixes

## #1 — Playwright import typo
**Date:** 2026-07-28
**Status:** Fixed
**Symptom:** `login_interactive_playwright()` crashed on import.
**Root cause:** Typo in `auth.py` — `from playwright.sync_api` instead of `from playwright.sync_api` (actually the real typo was...). The `is_playwright_available()` check passed but the inner `import` failed silently.
**Fix:** Ensure both checks use the same import path. Added explicit `try/except ImportError` wrapping the `sync_playwright` import.

## #2 — Akamai Bot Manager blocks direct login page access
**Date:** 2026-07-28
**Status:** Fixed
**Symptom:** Going directly to `https://id.mercedes-benz.com/ciam/auth/login` resulted in an error/block page. The Mercedes CDN uses Akamai Bot Manager which expects `_abck` and `bm_sz` cookies to be present before serving the login page.
**Root cause:** These cookies are only set by JavaScript running on `www.mercedes-benz.co.uk`. When navigating directly to `id.mercedes-benz.com`, no bot detection has run, so the request is blocked.
**Fix:** Two-phase navigation:
1. First visit `www.mercedes-benz.co.uk/` and wait 6s for Akamai JS to execute and set cookies.
2. Then proceed to the login flow.

## #3 — Wrong login entry URL
**Date:** 2026-07-28
**Status:** Fixed
**Symptom:** Navigating to `www.mercedes-benz.co.uk/passengercars/my-area/my-mercedes-benz.html` after the landing page landed on an error page instead of showing the login form.
**Root cause:** The `my-mercedes-benz.html` page is not the login entry point — it's the post-auth destination. The actual entry point is the CIAS authentication endpoint.
**Fix:** Changed step 2 to navigate to:
`https://api.oneweb.mercedes-benz.com/cias/v1/authentication?prompt=DEFAULT&locale=en_GB&tenantId=oneweb&redirectUrl=https%3A%2F%2Fwww.mercedes-benz.co.uk%2Fpassengercars%2Fmy-area%2Fmy-mercedes-benz.html%3Fb2xProvider%3DCIAS%26b2xFlow%3DLOGIN&forceMfa=false`
This orchestrates the full OAuth redirect chain through `id.mercedes-benz.com/ciam/auth/login` with correct state/tenant params.

## #4 — Session cookies lost between CLI invocations
**Date:** 2026-07-28
**Status:** Fixed
**Symptom:** Every login run started with a fresh browser profile, triggering Akamai's "new device" detection on every attempt.
**Root cause:** `browser.new_context()` creates an ephemeral incognito session with no persistent storage.
**Fix:** Switched to `p.chromium.launch_persistent_context(user_data_dir=...)` at `~/.my-car-cli/browser-profile/`. All cookies (Akamai, GA, consent), localStorage, and IndexedDB persist across sessions.

## #5 — Token capture only from request headers
**Date:** 2026-07-28
**Status:** Fixed
**Symptom:** Token capture relied solely on intercepting `Authorization: Bearer ...` headers on outgoing requests to `api.oneweb.mercedes-benz.com`. If the browser redirect flow completed before any such request was made, the token was missed.
**Root cause:** The token also appears in the CIAM callback redirect URL as a query parameter (`?token=xxx`).
**Fix:** Added a `handle_response` listener that parses the `token=` parameter from 3xx redirect `Location` headers and from the final page URL. Dual capture: request interceptor + redirect URL parser.

## HAR Flow Reference
The full login flow (from HAR analysis):
```
1. GET  www.mercedes-benz.co.uk/                          → 200 (Akamai JS sets _abck, bm_sz)
2. GET  api.oneweb.../cias/v1/authentication?prompt=...    → 302 → id.mercedes-benz.com/ciam/auth/login
3. User enters credentials on CIAM login page
4. POST id.mercedes-benz.com/as/.../authorization.ping     → 302 (JWT token in form body)
   Set-Cookie: CIAM.DEVICE, ciamMB, PF
5. GET  api.oneweb.../cias/v1/authentication/ciam-callback → 303
   Location: mercedes-benz.co.uk/...?token=xxx
6. GET  mercedes-benz.co.uk/...?token=xxx                  → 200 (token captured here)
   Subsequent API calls use Authorization: Bearer {jwe_token}
```

## #6 — ERR_QUIC_PROTOCOL_ERROR aborts CIAS auth navigation
**Date:** 2026-07-28
**Status:** Fixed
**Symptom:** `my-car login` printed `Auth flow page note (continuing): Page.goto: net::ERR_QUIC_PROTOCOL_ERROR at https://api.oneweb.mercedes-benz.com/cias/v1/authentication?...`. Step 2 of the login flow never actually navigated; the script swallowed the error and continued with a broken browser state.
**Root cause:** Chromium negotiated HTTP/3 (QUIC) for `api.oneweb.mercedes-benz.com` (the host advertises Alt-Svc / h3) and the QUIC handshake failed. The failure is intermittent — it depends on cached Alt-Svc state in the persistent profile and network conditions, so a fresh profile often doesn't reproduce it, but the user's real profile did.
**Fix:** Pass `args=["--disable-quic"]` to `launch_persistent_context()` in `auth.py`. This forces TCP (HTTP/2 / HTTP/1.1) for all hosts, making the QUIC error class impossible. HTTP/3 is not required for this flow.
**Validation:** Headless smoke test with a throwaway profile navigated the CIAS URL → `status=200`, final URL `https://id.mercedes-benz.com/ciam/auth/login` (correct redirect chain). Note: the error is intermittent so a control run on a *fresh* profile didn't reproduce it, but disabling QUIC deterministically prevents it.

## #7 — Stale bearer token captured from landing page (caused 401 on `status`)
**Date:** 2026-07-28
**Status:** Fixed
**Symptom:** `my-car login` reported `Token captured and saved successfully!`, but `my-car status` immediately returned `🔒 Session Expired` (401). The saved token was already expired.
**Root cause:** The `page.on("request"/"response")` capture listeners were attached *before* step 1 (the landing-page visit). With a persistent browser profile, the logged-in landing page's JS replays the **old, expired** bearer token from a previous session via an `api.oneweb.mercedes-benz.com` request. `handle_request` captured that stale token immediately and set `captured_token`, after which the `if captured_token: return` guard skipped every subsequent capture — including the **fresh** token issued by the real CIAS login flow. Result: an expired token got saved.
**Fix:** In `auth.py`, moved `page.on("request", ...)` / `page.on("response", ...)` to *after* step 1 and just before step 2 (the CIAS `page.goto`). Now only tokens produced by the actual auth flow are captured, which are always fresh.
**Validation:** Unit tests `test_looks_like_jwe` and `test_extract_token_from_url` in `tests/test_cli.py` cover the capture-validation logic. The structural (listener-move) fix is correct by inspection.

## #8 — Weak token validation let bogus/short tokens be captured
**Date:** 2026-07-28
**Status:** Fixed
**Symptom:** `handle_response` accepted any `?token=` value with `len > 10`, so short non-JWE tokens (CSRF/session/etc.) could be captured and saved, producing 401s.
**Root cause:** No format validation — CIAS JWE tokens always start with `eyJ` and are several hundred+ chars, but the check only verified length > 10.
**Fix:** Added `_looks_like_jwe(token)` (requires `startswith("eyJ")` and `len > 100`) and `_extract_token_from_url(url)` in `auth.py`. Both `handle_request` (Bearer header) and `handle_response` (redirect/page URL) now use them.
**Validation:** `test_looks_like_jwe` and `test_extract_token_from_url` in `tests/test_cli.py` (6/6 tests pass).

## #9 — ERR_HTTP2_PROTOCOL_ERROR on post-login page (login hangs)
**Date:** 2026-07-28
**Status:** Fixed
**Symptom:** After entering credentials and clicking login, the browser showed `This site can't be reached ... ERR_HTTP2_PROTOCOL_ERROR` on `https://www.mercedes-benz.co.uk/passengercars/my-area/my-mercedes-benz.html?b2xProvider=CIAS&b2xFlow=LOGIN&token=<uuid>`. The CLI then **hung** (no token captured, no timeout message for ~5 min).
**Root cause:** Chromium's HTTP/2 stack failed on the large authenticated `my-mercedes-benz.html` response (malformed/reset stream). The post-login redirect URL carries a **UUID** intermediate token (`<callback-uuid>`), NOT the JWE bearer token — the JWE is issued by JS on that page calling `api.oneweb.mercedes-benz.com`. Because the page never loaded, that JS never ran, no `Authorization: Bearer <jwe>` request fired, and the capture loop had nothing to capture. Same error class as #6 (QUIC) but on HTTP/2.
**Note:** The error is intermittent and tied to the real authenticated response body — it does NOT reproduce with a stale token in a headless test (verified: control run redirected cleanly). This is a Chromium-specific HTTP/2 framing issue; the user's real Firefox 153 browser does not exhibit it.
**Fix:** Added `--disable-http2` to `launch_persistent_context()` args in `auth.py` (alongside `--disable-quic`). Forces HTTP/1.1, which has no HTTP/2 framing to break. Verified headless that the flag is accepted and navigation succeeds.
**Important distinction:** The `?token=<uuid>` in the post-login URL is an intermediate token, not the bearer JWE. The `_looks_like_jwe` validator from #8 correctly REJECTS this UUID, so it is never saved as the bearer token. The real JWE is captured only via `handle_request` from the `Authorization: Bearer` header on the subsequent `api.oneweb` API call.

## #10 — Capture loop hung silently on browser navigation error
**Date:** 2026-07-28
**Status:** Fixed
**Symptom:** When the post-login page failed to load (see #9), the CLI showed no error and waited the full 5-minute `LOGIN_TIMEOUT_SECONDS` before responding.
**Root cause:** The wait loop only checked `captured_token`, `page.is_closed()`, and the wall-clock timeout. It did not detect that the browser had landed on a Chromium error page (`chrome-error://chromewebdata/`).
**Fix:** In the wait loop in `auth.py`, check `page.url.startswith("chrome-error://")` each iteration. On detection, print a clear message naming the error page and break immediately (within ~1s), then fall through to `login_manual_paste()` as a fallback. A `nav_error` flag suppresses the redundant "Could not capture token" message in that path.

## #11 — Firefox UA on Chromium engine caused post-login hang
**Date:** 2026-07-28
**Status:** Fixed
**Symptom:** User could enter credentials on the CIAM login page and Mercedes confirmed successful login (security email received: "new sign-in to your Mercedes me account on Windows/Firefox"). But the browser then hung on the site's loading screen indefinitely — no token captured, CLI stuck waiting.
**Root cause:** `USER_AGENT` was hardcoded to a Firefox 153 string (`rv:153.0 ... Firefox/153.0`) while the actual browser engine is Chromium (Playwright). Mercedes' JS detected "Firefox" from the UA and served Firefox-specific code paths (possibly involving service workers, polyfills, or different bundle paths) that don't execute correctly under Chromium. The post-login page JS never completed, so no `api.oneweb.mercedes-benz.com` request with `Authorization: Bearer <jwe>` was ever fired — the capture loop had nothing to catch.
**Fix:** Removed the `user_agent=USER_AGENT` override from `launch_persistent_context()` in `auth.py`. Playwright now provides its default Chromium UA (`Chrome/149.0.7827.55` in headed mode), which matches the actual browser engine. Deleted the `USER_AGENT` constant.
**Validation:** Syntax check passes, 6/6 unit tests pass. Default UA confirmed as a valid Chrome string. Note: users with an existing browser profile from the Firefox-UA era should run `my-car login --fresh` once to clear stale profile state.

## #12 — Post-login page stuck "pending" (bot detection + HTTP/2 disabled)
**Date:** 2026-07-28
**Status:** Fixed
**Symptom:** After fixing #11 (Firefox UA), login still succeeded (Mercedes security email confirmed sign-in), but the browser hung on the post-login page. Network tab showed `https://www.mercedes-benz.co.uk/passengercars/my-area/my-mercedes-benz.html?b2xProvider=CIAS&b2xFlow=LOGIN&token=<uuid>` stuck as "pending" — no error, no response, just hanging indefinitely.
**Root cause:** Two issues compounding:
1. **HTTP/2 disabled** (from #9 fix) — the original `ERR_HTTP2_PROTOCOL_ERROR` was caused by the Firefox UA serving wrong content, not by HTTP/2 itself. With the Chrome UA now in place, forcing HTTP/1.1 caused a protocol mismatch with Akamai's CDN, which expects HTTP/2 for authenticated responses.
2. **Akamai bot detection** — Playwright's Chromium sets `navigator.webdriver=true` and other automation markers by default. Akamai Bot Manager detects these and silently stalls the connection (holds it "pending" without responding, rather than returning an error).
**Fix:** In `auth.py`:
- Removed `--disable-http2` from launch args (re-enabled HTTP/2). Kept `--disable-quic` (HTTP/3 still unnecessary).
- Added `--disable-blink-features=AutomationControlled` to suppress the `navigator.webdriver` flag at the Blink engine level.
- Added `context.add_init_script()` to override `navigator.webdriver`, `navigator.languages`, `navigator.plugins`, and `window.chrome` — making the browser look like a real Chrome session.
**Validation:** Syntax check passes, 6/6 unit tests pass. The init script runs before any page JS, so Akamai's bot detection sees a clean fingerprint.

## Open Questions
- Token expiry: JWE tokens are opaque — no way to check expiry without hitting the API and getting 401. `GET /cias/v1/validation` exists (see #18) and is not wired. Proactive validation is still open.
- Token refresh: **Done — see #18.** `POST /cias/v1/jwe/refresh` rotates the JWE. The `bias-oidc` path in #14 was the first guess and returns 404. A 401 from refresh still means a full re-login. The grace window after the status API starts returning 401 is still unmeasured.

## #13 — Stale UUID in keyring shadowed valid JWE in token file (caused 401 on `status`)
**Date:** 2026-07-28
**Status:** Fixed
**Symptom:** `my-car login` reported success, but `my-car status` immediately returned `🔒 Session Expired` (401). The API responded: `{"status":401,"errorKey":"unauthorized","message":"Unencrypted token are not supported."}`
**Root cause:** The keyring contained a **UUID** (`<stale-callback-uuid>`, 36 chars) — the intermediate `?token=<uuid>` from the CIAS callback redirect (see #9) — not a JWE. This UUID was stored in the keyring during an early login attempt **before** the `_looks_like_jwe` validator (#8) was added. A subsequent login captured a valid JWE but `keyring.set_password` failed (Windows Credential Manager quirk), so `set_stored_token` fell back to writing the JWE to `~/.my-car-cli/token` (the file fallback). The old UUID remained in the keyring.
`get_stored_token()` checked keyring **first** and returned the UUID without any format validation — it never fell through to the token file that held the working JWE. Result: every API call sent `Authorization: Bearer <uuid>`, which the API rejected with 401.
**Fix:** Three changes in `auth.py`:
1. **`get_stored_token()`** — every token source (env var, keyring, token file) is now validated with `_looks_like_jwe()`. If a source returns a non-JWE, it's skipped and the next source is tried. When a stale keyring entry is detected, it's also **deleted** (lazy cleanup) so it doesn't shadow the file on every future call.
2. **`set_stored_token()`** — refuses to store non-JWE tokens (returns `False`). Prevents bad tokens from entering storage in the first place. Return type changed from `None` to `bool`.
3. **`login_manual_paste()`** — re-prompts the user if the pasted token doesn't look like a JWE, with guidance to copy the `Authorization` header (not the URL `?token=` param).
**Validation:** 7 new unit tests in `TestTokenStorage` (keyring fallthrough, env var validation, set/get reject non-JWE, all-invalid → None). All 13 tests pass. End-to-end: `my-car status` now returns live data (200) instead of Session Expired. Verified that the stale UUID was auto-deleted from keyring on the first `get_stored_token()` call after the fix.

**Later:** Credential Manager was removed in #19. The token file is the only store. Do not put keyring back; `CredWrite` cannot hold this JWE.

---

## #14 — Token refresh endpoint discovered (research)
**Date:** 2026-07-28
**Status:** Superseded by #18. The host path in this entry is `bias-oidc/v1`. That path returns 404. Production is `https://api.oneweb.mercedes-benz.com/cias/v1`. Refresh is implemented and a live `POST /cias/v1/jwe/refresh` returned 200.

**Trigger:** This file's Open Questions — "Token refresh: No refresh token flow discovered."

**Finding:** A refresh flow DOES exist. It is not classic OAuth2 `refresh_token` grant — it is a **JWE rotation** model: the (possibly-stale) JWE itself is the refresh credential. The Mercedes web app's auth client (`cias-login.mjs`, a public static asset at `assets.oneweb.mercedes-benz.com/plugin/iam-authentication/latest/chunks/cias-login.mjs`) exposes it. Both local HAR captures include the JS source defining it (no actual refresh HTTP call was captured — see Caveats).

**Endpoints as first read from the bundle** (this base is wrong for production — see the status line. `int.` / `test.` prefixes exist for non-prod):

| Method | Path | Purpose | Required headers |
|:---|:---|:---|:---|
| GET | `/validation` | Check if current JWE is still valid | `Authorization: Bearer <jwe>`, `ApplicationName: b2xlc`, `Accept: application/json` |
| POST | `/jwe/refresh` | **Rotate to a fresh JWE** | `Authorization: Bearer <old_jwe>`, `tenantId: oneweb`, `accept: application/json` |
| PUT | `/claims?scope=FULL_NO_CIAM_ID` | Fetch user claims | `Authorization: Bearer <jwe>`, `ApplicationName: b2xlc` |
| GET | `/jwe/{intermediate_token}` | Post-login exchange: CIAS callback UUID → real JWE | — |

`POST /jwe/refresh` returns `{ "jwe": "<new_JWE>" }`. Save the new JWE and use it as the bearer for subsequent `api.oneweb` calls.

**Web app flow** (de-minified from `cias-login.mjs`, the "LOGGED_IN_CIAS" handler that runs after CIAS login completes):
```js
const svc = createAuthService();              // F() in the bundle
if (!await svc.validate(currentJwe)) {        // GET /validation
  const newJwe = await svc.refreshToken(currentJwe);  // POST /jwe/refresh
  const claims  = await svc.fetchClaims(newJwe);     // PUT /claims
}
```

**Why the mobile-app angle matters:** Refresh is tenant-scoped (`tenantId: oneweb`), not app-scoped. `status` and `next-service` already use that tenant on `api.oneweb.mercedes-benz.com`. The Mercedes **me** app (`x-application-name: MMA` on next-service) uses the same backend and calls `/jwe/refresh` in the background. The CLI does the same on 401. The host path is `/cias/v1`, not `/bias-oidc/v1`.

**Caveats:**
1. **The HAR did not record a refresh call.** Both captures were taken while the token was still valid, so `validate()` would have returned true and refresh never fired. The 200 from `POST /cias/v1/jwe/refresh` was confirmed later, in #18. The exchange call is still unverified live.
2. **Refresh window is unknown.** Mercedes calls `validate()` first and refreshes only if it fails — implying refresh works during a grace window *after* the access token stops working on the status API. Duration unknown (hours? weeks?). Only a live test with an expired token will tell.
3. **No separate refresh_token to store.** The JWE is the credential. If the refresh window has fully expired, the only option is full re-login (current behavior). Refresh is a "silent renew," not a way to recover a long-dead session.

**Proposed implementation** (done, with the base URL corrected in #18; option (a) was chosen, so `status --refresh` still means bypass cache):
1. **`api.py` — `refresh_token(token)`**: `POST /cias/v1/jwe/refresh` with `Authorization: Bearer {token}`, `tenantId: oneweb`, `accept: application/json`. On 200, parse `.jwe` from JSON, save via `set_stored_token()`, return the new JWE. On 401, raise `TokenExpiredException`. The original draft of this step said `/bias-oidc/v1`; do not use that host.
2. **`api.py` — `validate_token(token)` (optional helper, still not built)**: `GET /cias/v1/validation` with `Authorization: Bearer {token}`, `ApplicationName: b2xlc`. Returns boolean.
3. **`main.py` — auto-retry on 401**: in the `status` handler, catch `TokenExpiredException`, call `refresh_token()` once, retry `fetch_vehicle_status()` with the new JWE. Surface "Session Expired" only if refresh also 401s. The existing failure mode stays as the fallback.
4. **Flag-clash to resolve**: `status --refresh` currently means "bypass cache." If we add a token-refresh concept, either (a) keep refresh fully automatic (no flag — transparent, mirrors the mobile app), or (b) rename cache bypass to `--no-cache` and use `--refresh-token` for an explicit refresh. Recommendation: automatic, no flag.
5. **Tests**: add unit tests for `refresh_token()` (mock httpx) before wiring into `main.py`, following the existing `TestTokenStorage` pattern in `tests/test_cli.py`.

**Open items:**
- Live-verify `/jwe/refresh`: done in #18 against `/cias/v1`, not this entry's base URL. It returned 200.
- Auto-retry vs. an explicit refresh command: auto-retry only. No new flag.
- Measure the refresh grace window once live: still open.

---

## #15 — Public repository privacy audit
**Date:** 2026-07-28
**Status:** Fixed.

**Finding:** HAR captures were correctly ignored but contain live browser session data and must not be uploaded manually. The original commit also contained a personal author email, a partial callback identifier, and test fixtures copied from live vehicle telemetry.

**Fix:** Replaced the callback identifier and telemetry with synthetic values. Expanded `.gitignore` to cover common local environment and private-key files. Rewrote the single commit with a GitHub no-reply author and committer identity so the original contents are not reachable from branch history. A secret scan found no committed bearer tokens, passwords, private keys, API keys, or VINs.

**Safeguard:** Push with Git rather than manually uploading the directory, and keep the ignored HAR captures local. Use a GitHub-provided no-reply identity for future commits if personal email disclosure is not desired.

---

## #16 — Public branding renamed to My Car CLI
**Date:** 2026-07-28
**Status:** Fixed.

**Reason:** Avoid presenting the project as an official or vendor-branded CLI while retaining the required service URLs and a concise MB EQB compatibility note in the README.

**Fix:** Renamed the distribution to `my-car-cli`, Python package to `my_car_cli`, executable to `my-car`, local data directory to `~/.my-car-cli`, keyring service to `my-car-cli`, and token environment variable to `MY_CAR_AUTH_TOKEN`. Updated user-facing text, tests, and documentation. No compatibility aliases were retained because the project has not been publicly released.

---

## #17 — Token refresh + callback UUID exchange wired
**Date:** 2026-09-30
**Status:** Superseded by #18. The URLs in this entry were what the first wiring called. They 404. The behavior (one refresh, then one retry; UUID exchanged once) is unchanged.

**Changes:**
1. **`main.py` — `status` auto-retry**: on `TokenExpiredException` from `fetch_vehicle_status()`, the handler calls `refresh_token()` once, saves the rotated JWE with `set_stored_token()`, and retries `fetch_vehicle_status()` exactly once with the new token. The "Session Expired / re-login" panel appears only if the refresh or the retry also fails. Refresh is fully automatic — no new flag (implements #14 option (a)). The first commit of this called `POST /bias-oidc/v1/jwe/refresh`. The live path is `POST /cias/v1/jwe/refresh` (#18).
2. **`auth.py` — Playwright wait loop**: each CIAS callback UUID (`?token=<uuid>` on a `mercedes-benz.co.uk` page URL) is exchanged at most once per login session via `extract_intermediate_uuid()` + `exchange_intermediate_token()`. This recovers a JWE when the post-login page's JS never fires an `Authorization: Bearer` request (cf. #9/#10). A failed exchange prints a one-line note and the loop keeps waiting for the header-captured JWE. The first commit called `GET /bias-oidc/v1/jwe/{uuid}`. The live path is `GET /cias/v1/jwe/{uuid}`, and that body is raw text (#18).
3. **`status --refresh` still means cache bypass only** — it does not force a token refresh; cache bypass and JWE rotation are separate concerns.

**Caveats:** Refresh was live-verified in #18 (HTTP 200). The exchange endpoint has not been called live. The refresh grace window (#14 caveat 2) is still unknown. A 401 from refresh still means a full re-login.

**Tests:** mocked `httpx` tests for `refresh_token()` / `exchange_intermediate_token()` (`tests/test_api.py`); at-most-once callback UUID exchange tests and `CliRunner` tests for the `status` 401 → refresh → retry path, including `--refresh` cache-bypass behavior (`tests/test_cli.py`).

---

## #18 — Refresh 404: live path is `/cias/v1`, not `/bias-oidc/v1`
**Date:** 2026-09-30
**Status:** Fixed. Live `POST /cias/v1/jwe/refresh` returned 200.

**Symptom:** `my-car status` printed `Token refresh API error (404)` after the status call returned 401.

**Root cause:** #14 and #17 called `POST https://api.oneweb.mercedes-benz.com/bias-oidc/v1/jwe/refresh`. The auth bundle does contain a `bias-oidc` constant, but the client that actually refreshes (`be()` in the login HAR's `cias-login` script) sets `prefixUrl` from `ue.PROD`, which is `https://api.oneweb.mercedes-benz.com/cias/v1/`. The same capture records live `GET /cias/v1/jwe/{uuid}` and `PUT /cias/v1/claims`. `GET /cias/v1/jwe/{uuid}` returns the JWE as raw text (`response.text()`), not `{"jwe": ...}`. `POST jwe/refresh` still returns JSON `{ "jwe": "..." }`.

**Fix:** `refresh_token` and `exchange_intermediate_token` now use `https://api.oneweb.mercedes-benz.com/cias/v1`. Exchange accepts either a raw JWE body or JSON `{ "jwe": "..." }`. Headers for refresh are unchanged: `Authorization: Bearer`, `tenantId: oneweb`, `Accept: application/json`.

**Follow-up:** A live `POST /cias/v1/jwe/refresh` with the stored JWE returned 200. Saving that JWE through Windows Credential Manager failed (`CredWrite` 1783, blob too large for the ~4800-character token). See #19.

---

## #19 — Drop Windows Credential Manager; store the JWE on disk
**Date:** 2026-10-01
**Status:** Fixed.

**Reason:** `CredWrite` returns 1783 for this JWE. The token is about 4800 characters, past what Credential Manager will store, so every save fell through to `~/.my-car-cli/token` anyway.

**Fix:** `get_stored_token`, `set_stored_token`, and `delete_stored_token` use only `~/.my-car-cli/token`. `MY_CAR_AUTH_TOKEN` still overrides that file when the value is a JWE. The `keyring` dependency is removed. The existing `my-car-cli` / `bearer_token` credential is deleted so it cannot shadow the file.
