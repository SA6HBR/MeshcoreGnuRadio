"""
Regression tests for meshcore_rx_epy_block_0.py.

Runs without a GNU Radio installation: stubs out `gnuradio.gr` and `pmt`
with the minimal surface the decoder actually uses, then feeds it real
packets captured over the air during development (see CHANGELOG.md /
docs/PROTOCOL_NOTES.md for how each was verified).

Run with:
    python -m unittest tests/test_decoder.py -v
"""

import sys
import os
import types
import unittest


def _install_gnuradio_stubs():
    """Install minimal fake gnuradio.gr / pmt modules so the decoder
    module can be imported outside of a real GNU Radio environment."""

    if "pmt" in sys.modules and hasattr(sys.modules["pmt"], "_is_stub"):
        return  # already installed

    gr = types.ModuleType("gnuradio.gr")

    class basic_block:
        def __init__(self, *a, **kw):
            pass

        def message_port_register_in(self, *a):
            pass

        def message_port_register_out(self, *a):
            pass

        def set_msg_handler(self, *a):
            pass

        def message_port_pub(self, *a):
            pass

    gr.basic_block = basic_block

    gnuradio_mod = types.ModuleType("gnuradio")
    gnuradio_mod.gr = gr
    sys.modules["gnuradio"] = gnuradio_mod
    sys.modules["gnuradio.gr"] = gr

    pmt = types.ModuleType("pmt")
    pmt.intern = lambda x: x
    pmt._is_stub = True
    sys.modules["pmt"] = pmt


def _load_decoder_module():
    """Import meshcore_rx_epy_block_0 from the repo root, simulating a
    real flowgraph run (argv[0] pointing at a top-level script) so
    _script_dir() resolves the same way it would in production."""

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.argv = [os.path.join(repo_root, "meshcore_rx.py")]
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    import importlib
    import meshcore_rx_epy_block_0 as module
    importlib.reload(module)
    return module


def _hex(s):
    return bytes.fromhex(s.replace(" ", ""))


class DecoderTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _install_gnuradio_stubs()
        cls.module = _load_decoder_module()

    def setUp(self):
        self.blk = self.module.blk()
        self.printed = []
        self._orig_print = print

    def feed(self, hex_packet):
        """Feed one raw MeshCore packet (as hex) through the decoder and
        capture everything it printed."""
        data = _hex(hex_packet)
        self.blk._extract_bytes = lambda msg: data

        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.blk.handle_msg(None)
        output = buf.getvalue()
        self.printed.append(output)
        return output

    # -----------------------------------------------------------------
    # Advert: DIRECT route, with GPS location
    # -----------------------------------------------------------------
    def test_advert_direct_with_location(self):
        out = self.feed(
            "12 00 49 77 1e 8a a6 99 6c 9f 91 44 d6 7a 53 a7 c5 2f c3 98 "
            "51 7b a4 fc 27 fb 22 30 16 04 04 64 b3 2e 5f d8 78 6a ea fd "
            "71 a9 0c 44 b6 cf 5a 15 b9 ad 05 27 ce d8 56 34 ee e7 70 7d "
            "32 52 40 01 70 57 6e c3 1c 54 bc 6e f2 14 e2 27 23 11 63 c6 "
            "a5 5d 71 e1 92 79 47 bc 77 ad 09 87 53 f0 89 7e 2f df 5c 38 "
            "21 0c 92 7f f2 66 03 dd 55 bb 00 53 45 31 33 38 33 2d 41 70 "
            "65 6c 76 69 6b 65 6e 2d 34 39 37 37"
        )
        self.assertIn("SE1383-Apelviken-4977", out)
        self.assertIn("57.078399", out)   # latitude - would be wrong if
        self.assertIn("12.277213", out)   # parsed as float instead of
                                           # scaled int32 (regression check)
        self.assertIn("direkt (0 hopp", out)

    # -----------------------------------------------------------------
    # Advert: FLOOD route, 1 hop - must NOT be rejected just because
    # header != 0x12 (this was the original bug)
    # -----------------------------------------------------------------
    def test_advert_flood_with_hop(self):
        out = self.feed(
            "11 81 77 6f 2f 49 77 1e 8a a6 99 6c 9f 91 44 d6 7a 53 a7 c5 "
            "2f c3 98 51 7b a4 fc 27 fb 22 30 16 04 04 64 b3 2e 99 a1 78 "
            "6a fc 60 c9 97 0b b4 8c f4 60 3e ba 5d cd 40 a6 4d 5d 44 d3 "
            "27 67 04 42 84 71 c0 c9 23 bb ba 20 94 13 4e 06 44 cd e3 9c "
            "a9 70 5f 07 1a ba 30 de 66 ca b4 37 a2 25 21 52 49 7d 8f 57 "
            "78 8f 05 db 01 92 7f f2 66 03 dd 55 bb 00 53 45 31 33 38 33 "
            "2d 41 70 65 6c 76 69 6b 65 6e 2d 34 39 37 37"
        )
        self.assertIn("SE1383-Apelviken-4977", out)
        self.assertIn("776f2f", out)  # the relaying hop's node-hash

    # -----------------------------------------------------------------
    # Response envelope: dest/src hash + MAC, no attempt to decrypt
    # -----------------------------------------------------------------
    def test_response_envelope(self):
        # First teach the decoder node-hash 49 = a known node, via advert
        self.feed(
            "12 00 49 77 1e 8a a6 99 6c 9f 91 44 d6 7a 53 a7 c5 2f c3 98 "
            "51 7b a4 fc 27 fb 22 30 16 04 04 64 b3 2e 5f d8 78 6a ea fd "
            "71 a9 0c 44 b6 cf 5a 15 b9 ad 05 27 ce d8 56 34 ee e7 70 7d "
            "32 52 40 01 70 57 6e c3 1c 54 bc 6e f2 14 e2 27 23 11 63 c6 "
            "a5 5d 71 e1 92 79 47 bc 77 ad 09 87 53 f0 89 7e 2f df 5c 38 "
            "21 0c 92 7f f2 66 03 dd 55 bb 00 53 45 31 33 38 33 2d 41 70 "
            "65 6c 76 69 6b 65 6e 2d 34 39 37 37"
        )
        out = self.feed(
            "06 00 f3 49 57 97 29 df ed a0 a9 c0 84 39 c3 52 30 f5 63 f9 "
            "92 19 2f 71 ab 9c e9 66 a7 3c 4a 07 6f 5a 9a 65 f2 cd"
        )
        self.assertIn("RESPONSE", out)
        self.assertIn("SE1383-Apelviken-4977", out)  # src_hash resolved
        self.assertIn("krypterat", out)  # must NOT attempt to decrypt

    # -----------------------------------------------------------------
    # CONTROL / DISCOVER_RESP: full public key leaked in cleartext
    # -----------------------------------------------------------------
    def test_discover_resp_extracts_full_pubkey(self):
        out = self.feed(
            "2e 00 92 fb 5d b1 57 7e 49 77 1e 8a a6 99 6c 9f 91 44 d6 7a "
            "53 a7 c5 2f c3 98 51 7b a4 fc 27 fb 22 30 16 04 04 64 b3 2e"
        )
        self.assertIn("DISCOVER_RESP", out)
        self.assertIn(
            "49771e8aa6996c9f9144d67a53a7c52fc398517ba4fc27fb223016040464b32e",
            out,
        )

    # -----------------------------------------------------------------
    # CONTROL / DISCOVER_REQ
    # -----------------------------------------------------------------
    def test_discover_req(self):
        out = self.feed("2e 00 80 04 23 07 c4 56 00 00 00 00")
        self.assertIn("DISCOVER_REQ", out)

    # -----------------------------------------------------------------
    # TRACE
    # -----------------------------------------------------------------
    def test_trace(self):
        out = self.feed("26 01 34 70 0b 84 82 00 00 00 00 00 77")
        self.assertIn("TRACE", out)
        self.assertIn("0X82840B70", out.upper())

    # -----------------------------------------------------------------
    # PMT symbol extraction must not crash on arbitrary binary data
    # (the original UnicodeDecodeError bug)
    # -----------------------------------------------------------------
    def test_extract_bytes_handles_non_utf8_symbol(self):
        payload = bytes([0x05, 0x44, 0xCD, 0x57, 0x0E, 0xB5, 0x88, 0xA9])
        raw = bytes([0x02]) + len(payload).to_bytes(2, "big") + payload

        class FakeSymbolPmt:
            pass

        import pmt as pmt_mod
        orig_is_symbol = getattr(pmt_mod, "is_symbol", None)
        orig_is_u8vector = getattr(pmt_mod, "is_u8vector", None)
        orig_is_blob = getattr(pmt_mod, "is_blob", None)
        orig_serialize_str = getattr(pmt_mod, "serialize_str", None)
        try:
            pmt_mod.is_u8vector = lambda m: False
            pmt_mod.is_blob = lambda m: False
            pmt_mod.is_symbol = lambda m: True
            pmt_mod.serialize_str = lambda m: raw

            extracted = self.blk._extract_bytes(FakeSymbolPmt())
            self.assertEqual(extracted, payload)
        finally:
            if orig_is_symbol is not None:
                pmt_mod.is_symbol = orig_is_symbol
            if orig_is_u8vector is not None:
                pmt_mod.is_u8vector = orig_is_u8vector
            if orig_is_blob is not None:
                pmt_mod.is_blob = orig_is_blob
            if orig_serialize_str is not None:
                pmt_mod.serialize_str = orig_serialize_str


if __name__ == "__main__":
    unittest.main()
