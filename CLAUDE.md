# CLAUDE.md

Context for Claude (or any agentic coding tool) working in this repository.

## What this project is

A GNU Radio Companion flowgraph that receives MeshCore LoRa packets off the air
with an RTL-SDR dongle and decodes the MeshCore packet format in Python — no
MeshCore hardware involved. See `README.md` for user-facing docs and
`docs/PROTOCOL_NOTES.md` for the full packet-format reference.

## File map

| File | Role |
|---|---|
| `Meshcore.grc` | GNU Radio Companion flowgraph — **the source of truth**. Edit this in GRC, not by hand. |
| `meshcore_rx.py` | Auto-generated from `Meshcore.grc` by GRC's "Generate" step. **Never edit directly** — regenerate from the `.grc` file instead, or changes will be silently lost. |
| `meshcore_rx_epy_block_0.py` | The embedded Python block that does all the actual packet parsing. This is the file that gets worked on day-to-day. |
| `docs/PROTOCOL_NOTES.md` | MeshCore wire-format reference, verified byte-for-byte against real captured packets. Consult this before changing any parsing offsets. |
| `CHANGELOG.md` | History of fixes and features, with the reasoning behind each. |
| `output/mapAdvert.csv` | Generated at runtime, not committed (see `.gitignore`). |

## Critical constraints when editing `meshcore_rx_epy_block_0.py`

These are not stylistic preferences — violating them breaks the block in ways
that are easy to miss in a code review because they only surface inside GNU
Radio Companion, not in a plain Python syntax check.

1. **Never reference `__file__` unconditionally.** GNU Radio Companion
   instantiates embedded Python blocks at *design time* (opening/editing the
   `.grc` file) by `exec()`-ing the code directly, not by importing the file —
   `__file__` is undefined in that context. Any code path that runs during
   `__init__` must tolerate this. Use the existing `_script_dir()` helper,
   which falls back through `__file__` → `sys.argv[0]` → `os.getcwd()`.

2. **Never do filesystem I/O in `__init__`.** GRC's design-time instantiation
   also runs with the current working directory pointed at GRC's own
   (frequently non-writable) installation folder. Creating directories or
   opening files there throws `PermissionError` and crashes block
   instantiation *before the flowgraph ever runs*. All filesystem setup must
   be lazy — see `_ensure_csv_ready()`, which only runs on the first real
   `handle_msg()` call, wrapped in `try/except` so a failure disables the
   feature instead of crashing the flowgraph.

3. **Never call `pmt.symbol_to_string()` on the raw LoRa payload.**
   `gr-lora_sdr` publishes the decoded payload as a PMT *symbol*, and
   `symbol_to_string()` performs a strict UTF-8 decode that throws
   `UnicodeDecodeError` on almost every real (binary) packet. Use
   `pmt.serialize_str()` instead (returns raw `bytes`, no text decoding) and
   parse the PMT binary serialization format manually — see
   `_extract_bytes()`.

4. **Test parsing logic outside of GNU Radio.** `gr.basic_block`, `pmt`, etc.
   are only importable inside a GNU Radio environment. When testing changes,
   stub out `gnuradio.gr.basic_block` and `pmt.intern` with minimal fakes
   (see the ad-hoc test snippets in the project history / CHANGELOG) rather
   than requiring a full GNU Radio install to validate a parsing change.

5. **Any change to byte offsets or field sizes must cite
   `docs/PROTOCOL_NOTES.md`, or update it.** Several offsets here were wrong
   in earlier versions (e.g. lat/long parsed as float instead of scaled
   int32) and only caught by comparing decoded output against real captured
   packets. Don't guess — verify against a real packet's hex dump.

## Known-good verification packets

When changing parsing logic, sanity-check against these real captured
packets (see `CHANGELOG.md` / `docs/PROTOCOL_NOTES.md` for how each was
verified):

- A `DIRECT` advert with GPS location (`SE1383-Apelviken-4977`,
  57.078399, 12.277213) — good for checking the appdata `flags`-driven
  field layout.
- A `FLOOD` advert with the same node's identity, 1 hop away — good for
  checking path-hash decoding (`hash_size`/`hash_count`).
- A `CONTROL` `DISCOVER_RESP` (sub-type `0x9`) packet — good for checking
  full public-key extraction.
- A `TRACE` packet — good for checking `tag`/`auth_code`/path-hash
  decoding.

## What NOT to build

Do not add decryption of `RESPONSE`/`TXT_MSG`/`REQ`/`PATH`/group payloads.
This isn't a missing feature — it's cryptographically impossible for a
passive receiver. Decryption requires the ECDH shared secret, which needs
one of the two communicating nodes' *private* key. If asked to "decrypt"
anything, the correct response is to explain why it can't be done here, not
to attempt it.
