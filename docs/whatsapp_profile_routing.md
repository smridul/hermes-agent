# WhatsApp profile routing

A single WhatsApp number can route inbound messages to different Hermes
profiles based on the sender's WhatsApp identity. Each profile keeps its
own memory, sessions, skills, hooks, and pairing/allowlist — fully
isolated because each runs as its own Hermes worker subprocess.

## How it works

The existing gateway process (the **ingress**) keeps owning the WhatsApp
bridge and session. When `whatsapp.profile_routing` is configured, the
ingress additionally spawns one Hermes worker subprocess per non-primary
profile, each started with `HERMES_HOME` pinned to that profile's
directory. Inbound WhatsApp messages whose canonical sender ID maps to a
non-primary profile are forwarded over an internal stdin/stdout JSON
pipe to the appropriate worker; the worker's reply travels back through
the ingress's WhatsApp adapter, so the user always sees responses on the
same number they messaged.

The ingress's own profile (the one the gateway booted with) is the
**primary** profile. Messages routed to the primary profile are handled
in-process, exactly as before — there is no subprocess overhead for the
primary path.

## Setup

1. Create the additional profile(s) under your existing Hermes home:
   ```bash
   hermes profile create family
   ```
2. Add a `profile_routing` block to your gateway's `config.yaml`,
   underneath the `whatsapp:` section:
   ```yaml
   whatsapp:
     # ... existing keys (dm_policy, allow_from, etc.) ...
     profile_routing:
       profiles: ["main", "family"]   # all profiles participating in routing
       default_profile: "main"
       sender_profile_map:
         "60123456789": "main"        # owner — routes to main profile
         "60987654321": "family"      # family member — routes to family
   ```
3. Restart the gateway. On boot it spawns one worker subprocess per
   non-primary profile listed in `profiles:` and verifies they emit a
   readiness signal before any inbound message can be routed.

## Sender identifier format

`sender_profile_map` keys are **canonicalised at config load**. All four
of these forms map to the same canonical id `60123456789` and are
accepted equivalently:

- `+60123456789`
- `60123456789@s.whatsapp.net`
- `60123456789@lid`
- `60123456789`

The canonicalisation uses the same helper Hermes applies at message
receive time (`gateway.whatsapp_identity.canonical_whatsapp_identifier`),
so lookups can never silently drift from your config keys.

## Pairing and allowlist

Each profile has its **own** pairing store and allowlist (because each
worker has its own `HERMES_HOME`). Listing a sender in
`sender_profile_map` does NOT approve them — they still need to pass
the target profile's pairing or allowlist gate. Approving sender X for
the `main` profile does NOT approve them for `family`, and vice versa.

This is the intended design: profile isolation is structural, and
authorisation lives inside each profile.

## Unmapped senders

Senders not listed in `sender_profile_map` are routed to
`default_profile`. The default profile's existing `dm_policy` /
`allow_from` / pairing rules then apply.

## Group routing (chat-id based)

In addition to `sender_profile_map`, you can bind a specific WhatsApp
group to a profile via `group_profile_map`. Every message in that group
that survives the inbound mention/free-response gating is handled by the
bound profile, regardless of who sent it.

```yaml
whatsapp:
  profile_routing:
    profiles: ["default", "main", "family"]
    default_profile: "default"
    sender_profile_map:
      "60123456789": "main"
    group_profile_map:
      "120363409860032836@g.us": "family"
```

### Precedence

For a given inbound message:

1. If the message is in a group AND the group's `chat_id` is in
   `group_profile_map`, route to that profile.
2. Otherwise consult `sender_profile_map`.
3. Otherwise route to `default_profile`.

Group binding is **exclusive**: when a group is mapped, the bound profile
is the only legitimate target. If that profile's worker is unavailable
at dispatch time, the message is dropped and an `ERROR` line of the form
`group_routing: chat=<jid> target=<profile> worker_unavailable; dropping
message` is logged. There is no fallback to `sender_profile_map` or
`default_profile` for a bound group — silently degrading would re-create
the security regression the routing feature exists to prevent.

### Group `chat_id` format

`group_profile_map` keys are matched **verbatim** against the bridge's
`chat_id` for inbound messages. Standard WhatsApp groups use the
`<id>@g.us` suffix; community/LID-only groups may use `<id>@lid`. There
is no canonicalisation — copy the JID exactly as it appears in your
gateway logs (`inbound message: ... chat=<jid>`).

### Validation

`group_profile_map` is validated at boot. A target profile that is not
in `profiles`, a non-string key/value, or an empty key all raise
`ProfileRoutingConfigError` and abort gateway start — same fail-closed
posture as `sender_profile_map`.

## Limitations (MVP)

- **Only WhatsApp DMs are routed.** Group messages always go to the
  primary profile.
- **WhatsApp credentials live with the primary profile** (the gateway's
  bootstrap `HERMES_HOME`). The single bridge session is shared across
  all profiles.
- **`unmapped_sender_behavior` only supports `default_profile` in MVP.**
  Other values (`deny`, `pair`, `ignore`) fail at boot with a
  "not yet supported" error.
- **Only WhatsApp is routed.** Other channels (Discord, Slack, Telegram,
  …) run in-process under the primary profile as before.
- **No hot reload.** Changing `profile_routing` requires a gateway
  restart.

## Troubleshooting

- **Gateway fails to boot with "profile X not found"**: create the
  profile first with `hermes profile create X`.
- **Gateway fails to boot with "default_profile must be one of profiles"**:
  add `default_profile`'s name to the `profiles:` list.
- **Worker keeps dying on start**: run
  `HERMES_HOME=<profile path> hermes profile-worker --name <profile> < /dev/null`
  to surface the underlying error on stderr.
- **Replies seem to come from the wrong profile**: confirm the inbound
  sender's canonical id by looking at the gateway logs (every routed
  message logs `profile_routing: dispatch to worker '<profile>'`).

## Design

For the full architecture rationale (including why we chose subprocess
workers over an in-process `ContextVar`-based approach), see
[`docs/superpowers/specs/2026-05-07-whatsapp-sender-profile-routing-design.md`](superpowers/specs/2026-05-07-whatsapp-sender-profile-routing-design.md).
