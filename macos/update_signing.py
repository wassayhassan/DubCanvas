"""Ed25519 release signatures. Private keys never belong in the repository."""

import base64
import re


def signing_bytes(manifest):
    if (
        manifest.get("schema") != 2
        or not re.fullmatch("[a-f0-9]{40}", manifest.get("revision", ""))
        or not re.fullmatch("[a-f0-9]{64}", manifest.get("sha256", ""))
        or not re.fullmatch("[A-Za-z0-9.+_-]{1,64}", manifest.get("version", ""))
        or type(manifest.get("sourceTimestamp")) is not int
        or manifest["sourceTimestamp"] <= 0
    ):
        raise ValueError("Invalid update manifest")
    return (
        "DubCanvas update v2\n"
        + manifest["revision"]
        + "\n"
        + str(manifest["sourceTimestamp"])
        + "\n"
        + manifest["version"]
        + "\n"
        + manifest["sha256"]
        + "\n"
    ).encode("ascii")


def sign(manifest, private_key, expected_public_key):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(private_key, validate=True)
    )
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    if public != base64.b64decode(expected_public_key, validate=True):
        raise ValueError("Release signing key does not match the embedded public key")
    return dict(
        manifest, signature=base64.b64encode(key.sign(signing_bytes(manifest))).decode()
    )


def verify(manifest, public_key, minimum_timestamp=0):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    body = signing_bytes(manifest)
    if manifest["sourceTimestamp"] < minimum_timestamp:
        raise ValueError("Update rollback rejected")
    Ed25519PublicKey.from_public_bytes(
        base64.b64decode(public_key, validate=True)
    ).verify(base64.b64decode(manifest["signature"], validate=True), body)
