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

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from crypto.crypto import generate_session_id

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

    response = httpx.post(
        _AUTHORIZE_TRANSACTION_URL,
        headers={"Api-Key": api_key, "content-type": "application/json"},
        json=payload,
        timeout=timeout,
    )
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

    response = httpx.post(
        _SETTLE_TRANSACTION_URL.format(transaction_id=transaction_id),
        headers={"Api-Key": api_key, "content-type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def _extract_transaction_id(authorization: dict[str, Any]) -> Optional[str]:
    """Best-effort pull of the transaction id out of an authorize response.

    Rain's simulate API has varied the field name/nesting across versions, so we
    probe the common shapes (top-level ``id``/``transactionId``, and the same
    keys nested under ``transaction``/``data``) rather than hard-coding one.
    """
    candidates: list[Any] = [
        authorization.get("transactionId"),
        authorization.get("transaction_id"),
        authorization.get("id"),
    ]
    for nested_key in ("transaction", "data", "result"):
        nested = authorization.get(nested_key)
        if isinstance(nested, dict):
            candidates.extend(
                [nested.get("transactionId"), nested.get("transaction_id"), nested.get("id")]
            )
    for value in candidates:
        if isinstance(value, str) and value:
            return value
    return None


def purchase_transaction(
    *,
    card_id: str,
    amount: int,
    merchant_name: str,
    merchant_category_code: str,
    currency: str = "USD",
    settle_amount: Optional[int] = None,
    decline_reason: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Authorize and then settle a card transaction in one call.

    This is the end-to-end "make the purchase" operation: it first authorizes
    ``amount`` (USD cents) against ``card_id`` via :func:`authorize_transaction`,
    then settles the resulting transaction via :func:`settle_transaction`.
    ``settle_amount`` (cents) lets the caller settle for less than the
    authorization; when omitted the full authorized amount is settled.

    Returns a combined envelope::

        {
            "status": "settled" | "declined" | "authorized_not_settled",
            "transaction_id": "<id or null>",
            "authorization": { ...authorize response... },
            "settlement": { ...settle response, or null if not settled... },
        }

    If ``decline_reason`` is passed the authorization is simulated as declined and
    no settlement is attempted. Raises ``ValueError`` for missing configuration or
    unsupported input, and ``httpx.HTTPStatusError`` if Rain returns a non-2xx
    status.
    """
    if settle_amount is not None and settle_amount <= 0:
        raise ValueError("settle_amount must be a positive integer (USD cents).")

    authorization = authorize_transaction(
        card_id=card_id,
        amount=amount,
        merchant_name=merchant_name,
        merchant_category_code=merchant_category_code,
        currency=currency,
        decline_reason=decline_reason,
        api_key=api_key,
        timeout=timeout,
    )

    # A simulated decline never produces a settleable transaction.
    if decline_reason:
        return {
            "status": "declined",
            "transaction_id": _extract_transaction_id(authorization),
            "authorization": authorization,
            "settlement": None,
        }

    transaction_id = _extract_transaction_id(authorization)
    if not transaction_id:
        # Authorization succeeded but we couldn't find an id to settle against;
        # surface the raw authorization so the caller can decide what to do.
        return {
            "status": "authorized_not_settled",
            "transaction_id": None,
            "authorization": authorization,
            "settlement": None,
        }

    settlement = settle_transaction(
        transaction_id=transaction_id,
        amount=settle_amount,
        api_key=api_key,
        timeout=timeout,
    )
    return {
        "status": "settled",
        "transaction_id": transaction_id,
        "authorization": authorization,
        "settlement": settlement,
    }
