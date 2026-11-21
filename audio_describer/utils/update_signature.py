# audio_describer/utils/update_signature.py
"""Ed25519 signatures for self-updates (audit section 5.2).

The release workflow signs the SHA-256 hex digest of each downloadable asset
(omni_describer.zip, updater.exe) and publishes it next to it as <asset>.sig
(base64). The app refuses an update whose signature doesn't verify against
config.UPDATE_SIGNING_PUBLIC_KEY. While that key is empty, nothing is checked
beyond the GitHub SHA-256 digest (previous behaviour).
No wx import: used by the release CI and the tests.

Maintainer setup, once:
    python -m audio_describer.utils.update_signature generate update_signing_key.pem
  Keep the .pem private: paste its content into the GitHub secret
  UPDATE_SIGNING_KEY, and the printed public key into
  config.UPDATE_SIGNING_PUBLIC_KEY. From then on every release must be signed.

Release CI:
    python -m audio_describer.utils.update_signature sign omni_describer.zip dist/updater.exe
"""
import base64
import hashlib
import os
import sys


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_key_pair():
    """Returns (private key PEM bytes, public key base64 str)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return pem, public_key_b64(pem)


def public_key_b64(private_pem):
    from cryptography.hazmat.primitives import serialization
    private = serialization.load_pem_private_key(private_pem, password=None)
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def sign_digest(private_pem, sha256_hex):
    from cryptography.hazmat.primitives import serialization
    private = serialization.load_pem_private_key(private_pem, password=None)
    return base64.b64encode(private.sign(sha256_hex.lower().encode("ascii"))).decode("ascii")


def verify_digest(public_b64, sha256_hex, signature_b64):
    """Raises ValueError unless signature_b64 signs sha256_hex for this public key."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64, validate=True))
        key.verify(base64.b64decode(signature_b64.strip(), validate=True), sha256_hex.lower().encode("ascii"))
    except (InvalidSignature, ValueError) as e:
        raise ValueError(f"Invalid update signature: {e or type(e).__name__}") from e


def _main(argv):
    if len(argv) == 2 and argv[0] == "generate":
        if os.path.exists(argv[1]):
            sys.exit(f"{argv[1]} already exists; not overwriting a signing key.")
        pem, pub = generate_key_pair()
        with open(argv[1], "wb") as f:
            f.write(pem)
        print(f"Private key written to {argv[1]} (secret: GitHub secret UPDATE_SIGNING_KEY).")
        print(f'Public key for config.UPDATE_SIGNING_PUBLIC_KEY:\n{pub}')
        return
    if len(argv) >= 2 and argv[0] == "sign":
        from .. import config
        expected_pub = config.UPDATE_SIGNING_PUBLIC_KEY
        pem = os.environ.get("UPDATE_SIGNING_KEY", "").strip().encode("ascii")
        if not pem:
            if expected_pub:
                sys.exit("UPDATE_SIGNING_KEY is not set but the app requires signed updates; refusing to release unsigned.")
            print("No signing key configured; release left unsigned (app checks GitHub SHA-256 only).")
            return
        if public_key_b64(pem) != expected_pub:
            sys.exit("UPDATE_SIGNING_KEY does not match config.UPDATE_SIGNING_PUBLIC_KEY; the app would reject this release.")
        for path in argv[1:]:
            digest = file_sha256(path)
            signature = sign_digest(pem, digest)
            verify_digest(expected_pub, digest, signature)
            with open(path + ".sig", "w", encoding="ascii") as f:
                f.write(signature + "\n")
            print(f"Signed {path} (sha256 {digest})")
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    _main(sys.argv[1:])
