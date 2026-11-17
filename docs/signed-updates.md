# Signed releases

Updates use Ed25519 signatures, SHA-256 archive verification and a monotonically increasing source timestamp. The app trusts only the public key embedded at build time. An unsigned manifest, changed digest, wrong key or rollback is rejected before extraction. Archives are immutable; only the signed pointer is replaced.

## One-time owner setup

Automatic publishing stays disabled until a repository variable named `DUBCANVAS_UPDATE_PUBLIC_KEY` is configured. Add the matching `DUBCANVAS_UPDATE_PRIVATE_KEY` secret to the **release** GitHub environment. Configure that environment to allow only the protected main branch, with an independent required reviewer for changes to release infrastructure. Restrict changes to workflows and signing settings. Someone who controls the signer or can change a trusted build can still compromise updates.

Generate the pair on a trusted machine with Python and `cryptography` installed:

```bash
umask 077
python - <<'PY'
import base64
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
key = Ed25519PrivateKey.generate()
Path('dubcanvas-update-private.txt').write_bytes(base64.b64encode(key.private_bytes_raw()))
Path('dubcanvas-update-public.txt').write_bytes(base64.b64encode(key.public_key().public_bytes_raw()))
PY
gh variable set DUBCANVAS_UPDATE_PUBLIC_KEY --repo wassayhassan/DubCanvas < dubcanvas-update-public.txt
gh secret set DUBCANVAS_UPDATE_PRIVATE_KEY --env release --repo wassayhassan/DubCanvas < dubcanvas-update-private.txt
```

Store an encrypted offline backup of the private key, remove the plaintext working copy, and never commit or paste it into chat. Rebuild main after configuration so its package embeds the public key. Existing builds without this trust anchor need one manual installation from a trusted release; they cannot securely bootstrap a new key through their old unsigned channel. Key rotation likewise requires a trusted manual build in this version.

Packaging runs with read-only repository permissions. A separate `workflow_run` gate verifies all four workflows for the same main commit, then downloads that commit's packaged artifact. Only the publishing job can access the release environment and write release assets. Incomplete checks, failed checks, a mismatched artifact/key, and superseded commits do not publish. There is no polling on a macOS runner.

## Apple signing

The manifest signature authenticates the release channel; it does not replace Apple distribution signing. Local release packaging supports `DUBCANVAS_SIGNING_IDENTITY` (Developer ID) and `DUBCANVAS_NOTARY_PROFILE` (an existing notarytool keychain profile). Those credentials are not included or provisioned by this change. Without them, packaging remains ad-hoc signed. Managed runtimes stay outside the app, preserving its distribution signature; migrating a legacy in-bundle runtime still requires ad-hoc re-signing.
