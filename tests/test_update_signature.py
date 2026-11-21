"""Update signature round trip (audit 5.2). Needs `cryptography`; skipped without it."""
import importlib.util
import unittest

from audio_describer.utils import update_signature as us

HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None
DIGEST = "ab" * 32


@unittest.skipUnless(HAS_CRYPTO, "cryptography not installed")
class UpdateSignatureTest(unittest.TestCase):
    def setUp(self):
        self.pem, self.pub = us.generate_key_pair()

    def test_valid_signature_passes(self):
        us.verify_digest(self.pub, DIGEST, us.sign_digest(self.pem, DIGEST))
        us.verify_digest(self.pub, DIGEST.upper(), us.sign_digest(self.pem, DIGEST) + "\n")

    def test_other_file_rejected(self):
        with self.assertRaises(ValueError):
            us.verify_digest(self.pub, "cd" * 32, us.sign_digest(self.pem, DIGEST))

    def test_other_key_rejected(self):
        other_pem, _ = us.generate_key_pair()
        with self.assertRaises(ValueError):
            us.verify_digest(self.pub, DIGEST, us.sign_digest(other_pem, DIGEST))

    def test_garbage_rejected(self):
        for bad_sig in ("", "not base64!", "AAAA"):
            with self.assertRaises(ValueError):
                us.verify_digest(self.pub, DIGEST, bad_sig)
        with self.assertRaises(ValueError):
            us.verify_digest("AAAA", DIGEST, us.sign_digest(self.pem, DIGEST))


if __name__ == "__main__":
    unittest.main()
