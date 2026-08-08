"""Partner-side checkout: process the transaction and settle the order.

The buyer platform issues a scoped card and then calls this checkout core with the
card, the amount, the merchant and the shopper's shipping address. Here on the
partner side we:

1. **process the transaction** — authorize the card through the payment processor
   (:mod:`partner_mock.payments`),
2. **settle the order** — capture the authorized amount, and
3. **build the order** — assemble an order JSON (incl. the shipping address) and
   log it.

The order is only logged, not persisted anywhere yet.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import payments

logger = logging.getLogger("partner_mock")


class ShippingAddress(BaseModel):
    """Where the order ships. Extra keys are tolerated so different profile shapes
    (e.g. an extra ``line2`` or ``name``) pass through unchanged."""

    model_config = ConfigDict(extra="allow")

    line1: Optional[str] = None
    line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: Optional[str] = None


class CheckoutRequest(BaseModel):
    """The checkout payload the buyer platform posts to the partner."""

    # Accept the documented camelCase body; snake_case also works.
    model_config = ConfigDict(populate_by_name=True)

    card_id: str = Field(..., alias="cardId", description="The scoped card to charge.")
    amount: int = Field(..., gt=0, description="Order total in USD cents.")
    currency: str = Field("USD", description='Order currency. Only "USD" is supported.')
    merchant_name: str = Field(..., alias="merchantName", description="Merchant being paid.")
    merchant_category_code: str = Field(
        ..., alias="merchantCategoryCode", description="Merchant category code (MCC)."
    )
    shipping_address: Optional[ShippingAddress] = Field(
        None, alias="shippingAddress", description="Where to ship the order."
    )


def process_order(request: CheckoutRequest) -> dict[str, Any]:
    """Authorize + settle the charge, build the order JSON, log it, and return it.

    Returns ``{"status", "order", "authorization", "settlement"}``. Raises the same
    errors as :mod:`partner_mock.payments` (``ValueError`` / ``httpx.HTTPStatusError``).
    """
    logger.info(
        "[checkout] order received (card=%s, amount=%d cents, merchant=%s, mcc=%s)",
        request.card_id,
        request.amount,
        request.merchant_name,
        request.merchant_category_code,
    )

    # 1. Process the transaction: authorize the card.
    authorization = payments.authorize_transaction(
        card_id=request.card_id,
        amount=request.amount,
        merchant_name=request.merchant_name,
        merchant_category_code=request.merchant_category_code,
        currency=request.currency,
    )
    transaction_id = payments.extract_transaction_id(authorization)
    if not transaction_id:
        raise ValueError("Could not resolve the transaction id from the authorize response.")

    # 2. Settle the order for the full authorized amount.
    settlement = payments.settle_transaction(transaction_id=transaction_id, amount=request.amount)

    # 3. Build the order JSON (not persisted — logged only, for now).
    order = {
        "order_id": f"ord_{uuid.uuid4().hex[:12]}",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "confirmed",
        "currency": request.currency,
        "amount": request.amount,
        "payment": {
            "card_id": request.card_id,
            "transaction_id": transaction_id,
        },
        "merchant": {
            "name": request.merchant_name,
            "category_code": request.merchant_category_code,
        },
        "shipping_address": (
            request.shipping_address.model_dump(exclude_none=True)
            if request.shipping_address
            else None
        ),
    }
    logger.info("[checkout] order created:\n%s", json.dumps(order, indent=2, ensure_ascii=False))

    return {
        "status": "confirmed",
        "order": order,
        "authorization": authorization,
        "settlement": settlement,
    }
