# Setup Notes

Best-effort record of the environment this project was developed and tested against. If you're setting this up fresh, confirm versions still line up — GNU Radio and its out-of-tree modules move fast enough that exact reproducibility isn't guaranteed.

## Confirmed working versions

Observed directly from a working run (see terminal output captured during development):

- GNU Radio: **3.10.12.0**
- `gr-osmosdr`: **0.2.0.0**
- Distribution: [radioconda](https://github.com/ryanvolz/radioconda) on Windows (`C:\ProgramData\radioconda`)
- SDR hardware: RTL2838U dongle with an R820T tuner, run with direct sampling mode enabled at startup

`gr-lora_sdr` version was not captured from a running log — check
[`tapparelj/gr-lora_sdr`](https://github.com/tapparelj/gr-lora_sdr) for the
current release, and confirm the `lora_sdr_lora_rx` hierarchical block's
parameter list still matches what's used in `Meshcore.grc` (`bw`, `cr`,
`has_crc`, `impl_head`, `pay_len`, `samp_rate`, `sf`, `sync_word`,
`soft_decoding`, `ldro_mode`, `print_rx`).

## Install outline

1. Install [radioconda](https://github.com/ryanvolz/radioconda) (bundles GNU Radio + GRC + `gr-osmosdr`).
2. Build and install [`gr-lora_sdr`](https://github.com/tapparelj/gr-lora_sdr) as an out-of-tree module against that GNU Radio install (it's not a conda package — build from source per its own README).
3. Install RTL-SDR drivers (Zadig on Windows, or your distro's `rtl-sdr` package on Linux) so `gr-osmosdr` can see the dongle.
4. Open `Meshcore.grc` in GNU Radio Companion, generate, and run.

## Running the tests

`tests/test_decoder.py` stubs out `gnuradio.gr` and `pmt` so the parsing
logic in `meshcore_rx_epy_block_0.py` can be tested without a full GNU Radio
install — any Python 3 interpreter works:

```
python -m unittest tests/test_decoder.py -v
```
