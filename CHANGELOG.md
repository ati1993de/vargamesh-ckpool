# Changelog

## v0.1.1 - pinned VMESH development fee

- Lock the official AuxPoW bridge developer payout to
  `vm1qsfsgj70pdv86nguvexzvsflmcknz4lrsj87wx9` (1% of mainnet subsidy).
- Miner receives 99% of VMESH block subsidy plus all normal transaction fees.
- Follow mainnet subsidy halvings using integer arithmetic.
- Reject a mismatching legacy payout script at startup, rather than
  inadvertently redirecting the development fee.
- Add unit tests, AuxPoW self-test CI, and deployment documentation.
- Existing deployments must update their payout script before restarting;
  no services are automatically restarted by a repository commit.
## v0.1.0

Initial public VargaMesh CKPool source release.

- VargaMesh SHA256 Stratum integration
- VargaMesh per-user mining work support
- VargaMesh AuxPoW bridge
- public example configuration
- hardened systemd examples
- upstream licensing and attribution retained
