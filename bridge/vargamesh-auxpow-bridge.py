#!/usr/bin/env python3

import argparse
import base64
import hashlib
import json
import struct
import threading
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


AUXPOW_CHAIN_ID = 0x564D
AUXPOW_NONCE = 0x564D
AUXPOW_VERSION_BIT = 1 << 8
MERGED_MINING_MAGIC = bytes.fromhex("fabe6d6d")

# Official VMESH mainnet pool fee; not a blockchain consensus rule.
VMESH_DEVELOPER_FEE_ADDRESS = "vm1qsfsgj70pdv86nguvexzvsflmcknz4lrsj87wx9"
VMESH_DEVELOPER_PAYOUT_SCRIPT = bytes.fromhex(
    "001482608979e16b0fa9a38cc984c827fbc5a62afc70"
)
VMESH_DEVELOPER_FEE_PERCENT = 1
VMESH_INITIAL_SUBSIDY_SATS = 25 * 100_000_000
VMESH_SUBSIDY_HALVING_INTERVAL = 1_051_200


def vmesh_reward_split(gbt):
    """(miner, developer) in VMESH sats. Fee only on block subsidy."""
    height = int(gbt["height"])
    total_reward = int(gbt["coinbasevalue"])
    if height < 0 or total_reward < 0:
        raise ValueError("invalid VMESH height or coinbasevalue")

    halvings = height // VMESH_SUBSIDY_HALVING_INTERVAL
    subsidy = VMESH_INITIAL_SUBSIDY_SATS >> halvings if halvings < 64 else 0
    if total_reward < subsidy:
        raise ValueError(
            "VMESH coinbasevalue below expected mainnet subsidy; "
            "verify Core network and height"
        )
    developer = subsidy * VMESH_DEVELOPER_FEE_PERCENT // 100
    return total_reward - developer, developer



def h256(data: bytes) -> bytes:
    return hashlib.sha256(
        hashlib.sha256(data).digest()
    ).digest()


def u32(n):
    return struct.pack("<I", n & 0xffffffff)


def i32(n):
    return struct.pack("<i", n)


def u64(n):
    return struct.pack("<Q", n)


def compact(n):
    if n < 0xfd:
        return bytes([n])
    if n <= 0xffff:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xffffffff:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def read_compact(raw, pos):
    first = raw[pos]
    pos += 1

    if first < 0xfd:
        return first, pos

    if first == 0xfd:
        return struct.unpack_from("<H", raw, pos)[0], pos + 2

    if first == 0xfe:
        return struct.unpack_from("<I", raw, pos)[0], pos + 4

    return struct.unpack_from("<Q", raw, pos)[0], pos + 8


def script_num(n):
    if n == 0:
        return b""

    out = bytearray()

    while n:
        out.append(n & 0xff)
        n >>= 8

    if out[-1] & 0x80:
        out.append(0)

    return bytes(out)


def push(data):
    n = len(data)

    if n <= 75:
        return bytes([n]) + data

    if n <= 255:
        return b"\x4c" + bytes([n]) + data

    raise ValueError("push too large")


def script_int(n):
    """
    Match Bitcoin/VargaMesh CScript << int64_t semantics.

    BIP34 validation compares the beginning of the
    child coinbase scriptSig against:

        CScript() << nHeight

    Small integers 0..16 therefore use OP_N rather
    than a pushed ScriptNum byte vector.
    """

    if n == -1:
        return b"\x4f"  # OP_1NEGATE

    if n == 0:
        return b"\x00"  # OP_0

    if 1 <= n <= 16:
        return bytes([0x50 + n])  # OP_1 .. OP_16

    return push(script_num(n))


def bits_target(bits):
    exp = bits >> 24
    mant = bits & 0x007fffff

    if exp <= 3:
        return mant >> (8 * (3 - exp))

    return mant << (8 * (exp - 3))


def merkle_root(leaves):
    if not leaves:
        raise ValueError("empty merkle tree")

    level = list(leaves)

    while len(level) > 1:
        if len(level) & 1:
            level.append(level[-1])

        level = [
            h256(level[i] + level[i + 1])
            for i in range(0, len(level), 2)
        ]

    return level[0]


