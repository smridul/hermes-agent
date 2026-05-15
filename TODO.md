# TODO

## WhatsApp bridge: cache quoted-media by stanzaId to avoid re-download

Context: when the WhatsApp trigger message is a reply to a media message
(forwarded file, prior upload, etc.), the bridge now re-downloads the
quoted media from WhatsApp servers via `downloadMediaMessage` using the
`mediaKey` carried in `contextInfo.quotedMessage`. See
`scripts/whatsapp-bridge/bridge.js` — the block guarded by
`if (!hasMedia && quotedMessage && quotedMessageId)`.

This re-download happens on every reply-with-quoted-media, even though
the bridge typically already downloaded the original message when it
first arrived. For chatty groups with media-heavy replies it's wasted
bandwidth and IO.

Optimization: keep an in-memory `Map<stanzaId, localPath>` of every media
the bridge has downloaded during this session. When handling a reply,
look up `contextInfo.stanzaId` first; if present, reuse the cached path.
Fall back to the current Baileys re-download path on miss (covers bridge
restarts and replies to messages never seen by this bridge instance).

Cache should be bounded (LRU, ~500 entries) and entries should reference
files that may have been swept by the host's cleanup — verify
`existsSync(cachedPath)` before reusing.

Out of scope until needed; current behavior is correct, just suboptimal.

## Session Branching / Checkpoints

Save and restore conversation state at any point. Branch off to explore
alternatives without losing progress.

What other agents do:
- Pi: full branching — create branches from any point in conversation,
  branch summary entries, parent session tracking for tree-like session
  structures.
- Cline: checkpoints — workspace snapshots at each step with
  Compare/Restore UI.
- OpenCode: git-backed workspace snapshots per step, with weekly gc.

Our approach:
- `checkpoint` tool: saves current message history + working directory
  state as a named snapshot.
- `restore` tool: rolls back to a named checkpoint.
- Stored in `~/.hermes/checkpoints/<session_id>/<name>.json`.
- For file changes: git stash or tar snapshot of working directory.
- Useful for: "let me try approach A, and if it doesn't work, roll back
  and try B".
- Later: full branching with tree visualization.
