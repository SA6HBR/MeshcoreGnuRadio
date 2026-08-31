# MeshCore RX — RTL-SDR / GNU Radio Passive Sniffer

A GNU Radio Companion flowgraph that receives and decodes [MeshCore](https://meshcore.dev/) LoRa packets off the air using a cheap RTL-SDR dongle — no MeshCore hardware required.

It demodulates the LoRa PHY with [`gr-lora_sdr`](https://github.com/tapparelj/gr-lora_sdr), then fully parses the MeshCore packet format (header, routing path, payload types) in a GNU Radio embedded Python block, and logs every received node advertisement to a CSV file for mapping.

## What it does

- Receives on 869.618 MHz (configurable) via RTL-SDR
- Decodes the MeshCore packet header: route type (Direct/Flood/Transport), payload type, and version
- Parses the variable-length routing path (hop node-hashes)
- Fully decodes `ADVERT` packets: node name, node type, public key, timestamp, and GPS coordinates
- Parses the cleartext envelope of encrypted payload types (`REQUEST`, `RESPONSE`, `TXT_MSG`, `PATH`, `GRP_TXT`, `GRP_DATA`, `ANON_REQ`) — destination/source node-hash and MAC, without attempting decryption
- Decodes `CONTROL` packets, including `DISCOVER_REQ`/`DISCOVER_RESP` (the latter reveals a node's full public key in cleartext)
- Decodes `TRACE` (path-tracing) packets
- Cross-references node-hashes against previously seen adverts so you see node names, not just raw hashes, wherever possible
- Logs every received advert (with timestamp) to `output/mapAdvert.csv` for later mapping/analysis

## Requirements

- GNU Radio 3.10.x (tested on 3.10.12.0)
- [`gr-osmosdr`](https://github.com/osmocom/gr-osmosdr) (RTL-SDR support)
- [`gr-lora_sdr`](https://github.com/tapparelj/gr-lora_sdr) (LoRa PHY demodulation)
- An RTL-SDR dongle (tested with an RTL2838U + R820T tuner)
- Python 3 (bundled with your GNU Radio distribution — this project was built against [radioconda](https://github.com/ryanvolz/radioconda))

## Files

| File | Description |
|---|---|
| `Meshcore.grc` | GNU Radio Companion flowgraph (source of truth — edit this) |
| `meshcore_rx.py` | Auto-generated from the `.grc` file by GRC — do not edit by hand |
| `meshcore_rx_epy_block_0.py` | The embedded Python block that decodes MeshCore packets and writes the CSV log |

## Usage

1. Open `Meshcore.grc` in GNU Radio Companion.
2. Adjust `center_freq` / `bandwith` if your region uses a different MeshCore frequency plan.
3. Generate the flowgraph 
4. Run in console `C:\ProgramData\radioconda\python.exe -u meshcore_rx.py`.
5. Decoded packets are printed to the console; every received `ADVERT` is also appended to `output/mapAdvert.csv`, created automatically next to the flowgraph.

### `mapAdvert.csv` columns

```
Timestamp,NodeName,Latitude,Longitude,Nodtyp,LastNodHash,SecondLastNodHash,HashCount,PublicKey
```

One row is appended per received advert (not deduplicated), so the same node will appear multiple times as it's re-heard — useful for tracking reception over time.

## Known limitations

- **No decryption.** `RESPONSE`, `TXT_MSG`, `REQ`, `PATH`, and group payloads are encrypted with a shared secret derived via ECDH between the two communicating nodes' private keys. Passively receiving RF traffic never exposes a private key, so decryption is not possible here, by design — this tool only extracts what MeshCore itself sends in cleartext (routing metadata, node identities, adverts).
- **No RSSI/SNR.** `gr-lora_sdr` estimates signal quality internally but does not expose it via the message port in this configuration; the RTL-SDR/`gr-osmosdr` chain also has no calibrated RSSI output. A relative signal-strength probe on the raw IQ stream is a possible future addition.
- **`DISCOVER_REQ` field semantics are a best-effort interpretation** (byte layout confirmed against real captured packets; the exact meaning of the `type_filter` byte is not independently confirmed against the MeshCore source).

## Acknowledgements

- [MeshCore](https://github.com/meshcore-dev/MeshCore) protocol and documentation
- [`gr-lora_sdr`](https://github.com/tapparelj/gr-lora_sdr) — J. Tapparel et al., EPFL Telecommunication Circuits Laboratory

## Dependency licenses

This project itself is licensed under GPL-3.0 (see `LICENSE`). The tools and protocol it depends on are licensed separately, and any redistribution needs to respect those terms too:

| Dependency                                                      | License          |
|-----------------------------------------------------------------| ---------------- |
| [GNU Radio](https://www.gnuradio.org/)                          | GPL-3.0-or-later |
| [`gr-osmosdr`](https://github.com/osmocom/gr-osmosdr)           | GPL-3.0-or-later |
| [`gr-lora_sdr`](https://github.com/tapparelj/gr-lora_sdr)       | GPL-3.0-or-later |
| [MeshCore](https://github.com/meshcore-dev/MeshCore#-license)   | MIT              |