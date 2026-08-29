# MeshCore Packet Format — Notes

Reference notes on the MeshCore wire format, as implemented by the decoder in this repo. Cross-checked against the official [MeshCore protocol docs](https://github.com/meshcore-dev/MeshCore) and verified byte-for-byte against real packets captured over the air.

## Header byte

```
bit 0-1 : route type
bit 2-5 : payload type
bit 6-7 : version
```

| Route type | Value |
|---|---|
| `TRANSPORT_FLOOD` | `0x00` |
| `FLOOD` | `0x01` |
| `DIRECT` | `0x02` |
| `TRANSPORT_DIRECT` | `0x03` |

| Payload type | Value |
|---|---|
| `REQ` | `0x00` |
| `RESPONSE` | `0x01` |
| `TXT_MSG` | `0x02` |
| `ACK` | `0x03` |
| `ADVERT` | `0x04` |
| `GRP_TXT` | `0x05` |
| `GRP_DATA` | `0x06` |
| `ANON_REQ` | `0x07` |
| `PATH` | `0x08` |
| `TRACE` | `0x09` |
| `MULTIPART` | `0x0A` |
| `CONTROL` | `0x0B` |
| `RAW_CUSTOM` | `0x0F` |

`TRANSPORT_FLOOD`/`TRANSPORT_DIRECT` route types are followed by a 4-byte transport-code field before the path.

## Routing path

Immediately follows the header (and transport codes, if present): one `path_len` byte, then the path itself.

```
hash_size  = ((path_len_byte >> 6) & 0x03) + 1   # bytes per hop (1, 2, or 3)
hash_count = path_len_byte & 0x3F                 # number of hops recorded so far
path_bytes = hash_size * hash_count
```

Each hop is `hash_size` bytes — the first byte(s) of the relaying node's public key. For `FLOOD` packets this grows by one entry per relay; an empty path means the packet is being heard directly from its origin.

## Advert payload (`0x04`)

```
public_key   32 bytes
timestamp    4 bytes,  uint32 LE
signature    64 bytes
appdata      rest
```

`appdata` starts with a `flags` byte:

```
bit 0-3 : node type   (0x01 chat node, 0x02 repeater, 0x03 room server, 0x04 sensor)
bit 4   : has_location
bit 5   : has_feature1
bit 6   : has_feature2
bit 7   : has_name
```

Fields present are packed in order, only if their flag bit is set:

| Field | Size | Notes |
|---|---|---|
| latitude | 4 bytes, int32 LE | decimal degrees × 1,000,000 — **not a float** |
| longitude | 4 bytes, int32 LE | decimal degrees × 1,000,000 |
| feature1 | 2 bytes | reserved, meaning not yet determined |
| feature2 | 2 bytes | reserved, meaning not yet determined |
| name | remainder | UTF-8, null-padded |

The node's own identifying hash (used everywhere else in the protocol as a 1-byte node reference) is simply `public_key[0]`.

## Encrypted payload envelope (`REQ`, `RESPONSE`, `TXT_MSG`, `PATH`)

```
dest_hash    1 byte
src_hash     1 byte
cipher_mac   2 bytes
ciphertext   rest
```

`ciphertext` is encrypted with a shared secret derived via X25519 ECDH between the two communicating nodes' key pairs — it cannot be decrypted by a passive receiver under any circumstances, since that requires one side's *private* key.

## Group payload envelope (`GRP_TXT`, `GRP_DATA`)

```
channel_hash   1 byte
cipher_mac     2 bytes
ciphertext     rest
```

## Anonymous request (`ANON_REQ`, `0x07`)

```
dest_hash        1 byte
sender_pubkey    32 bytes   (cleartext)
cipher_mac       2 bytes
ciphertext       rest
```

## Control (`0x0B`) — always cleartext

```
flags   1 byte   (bits 4-7 = sub-type)
data    rest
```

### `DISCOVER_RESP` (sub-type `0x9`)

```
tag        4 bytes, uint32 LE
reserved   1 byte
pubkey     32 bytes   (cleartext — full public key, not just a hash)
```

Confirmed byte-for-byte against three captured packets: the trailing 32 bytes exactly match known nodes' public keys from their adverts.

### `DISCOVER_REQ` (sub-type `0x8`)

```
type_filter   1 byte    (exact semantics not independently confirmed)
tag           4 bytes, uint32 LE
reserved      4 bytes
```

Byte layout confirmed identical across two captured packets; the meaning of `type_filter` is a best-effort interpretation based on public documentation, not verified against source.

## Trace (`0x09`) — always cleartext

```
tag          4 bytes, uint32 LE
auth_code    4 bytes, uint32 LE
flags        1 byte
path_hashes  rest (1 byte per hop)
```

Used for path diagnostics — collects the chain of node-hashes (and, per the MeshCore firmware, per-hop SNR — not currently exposed at this layer of the protocol as received here).

## PMT symbol extraction (GNU Radio–specific gotcha)

`gr-lora_sdr`'s `lora_rx` block publishes the decoded payload as a PMT **symbol** rather than a `u8vector`/PDU. `pmt.symbol_to_string()` performs a strict UTF-8 decode when converting to a Python string, which crashes on arbitrary binary payloads (i.e. almost every real packet). The workaround used in this decoder is to call `pmt.serialize_str()` — which returns raw `bytes`, no text decoding involved — and manually parse the PMT binary serialization format (`[0x02 tag][2-byte big-endian length][raw bytes]` for a symbol) to recover the original payload bytes.