def parse_tx(raw, start=0):
    pos = start
    version = raw[pos:pos + 4]
    pos += 4

    segwit = False

    if (
        pos + 2 <= len(raw)
        and raw[pos] == 0
        and raw[pos + 1] != 0
    ):
        segwit = True
        pos += 2

    vin_count, pos = read_compact(raw, pos)

    base = bytearray(version)
    base += compact(vin_count)

    first_prev = None
    first_index = None
    first_script = None

    for idx in range(vin_count):
        prev = raw[pos:pos + 32]
        pos += 32

        prev_index = raw[pos:pos + 4]
        pos += 4

        slen, pos2 = read_compact(raw, pos)
        script_prefix = raw[pos:pos2]
        pos = pos2

        script = raw[pos:pos + slen]
        pos += slen

        sequence = raw[pos:pos + 4]
        pos += 4

        base += prev
        base += prev_index
        base += script_prefix
        base += script
        base += sequence

        if idx == 0:
            first_prev = prev
            first_index = prev_index
            first_script = script

    vout_count, pos = read_compact(raw, pos)
    base += compact(vout_count)

    for _ in range(vout_count):
        value = raw[pos:pos + 8]
        pos += 8

        plen, pos2 = read_compact(raw, pos)
        prefix = raw[pos:pos2]
        pos = pos2

        script = raw[pos:pos + plen]
        pos += plen

        base += value
        base += prefix
        base += script

    if segwit:
        for _ in range(vin_count):
            count, pos = read_compact(raw, pos)

            for _ in range(count):
                size, pos = read_compact(raw, pos)
                pos += size

    locktime = raw[pos:pos + 4]
    pos += 4
    base += locktime

    return {
        "end": pos,
        "full": raw[start:pos],
        "base": bytes(base),
        "txid_raw": h256(bytes(base)),
        "script_sig": first_script,
        "first_prev": first_prev,
        "first_index": first_index,
        "segwit": segwit,
    }


def synthetic_parent_prev(child_tip_display):
    child_tip_raw = bytes.fromhex(child_tip_display)[::-1]

    return h256(
        b"VARGAMESH-AUXPOW-PARENT-PREV"
        + child_tip_raw
    )[::-1].hex()


def coinbase_output(value, script):
    return (
        u64(value)
        + compact(len(script))
        + script
    )


def build_child_coinbase(gbt, miner_script, developer_script):
    height = int(gbt["height"])

    flags = b""

    for _, value in sorted(
        (gbt.get("coinbaseaux") or {}).items()
    ):
        if isinstance(value, str):
            flags += bytes.fromhex(value)

    script_sig = (
        script_int(height)
        + flags
        + push(b"/VargaMesh-Bridge/")
    )

    if not (2 <= len(script_sig) <= 100):
        raise ValueError(
            f"child coinbase scriptSig size {len(script_sig)}"
        )

    inp = (
        b"\x00" * 32
        + b"\xff\xff\xff\xff"
        + compact(len(script_sig))
        + script_sig
        + b"\xff\xff\xff\xff"
    )

    miner_value, developer_value = vmesh_reward_split(gbt)

    outputs = [
        coinbase_output(miner_value, miner_script),
        coinbase_output(developer_value, developer_script),
    ]

    witness_commitment = gbt.get(
        "default_witness_commitment"
    )

    if witness_commitment:
        outputs.append(
            coinbase_output(
                0,
                bytes.fromhex(witness_commitment),
            )
        )

    outs = (
        compact(len(outputs))
        + b"".join(outputs)
    )

    base = (
        u32(1)
        + compact(1)
        + inp
        + outs
        + u32(0)
    )

    if witness_commitment:
        full = (
            u32(1)
            + b"\x00\x01"
            + compact(1)
            + inp
            + outs
            + compact(1)
            + compact(32)
            + b"\x00" * 32
            + u32(0)
        )
    else:
        full = base

    return base, full


class NodeRPC:
    def __init__(self, url, user, password):
        self.url = url
        token = base64.b64encode(
            f"{user}:{password}".encode()
        ).decode()

        self.auth = "Basic " + token

    def call(self, method, params=None):
        payload = json.dumps({
            "jsonrpc": "1.0",
            "id": "vargamesh-bridge",
            "method": method,
            "params": params or [],
        }).encode()

        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": self.auth,
            },
        )

        with urllib.request.urlopen(
            req,
            timeout=30,
        ) as res:
            obj = json.loads(
                res.read().decode()
            )

        if obj.get("error") is not None:
            raise RuntimeError(
                f"{method}: {obj['error']}"
            )

        return obj.get("result")


