"""The partner's payment-processing client.

On the partner (merchant) side, checkout charges the shopper's card through a
payment processor. Here that processor is the Rain *simulate* API: we authorize
the transaction and then settle it. This mirrors :mod:`gift_agent.cards` but lives
on the partner side on purpose — card *issuing* is the buyer platform's job, while
*processing and settling* the charge is the merchant's.

Endpoints are overridable for staging/prod and the API key is read from the
environment, so the same ``.env`` used elsewhere in the demo works here too.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger("partner_mock")

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


def extract_transaction_id(response: dict[str, Any]) -> Optional[str]:
    """Best-effort pull of the transaction id out of an authorize response.

    Rain has varied the field name/nesting across versions, so we probe the common
    shapes (top-level ``transactionId``/``id`` and the same keys nested under
    ``transaction``/``data``/``result``) rather than hard-coding one.
    """
    containers = [response]
    for nested_key in ("transaction", "data", "result"):
        nested = response.get(nested_key)
        if isinstance(nested, dict):
            containers.append(nested)
    for container in containers:
        for key in ("transactionId", "transaction_id", "id"):
            value = container.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def authorize_transaction(
    *,
    card_id: str,
    amount: int,
    merchant_name: str,
    merchant_category_code: str,
    currency: str = "USD",
    api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Authorize ``amount`` (USD cents) on ``card_id`` via the Rain simulate API.

    ``api_key`` defaults to the ``API_KEY`` environment variable. Raises
    ``ValueError`` for missing configuration or unsupported input, and
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
    logger.info(
        "[payments] POST authorize (card=%s, amount=%d cents, mcc=%s)",
        card_id,
        amount,
        merchant_category_code,
    )
    response = httpx.post(
        _AUTHORIZE_TRANSACTION_URL,
        headers={"Api-Key": api_key, "content-type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    logger.info(
        "[payments] authorize response: %d %s", response.status_code, response.text
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

    ``amount`` (USD cents) settles for less than the authorization; omit it to
    settle the full authorized amount. ``api_key`` defaults to the ``API_KEY``
    environment variable. Raises ``ValueError`` for missing configuration or
    unsupported input, and ``httpx.HTTPStatusError`` if Rain returns a non-2xx
    status.
    """
    api_key = api_key or os.environ.get("API_KEY")
    if not api_key:
        raise ValueError("API_KEY is not set.")
    if amount is not None and amount <= 0:
        raise ValueError("amount must be a positive integer (USD cents).")

    payload: dict[str, Any] = {}
    if amount is not None:
        payload["amount"] = amount

    logger.info(
        "[payments] POST settle (transaction=%s, amount=%s)",
        transaction_id,
        amount if amount is not None else "<original>",
    )
    response = httpx.post(
        _SETTLE_TRANSACTION_URL.format(transaction_id=transaction_id),
        headers={"Api-Key": api_key, "content-type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    logger.info(
        "[payments] settle response: %d %s", response.status_code, response.text
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}
