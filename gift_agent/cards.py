"""Reusable client for the Rain issuing API.

The one function that matters here is :func:`issue_scoped_card`, which issues a
single-use "scoped" card for a fixed dollar amount:

    POST /v1/issuing/users/{userId}/cards/scoped
        Api-Key: <API_KEY>
        sessionid: <ENCRYPTED_SESSION_ID>
        { "amountInUSDCents": 4299 }

The ``sessionid`` header must be an *encrypted* session id. Callers can either
pass a plaintext ``session_id`` (encrypted here with the RSA public key in
``SESSION_ID_CRYPTO_PUBLIC_KEY``) or a pre-encrypted ``encrypted_session_id``.
"""

from __future__ import annotations

import base64
import os
from typing import Any, Optional

import httpx
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

# Endpoint template. Overridable for staging/prod; ``{user_id}`` is filled in.
_SCOPED_CARD_URL = os.environ.get(
    "RAIN_SCOPED_CARD_URL",
    "https://api-dev.raincards.xyz/v1/issuing/users/{user_id}/cards/scoped",
)


def encrypt_session_id(session_id: str, public_key_pem: Optional[str] = None) -> str:
    """RSA-encrypt a plaintext session id into the ``sessionid`` header value.

    Uses ``SESSION_ID_CRYPTO_PUBLIC_KEY`` (a PEM public key) unless a key is
    passed explicitly, and returns the ciphertext base64-encoded.
    """
    pem = public_key_pem if public_key_pem is not None else os.environ.get(
        "SESSION_ID_CRYPTO_PUBLIC_KEY"
    )
    if not pem:
        raise ValueError("SESSION_ID_CRYPTO_PUBLIC_KEY is not set.")
    # Tolerate keys stored with escaped newlines (e.g. single-line .env values).
    pem = pem.replace("\\n", "\n")
    public_key = load_pem_public_key(pem.encode("utf-8"))
    ciphertext = public_key.encrypt(session_id.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(ciphertext).decode("ascii")


def issue_scoped_card(
    *,
    amount_in_usd_cents: int,
    session_id: Optional[str] = None,
    encrypted_session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Issue a scoped card for ``amount_in_usd_cents`` and return the Rain response.

    ``user_id`` and ``api_key`` default to the ``USER_ID`` / ``API_KEY``
    environment variables. Provide either ``session_id`` (encrypted here) or an
    already-encrypted ``encrypted_session_id``.

    Raises ``ValueError`` for missing configuration and ``httpx.HTTPStatusError``
    if Rain returns a non-2xx status.
    """
    api_key = api_key or os.environ.get("API_KEY")
    user_id = user_id or os.environ.get("USER_ID")
    if not api_key:
        raise ValueError("API_KEY is not set.")
    if not user_id:
        raise ValueError("USER_ID is not set.")

    if encrypted_session_id is None:
        if not session_id:
            raise ValueError("Provide either session_id or encrypted_session_id.")
        encrypted_session_id = encrypt_session_id(session_id)

    response = httpx.post(
        _SCOPED_CARD_URL.format(user_id=user_id),
        headers={
            "Api-Key": api_key,
            "sessionid": encrypted_session_id,
            "content-type": "application/json",
        },
        json={"amountInUSDCents": amount_in_usd_cents},
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}