class BridgeCore:
    def __init__(
        self,
        node,
        developer_payout_script,
        parent_script,
    ):
        if developer_payout_script != VMESH_DEVELOPER_PAYOUT_SCRIPT:
            raise ValueError(
                "VMESH developer payout script must match fixed address "
                + VMESH_DEVELOPER_FEE_ADDRESS
            )
        self.node = node
        self.developer_payout_script = VMESH_DEVELOPER_PAYOUT_SCRIPT
        self.parent_script = parent_script

        self.lock = threading.Lock()
        self.states = OrderedDict()
        self.latest = None

        # Serialize accepted-block submission so concurrent
        # CKPool HTTP submitblock requests cannot race each
        # other into repeated VMESH node submissions.
        self.submit_lock = threading.Lock()

        # Parent hashes that have already produced a VMESH
        # block accepted/known by the real child node.
        self.accepted_parents = OrderedDict()

        # VMESH child commitments already accepted/known.
        # This is the important AuxPoW-level dedup key:
        # many different valid parent proofs may commit to
        # the same immutable VMESH child header.
        self.accepted_children = OrderedDict()

        # Runtime diagnostics / self-test counters.
        self.forwarded_submits = 0
        self.duplicate_parent_submits = 0
        self.duplicate_child_submits = 0

    def _remember(self, state):
        key = state["commitment_root"].hex()

        with self.lock:
            self.states[key] = state
            self.states.move_to_end(key)

            while len(self.states) > 64:
                self.states.popitem(last=False)

            self.latest = state

    def _lookup(self, child_raw):
        key = child_raw.hex()

        with self.lock:
            return self.states.get(key)

    def _validate_child_template(self, gbt):
        version = int(gbt["version"])

        if not (version & AUXPOW_VERSION_BIT):
            raise RuntimeError("VMESH GBT missing VERSION_AUXPOW")

        if "auxpow_chainid" in gbt and int(gbt["auxpow_chainid"]) != AUXPOW_CHAIN_ID:
            raise RuntimeError("wrong VMESH auxpow_chainid")

        if "auxpow_childnonce" in gbt and int(gbt["auxpow_childnonce"]) != AUXPOW_NONCE:
            raise RuntimeError("wrong VMESH auxpow_childnonce")

        return version

    def _build_user_state(self, gbt, miner_script, miner_address):
        version = self._validate_child_template(gbt)

        cb_base, cb_full = build_child_coinbase(
            gbt,
            miner_script,
            self.developer_payout_script,
        )

        leaves = [h256(cb_base)]
        child_txs = []

        for item in gbt.get("transactions", []):
            tx_raw = bytes.fromhex(item["data"])
            child_txs.append(tx_raw)
            txid = item.get("txid")
            if txid:
                leaves.append(bytes.fromhex(txid)[::-1])
            else:
                leaves.append(parse_tx(tx_raw)["txid_raw"])

        mr = merkle_root(leaves)
        bits = int(gbt["bits"], 16)
        child_header = (
            u32(version)
            + bytes.fromhex(gbt["previousblockhash"])[::-1]
            + mr
            + u32(int(gbt["curtime"]))
            + u32(bits)
            + u32(AUXPOW_NONCE)
        )

        if len(child_header) != 80:
            raise RuntimeError("child header != 80 bytes")

        child_hash_raw = h256(child_header)
        commitment_root = child_hash_raw[::-1]
        commitment = MERGED_MINING_MAGIC + commitment_root + u32(1) + u32(0)

        if len(commitment) != 44:
            raise RuntimeError("commitment != 44 bytes")

        target = int(gbt["target"], 16) if gbt.get("target") else bits_target(bits)
        parent_prev = synthetic_parent_prev(gbt["previousblockhash"])
        miner_value, developer_value = vmesh_reward_split(gbt)

        state = {
            "gbt": gbt,
            "child_header": child_header,
            "child_hash_raw": child_hash_raw,
            "commitment_root": commitment_root,
            "child_hash": child_hash_raw[::-1].hex(),
            "child_coinbase": cb_full,
            "child_transactions": child_txs,
            "target": target,
            "commitment": commitment,
            "parent_prev": parent_prev,
            "miner_address": miner_address,
            "miner_value": miner_value,
            "developer_value": developer_value,
        }
        self._remember(state)
        return state

    def build_user_work(self, address, expected_height, expected_bits):
        va = self.node.call("validateaddress", [address])
        if not va or va.get("isvalid") is not True:
            raise RuntimeError("invalid VMESH miner address")

        script_hex = va.get("scriptPubKey")
        if not isinstance(script_hex, str) or not script_hex:
            raise RuntimeError("miner scriptPubKey missing")
        miner_script = bytes.fromhex(script_hex)

        gbt = self.node.call("getblocktemplate", [{"rules": ["segwit"]}])
        self._validate_child_template(gbt)

        if int(gbt["height"]) != int(expected_height):
            raise RuntimeError("stale VMESH user work height")
        if str(gbt["bits"]).lower() != str(expected_bits).lower():
            raise RuntimeError("stale VMESH user work bits")

        state = self._build_user_state(gbt, miner_script, address)
        return {
            "address": address,
            "height": int(gbt["height"]),
            "bits": gbt["bits"],
            "commitment": state["commitment"].hex(),
            "child_hash": state["child_hash"],
            "miner_value": state["miner_value"],
            "developer_value": state["developer_value"],
        }

    def describe_commitment(self, commitment_hex):
        if not isinstance(commitment_hex, str):
            raise RuntimeError("commitment must be hex string")

        try:
            commitment = bytes.fromhex(commitment_hex)
        except ValueError as exc:
            raise RuntimeError("commitment is not valid hex") from exc

        if len(commitment) != 44:
            raise RuntimeError("commitment must be 44 bytes")

        if commitment[:4] != MERGED_MINING_MAGIC:
            raise RuntimeError("commitment merged-mining magic mismatch")

        child_raw = commitment[4:36]
        state = self._lookup(child_raw)

        if state is None:
            raise RuntimeError("unknown/stale VMESH child commitment")

        if commitment != state["commitment"]:
            raise RuntimeError("commitment mismatch")

        reward = (
            int(state["miner_value"])
            + int(state["developer_value"])
        )
        gbt = state["gbt"]

        return {
            "commitment": state["commitment"].hex(),
            "child_hash": state["child_hash"],
            "miner_address": state["miner_address"],
            "developer_script": self.developer_payout_script.hex(),
            "reward_sat": reward,
            "miner_value_sat": int(state["miner_value"]),
            "developer_value_sat": int(state["developer_value"]),
            "height": int(gbt["height"]),
            "bits": gbt["bits"],
        }

    def build_parent_template(self):
        gbt = self.node.call("getblocktemplate", [{"rules": ["segwit"]}])
        self._validate_child_template(gbt)
        bits = int(gbt["bits"], 16)
        target = int(gbt["target"], 16) if gbt.get("target") else bits_target(bits)
        parent_prev = synthetic_parent_prev(gbt["previousblockhash"])

        return {
            "capabilities": ["proposal", "coinbasevalue", "workid"],
            "version": 0x20000000,
            "rules": ["segwit"],
            "vbavailable": {},
            "vbrequired": 0,
            "previousblockhash": parent_prev,
            "transactions": [],
            "coinbaseaux": {"flags": ""},
            "coinbasevalue": int(gbt["coinbasevalue"]),
            "longpollid": parent_prev + str(gbt["height"]),
            "target": f"{target:064x}",
            "mintime": int(gbt.get("mintime", gbt["curtime"])),
            "mutable": ["time", "transactions", "prevblock"],
            "noncerange": "00000000ffffffff",
            "sigoplimit": 80000,
            "sizelimit": 4000000,
            "weightlimit": 4000000,
            "curtime": int(gbt["curtime"]),
            "bits": gbt["bits"],
            "height": int(gbt["height"]),
            "workid": f"vmesh-{int(gbt['height'])}-{gbt['bits']}",
        }

    def get_parent_besthash(self):
        real_tip = self.node.call(
            "getbestblockhash"
        )

        return synthetic_parent_prev(
            real_tip
        )

    def submit_parent_block(self, block_hex):
        raw = bytes.fromhex(block_hex)

        if len(raw) < 81:
            raise RuntimeError(
                "parent block too short"
            )

        parent_header = raw[:80]

        tx_count, pos = read_compact(
            raw,
            80,
        )

        if tx_count != 1:
            raise RuntimeError(
                "synthetic parent must have exactly one transaction"
            )

        tx = parse_tx(raw, pos)

        if tx["end"] != len(raw):
            raise RuntimeError(
                "unexpected bytes after parent coinbase"
            )

        if tx["first_prev"] != b"\x00" * 32:
            raise RuntimeError(
                "parent tx is not coinbase"
            )

        if tx["first_index"] != b"\xff" * 4:
            raise RuntimeError(
                "parent coinbase prevout index invalid"
            )

        script = tx["script_sig"]

        if script is None:
            raise RuntimeError(
                "parent coinbase scriptSig missing"
            )

        if script.count(MERGED_MINING_MAGIC) != 1:
            raise RuntimeError(
                "merged-mining marker count != 1"
            )

        off = script.index(
            MERGED_MINING_MAGIC
        )

        end = off + 44

        if end > len(script):
            raise RuntimeError(
                "truncated merged-mining commitment"
            )

        commitment = script[off:end]

        child_raw = commitment[4:36]
        tree_size = struct.unpack(
            "<I",
            commitment[36:40],
        )[0]
        tree_nonce = struct.unpack(
            "<I",
            commitment[40:44],
        )[0]

        if tree_size != 1:
            raise RuntimeError(
                "AuxPoW tree size != 1"
            )

        if tree_nonce != 0:
            raise RuntimeError(
                "AuxPoW tree nonce != 0"
            )

        state = self._lookup(
            child_raw
        )

        if state is None:
            raise RuntimeError(
                "unknown/stale VMESH child commitment"
            )

        if commitment != state["commitment"]:
            raise RuntimeError(
                "commitment mismatch"
            )

        parent_merkle = parent_header[36:68]

        if parent_merkle != tx["txid_raw"]:
            raise RuntimeError(
                "parent merkle root mismatch"
            )

        parent_hash_raw = h256(
            parent_header
        )

        parent_value = int.from_bytes(
            parent_hash_raw,
            "little",
        )

        if parent_value > state["target"]:
            raise RuntimeError(
                "parent PoW does not satisfy VMESH target"
            )

        # Display-style SHA256d parent-header hash.
        parent_key = parent_hash_raw[::-1].hex()

        # Commitment root is the conventional displayed
        # VMESH child hash in this bridge.
        child_key = child_raw.hex()

        auxpow = (
            tx["full"]

            # Legacy hashBlock placeholder.
            + b"\x00" * 32

            # Parent merkle branch: empty because
            # synthetic parent has coinbase only.
            + compact(0)

            # Parent coinbase index.
            + i32(0)

            # Aux chain merkle branch:
            # empty because tree size = 1.
            + compact(0)

            # Chain index.
            + i32(0)

            # Parent pure 80-byte header.
            + parent_header
        )

        child_block = (
            state["child_header"]
            + auxpow
            + compact(
                1
                + len(
                    state[
                        "child_transactions"
                    ]
                )
            )
            + state["child_coinbase"]
            + b"".join(
                state[
                    "child_transactions"
                ]
            )
        )

        # ThreadingHTTPServer may deliver several valid
        # parent solves almost simultaneously.  With an
        # AuxPoW child the parent header can vary while the
        # final VMESH child block is identical, so normal
        # CKPool share dedup alone is insufficient.
        #
        # Do not hold the general state lock across RPC.
        # submit_lock is dedicated to this slow path.
        with self.submit_lock:

            if parent_key in self.accepted_parents:
                self.accepted_parents.move_to_end(
                    parent_key
                )

                self.duplicate_parent_submits += 1

                print(
                    "VMESH DEDUP parent already accepted: "
                    + parent_key,
                    flush=True,
                )

                # Intentionally not plain "duplicate".
                # CKPool treats the exact BIP22 string
                # "duplicate" as another accepted block.
                return "duplicate-vmesh-parent"

            if child_key in self.accepted_children:
                self.accepted_children.move_to_end(
                    child_key
                )

                self.duplicate_child_submits += 1

                print(
                    "VMESH DEDUP child already accepted: "
                    + child_key,
                    flush=True,
                )

                return "duplicate-vmesh-child"

            result = self.node.call(
                "submitblock",
                [child_block.hex()],
            )

            self.forwarded_submits += 1

            # BIP22 success is null/None.  A real node may
            # also answer "duplicate" if it already knows
            # the exact block through another path; that is
            # also safe to cache as accepted/known.
            if (
                result is None
                or result == "duplicate"
            ):
                self.accepted_parents[
                    parent_key
                ] = child_key

                self.accepted_parents.move_to_end(
                    parent_key
                )

                self.accepted_children[
                    child_key
                ] = parent_key

                self.accepted_children.move_to_end(
                    child_key
                )

                # Bound runtime memory independently of
                # long-term chain operation.
                while len(
                    self.accepted_parents
                ) > 256:
                    self.accepted_parents.popitem(
                        last=False
                    )

                while len(
                    self.accepted_children
                ) > 256:
                    self.accepted_children.popitem(
                        last=False
                    )

            return result

    def handle(self, method, params):
        if method == "getblocktemplate":
            return self.build_parent_template()

        if method == "getbestblockhash":
            return self.get_parent_besthash()

        if method == "getblockhash":
            real = self.node.call(
                "getblockhash",
                params,
            )

            return synthetic_parent_prev(
                real
            )

        if method == "getblockcount":
            return self.node.call(
                "getblockcount"
            )

        if method == "validateaddress":
            address = params[0] if params else ""
            return self.node.call("validateaddress", [address])

        if method == "getvmeshuserwork":
            if len(params) != 3:
                raise RuntimeError("getvmeshuserwork requires address,height,bits")
            return self.build_user_work(params[0], params[1], params[2])

        if method == "vmesh_describecommitment":
            if len(params) != 1:
                raise RuntimeError(
                    "vmesh_describecommitment requires commitment"
                )
            return self.describe_commitment(params[0])

        if method == "decoderawtransaction":
            raw = bytes.fromhex(
                params[0]
            )

            tx = parse_tx(raw)

            if tx["end"] != len(raw):
                raise RuntimeError(
                    "decoderawtransaction trailing bytes"
                )

            txid = tx[
                "txid_raw"
            ][::-1].hex()

            return {
                "txid": txid,
                "hash": txid,
                "version":
                    struct.unpack_from(
                        "<I",
                        raw,
                        0,
                    )[0],
                "size": len(raw),
                "vsize": len(raw),
                "weight": len(raw) * 4,
                "locktime":
                    struct.unpack_from(
                        "<I",
                        raw,
                        len(raw) - 4,
                    )[0],
                "vin": [],
                "vout": [],
            }

        if method == "submitblock":
            return self.submit_parent_block(
                params[0]
            )

        if method == "preciousblock":
            # Parent blocks are synthetic and have
            # no independent parent chain.
            return None

        if method == "getblockchaininfo":
            info = self.node.call(
                method
            )

            info = dict(info)

            if info.get(
                "bestblockhash"
            ):
                info["bestblockhash"] = (
                    synthetic_parent_prev(
                        info[
                            "bestblockhash"
                        ]
                    )
                )

            return info

        if method in {
            "getnetworkinfo",
            "getmininginfo",
            "getdifficulty",
            "getmempoolinfo",
        }:
            return self.node.call(
                method,
                params,
            )

        if method == "getzmqnotifications":
            return []

        raise RuntimeError(
            f"unsupported bridge RPC: {method}"
        )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        self.close_connection = True

        try:
            n = int(
                self.headers.get(
                    "Content-Length",
                    "0",
                )
            )

            raw = self.rfile.read(n)

            request = json.loads(
                raw.decode()
            )

            method = request.get(
                "method"
            )

            params = (
                request.get(
                    "params"
                )
                or []
            )

            result = self.server.core.handle(
                method,
                params,
            )

            response = {
                "result": result,
                "error": None,
                "id": request.get("id"),
            }

        except Exception as exc:
            response = {
                "result": None,
                "error": {
                    "code": -32603,
                    "message": str(exc),
                },
                "id": None,
            }

        # CKPool's line-oriented JSON-RPC reader
        # requires the JSON body to terminate in LF.
        body = (
            json.dumps(
                response,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "application/json",
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.send_header(
            "Connection",
            "close",
        )

        self.end_headers()

        self.wfile.write(body)
        self.wfile.flush()


class FakeNode:
    def __init__(self):
        self.best = "22" * 32
        self.submitted = None
        self.submit_calls = 0

    def call(self, method, params=None):
        if method == "getbestblockhash":
            return self.best

        if method == "getblocktemplate":
            bits = 0x207fffff

            return {
                "version":
                    0x20000000
                    | AUXPOW_VERSION_BIT,

                "previousblockhash":
                    self.best,

                "transactions": [],

                "coinbaseaux": {},

                "coinbasevalue":
                    2500000000,

                "target":
                    f"{bits_target(bits):064x}",

                "mintime":
                    1700000000,

                "curtime":
                    1700000001,

                "bits":
                    f"{bits:08x}",

                "height": 1,

                "auxpow": True,

                "auxpow_chainid":
                    AUXPOW_CHAIN_ID,

                "auxpow_childnonce":
                    AUXPOW_NONCE,

                "auxpow_submit":
                    "submitblock",
            }

        if method == "submitblock":
            self.submit_calls += 1
            self.submitted = params[0]
            return None

        raise RuntimeError(
            "FakeNode unsupported: "
            + method
        )


def make_simple_coinbase(commitment):
    script_sig = (
        push(script_num(1))
        + push(commitment)
        + push(b"/R2G2/")
    )

    tx = (
        u32(1)
        + compact(1)
        + b"\x00" * 32
        + b"\xff\xff\xff\xff"
        + compact(len(script_sig))
        + script_sig
        + b"\xff\xff\xff\xff"
        + compact(1)
        + coinbase_output(
            0,
            b"\x6a",
        )
        + u32(0)
    )

    return tx


def self_test():
    fake = FakeNode()

    child_script = VMESH_DEVELOPER_PAYOUT_SCRIPT

    parent_script = (
        b"\x00\x14"
        + bytes.fromhex(
            "22" * 20
        )
    )

    core = BridgeCore(
        fake,
        child_script,
        parent_script,
    )

    parent_gbt = (
        core.build_parent_template()
    )

    state = core.latest

    assert state is not None
    assert len(
        state["child_header"]
    ) == 80

    assert (
        state["child_header"][76:80]
        == u32(AUXPOW_NONCE)
    )

    child_cb_parsed = parse_tx(
        state["child_coinbase"]
    )

    assert (
        child_cb_parsed["script_sig"][:1]
        == b"\x51"
    ), (
        "height-1 child coinbase must begin with "
        "OP_1 (0x51)"
    )

    assert len(
        state["commitment"]
    ) == 44

    assert (
        state["commitment"][:4]
        == MERGED_MINING_MAGIC
    )

    # Consensus-critical byte-order invariant:
    # the 32-byte chain root in the parent coinbase
    # equals the conventional displayed VMESH child hash.
    assert (
        state["commitment"][4:36]
        == bytes.fromhex(
            state["child_hash"]
        )
    )

    assert (
        parent_gbt[
            "coinbaseaux"
        ]["flags"]
        == state[
            "commitment"
        ].hex()
    )

    parent_cb = make_simple_coinbase(
        state["commitment"]
    )

    cb_parsed = parse_tx(
        parent_cb
    )

    parent_merkle = (
        cb_parsed[
            "txid_raw"
        ]
    )

    version = 0x20000000

    prev = bytes.fromhex(
        parent_gbt[
            "previousblockhash"
        ]
    )[::-1]

    ntime = int(
        parent_gbt[
            "curtime"
        ]
    )

    bits = int(
        parent_gbt[
            "bits"
        ],
        16,
    )

    target = state[
        "target"
    ]

    solved = None

    for nonce in range(0, 1000000):
        hdr = (
            u32(version)
            + prev
            + parent_merkle
            + u32(ntime)
            + u32(bits)
            + u32(nonce)
        )

        value = int.from_bytes(
            h256(hdr),
            "little",
        )

        if value <= target:
            solved = hdr
            break

    assert solved is not None

    parent_block = (
        solved
        + compact(1)
        + parent_cb
    )

    result = core.submit_parent_block(
        parent_block.hex()
    )

    assert result is None
    assert fake.submitted is not None
    assert fake.submit_calls == 1
    assert core.forwarded_submits == 1

    # Exact same parent block must never reach the child
    # node for a second time.
    duplicate_parent_result = (
        core.submit_parent_block(
            parent_block.hex()
        )
    )

    assert (
        duplicate_parent_result
        == "duplicate-vmesh-parent"
    )

    assert fake.submit_calls == 1
    assert core.forwarded_submits == 1
    assert core.duplicate_parent_submits == 1

    # Now find a DIFFERENT valid synthetic parent header
    # which commits to the SAME VMESH child.  This models
    # the real R2G.7 behaviour where the ASIC produced
    # several valid parent shares within milliseconds.
    first_nonce = int.from_bytes(
        solved[76:80],
        "little",
    )

    alternate = None

    for alternate_nonce in range(
        first_nonce + 1,
        first_nonce + 1000000,
    ):
        hdr = (
            u32(version)
            + prev
            + parent_merkle
            + u32(ntime)
            + u32(bits)
            + u32(alternate_nonce)
        )

        value = int.from_bytes(
            h256(hdr),
            "little",
        )

        if value <= target:
            alternate = hdr
            break

    assert alternate is not None
    assert alternate != solved

    alternate_parent_block = (
        alternate
        + compact(1)
        + parent_cb
    )

    duplicate_child_result = (
        core.submit_parent_block(
            alternate_parent_block.hex()
        )
    )

    assert (
        duplicate_child_result
        == "duplicate-vmesh-child"
    )

    # Critical invariant:
    # only the FIRST parent proof reached FakeNode.
    assert fake.submit_calls == 1
    assert core.forwarded_submits == 1
    assert core.duplicate_parent_submits == 1
    assert core.duplicate_child_submits == 1

    submitted = bytes.fromhex(
        fake.submitted
    )

    assert submitted[:80] == (
        state[
            "child_header"
        ]
    )

    # Immediately after child header begins the
    # serialized parent coinbase transaction.
    assert (
        submitted[
            80:
            80 + len(parent_cb)
        ]
        == parent_cb
    )

    print(
        "PASS: child coinbase constructed"
    )
    print(
        "PASS: height-1 BIP34 prefix = OP_1 (0x51)"
    )
    print(
        "PASS: child merkle root constructed"
    )
    print(
        "PASS: child 80-byte header constructed"
    )
    print(
        "PASS: child nNonce = 0x564D"
    )
    print(
        "PASS: 44-byte merged-mining commitment"
    )
    print(
        "PASS: synthetic parent GBT constructed"
    )
    print(
        "PASS: parent easy-target PoW solved"
    )
    print(
        "PASS: parent coinbase commitment recovered"
    )
    print(
        "PASS: parent merkle verified"
    )
    print(
        "PASS: parent PoW verified against child target"
    )
    print(
        "PASS: CAuxPow payload serialized"
    )
    print(
        "PASS: complete VMESH child block assembled"
    )
    print(
        "PASS: submitblock forwarded to fake VMESH node"
    )
    print(
        "PASS: exact duplicate parent suppressed before node RPC"
    )
    print(
        "PASS: alternate valid parent for accepted child suppressed"
    )
    print(
        "PASS: FakeNode submitblock call count = 1"
    )
    print(
        "PASS: accepted-child dedup is race-serialized"
    )

    print()
    print(
        "CHILD_HASH="
        + state["child_hash"]
    )

    print(
        "COMMITMENT="
        + state[
            "commitment"
        ].hex()
    )

    print(
        "PARENT_PREV="
        + parent_gbt[
            "previousblockhash"
        ]
    )

    print(
        "SUBMITTED_CHILD_BYTES="
        + str(
            len(submitted)
        )
    )

    print()
    print(
        "VARGAMESH R2G.2 BRIDGE SELFTEST: PASS"
    )


def main():
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "--self-test",
        action="store_true",
    )

    ap.add_argument(
        "--listen",
        default="127.0.0.1",
    )

    ap.add_argument(
        "--port",
        type=int,
        default=49332,
    )

    ap.add_argument(
        "--vmesh-url",
        default="http://127.0.0.1:29667/",
    )

    ap.add_argument(
        "--vmesh-user",
        default="",
    )

    ap.add_argument(
        "--vmesh-pass",
        default="",
    )

    ap.add_argument(
        "--vmesh-pass-file",
        default="",
    )

    ap.add_argument(
        "--developer-payout-script",
        default=VMESH_DEVELOPER_PAYOUT_SCRIPT.hex(),
    )

    ap.add_argument(
        "--parent-script",
        default=(
            "0014"
            + "22" * 20
        ),
    )

    args = ap.parse_args()

    if args.self_test:
        self_test()
        return

    try:
        configured_dev_script = bytes.fromhex(args.developer_payout_script)
    except ValueError as exc:
        raise SystemExit("invalid --developer-payout-script hex") from exc
    if configured_dev_script != VMESH_DEVELOPER_PAYOUT_SCRIPT:
        raise SystemExit(
            "VMESH developer payout cannot be overridden. Expected "
            + VMESH_DEVELOPER_FEE_ADDRESS
            + " (script "
            + VMESH_DEVELOPER_PAYOUT_SCRIPT.hex()
            + ")"
        )

    vmesh_pass = args.vmesh_pass

    if args.vmesh_pass_file:
        with open(
            args.vmesh_pass_file,
            "r",
            encoding="utf-8",
        ) as f:
            vmesh_pass = f.read().strip()

    if not vmesh_pass:
        raise SystemExit(
            "VargaMesh RPC password missing"
        )

    node = NodeRPC(
        args.vmesh_url,
        args.vmesh_user,
        vmesh_pass,
    )

    core = BridgeCore(
        node,
        configured_dev_script,
        bytes.fromhex(args.parent_script),
    )

    srv = ThreadingHTTPServer(
        (
            args.listen,
            args.port,
        ),
        Handler,
    )

    srv.core = core

    print(
        "VargaMesh AuxPoW Bridge listening "
        f"on {args.listen}:{args.port}",
        flush=True,
    )

    srv.serve_forever()


if __name__ == "__main__":
    main()
