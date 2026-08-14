# Secrets (ADR-7.5: SOPS + age)

- Encrypted bundles (`*.enc.yaml`) are committed; decrypted output never is
  (`.gitignore` blocks `*.dec.*`).
- Bootstrap: generate an age key (`age-keygen`), register its public key in
  `.sops.yaml`, then `make secrets-edit`.
- `dev.enc.yaml` in this directory is a **Sprint 0 mechanism demo**: it
  proves encrypt/decrypt round-trips correctly (`sops --encrypt`/`--decrypt`
  against the `age` recipient in `.sops.yaml`) and holds no real secret. The
  demo key that produced it is not committed anywhere and was discarded
  after verification — it cannot be used to decrypt anything sensitive
  because nothing sensitive was ever encrypted with it.
- **Before any real secret lands (Sprint 1):** each bootstrapping developer
  runs `age-keygen`, replaces the recipient in `.sops.yaml` with their own
  public key, and re-encrypts (`make secrets-edit`) so only that developer's
  private key can decrypt. Sprint 0 ships the machinery only; the first real
  secret (JWT signing key) lands in Sprint 1. Dev-profile infra uses
  non-secret defaults from `.env.example` by design.
- Prod deploy renders secrets to a root-owned tmpfs, never to disk
  (Blueprint Ch. 10 F-2).
