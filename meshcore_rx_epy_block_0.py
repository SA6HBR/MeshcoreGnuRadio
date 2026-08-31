import csv
import os
import sys
import struct
import datetime
from gnuradio import gr
import pmt


# -------------------------------------------------------------
# CSV export of received Advert packets (a log - a new row for
# every received Advert, not limited to one unique row per node)
# -------------------------------------------------------------

CSV_FILENAME = "mapAdvert.csv"
CSV_HEADER = [
    "Timestamp",
    "NodeName",
    "Latitude",
    "Longitude",
    "NodeType",
    "LastNodeHash",
    "SecondLastNodeHash",
    "HashCount",
    "PublicKey",
]


def _script_dir():
    """
    Figure out the folder where the .grc file (and the generated
    meshcore_rx.py) lives.

    NOTE: __file__ is NOT defined here, because GNU Radio
    Companion runs embedded Python blocks via exec() instead of
    a normal module import. So fall back to sys.argv[0], which
    points at the top-level file (meshcore_rx.py) that was
    actually started with "python meshcore_rx.py" / GRC's
    Execute button, and which lives in the same folder as the
    .grc file.
    """
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        pass

    try:
        argv0 = sys.argv[0]
        if argv0:
            return os.path.dirname(os.path.abspath(argv0))
    except Exception:
        pass

    return os.getcwd()


# -------------------------------------------------------------
# MeshCore header fields (src/Packet.h in meshcore-dev/MeshCore)
# Header byte: 0bVVPPPPRR
#   bit 0-1 : route type
#   bit 2-5 : payload type
#   bit 6-7 : version
# -------------------------------------------------------------

ROUTE_TYPE_TRANSPORT_FLOOD = 0x00
ROUTE_TYPE_FLOOD = 0x01
ROUTE_TYPE_DIRECT = 0x02
ROUTE_TYPE_TRANSPORT_DIRECT = 0x03

ROUTE_TYPE_NAMES = {
    ROUTE_TYPE_TRANSPORT_FLOOD: "TRANSPORT_FLOOD",
    ROUTE_TYPE_FLOOD: "FLOOD",
    ROUTE_TYPE_DIRECT: "DIRECT",
    ROUTE_TYPE_TRANSPORT_DIRECT: "TRANSPORT_DIRECT",
}

PAYLOAD_TYPE_REQ = 0x00
PAYLOAD_TYPE_RESPONSE = 0x01
PAYLOAD_TYPE_TXT_MSG = 0x02
PAYLOAD_TYPE_ACK = 0x03
PAYLOAD_TYPE_ADVERT = 0x04
PAYLOAD_TYPE_GRP_TXT = 0x05
PAYLOAD_TYPE_GRP_DATA = 0x06
PAYLOAD_TYPE_ANON_REQ = 0x07
PAYLOAD_TYPE_PATH = 0x08
PAYLOAD_TYPE_TRACE = 0x09
PAYLOAD_TYPE_MULTIPART = 0x0A
PAYLOAD_TYPE_CONTROL = 0x0B
PAYLOAD_TYPE_RAW_CUSTOM = 0x0F

PAYLOAD_TYPE_NAMES = {
    PAYLOAD_TYPE_REQ: "REQ",
    PAYLOAD_TYPE_RESPONSE: "RESPONSE",
    PAYLOAD_TYPE_TXT_MSG: "TXT_MSG",
    PAYLOAD_TYPE_ACK: "ACK",
    PAYLOAD_TYPE_ADVERT: "ADVERT",
    PAYLOAD_TYPE_GRP_TXT: "GRP_TXT",
    PAYLOAD_TYPE_GRP_DATA: "GRP_DATA",
    PAYLOAD_TYPE_ANON_REQ: "ANON_REQ",
    PAYLOAD_TYPE_PATH: "PATH",
    PAYLOAD_TYPE_TRACE: "TRACE",
    PAYLOAD_TYPE_MULTIPART: "MULTIPART",
    PAYLOAD_TYPE_CONTROL: "CONTROL",
    PAYLOAD_TYPE_RAW_CUSTOM: "RAW_CUSTOM",
}

# -------------------------------------------------------------
# Advert appdata flags (docs/payloads.md)
# -------------------------------------------------------------

