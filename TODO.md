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
