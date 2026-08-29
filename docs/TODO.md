# TODO / Roadmap

Open ideas and known gaps, carried over from the project's development history. Nothing here is urgent — this is a parking lot so future work doesn't have to be rediscovered from scratch.

## Signal quality

- [ ] Add a relative signal-strength readout. `gr-lora_sdr` estimates energy/CFO internally (`frame_sync_impl.cc`) but only logs it to a local file when compiled with `GRLORA_MEASUREMENTS`, and doesn't expose it via the message port as used in this flowgraph. Two options:
  - Quick: add a `Probe Avg Mag^2` block on `rtl_source`'s raw IQ output in the `.grc` flowgraph for a relative (uncalibrated) power reading.
  - Correct but more work: rebuild `gr-lora_sdr` with a message port that publishes its internal SNR/energy estimate, then read it in `meshcore_rx_epy_block_0.py` the same way the payload is read today.
- [ ] Once available, add an `SNR`/`RSSI` column to `mapAdvert.csv`.

## Data quality

- [ ] Add a sanity filter before writing an advert to `mapAdvert.csv`: reject or flag rows whose decoded `NodeName` contains non-printable characters. Confirmed cause: weak-signal bit errors that still pass CRC occasionally produce garbled names (see `CHANGELOG.md`) — these currently get logged as if valid.
- [ ] Consider re-enabling `print_rx` on `lora_rx_0` (or finding another way to surface per-packet CRC pass/fail) so we can distinguish "genuinely garbled but CRC-valid" from "should have been dropped".

## Protocol coverage

- [ ] `MULTIPART` payload type (`0x0A`) is not parsed yet — falls through to the generic "not supported" branch.
- [ ] Advert appdata `feature1`/`feature2` fields (2 bytes each, gated by flag bits `0x20`/`0x40`) are skipped but not interpreted — their meaning isn't confirmed against the MeshCore source yet.
- [ ] `DISCOVER_REQ` (`CONTROL` sub-type `0x8`) field layout is confirmed by byte-position, but the semantic meaning of `type_filter` is a best-effort guess — worth checking against the actual MeshCore firmware source if/when convenient.
- [ ] `TRACE` per-hop SNR: the MeshCore firmware appends an SNR byte per hop as a trace packet is relayed, but this hasn't been located/verified in the over-the-air `TRACE` payload as captured here — current parsing only extracts `tag`/`auth_code`/`flags`/hop hashes.
- [ ] Other `CONTROL` sub-types besides `0x8`/`0x9` haven't been observed yet — extend `_handle_control()` if/when one shows up in a capture.

## Explicitly out of scope

- Decrypting `RESPONSE`/`TXT_MSG`/`REQ`/`PATH`/group payloads. Not a missing feature — cryptographically impossible for a passive receiver (see `CLAUDE.md`). Do not revisit unless the project itself gains an active MeshCore identity with a real private key.

## Tooling

- [x] Formalize the ad-hoc verification snippets used throughout development into a real test suite — see `tests/test_decoder.py`.
- [ ] `docs/SETUP.md` environment notes are best-effort based on what was observed at development time; confirm/update exact package versions if the environment is rebuilt from scratch.
