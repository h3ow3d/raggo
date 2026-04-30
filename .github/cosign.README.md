# Cosign signing key

raggo release artifacts (container images and the Helm chart) are
signed with [cosign](https://docs.sigstore.dev/cosign/overview/) using
a long-lived key pair, as specified in `PROJECT_SPEC.md` §21.3.

The **public key** lives at `.github/cosign.pub` and is checked into
this repository so that anyone can verify release artifacts without
trusting GitHub OIDC infrastructure or going through keyless mode.

The **private key** lives only in repository secrets:

| Secret                | Purpose                              |
| --------------------- | ------------------------------------ |
| `COSIGN_PRIVATE_KEY`  | PEM-encoded encrypted private key.   |
| `COSIGN_PASSWORD`     | Passphrase used to decrypt the key.  |

## One-time setup (maintainer)

The committed `.github/cosign.pub` ships as a placeholder. A maintainer
with `cosign` installed must replace it once and only once:

```bash
cosign generate-key-pair
# produces cosign.key (encrypted private) and cosign.pub (public)
```

1. Copy the entire contents of the generated `cosign.pub` into
   `.github/cosign.pub`, replacing the placeholder. Preserve the
   `-----BEGIN PUBLIC KEY-----` / `-----END PUBLIC KEY-----` markers.
2. Add the contents of `cosign.key` to the repository secret
   `COSIGN_PRIVATE_KEY`.
3. Add the passphrase you supplied to `cosign generate-key-pair` to
   `COSIGN_PASSWORD`.
4. Securely destroy the local copy of `cosign.key`.

Once the public key is committed, signed builds will succeed when
`build-images.yml` is invoked with `sign: true`. Until then, signed
runs will fail fast with a clear error so unsigned releases are not
silently published. Unsigned PR builds (`pr-images.yml`, which calls
`build-images.yml` with `sign: false`) are unaffected.

## Verification (consumers)

End users verify a signed image against the committed public key:

```bash
cosign verify \
  --key https://raw.githubusercontent.com/h3ow3d/raggo/main/.github/cosign.pub \
  ghcr.io/h3ow3d/raggo/backend@sha256:<digest>
```

See `docs/images.md` for the full image inventory, tagging scheme, and
verification workflow.
