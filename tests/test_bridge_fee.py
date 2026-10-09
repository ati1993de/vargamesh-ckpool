#!/usr/bin/env python3
"""Offline tests for the official VMESH 1% child coinbase payout."""
import importlib.util
import json
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = ROOT / "bridge" / "vargamesh-auxpow-bridge.py"
spec = importlib.util.spec_from_file_location("vmesh_bridge", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

ADDRESS = "vm1qsfsgj70pdv86nguvexzvsflmcknz4lrsj87wx9"
SCRIPT = bytes.fromhex("001482608979e16b0fa9a38cc984c827fbc5a62afc70")
MINER_SCRIPT = bytes.fromhex("0014" + "11" * 20)
PARENT_SCRIPT = bytes.fromhex("0014" + "22" * 20)


def decode_coinbase_outputs(raw):
    """Decode outputs from a serialized non-witness transaction."""
    pos = 4
    input_count, pos = bridge.read_compact(raw, pos)
    for _ in range(input_count):
        pos += 32 + 4
        size, pos = bridge.read_compact(raw, pos)
        pos += size + 4
    output_count, pos = bridge.read_compact(raw, pos)
    outputs = []
    for _ in range(output_count):
        value = struct.unpack_from("<Q", raw, pos)[0]
        pos += 8
        size, pos = bridge.read_compact(raw, pos)
        script = raw[pos:pos + size]
        pos += size
        outputs.append((value, script))
    return outputs


class TestFixedFee(unittest.TestCase):
    def gbt(self, height=1, tx_fees=0, subsidy=None):
        template = bridge.FakeNode().call("getblocktemplate")
        template["height"] = height
        if subsidy is None:
            halvings = height // bridge.VMESH_SUBSIDY_HALVING_INTERVAL
            subsidy = bridge.VMESH_INITIAL_SUBSIDY_SATS >> halvings
        template["coinbasevalue"] = subsidy + tx_fees
        return template

    def test_recipient_is_fixed(self):
        self.assertEqual(bridge.VMESH_DEVELOPER_FEE_ADDRESS, ADDRESS)
        self.assertEqual(bridge.VMESH_DEVELOPER_PAYOUT_SCRIPT, SCRIPT)
        self.assertEqual(bridge.VMESH_DEVELOPER_FEE_PERCENT, 1)
        with self.assertRaisesRegex(ValueError, "must match"):
            bridge.BridgeCore(bridge.FakeNode(), MINER_SCRIPT, PARENT_SCRIPT)
        core = bridge.BridgeCore(bridge.FakeNode(), SCRIPT, PARENT_SCRIPT)
        self.assertEqual(core.developer_payout_script, SCRIPT)

    def test_25_vmesh_block(self):
        self.assertEqual(bridge.vmesh_reward_split(self.gbt()),
                         (2_475_000_000, 25_000_000))

    def test_transaction_fees_are_not_charged(self):
        self.assertEqual(bridge.vmesh_reward_split(self.gbt(tx_fees=123_456)),
                         (2_475_123_456, 25_000_000))

    def test_halving_and_rounding(self):
        h = bridge.VMESH_SUBSIDY_HALVING_INTERVAL
        self.assertEqual(bridge.vmesh_reward_split(self.gbt(height=h)),
                         (1_237_500_000, 12_500_000))
        self.assertEqual(bridge.vmesh_reward_split(self.gbt(height=h * 2)),
                         (618_750_000, 6_250_000))
        self.assertEqual(bridge.vmesh_reward_split(self.gbt(height=h * 64, subsidy=0)),
                         (0, 0))

    def test_invalid_template_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "below expected"):
            bridge.vmesh_reward_split(self.gbt(subsidy=0))
        with self.assertRaisesRegex(ValueError, "invalid VMESH"):
            bridge.vmesh_reward_split({"height": -1, "coinbasevalue": 0})

    def test_child_coinbase_outputs(self):
        gbt = self.gbt()
        base, full = bridge.build_child_coinbase(gbt, MINER_SCRIPT, SCRIPT)
        self.assertEqual(decode_coinbase_outputs(base), [
            (2_475_000_000, MINER_SCRIPT),
            (25_000_000, SCRIPT),
        ])
        self.assertEqual(decode_coinbase_outputs(full),
                         decode_coinbase_outputs(base))
        gbt = self.gbt(tx_fees=123_456)
        base, _ = bridge.build_child_coinbase(gbt, MINER_SCRIPT, SCRIPT)
        self.assertEqual(decode_coinbase_outputs(base), [
            (2_475_123_456, MINER_SCRIPT),
            (25_000_000, SCRIPT),
        ])

    def test_example_donation_is_zero(self):
        config = json.loads(
            (ROOT / "config" / "ckpool-vmesh.conf.example").read_text()
        )
        self.assertEqual(config["donation"], 0.0)
        env = (ROOT / "config" / "mining.env.example").read_text()
        self.assertIn(SCRIPT.hex(), env)


if __name__ == "__main__":
    unittest.main()