ADV_TYPE_MASK = 0x0F          # low 4 bits: node type (enum, not a bitmask)
ADV_TYPE_NAMES = {
    0x01: "chat node",
    0x02: "repeater",
    0x03: "room server",
    0x04: "sensor",
}

ADV_FLAG_HAS_LOCATION = 0x10
ADV_FLAG_HAS_FEATURE1 = 0x20
ADV_FLAG_HAS_FEATURE2 = 0x40
ADV_FLAG_HAS_NAME = 0x80


class blk(gr.basic_block):
    """
    MeshCore packet decoder (Advert, Request/Response/TxtMsg/Path,
    Ack, Group Txt/Data, Anon Request, Control)
    """

    def __init__(self):
        print("=" * 40)
        print("MeshCore RX decoder")
        print("Written by SA6HBR")
        print("=" * 40)

        gr.basic_block.__init__(
            self,
            name="MeshCore Decoder",
            in_sig=None,
            out_sig=None
        )

        self.message_port_register_in(pmt.intern("in"))
        self.set_msg_handler(
            pmt.intern("in"),
            self.handle_msg
        )

        self.message_port_register_out(pmt.intern("out"))

        # Node-hash (1 byte) -> node name, built up as we see
        # Advert packets. Used to look up senders/recipients in
        # encrypted packets (Request, Response, TxtMsg, Path).
        self.known_nodes = {}

        # Node-hash (1 byte) -> full public key (hex, 32 bytes).
        # Populated from both Advert and CONTROL/DISCOVER_RESP
        # (which leaks the full key completely unencrypted).
        self.known_pubkeys = {}

        # CSV export. self.csv_path is NOT set here - see
        # _ensure_csv_ready() below for why.
        self.csv_path = None
        self._csv_ready = False

    # ---------------------------------------------------------
    # Lazy setup of the CSV path/folder.
    #
    # IMPORTANT: this must NOT be done in __init__(). GNU Radio
    # Companion already instantiates embedded Python blocks at
    # design time (when you open/edit the .grc file) by
    # exec()-ing the code box content directly - not by
    # importing the file itself. In that context:
    #   - __file__ is not defined
    #   - sys.argv[0]/cwd point at GRC's own installation
    #     folder (e.g. radioconda\Library\bin), where you
    #     usually do NOT have write permission
    # Creating folders/files in __init__ therefore crashes the
    # block's instantiation as soon as you open the flowgraph,
    # long before it's actually run.
    #
    # By deferring this to the first real handle_msg call, the
    # code instead runs inside the process actually started via
    # "python meshcore_rx.py" (where meshcore_rx_epy_block_0 is
    # loaded with a normal import), and __file__ is then
    # correctly set to the path next to the .grc file.
    # ---------------------------------------------------------
    def _ensure_csv_ready(self):

        if self._csv_ready:
            return

        self._csv_ready = True  # only try once

        try:
            script_dir = _script_dir()
            output_dir = os.path.join(script_dir, "output")
            os.makedirs(output_dir, exist_ok=True)

            self.csv_path = os.path.join(output_dir, CSV_FILENAME)

            # Only write the header row if the file doesn't
            # already exist (or is empty) - otherwise just keep
            # appending.
            need_header = (
                not os.path.exists(self.csv_path)
                or os.path.getsize(self.csv_path) == 0
            )
            if need_header:
                with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow(CSV_HEADER)

        except Exception as e:
            print(
                "Could not prepare mapAdvert.csv (CSV export "
                "disabled for this run):", repr(e)
            )
            self.csv_path = None

    # ---------------------------------------------------------
    # Append a new row to mapAdvert.csv (one row per received
    # Advert packet - not limited to one unique row per node)
    # ---------------------------------------------------------
    def _append_csv_row(self, row):

        if not self.csv_path:
            return

        try:
            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=CSV_HEADER).writerow(row)
        except Exception as e:
            print("Could not write to", self.csv_path, ":", repr(e))

    # ---------------------------------------------------------
    # Read out the raw payload from the incoming PMT
    # ---------------------------------------------------------
    def _extract_bytes(self, msg):

        if pmt.is_u8vector(msg):
            return bytes(pmt.u8vector_elements(msg))

        if pmt.is_blob(msg):
            return bytes(pmt.blob_data(msg))

        if pmt.is_symbol(msg):

            # Important:
            # The LoRa block sends binary bytes as a PMT
            # "symbol". pmt.symbol_to_string() crashes on this
            # because it performs a STRICT UTF-8 decode in the
            # pybind11 layer - and LoRa payloads are raw binary
            # data, not text, so almost every packet contains
            # invalid UTF-8 sequences.
            #
            # Workaround: go via PMT's binary serialization
            # (pmt.serialize_str), which returns plain 'bytes'
            # with no UTF-8 enforcement, and manually pick the
            # raw bytes out of the PMT serialization format:
            #   [1 byte tag][2 byte length, big-endian][data]
            # The tag for a symbol in the PMT serialization
            # format is 0x02.
            raw = pmt.serialize_str(msg)

            if len(raw) < 3 or raw[0] != 0x02:
                print(
                    "Unexpected PMT serialization format, first byte:",
                    raw[0:1].hex() if raw else "(empty)"
                )
                return None

            length = (raw[1] << 8) | raw[2]
            data = raw[3:3 + length]

            if len(data) != length:
                print(
                    "Serialized length mismatch:",
                    "expected", length,
                    "got", len(data)
                )
                return None

            return data

        print("Unknown PMT type:")
        print(pmt.write_string(msg))
        return None

    # ---------------------------------------------------------
    # Look up a node-hash against known node names/public keys
    # (if we've seen its Advert or DISCOVER_RESP before)
    # ---------------------------------------------------------
    def _fmt_hash(self, node_hash):
        name = self.known_nodes.get(node_hash)
        if name:
            return "%02x (%s)" % (node_hash, name)
        if node_hash in self.known_pubkeys:
            return "%02x (name unknown, but pubkey seen)" % node_hash
        return "%02x (unknown node)" % node_hash

    # ---------------------------------------------------------
    # Advert
    # ---------------------------------------------------------
    def _handle_advert(self, payload, hops):

        # Public key(32) + timestamp(4) + signature(64)
        # + appdata flags(1) = 101 bytes minimum
        if len(payload) < 101:
            print(
                "Too short for a MeshCore Advert payload:",
                len(payload)
            )
            return

        public_key = payload[0:32]
        node_hash = public_key[0]

        timestamp = struct.unpack("<I", payload[32:36])[0]
        signature = payload[36:100]

        appdata = payload[100:]
        flags = appdata[0]

        node_type = ADV_TYPE_NAMES.get(
            flags & ADV_TYPE_MASK, "unknown/none"
        )

        p = 1
        latitude = None
        longitude = None

        if flags & ADV_FLAG_HAS_LOCATION:
            if len(appdata) < p + 8:
                print("Appdata too short for lat/long")
                return
            lat_raw = struct.unpack("<i", appdata[p:p + 4])[0]
            lon_raw = struct.unpack("<i", appdata[p + 4:p + 8])[0]
            # Per spec: decimal degrees * 1,000,000, as int32
            latitude = lat_raw / 1_000_000.0
            longitude = lon_raw / 1_000_000.0
            p += 8

        if flags & ADV_FLAG_HAS_FEATURE1:
            p += 2  # reserved for future use

        if flags & ADV_FLAG_HAS_FEATURE2:
            p += 2  # reserved for future use

        node_name = None
        if flags & ADV_FLAG_HAS_NAME:
            name_bytes = appdata[p:].rstrip(b"\x00")
            node_name = name_bytes.decode("utf-8", errors="replace")

        # Remember the node for future lookups
        self.known_pubkeys[node_hash] = public_key.hex()
        if node_name:
            self.known_nodes[node_hash] = node_name

        # -------------------------------------------------
        # CSV: a new row per received Advert (log, not
        # limited to one unique row per node)
        # -------------------------------------------------

        last_hop = hops[-1] if hops else ""
        second_last_hop = hops[-2] if len(hops) >= 2 else ""

        # Timestamp = when WE received the packet (system time),
        # not the advert packet's own internal timestamp field
        # (printed separately below as "Timestamp"). This lets
        # you see exactly when each row in your log came in.
        received_at = datetime.datetime.now().isoformat(timespec="seconds")

        csv_row = {
            "Timestamp": received_at,
            "NodeName": node_name if node_name else "",
            "Latitude": (
                "%.6f" % latitude if latitude is not None else ""
            ),
            "Longitude": (
                "%.6f" % longitude if longitude is not None else ""
            ),
            "NodeType": node_type,
            "LastNodeHash": last_hop,
            "SecondLastNodeHash": second_last_hop,
            "HashCount": len(hops),
            "PublicKey": public_key.hex(),
        }

        self._append_csv_row(csv_row)

        print()
        print("================================")
        print("       MESHCORE ADVERT")
        print("================================")

        print("Node Name       :", node_name if node_name else "(no name in packet)")
        print("Node hash       : %02x" % node_hash)
        print("Node type       :", node_type)

        if hops:
            print("From node-hash (last hop) : %s" % hops[-1])
            print("Full path (hops)          :", " -> ".join(hops))
        else:
            print("Received        : directly (0 hops, no repeater)")

        print("Public Key      :", public_key.hex())
        print("Timestamp       :", timestamp)
        print("App Flags       : 0x%02X" % flags)

        if latitude is not None:
            print("Latitude        :", latitude)
            print("Longitude       :", longitude)
        else:
            print("Latitude/Long   : (no location info in packet)")

        print("================================")
        if self.csv_path:
            print("CSV updated     :", self.csv_path)

        if node_name:
            self.message_port_pub(
                pmt.intern("out"),
                pmt.intern(node_name)
            )

    # ---------------------------------------------------------
    # Request / Response / TxtMsg / (Returned) Path
    # Share the same envelope:
    #   dest_hash(1) + src_hash(1) + cipher_mac(2) + ciphertext
    # The content (ciphertext) is encrypted and cannot be read
    # here - we can only show who's talking to whom.
    # ---------------------------------------------------------
    def _handle_enveloped(self, payload, payload_name):

        if len(payload) < 4:
            print("Too short for a %s envelope:" % payload_name, len(payload))
            return

        dest_hash = payload[0]
        src_hash = payload[1]
        cipher_mac = payload[2:4]
        ciphertext = payload[4:]

        print()
        print("================================")
        print("       MESHCORE %s" % payload_name)
        print("================================")
        print("To (dest_hash)    :", self._fmt_hash(dest_hash))
        print("From (src_hash)   :", self._fmt_hash(src_hash))
        print("Cipher MAC        :", cipher_mac.hex())
        print("Ciphertext length :", len(ciphertext), "bytes")
        print("(Content is encrypted - cannot be read here)")
        print("================================")

    # ---------------------------------------------------------
    # Ack
    # ---------------------------------------------------------
    def _handle_ack(self, payload):

        if len(payload) < 4:
            print("Too short for ACK:", len(payload))
            return

        checksum = struct.unpack("<I", payload[0:4])[0]

        print()
        print("=== MESHCORE ACK ===")
        print("Checksum: 0x%08X" % checksum)

    # ---------------------------------------------------------
    # Group Text / Group Datagram
    # channel_hash(1) + cipher_mac(2) + ciphertext
    # ---------------------------------------------------------
    def _handle_group(self, payload, payload_name):

        if len(payload) < 3:
            print("Too short for %s:" % payload_name, len(payload))
            return

        channel_hash = payload[0]
        cipher_mac = payload[1:3]
        ciphertext = payload[3:]

        print()
        print("=== MESHCORE %s ===" % payload_name)
        print("Channel hash      : %02x" % channel_hash)
        print("Cipher MAC        :", cipher_mac.hex())
        print("Ciphertext length :", len(ciphertext), "bytes")
        print("(Content is encrypted - cannot be read here)")

    # ---------------------------------------------------------
    # Anonymous Request
    # dest_hash(1) + public_key(32) + cipher_mac(2) + ciphertext
    # ---------------------------------------------------------
    def _handle_anon_req(self, payload):

        if len(payload) < 35:
            print("Too short for ANON_REQ:", len(payload))
            return

        dest_hash = payload[0]
        sender_pubkey = payload[1:33]
        cipher_mac = payload[33:35]
        ciphertext = payload[35:]

        print()
        print("=== MESHCORE ANON_REQ ===")
        print("To (dest_hash)     :", self._fmt_hash(dest_hash))
        print("Sender's pubkey    :", sender_pubkey.hex())
        print("Cipher MAC         :", cipher_mac.hex())
        print("Ciphertext length  :", len(ciphertext), "bytes")
        print("(Content is encrypted - cannot be read here)")

    # ---------------------------------------------------------
    # Control data (always cleartext)
    # flags(1, top 4 bits = sub_type) + data
    #
    # Sub-type 0x8 = DISCOVER_REQ, 0x9 = DISCOVER_RESP
    # (confirmed against official documentation + verified
    # byte-for-byte against real captured packets).
    # ---------------------------------------------------------
    CONTROL_SUBTYPE_DISCOVER_REQ = 0x8
    CONTROL_SUBTYPE_DISCOVER_RESP = 0x9

    def _handle_control(self, payload):

        if len(payload) < 1:
            print("Too short for CONTROL:", len(payload))
            return

        flags = payload[0]
        sub_type = (flags >> 4) & 0x0F
        data = payload[1:]

        print()
        print("=== MESHCORE CONTROL ===")
        print("Sub-type : 0x%X" % sub_type)

        if sub_type == self.CONTROL_SUBTYPE_DISCOVER_RESP:
            self._handle_discover_resp(data)

        elif sub_type == self.CONTROL_SUBTYPE_DISCOVER_REQ:
            self._handle_discover_req(data)

        else:
            print("Data     :", data.hex())

    # ---------------------------------------------------------
    # DISCOVER_RESP: tag(4, LE) + reserved(1) + public_key(32)
    #
    # IMPORTANT FINDING: the responding node's FULL public key
    # is sent completely unencrypted here - not just a 1-byte
    # hash. Verified against three real packets where node_hash
    # (the first byte of the key) exactly matches already-known
    # nodes.
    # ---------------------------------------------------------
    def _handle_discover_resp(self, data):

        if len(data) < 37:
            print(
                "Too short for DISCOVER_RESP (expected at least "
                "37 bytes, got %d)" % len(data)
            )
            print("Data     :", data.hex())
            return

        tag = struct.unpack("<I", data[0:4])[0]
        reserved = data[4]
        pubkey = data[5:37]
        node_hash = pubkey[0]

        # Remember the key even if we don't know the name yet -
        # may be useful later, and strengthens lookups in
        # known_nodes (which is otherwise only built up via
        # Advert packets).
        self.known_pubkeys[node_hash] = pubkey.hex()

        print("  -> DISCOVER_RESP (reply to a 'who's here' query)")
        print("Tag        : 0x%08X" % tag)
        print("Reserved   : 0x%02X" % reserved)
        print("Public Key : %s" % pubkey.hex())
        print("Node hash  :", self._fmt_hash(node_hash))

    # ---------------------------------------------------------
    # DISCOVER_REQ: best-effort interpretation based on
    # available documentation + byte-for-byte comparison
    # against two real packets (identical layout in both):
    #   type_filter(1) + tag(4, LE) + reserved(4)
    # The exact semantics of type_filter are not 100% confirmed -
    # shown as raw hex rather than an interpreted label.
    # ---------------------------------------------------------
    def _handle_discover_req(self, data):

        if len(data) < 9:
            print(
                "Too short for DISCOVER_REQ (expected at least "
                "9 bytes, got %d)" % len(data)
            )
            print("Data     :", data.hex())
            return

        type_filter = data[0]
        tag = struct.unpack("<I", data[1:5])[0]
        reserved = data[5:9]

        print("  -> DISCOVER_REQ ('who's here' query)")
        print("Type filter : 0x%02X" % type_filter)
        print("Tag         : 0x%08X" % tag)
        print("Reserved    :", reserved.hex())

    # ---------------------------------------------------------
    # Trace: traces a path through the mesh network and
    # collects SNR per hop. Always cleartext.
    #   tag(4, LE) + auth_code(4, LE) + flags(1) + path_hashes(rest)
    # ---------------------------------------------------------
    def _handle_trace(self, payload):

        if len(payload) < 9:
            print("Too short for TRACE:", len(payload))
            return

        tag = struct.unpack("<I", payload[0:4])[0]
        auth_code = struct.unpack("<I", payload[4:8])[0]
        flags = payload[8]
        path_hashes = payload[9:]

        hop_list = [
            self._fmt_hash(h) for h in path_hashes
        ]

        print()
        print("=== MESHCORE TRACE ===")
        print("Tag        : 0x%08X" % tag)
        print("Auth code  : 0x%08X" % auth_code)
        print("Flags      : 0x%02X" % flags)
        if hop_list:
            print("Path so far (node-hashes):", " -> ".join(hop_list))
        else:
            print("Path so far: (empty, trace starts here)")

    # ---------------------------------------------------------
    # Main handler
    # ---------------------------------------------------------
    def handle_msg(self, msg):

        print()
        print("=== MESHCORE PACKET ===")

        self._ensure_csv_ready()

        try:

            data = self._extract_bytes(msg)

            if data is None:
                return

            # -------------------------------------------------
            # Debug
            # -------------------------------------------------

            print("Length:", len(data))
            print("HEX:", data.hex(" "))

            if len(data) < 2:
                print("Too short to even contain header + path_len")
                return

            # -------------------------------------------------
            # Header
            # -------------------------------------------------

            header = data[0]

            version = (header >> 6) & 0x03
            payload_type = (header >> 2) & 0x0F
            route_type = header & 0x03

            route_name = ROUTE_TYPE_NAMES.get(
                route_type, "UNKNOWN(0x%X)" % route_type
            )
            payload_name = PAYLOAD_TYPE_NAMES.get(
                payload_type, "UNKNOWN(0x%X)" % payload_type
            )

            print("Header:       0x%02X" % header)
            print("Version:      ", version)
            print("Route type:   ", route_name)
            print("Payload type: ", payload_name)

            offset = 1

            # -------------------------------------------------
            # Transport codes (only for *_TRANSPORT_* route types)
            # -------------------------------------------------

            has_transport = route_type in (
                ROUTE_TYPE_TRANSPORT_FLOOD,
                ROUTE_TYPE_TRANSPORT_DIRECT,
            )

            if has_transport:

                if len(data) < offset + 4:
                    print("Too short for transport codes")
                    return

                transport_codes = data[offset:offset + 4]
                offset += 4

                print("Transport codes:", transport_codes.hex())

            # -------------------------------------------------
            # Path length + path (node-hashes for each hop)
            # -------------------------------------------------

            if len(data) < offset + 1:
                print("Too short for the path_len byte")
                return

            path_len_byte = data[offset]
            offset += 1

            hash_size = ((path_len_byte >> 6) & 0x03) + 1
            hash_count = path_len_byte & 0x3F
            path_bytes_len = hash_size * hash_count

            print(
                "Path_len byte: 0x%02X  "
                "(hash_size=%d bytes/hop, hash_count=%d hops)"
                % (path_len_byte, hash_size, hash_count)
            )

            if len(data) < offset + path_bytes_len:
                print("Too short for the path field")
                return

            path_bytes = data[offset:offset + path_bytes_len]
            offset += path_bytes_len

            hops = [
                path_bytes[i:i + hash_size].hex()
                for i in range(0, path_bytes_len, hash_size)
            ]

            if hops:
                print(
                    "Path (hops, oldest -> nearest):",
                    " -> ".join(hops)
                )
                print("Received via (most recent hop):", hops[-1])
            else:
                print("Path: empty (received directly, 0 hops)")

            # -------------------------------------------------
            # Payload - decoded differently depending on type
            # -------------------------------------------------

            payload = data[offset:]

            if payload_type == PAYLOAD_TYPE_ADVERT:
                self._handle_advert(payload, hops)

            elif payload_type in (
                PAYLOAD_TYPE_REQ,
                PAYLOAD_TYPE_RESPONSE,
                PAYLOAD_TYPE_TXT_MSG,
                PAYLOAD_TYPE_PATH,
            ):
                self._handle_enveloped(payload, payload_name)

            elif payload_type == PAYLOAD_TYPE_ACK:
                self._handle_ack(payload)

            elif payload_type in (
                PAYLOAD_TYPE_GRP_TXT,
                PAYLOAD_TYPE_GRP_DATA,
            ):
                self._handle_group(payload, payload_name)

            elif payload_type == PAYLOAD_TYPE_ANON_REQ:
                self._handle_anon_req(payload)

            elif payload_type == PAYLOAD_TYPE_TRACE:
                self._handle_trace(payload)

            elif payload_type == PAYLOAD_TYPE_CONTROL:
                self._handle_control(payload)

            else:
                print(
                    "Payload type %s is not yet supported for "
                    "detailed parsing." % payload_name
                )

        except Exception as e:

            print(
                "MeshCore decoder error:",
                repr(e)
            )
