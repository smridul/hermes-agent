# Obtaining an Alexa refresh token

The `alexa-voice` skill needs an Amazon `REFRESH_TOKEN`. Getting one is the single hard step,
and it is **fragile and not reliably repeatable**. Back up the token you have rather than
planning to mint another.

## What works

Run the login **on a local machine, at `http://127.0.0.1`** — not on the server.

```bash
curl -L -o alexa-cookie-cli \
  https://github.com/adn77/alexa-cookie-cli/releases/download/v5.0.5/alexa-cookie-cli-macos
chmod +x alexa-cookie-cli
./alexa-cookie-cli -q -p amazon.com -a en_US -L en-US -H 127.0.0.1 -P 8080 > token.txt
```

Then open `http://127.0.0.1:8080` and sign in to Amazon. On success the output contains
`refreshToken: Atnr|...`.

Install it (quoted — the `|` is a shell pipe otherwise):

```bash
printf "REFRESH_TOKEN='%s'\n" "$TOKEN" > /opt/data/alexa/.env
chown 10000:10000 /opt/data/alexa/.env && chmod 600 /opt/data/alexa/.env
```

Verify: `/opt/data/alexa/alexa -a` should list the account's Echo devices.

## Expect the login to fight you

Observed 2026-09-07 while setting this up:

- Repeated redirects to `/a/c/404` ("Looking for Something?") after a correct password and
  OTP. This is Amazon refusing the OpenID device-authorization leg, **not** bad credentials.
- The OTP field clearing itself, requiring several attempts.

It eventually succeeded after persisting through several attempts. Do not conclude it is
permanently broken after a few `/a/c/404`s — that call was made prematurely during setup and
was wrong.

## Ruled out as causes (do not re-investigate)

- **Datacenter IP** — localhost fails identically, so the server's IP is not the issue.
- **User-agent** — only affects `curl`, which Amazon answers with `503` and an anti-bot page.
  Test with a browser UA. This does not affect real browsers.
- **`alexa-cookie2` version** — 5.0.5 and 5.0.6 behave the same.
- **Marketplace config** — `amazon.com` / `pitangui.amazon.com` are correct for a US account.

## A real bug that matters if you serve the proxy over a network

`alexa-cookie2` strips `Secure` from proxied cookies but leaves `SameSite=None`. Chrome
rejects that combination outright, so a login served over plain HTTP on a LAN/Tailscale IP
loops back to the sign-in page forever. Upstream: `Apollon77/alexa-cookie#217`.

`localhost` is exempt from that rule, which is why the local flow above is the only one worth
using. Serving it over a network needs real TLS *and* a patch to preserve `Secure`.

## Revoking

The token registers a device named `alexa_cookie_cli` on the Amazon account. Deregistering it
from the Amazon device list revokes access immediately.

## Direct smart-home API (preferred over voice)

Once the cookie exists, devices can be read and controlled directly — no Echo involved:

- `GET /api/behaviors/entities?skillId=amzn1.ask.1p.smarthome` — device list with entity IDs
- `POST /api/phoenix/state` with `{"stateRequests":[{"entityId":..,"entityType":"ENTITY"}]}` — read state
- `PUT /api/phoenix/state` with `{"controlRequests":[{..,"parameters":{"action":"turnOn"}}]}` — control

`entityType` must be **`ENTITY`**. `APPLIANCE` returns `TargetApplianceNotFoundException`.

`/api/phoenix` (no suffix) returns HTTP 299 with an empty body — it is not the right endpoint.

Wrapped as `/opt/data/alexa/alexa-device` (source in
`skills/smart-home/alexa-voice/scripts/alexa-device`).
