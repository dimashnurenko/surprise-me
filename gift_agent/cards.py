"""Reusable client for the Rain issuing API.

The one function that matters here is :func:`issue_scoped_card`, which issues a
single-use "scoped" card for a fixed dollar amount:

    POST /v1/issuing/users/{userId}/cards/scoped
        Api-Key: <API_KEY>
        sessionid: <ENCRYPTED_SESSION_ID>
        { "amountInUSDCents": 4299, "allowedMccs": ["5411", "5812"] }

The ``sessionid`` header must be an *encrypted* session id. A fresh one is
minted (and encrypted with the RSA public key in
``SESSION_ID_CRYPTO_PUBLIC_KEY``) on every call via
:func:`crypto.crypto.generate_session_id`.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from crypto.crypto import generate_session_id

logger = logging.getLogger("gift_agent")

# Endpoint template. Overridable for staging/prod; ``{user_id}`` is filled in.
_SCOPED_CARD_URL = os.environ.get(
    "RAIN_SCOPED_CARD_URL",
    "https://api-dev.raincards.xyz/v1/issuing/users/{user_id}/cards/scoped",
)

# The Rain "simulate transaction authorize" endpoint. Overridable for staging/prod.
_AUTHORIZE_TRANSACTION_URL = os.environ.get(
    "RAIN_AUTHORIZE_TRANSACTION_URL",
    "https://api-dev.raincards.xyz/v1/simulate/transactions/authorize",
)

# The Rain "simulate transaction settle" endpoint. ``{transaction_id}`` is filled in.
_SETTLE_TRANSACTION_URL = os.environ.get(
    "RAIN_SETTLE_TRANSACTION_URL",
    "https://api-dev.raincards.xyz/v1/simulate/transactions/{transaction_id}/settle",
)


def issue_scoped_card(
    *,
    amount_in_usd_cents: int,
    allowed_mccs: Optional[list[str]] = None,
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

    # Expire the card one hour from now (e.g. "2026-09-01T00:00:00Z").
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    payload: dict[str, Any] = {
        "amountInUSDCents": amount_in_usd_cents,
        "expiresAt": expires_at,
    }
    # Optionally restrict the card to specific merchant category codes,
    # e.g. "allowedMccs": ["5411", "5812"].
    if allowed_mccs:
        payload["allowedMccs"] = allowed_mccs

    logger.info(
        "[rain] POST scoped card (user=%s, amount=%d cents, mccs=%s)",
        user_id,
        amount_in_usd_cents,
        allowed_mccs or "<any>",
    )
    response = httpx.post(
        _SCOPED_CARD_URL.format(user_id=user_id),
        headers={
            "Api-Key": api_key,
            "sessionid": encrypted_session_id,
            "content-type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    logger.info("[rain] scoped card response: %d", response.status_code)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def authorize_transaction(
    *,
    card_id: str,
    amount: int,
    merchant_name: str,
    merchant_category_code: str,
    currency: str = "USD",
    decline_reason: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Simulate a card authorization via the Rain API and return the response.

    The card must be active and belong to the tenant resolved from ``api_key``;
    cards in locked, canceled, or unactivated status cannot be authorized.

    ``api_key`` defaults to the ``API_KEY`` environment variable. Pass
    ``decline_reason`` to simulate a declined authorization.

    Raises ``ValueError`` for missing configuration or unsupported input, and
    ``httpx.HTTPStatusError`` if Rain returns a non-2xx status.
    """
    api_key = api_key or os.environ.get("API_KEY")
    if not api_key:
        raise ValueError("API_KEY is not set.")
    if currency != "USD":
        raise ValueError('currency must be "USD".')
    if amount <= 0:
        raise ValueError("amount must be a positive integer (USD cents).")

    payload: dict[str, Any] = {
        "cardId": card_id,
        "amount": amount,
        "currency": currency,
        "merchantName": merchant_name,
        "merchantCategoryCode": merchant_category_code,
    }
    # Optionally simulate a declined authorization instead of an approval.
    if decline_reason:
        payload["declineReason"] = decline_reason

    logger.info(
        "[rain] POST authorize (card=%s, amount=%d cents, mcc=%s, decline=%s)",
        card_id,
        amount,
        merchant_category_code,
        decline_reason or "<none>",
    )
    response = httpx.post(
        _AUTHORIZE_TRANSACTION_URL,
        headers={"Api-Key": api_key, "content-type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    logger.info("[rain] authorize response: %d", response.status_code)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def settle_transaction(
    *,
    transaction_id: str,
    amount: Optional[int] = None,
    api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Settle an authorized transaction via the Rain simulate API.

    ``api_key`` defaults to the ``API_KEY`` environment variable; the tenant
    scope is resolved from it. ``amount`` is the settlement amount in cents; if
    omitted, the original authorization amount is settled.

    Raises ``ValueError`` for missing configuration or unsupported input, and
    ``httpx.HTTPStatusError`` if Rain returns a non-2xx status.
    """
    api_key = api_key or os.environ.get("API_KEY")
    if not api_key:
        raise ValueError("API_KEY is not set.")
    if amount is not None and amount <= 0:
        raise ValueError("amount must be a positive integer (cents).")

    payload: dict[str, Any] = {}
    if amount is not None:
        payload["amount"] = amount

    logger.info(
        "[rain] POST settle (transaction=%s, amount=%s)",
        transaction_id,
        amount if amount is not None else "<original>",
    )
    response = httpx.post(
        _SETTLE_TRANSACTION_URL.format(transaction_id=transaction_id),
        headers={"Api-Key": api_key, "content-type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    logger.info("[rain] settle response: %d", response.status_code)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def _extract_id(response: dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    """Best-effort pull of an id out of a Rain response.

    Rain's API has varied the field name/nesting across versions, so we probe the
    given ``keys`` at the top level and nested under ``card``/``transaction``/
    ``data``/``result`` rather than hard-coding a single shape.
    """
    containers = [response]
    for nested_key in ("card", "transaction", "data", "result"):
        nested = response.get(nested_key)
        if isinstance(nested, dict):
            containers.append(nested)
    for container in containers:
        for key in keys:
            value = container.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def extract_card_id(response: dict[str, Any]) -> Optional[str]:
    """Pull the issued card id out of a scoped-card response."""
    return _extract_id(response, ("cardId", "card_id", "id"))


def extract_transaction_id(response: dict[str, Any]) -> Optional[str]:
    """Pull the transaction id out of an authorize response."""
    return _extract_id(response, ("transactionId", "transaction_id", "id"))
