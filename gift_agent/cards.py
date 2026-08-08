"""Reusable client for the Rain issuing API.

The one function that matters here is :func:`issue_scoped_card`, which issues a
single-use "scoped" card for a fixed dollar amount:

    POST /v1/issuing/users/{userId}/cards/scoped
        Api-Key: <API_KEY>
        sessionid: <ENCRYPTED_SESSION_ID>
        { "amountInUSDCents": 4299 }

The ``sessionid`` header must be an *encrypted* session id. A fresh one is
minted (and encrypted with the RSA public key in
``SESSION_ID_CRYPTO_PUBLIC_KEY``) on every call via
:func:`crypto.crypto.generate_session_id`.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from crypto.crypto import generate_session_id

# Endpoint template. Overridable for staging/prod; ``{user_id}`` is filled in.
_SCOPED_CARD_URL = os.environ.get(
    "RAIN_SCOPED_CARD_URL",
    "https://api-dev.raincards.xyz/v1/issuing/users/{user_id}/cards/scoped",
)


def issue_scoped_card(
    *,
    amount_in_usd_cents: int,
    user_id: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Issue a scoped card for ``amount_in_usd_cents`` and return the Rain response.

    ``user_id`` and ``api_key`` default to the ``USER_ID`` / ``API_KEY``
    environment variables. A fresh (encrypted) session id is minted internally
    via :func:`crypto.crypto.generate_session_id` for every request.

    Raises ``ValueError`` for missing configuration and ``httpx.HTTPStatusError``
    if Rain returns a non-2xx status.
    """
    api_key = api_key or os.environ.get("API_KEY")
    user_id = user_id or os.environ.get("USER_ID")
    if not api_key:
        raise ValueError("API_KEY is not set.")
    if not user_id:
        raise ValueError("USER_ID is not set.")

    encrypted_session_id = generate_session_id()["sessionId"]

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
