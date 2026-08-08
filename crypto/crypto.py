import base64
import binascii
import re
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
import os
from dotenv import load_dotenv

load_dotenv()
pem = os.environ["SESSION_ID_CRYPTO_PUBLIC_KEY"]


def generate_session_id(secret: str | None = None) -> dict:
    if not pem:
        raise ValueError("pem is required")

    if secret and not re.fullmatch(r"[0-9A-Fa-f]+", secret):
        raise ValueError("secret must be a hex string")

    secret_key = secret if secret is not None else uuid.uuid4().hex

    # hex string -> raw bytes -> base64 string (same as the hbytes/btoa dance)
    secret_key_bytes = binascii.unhexlify(secret_key)
    secret_key_base64 = base64.b64encode(secret_key_bytes).decode("ascii")

    # pem may be a full "-----BEGIN PUBLIC KEY-----...-----END PUBLIC KEY-----"
    # string (SPKI/PEM), which load_pem_public_key handles directly —
    # no need to manually strip headers/footers like the JS does.
    public_key = serialization.load_pem_public_key(pem.encode("utf-8"))

    encrypted = public_key.encrypt(
        secret_key_base64.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA1()),
            algorithm=hashes.SHA1(),
            label=None,
        ),
    )

    return {
        "secretKey": secret_key,
        "sessionId": base64.b64encode(encrypted).decode("ascii"),
    }
