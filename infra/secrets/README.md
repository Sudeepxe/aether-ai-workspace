# Secrets (ADR-7.5: SOPS + age)

- Encrypted bundles (`*.enc.yaml`) are committed; decrypted output never is
  (`.gitignore` blocks `*.dec.*`).
- Bootstrap: generate an age key (`age-keygen`), register its public key in
  `.sops.yaml`, then `make secrets-edit`.
- Sprint 0 ships the machinery only; the first real secret (JWT signing key)
  lands in Sprint 1. Dev-profile infra uses non-secret defaults from
  `.env.example` by design.
- Prod deploy renders secrets to a root-owned tmpfs, never to disk
  (Blueprint Ch. 10 F-2).
