import csv
import os
import sys
import struct
import datetime
from gnuradio import gr
import pmt


# -------------------------------------------------------------
# CSV-export av mottagna Advert-paket (en logg - en ny rad för
# varje mottaget Advert, inte begränsat till en unik rad per nod)
# -------------------------------------------------------------

CSV_FILENAME = "mapAdvert.csv"
CSV_HEADER = [
    "Timestamp",
    "NodeName",
    "Latitude",
    "Longitude",
    "Nodtyp",
    "LastNodHash",
    "SecondLastNodHash",
    "HashCount",
    "PublicKey",
]


def _script_dir():
    """
    Ta reda på mappen där .grc-filen (och den genererade
    meshcore_rx.py) ligger.

    OBS: __file__ finns INTE definierat här, eftersom GNU Radio
    Companion kör embedded Python-block via exec() istället för
    en vanlig modulimport. Använd därför sys.argv[0], som pekar
    på den toppnivåfil (meshcore_rx.py) som faktiskt startades
    med "python meshcore_rx.py" / GRC:s Execute-knapp, och som
    ligger i samma mapp som .grc-filen.
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
# MeshCore header-fält (src/Packet.h i meshcore-dev/MeshCore)
# Headerbyte: 0bVVPPPPRR
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
# Advert appdata-flaggor (docs/payloads.md)
# -------------------------------------------------------------

ADV_TYPE_MASK = 0x0F          # låga 4 bitarna: nodtyp (enum, ej bitmask)
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
    MeshCore paketdekoder (Advert, Request/Response/TxtMsg/Path,
    Ack, Group Txt/Data, Anon Request, Control)
    """

    def __init__(self):
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

        # Nod-hash (1 byte) -> nodnamn, byggs upp allteftersom
        # vi ser Advert-paket. Används för att slå upp
        # avsändare/mottagare i krypterade paket (Request,
        # Response, TxtMsg, Path).
        self.known_nodes = {}

        # Nod-hash (1 byte) -> full publik nyckel (hex, 32 byte).
        # Fylls på både från Advert och från CONTROL/DISCOVER_RESP
        # (som läcker den fulla nyckeln helt okrypterat).
        self.known_pubkeys = {}

        # CSV-export: en unik rad per nod (nyckel = publik nyckel
        # i hex). self.csv_path sätts INTE här - se
        # _ensure_csv_ready() nedan för varför.
        self.csv_path = None
        self._csv_ready = False

    # ---------------------------------------------------------
    # Lat initiering av CSV-sökväg/-mapp.
    #
    # VIKTIGT: detta får INTE göras i __init__(). GNU Radio
    # Companion instansierar embedded Python-block redan vid
    # designtid (när du öppnar/redigerar .grc-filen) genom att
    # exec():a kodrutans innehåll direkt - inte genom att
    # importera själva filen. I det läget:
    #   - finns __file__ inte definierat
    #   - pekar sys.argv[0]/cwd på GRC:s egen installations-
    #     mapp (t.ex. radioconda\Library\bin), där man oftast
    #     INTE har skrivrättigheter
    # Att skapa mappar/filer i __init__ kraschar därför
    # blockets instansiering redan när du öppnar flowgraphen,
    # långt innan den faktiskt körs.
    #
    # Genom att skjuta upp detta till första riktiga
    # handle_msg-anrop körs koden istället i den process som
    # faktiskt startas via "python meshcore_rx.py" (där
    # meshcore_rx_epy_block_0 laddas med en vanlig import), och
    # då är __file__ korrekt satt till sökvägen bredvid
    # .grc-filen.
    # ---------------------------------------------------------
    def _ensure_csv_ready(self):

        if self._csv_ready:
            return

        self._csv_ready = True  # försök bara en gång

        try:
            script_dir = _script_dir()
            output_dir = os.path.join(script_dir, "output")
            os.makedirs(output_dir, exist_ok=True)

            self.csv_path = os.path.join(output_dir, CSV_FILENAME)

            # Skriv rubrikraden bara om filen inte redan finns
            # (eller är tom) - annars fortsätter vi bara fylla på.
            need_header = (
                not os.path.exists(self.csv_path)
                or os.path.getsize(self.csv_path) == 0
            )
            if need_header:
                with open(self.csv_path, "w", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow(CSV_HEADER)

        except Exception as e:
            print(
                "Kunde inte förbereda mapAdvert.csv (CSV-export "
                "avstängd för denna körning):", repr(e)
            )
            self.csv_path = None

    # ---------------------------------------------------------
    # Lägg till en ny rad i mapAdvert.csv (en rad per mottaget
    # Advert-paket - inte begränsat till en unik rad per nod)
    # ---------------------------------------------------------
    def _append_csv_row(self, row):

        if not self.csv_path:
            return

        try:
            with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=CSV_HEADER).writerow(row)
        except Exception as e:
            print("Kunde inte skriva till", self.csv_path, ":", repr(e))

    # ---------------------------------------------------------
    # Läs ut den råa payloaden ur den inkommande PMT:n
    # ---------------------------------------------------------
    def _extract_bytes(self, msg):

        if pmt.is_u8vector(msg):
            return bytes(pmt.u8vector_elements(msg))

        if pmt.is_blob(msg):
            return bytes(pmt.blob_data(msg))

        if pmt.is_symbol(msg):

            # Viktigt:
            # LoRa-blocket skickar binära bytes som en PMT
            # "symbol". pmt.symbol_to_string() kraschar på detta
            # eftersom den gör en STRIKT UTF-8-avkodning i
            # pybind11-lagret - och LoRa-payloads är rå binärdata,
            # inte text, så nästan varje paket innehåller ogiltiga
            # UTF-8-sekvenser.
            #
            # Workaround: gå via PMT:s binära serialisering
            # (pmt.serialize_str), som returnerar rena 'bytes'
            # utan någon UTF-8-tvingning, och plocka ut råbytesen
            # manuellt ur PMT-serieformatet:
            #   [1 byte tagg][2 byte längd, big-endian][data]
            # Taggen för en symbol i PMT-serieformatet är 0x02.
            raw = pmt.serialize_str(msg)

            if len(raw) < 3 or raw[0] != 0x02:
                print(
                    "Oväntat PMT-serieformat, första byte:",
                    raw[0:1].hex() if raw else "(tomt)"
                )
                return None

            length = (raw[1] << 8) | raw[2]
            data = raw[3:3 + length]

            if len(data) != length:
                print(
                    "Serialiserad längd stämmer inte:",
                    "förväntade", length,
                    "fick", len(data)
                )
                return None

            return data

        print("Okänd PMT-typ:")
        print(pmt.write_string(msg))
        return None

    # ---------------------------------------------------------
    # Slå upp ett nod-hash mot kända nodnamn/publika nycklar
    # (om vi sett dess Advert eller DISCOVER_RESP tidigare)
    # ---------------------------------------------------------
    def _fmt_hash(self, node_hash):
        name = self.known_nodes.get(node_hash)
        if name:
            return "%02x (%s)" % (node_hash, name)
        if node_hash in self.known_pubkeys:
            return "%02x (namn okänt, men pubkey sedd)" % node_hash
        return "%02x (okänd nod)" % node_hash

    # ---------------------------------------------------------
    # Advert
    # ---------------------------------------------------------
    def _handle_advert(self, payload, hops):

        # Public key(32) + timestamp(4) + signature(64)
        # + appdata flags(1) = 101 byte minimum
        if len(payload) < 101:
            print(
                "För kort för MeshCore Advert-payload:",
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
            flags & ADV_TYPE_MASK, "okänd/ingen"
        )

        p = 1
        latitude = None
        longitude = None

        if flags & ADV_FLAG_HAS_LOCATION:
            if len(appdata) < p + 8:
                print("Appdata för kort för lat/long")
                return
            lat_raw = struct.unpack("<i", appdata[p:p + 4])[0]
            lon_raw = struct.unpack("<i", appdata[p + 4:p + 8])[0]
            # Enligt spec: decimalgrader * 1 000 000, som int32
            latitude = lat_raw / 1_000_000.0
            longitude = lon_raw / 1_000_000.0
            p += 8

        if flags & ADV_FLAG_HAS_FEATURE1:
            p += 2  # reserverat för framtida bruk

        if flags & ADV_FLAG_HAS_FEATURE2:
            p += 2  # reserverat för framtida bruk

        node_name = None
        if flags & ADV_FLAG_HAS_NAME:
            name_bytes = appdata[p:].rstrip(b"\x00")
            node_name = name_bytes.decode("utf-8", errors="replace")

        # Kom ihåg noden för framtida uppslag
        self.known_pubkeys[node_hash] = public_key.hex()
        if node_name:
            self.known_nodes[node_hash] = node_name

        # -------------------------------------------------
        # CSV: en ny rad per mottaget Advert (logg, inte
        # begränsat till en unik rad per nod)
        # -------------------------------------------------

        last_hop = hops[-1] if hops else ""
        second_last_hop = hops[-2] if len(hops) >= 2 else ""

        # Tidsstämpel = när VI tog emot paketet (systemtid), inte
        # advert-paketets egen interna timestamp-fält (som skrivs
        # ut separat nedan under "Timestamp"). Så här ser man
        # exakt när i din logg varje rad kom in.
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
            "Nodtyp": node_type,
            "LastNodHash": last_hop,
            "SecondLastNodHash": second_last_hop,
            "HashCount": len(hops),
            "PublicKey": public_key.hex(),
        }

        self._append_csv_row(csv_row)

        print()
        print("================================")
        print("       MESHCORE ADVERT")
        print("================================")

        print("Node Name       :", node_name if node_name else "(inget namn i paketet)")
        print("Nod-hash        : %02x" % node_hash)
        print("Nodtyp          :", node_type)

        if hops:
            print("Kommer från nod-hash (senaste hopp): %s" % hops[-1])
            print("Fullständig väg (hopp)             :", " -> ".join(hops))
        else:
            print("Kommer från     : direkt (0 hopp, ingen repeater)")

        print("Public Key      :", public_key.hex())
        print("Timestamp       :", timestamp)
        print("App Flags       : 0x%02X" % flags)

        if latitude is not None:
            print("Latitude        :", latitude)
            print("Longitude       :", longitude)
        else:
            print("Latitude/Long   : (ingen platsinfo i paketet)")

        print("================================")
        if self.csv_path:
            print("CSV uppdaterad  :", self.csv_path)

        if node_name:
            self.message_port_pub(
                pmt.intern("out"),
                pmt.intern(node_name)
            )

    # ---------------------------------------------------------
    # Request / Response / TxtMsg / (Returned) Path
    # Delar samma kuvert:
    #   dest_hash(1) + src_hash(1) + cipher_mac(2) + ciphertext
    # Innehållet (ciphertext) är krypterat och kan inte läsas
    # här - vi kan bara visa vem som pratar med vem.
    # ---------------------------------------------------------
    def _handle_enveloped(self, payload, payload_name):

        if len(payload) < 4:
            print("För kort för %s-kuvert:" % payload_name, len(payload))
            return

        dest_hash = payload[0]
        src_hash = payload[1]
        cipher_mac = payload[2:4]
        ciphertext = payload[4:]

        print()
        print("================================")
        print("       MESHCORE %s" % payload_name)
        print("================================")
        print("Till (dest_hash)  :", self._fmt_hash(dest_hash))
        print("Från (src_hash)   :", self._fmt_hash(src_hash))
        print("Cipher MAC        :", cipher_mac.hex())
        print("Ciphertext-längd  :", len(ciphertext), "byte")
        print("(Innehållet är krypterat - kan inte läsas här)")
        print("================================")

    # ---------------------------------------------------------
    # Ack
    # ---------------------------------------------------------
    def _handle_ack(self, payload):

        if len(payload) < 4:
            print("För kort för ACK:", len(payload))
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
            print("För kort för %s:" % payload_name, len(payload))
            return

        channel_hash = payload[0]
        cipher_mac = payload[1:3]
        ciphertext = payload[3:]

        print()
        print("=== MESHCORE %s ===" % payload_name)
        print("Channel hash      : %02x" % channel_hash)
        print("Cipher MAC        :", cipher_mac.hex())
        print("Ciphertext-längd  :", len(ciphertext), "byte")
        print("(Innehållet är krypterat - kan inte läsas här)")

    # ---------------------------------------------------------
    # Anonymous Request
    # dest_hash(1) + public_key(32) + cipher_mac(2) + ciphertext
    # ---------------------------------------------------------
    def _handle_anon_req(self, payload):

        if len(payload) < 35:
            print("För kort för ANON_REQ:", len(payload))
            return

        dest_hash = payload[0]
        sender_pubkey = payload[1:33]
        cipher_mac = payload[33:35]
        ciphertext = payload[35:]

        print()
        print("=== MESHCORE ANON_REQ ===")
        print("Till (dest_hash)   :", self._fmt_hash(dest_hash))
        print("Avsändarens pubkey :", sender_pubkey.hex())
        print("Cipher MAC         :", cipher_mac.hex())
        print("Ciphertext-längd   :", len(ciphertext), "byte")
        print("(Innehållet är krypterat - kan inte läsas här)")

    # ---------------------------------------------------------
    # Control data (helt okrypterat)
    # flags(1, övre 4 bitar = sub_type) + data
    #
    # Sub-type 0x8 = DISCOVER_REQ, 0x9 = DISCOVER_RESP
    # (bekräftat mot officiell dokumentation + verifierat
    # byte-för-byte mot riktiga fångade paket).
    # ---------------------------------------------------------
    CONTROL_SUBTYPE_DISCOVER_REQ = 0x8
    CONTROL_SUBTYPE_DISCOVER_RESP = 0x9

    def _handle_control(self, payload):

        if len(payload) < 1:
            print("För kort för CONTROL:", len(payload))
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
    # VIKTIGT FYND: den svarande nodens FULLA publika nyckel
    # skickas helt okrypterat här - inte bara en 1-byte-hash.
    # Verifierat mot tre riktiga paket där node_hash (första
    # byten i nyckeln) stämmer exakt mot redan kända noder.
    # ---------------------------------------------------------
    def _handle_discover_resp(self, data):

        if len(data) < 37:
            print(
                "För kort för DISCOVER_RESP (förväntade minst 37 "
                "byte, fick %d)" % len(data)
            )
            print("Data     :", data.hex())
            return

        tag = struct.unpack("<I", data[0:4])[0]
        reserved = data[4]
        pubkey = data[5:37]
        node_hash = pubkey[0]

        # Spara nyckeln även om vi inte känner namnet ännu - kan
        # bli användbart senare, och stärker uppslag i known_nodes
        # (som annars bara byggs upp via Advert-paket).
        self.known_pubkeys[node_hash] = pubkey.hex()

        print("  -> DISCOVER_RESP (svar på 'vem finns här'-fråga)")
        print("Tag        : 0x%08X" % tag)
        print("Reserved   : 0x%02X" % reserved)
        print("Public Key : %s" % pubkey.hex())
        print("Nod-hash   :", self._fmt_hash(node_hash))

    # ---------------------------------------------------------
    # DISCOVER_REQ: bästa tolkning enligt tillgänglig
    # dokumentation + byte-för-byte-jämförelse mot två riktiga
    # paket (identisk layout i båda):
    #   type_filter(1) + tag(4, LE) + reserved(4)
    # Exakt semantik för type_filter är inte 100% bekräftad -
    # visas därför som rå hex snarare än en tolkad etikett.
    # ---------------------------------------------------------
    def _handle_discover_req(self, data):

        if len(data) < 9:
            print(
                "För kort för DISCOVER_REQ (förväntade minst 9 "
                "byte, fick %d)" % len(data)
            )
            print("Data     :", data.hex())
            return

        type_filter = data[0]
        tag = struct.unpack("<I", data[1:5])[0]
        reserved = data[5:9]

        print("  -> DISCOVER_REQ ('vem finns här'-fråga)")
        print("Type filter : 0x%02X" % type_filter)
        print("Tag         : 0x%08X" % tag)
        print("Reserved    :", reserved.hex())

    # ---------------------------------------------------------
    # Trace: spårar en väg genom mesh-nätet och samlar SNR per
    # hopp. Helt okrypterat.
    #   tag(4, LE) + auth_code(4, LE) + flags(1) + path_hashes(rest)
    # ---------------------------------------------------------
    def _handle_trace(self, payload):

        if len(payload) < 9:
            print("För kort för TRACE:", len(payload))
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
            print("Väg hittills (nod-hashar):", " -> ".join(hop_list))
        else:
            print("Väg hittills: (tom, spårningen börjar här)")

    # ---------------------------------------------------------
    # Huvudhanterare
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
                print("För kort för att ens innehålla header + path_len")
                return

            # -------------------------------------------------
            # Header
            # -------------------------------------------------

            header = data[0]

            version = (header >> 6) & 0x03
            payload_type = (header >> 2) & 0x0F
            route_type = header & 0x03

            route_name = ROUTE_TYPE_NAMES.get(
                route_type, "OKÄND(0x%X)" % route_type
            )
            payload_name = PAYLOAD_TYPE_NAMES.get(
                payload_type, "OKÄND(0x%X)" % payload_type
            )

            print("Header:       0x%02X" % header)
            print("Version:      ", version)
            print("Route type:   ", route_name)
            print("Payload type: ", payload_name)

            offset = 1

            # -------------------------------------------------
            # Transport codes (bara för *_TRANSPORT_* route types)
            # -------------------------------------------------

            has_transport = route_type in (
                ROUTE_TYPE_TRANSPORT_FLOOD,
                ROUTE_TYPE_TRANSPORT_DIRECT,
            )

            if has_transport:

                if len(data) < offset + 4:
                    print("För kort för transport codes")
                    return

                transport_codes = data[offset:offset + 4]
                offset += 4

                print("Transport codes:", transport_codes.hex())

            # -------------------------------------------------
            # Path length + path (nod-hashar för varje hopp)
            # -------------------------------------------------

            if len(data) < offset + 1:
                print("För kort för path_len-byten")
                return

            path_len_byte = data[offset]
            offset += 1

            hash_size = ((path_len_byte >> 6) & 0x03) + 1
            hash_count = path_len_byte & 0x3F
            path_bytes_len = hash_size * hash_count

            print(
                "Path_len byte: 0x%02X  "
                "(hash_size=%d byte/hopp, hash_count=%d hopp)"
                % (path_len_byte, hash_size, hash_count)
            )

            if len(data) < offset + path_bytes_len:
                print("För kort för path-fältet")
                return

            path_bytes = data[offset:offset + path_bytes_len]
            offset += path_bytes_len

            hops = [
                path_bytes[i:i + hash_size].hex()
                for i in range(0, path_bytes_len, hash_size)
            ]

            if hops:
                print(
                    "Path (hopp, äldst -> närmast):",
                    " -> ".join(hops)
                )
                print("Mottaget via (senaste hoppet):", hops[-1])
            else:
                print("Path: tom (mottaget direkt, 0 hopp)")

            # -------------------------------------------------
            # Payload - avkodas olika beroende på typ
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
                    "Payload type %s stöds inte för detaljerad "
                    "parsning ännu." % payload_name
                )

        except Exception as e:

            print(
                "MeshCore decoder error:",
                repr(e)
            )
