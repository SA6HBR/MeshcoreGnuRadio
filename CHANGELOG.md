# Changelog

All notable changes to the MeshCore decoder (`meshcore_rx_epy_block_0.py`) are documented here.

## Unreleased

### Added
- Full header decoding: route type (Direct/Flood/Transport), payload type, and version, replacing the original hardcoded `header == 0x12` check.
- Correct variable-length routing-path parsing (`hash_size` / `hash_count` decoded from the `path_len` byte), including the list of relay node-hashes for each hop.
- `Advert` payload fully decoded per the MeshCore appdata `flags` byte (node type, optional GPS location, optional name) instead of fixed offsets.
- Cleartext envelope parsing for encrypted payload types: `REQUEST`, `RESPONSE`, `TXT_MSG`, `PATH` (dest/src node-hash + MAC), `GRP_TXT`/`GRP_DATA` (channel hash + MAC), and `ANON_REQ` (dest hash + full sender public key + MAC).
- `CONTROL` packet parsing, including `DISCOVER_REQ` (`0x8`) and `DISCOVER_RESP` (`0x9`) sub-types — the latter reveals a node's full public key in cleartext.
- `TRACE` payload parsing (tag, auth code, flags, traced hop list).
- Node-hash → node-name lookup table (`known_nodes`), built automatically from received adverts, used to annotate every other packet type.
- Node-hash → public-key lookup table (`known_pubkeys`), populated from both adverts and `DISCOVER_RESP`.
- CSV export (`output/mapAdvert.csv`) of every received advert, timestamped, appended (not deduplicated).

### Fixed
- **Crash on every packet**: `pmt.symbol_to_string()` enforces strict UTF-8 decoding and threw `UnicodeDecodeError` on the binary LoRa payload before any parsing could run. Replaced with `pmt.serialize_str()` + manual PMT-serialization parsing, which never performs a text decode.
- **Latitude/longitude were garbage**: originally parsed as 32-bit floats; per the MeshCore spec they are signed 32-bit integers scaled by 1,000,000.
- **Adverts sent via `FLOOD` routing (header `0x11`) were silently dropped**: the original code only accepted `header == 0x12` (`DIRECT` route). Payload type is now decoded independently of route type.
- **`Can't create an instance of your block` (missing `__file__`)**: GNU Radio Companion instantiates embedded Python blocks at design time via `exec()`, where `__file__` is not defined. Added a `sys.argv[0]`-based fallback.
- **`[WinError 5] Access denied` on block instantiation**: the same design-time instantiation ran in GRC's own (non-writable) installation directory. All filesystem access (creating `output/`, opening the CSV) was moved out of `__init__` into a lazy, exception-safe initializer that only runs on the first real received packet.
- CSV output location changed from the process's current working directory (which could be anywhere, e.g. `C:\`) to a fixed `output/` folder next to the flowgraph.
- CSV logging changed from "rewrite the whole file, one unique row per node" to a simple timestamped append-only log, so repeated receptions of the same node are all recorded.
