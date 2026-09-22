# VargaMesh CKPool

<p align="center">
  <strong>SHA256 Stratum mining server and AuxPoW bridge for VargaMesh (VMESH).</strong>
</p>

<p align="center">
  CKPool · SHA256 · Stratum · AuxPoW · VargaMesh Core
</p>

---

## Overview

**VargaMesh CKPool** is the reference public mining-stack implementation used
to connect SHA256 miners to the **VargaMesh (VMESH)** network.

The stack combines a VargaMesh-modified CKPool-LHR source tree with the
VargaMesh AuxPoW bridge.

The bridge provides the compatibility layer between CKPool mining work and
VargaMesh Core's VMESH-specific mining RPC functionality.

| Project | Link |
| --- | --- |
| VargaMesh Website | https://mesh.vargatech.net |
| VargaMesh Core | https://github.com/ati1993de/vargamesh-core |
| VargaMesh Desktop | https://github.com/ati1993de/vargamesh-desktop |
| VargaMesh Explorer | https://mempool.vargacoin.com |

---

## Architecture

```text
SHA256 Miner
     |
     | Stratum TCP
     | :3933
     v
VargaMesh CKPool
     |
     | local bridge RPC
     | :3932
     v
VargaMesh AuxPoW Bridge
     |
     | localhost JSON-RPC
     | :29667
     v
VargaMesh Core
     |
     | P2P :29666
     v
VargaMesh Mainnet
```

---

## Components

This repository contains:

- CKPool-LHR based SHA256 Stratum source
- VargaMesh-specific mining modifications
- VMESH per-user mining-work support
- VMESH commitment handling
- VargaMesh AuxPoW bridge
- example CKPool configuration
- example bridge configuration
- hardened systemd unit examples
- upstream licensing and attribution

---

## Default Example Ports

| Component | Address |
| --- | --- |
| Miner Stratum | `0.0.0.0:3933` |
| CKPool → AuxPoW Bridge | `127.0.0.1:3932` |
| VargaMesh Core RPC | `127.0.0.1:29667` |
| VargaMesh Core P2P | `29666/TCP` |

The VargaMesh Core RPC interface should normally remain bound to localhost.

Do not expose Core RPC directly to the public Internet.

---

## Mining

A SHA256 miner normally connects to:

```text
stratum+tcp://YOUR_POOL_HOST:3933
```

The mining username is normally a valid VMESH payout address.

A worker suffix may be appended.

Example:

```text
vm1q...example.worker1
```

Miner password handling depends on the mining software and pool
configuration.

---

## Configuration

Example files are located under:

```text
config/
```

Available examples:

```text
config/ckpool-vmesh.conf.example
config/mining.env.example
config/bridge-rpc.secret.example
```

Never place production credentials into the repository.

---

## CKPool Configuration

Example:

```json
{
  "btcd": [
    {
      "url": "127.0.0.1:3932",
      "auth": "CHANGE_ME",
      "pass": "CHANGE_ME",
      "notify": true
    }
  ],
  "btcaddress": "YOUR_VMESH_MINING_ADDRESS",
  "btcsig": "/VMESH/",
  "serverurl": [
    "0.0.0.0:3933"
  ],
  "mindiff": 1024,
  "startdiff": 1024,
  "highdiff": 1024,
  "maxdiff": 0,
  "allow_low_diff": false,
  "blockpoll": 100,
  "update_interval": 10,
  "nonce1length": 4,
  "nonce2length": 8,
  "version_mask": "1fffe000",
  "logdir": "/var/log/vargamesh-ckpool",
  "donation": 0.0
}
```

The example values are not production credentials.

---

## VargaMesh AuxPoW Bridge

The bridge sits between CKPool and VargaMesh Core.

It provides the VMESH-specific work flow required by the VargaMesh mining
stack.

The implementation includes support for VMESH-specific mining work and
commitment handling.

The bridge should normally be reachable only locally by CKPool.

---

## Build

The CKPool source remains compatible with the build process inherited from
CKPool-LHR.

Preserved upstream documentation:

- `UPSTREAM-README.md`
- `README-INSTALL-SCRIPT.md`

Typical Linux build requirements include:

- GCC / build-essential
- autoconf
- automake
- libtool
- pkg-config
- development libraries required by CKPool-LHR

Follow the preserved upstream documentation for the exact build procedure.

---

## systemd

Example units are provided under:

```text
systemd/
```

They are examples and should be reviewed before deployment.

The production operator remains responsible for:

- service user
- filesystem permissions
- configuration paths
- firewall rules
- RPC security
- log rotation
- monitoring

---

## Security

Never publish:

- RPC credentials
- RPC cookies
- bridge secrets
- wallet files
- wallet passwords
- WIF private keys
- seed material
- SSH private keys
- TLS private keys
- API credentials
- miner IP logs
- production `.env` files

See:

`SECURITY.md`

---

## Upstream

VargaMesh CKPool is based on:

https://github.com/Z3r0XG/ckpool-lhr

The VargaMesh development line was based on upstream commit:

```text
b51d54b448d50b3ec3399d48c647ef30b0f9cb2f
```

CKPool-LHR itself is derived from CKPool by Con Kolivas.

See:

`UPSTREAM.md`

---

## License

The CKPool-derived source remains distributed under the
**GNU General Public License version 3**.

See:

`COPYING`

Bundled third-party components retain their respective licenses.

---

## Status

Current public project status:

**v0.1.0 / testing**

This repository is intended to make the VargaMesh mining stack reproducible
and auditable.

Operators should test configuration and mining behavior before using it in
production.

---

## Important Notice

Mining software interacts directly with consensus-critical infrastructure.

Always verify:

- VargaMesh Core version
- mining configuration
- payout address
- bridge configuration
- local RPC security
- firewall configuration

VargaMesh consensus behavior is defined by VargaMesh Core.
