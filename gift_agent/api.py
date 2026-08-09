"""HTTP API that triggers the gift-selection agent.

    POST /agent/gift
        Body (all optional):
          {
            "user_profile": { ... gift profile ... },   # defaults to user_profile.json
            "current_date": "2026-08-08"                 # defaults to today
          }
        Runs the full search -> select -> purchase -> fulfillment graph: the
        select step chooses a gift, the purchase node buys it (our side issues a
        scoped card sized to the gift, then the partner checkout API processes and
        settles the transaction), and the fulfillment node persists the order.
        Returns only the overall status: "fulfilled" or "failed".

    GET /orders
        Returns the stored orders for the current user (hardcoded to the USER_ID
        environment variable). Orders are persisted by the graph's fulfillment node
        in an in-memory store, so this reflects orders placed since the server started.

    GET /profile
        Returns the profile for the current user (hardcoded to the USER_ID
        environment variable): the profile stored via PUT /profile if present,
        otherwise the default from user_profile.json.

    PUT /profile
        Body:
          { "profile": { ... full user profile ... } }  # overwrites stored profile
        Overwrites the stored profile for the current user. Subsequent
        POST /agent/gift runs use this profile, so edits change the results.

    POST /card/fund
        Body:
          { "amount": 100000 }                          # funding amount, required
        Funds the collateral account via the Rain cards API and returns its response.

    POST /card/scoped
        Body:
          {
            "amount_in_usd_cents": 4299,                 # card spend limit, required
            "user_id": "...",                            # defaults to USER_ID env
            "allowed_mccs": ["5411", "5812"]             # optional MCC restriction
          }
        Issues a single-use scoped card via the Rain issuing API. A fresh
        session id is generated server-side for every request.

    POST /v1/simulate/transactions/authorize
        Body:
          {
            "cardId": "a7f2c5b1-...",                    # Rain card id, required
            "amount": 5000,                              # USD cents, required
            "currency": "USD",                           # only "USD" supported
            "merchantName": "Coffee Shop",               # required
            "merchantCategoryCode": "5814",              # MCC, required
            "declineReason": "..."                       # optional, simulate a decline
          }
        Simulates a card authorization via the Rain simulate API and returns its
        response. The card must be active and belong to the calling tenant.

    POST /v1/simulate/transactions/{transactionId}/settle
        Body (optional):
          {
            "amount": 5000                               # settlement cents, optional
          }
        Settles an existing authorized transaction via the Rain simulate API. If
        "amount" is omitted, settles for the original authorization amount.

Run with:  python -m gift_agent.api   (or: uvicorn gift_agent.api:app)
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import cards, profile_store, store
from .graph import run_agent

logger = logging.getLogger("gift_agent.api")

# The Rain "simulate collateral fund" endpoint. Overridable for staging/prod.
_FUND_URL = os.environ.get(
    "RAIN_FUND_URL", "https://api-dev.raincards.xyz/v1/simulate/collateral/fund"
)
_FUND_CURRENCY = "rusd"

app = FastAPI(
    title="Surprise Me — Gift Selection Agent",
    description="LangGraph agent (search -> select) exposed over HTTP.",
    version="0.1.0",
)


class GiftRequest(BaseModel):
    user_profile: Optional[dict[str, Any]] = None
    current_date: Optional[str] = None


class GiftResponse(BaseModel):
    # The overall outcome of the run: "fulfilled" if the order was placed and
    # persisted, "failed" otherwise.
    status: str


class OrdersResponse(BaseModel):
    user_id: str
    orders: list[dict[str, Any]]


class ClearOrdersResponse(BaseModel):
    status: str


class ProfileResponse(BaseModel):
    user_id: str
    # "stored" if the profile came from the in-memory store, "default" if it fell
    # back to user_profile.json (nothing stored for this user yet).
    source: str
    profile: dict[str, Any]


class UpdateProfileRequest(BaseModel):
    # The full profile to store. This overwrites any previously stored profile
    # for the user, so send the complete document (not a partial patch).
    profile: dict[str, Any] = Field(
        ..., description="The complete user profile to store (overwrites the existing one)."
    )


class FundCardRequest(BaseModel):
    amount: int = Field(..., gt=0, description="Amount to fund the collateral account with.")


class FundCardResponse(BaseModel):
    result: dict[str, Any]


class ScopedCardRequest(BaseModel):
    amount_in_usd_cents: int = Field(
        ..., gt=0, description="Card spend limit in US cents (e.g. 4299 = $42.99)."
    )
    user_id: Optional[str] = Field(
        None, description="Rain user id. Defaults to the USER_ID environment variable."
    )
    allowed_mccs: Optional[list[str]] = Field(
        None,
        description='Optional merchant category codes to restrict the card to, e.g. ["5411", "5812"].',
    )


class ScopedCardResponse(BaseModel):
    result: dict[str, Any]


class AuthorizeTransactionRequest(BaseModel):
    # Accept the documented camelCase body (e.g. "cardId"); snake_case also works.
    model_config = ConfigDict(populate_by_name=True)

    card_id: str = Field(
        ..., alias="cardId", description="The Rain card ID to authorize against (UUID)."
    )
    amount: int = Field(
        ..., gt=0, description="The transaction amount in USD cents. Must be positive."
    )
    currency: str = Field(
        "USD", description='The merchant currency code. Only "USD" is supported.'
    )
    merchant_name: str = Field(..., alias="merchantName", description="The merchant name.")
    merchant_category_code: str = Field(
        ...,
        alias="merchantCategoryCode",
        description='The merchant category code (MCC), e.g. "5814" for fast food.',
    )
    decline_reason: Optional[str] = Field(
        None,
        alias="declineReason",
        description="Simulate a declined authorization with this decline reason.",
    )

    @field_validator("currency")
    @classmethod
    def _only_usd(cls, value: str) -> str:
        if value != "USD":
            raise ValueError('currency must be "USD".')
        return value


class AuthorizeTransactionResponse(BaseModel):
    result: dict[str, Any]


class SettleTransactionRequest(BaseModel):
    amount: Optional[int] = Field(
        None,
        gt=0,
        description="Settlement amount in cents. If omitted, settles for the original authorization amount.",
    )


class SettleTransactionResponse(BaseModel):
    result: dict[str, Any]


_DEMO_PAGE = os.path.join(os.path.dirname(__file__), "static", "demo.html")


@app.get("/", include_in_schema=False)
def demo_page() -> FileResponse:
    """Serve the interactive demo page (same-origin, so no CORS needed)."""
    return FileResponse(_DEMO_PAGE, media_type="text/html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/agent/gift", response_model=GiftResponse)
def choose_gift(request: GiftRequest) -> GiftResponse:
    """Run the search -> select -> purchase -> fulfillment graph.

    Returns only the overall outcome: ``"fulfilled"`` when the order was placed
    and persisted by the fulfillment node, ``"failed"`` otherwise.

    The agent always reads the user profile from the store (keyed by ``USER_ID``,
    falling back to ``user_profile.json``). If a ``user_profile`` is supplied in
    the request body it is first saved to the store — keeping the database the
    single source of truth the graph reads from.
    """
    current_date = request.current_date or date.today().isoformat()
    if request.user_profile is not None:
        user_id = os.environ.get("USER_ID")
        if not user_id:
            raise HTTPException(
                status_code=500,
                detail="USER_ID is not set; cannot store the supplied user_profile.",
            )
        profile_store.save_profile(user_id, request.user_profile)
    logger.info(
        "POST /agent/gift (custom_profile=%s, current_date=%s)",
        request.user_profile is not None,
        current_date,
    )
    result = run_agent(current_date)
    fulfilled = (result.get("fulfillment") or {}).get("status") == "stored"
    status = "fulfilled" if fulfilled else "failed"
    logger.info("POST /agent/gift done: status=%s", status)
    return GiftResponse(status=status)


@app.get("/orders", response_model=OrdersResponse)
def list_orders() -> OrdersResponse:
    """Return the stored orders for the current user.

    The user is hardcoded to the ``USER_ID`` environment variable — the same id the
    fulfillment node stores orders under. Orders live in an in-memory store, so this
    only returns orders placed since the server last started.
    """
    user_id = os.environ.get("USER_ID")
    if not user_id:
        raise HTTPException(status_code=500, detail="USER_ID is not set.")
    orders = store.get_orders(user_id)
    logger.info("GET /orders (user_id=%s): %d order(s)", user_id, len(orders))
    return OrdersResponse(user_id=user_id, orders=orders)


@app.delete("/orders", response_model=ClearOrdersResponse)
def clear_orders() -> ClearOrdersResponse:
    store.clear()
    logger.info("DELETE /orders: cleared all orders")
    return ClearOrdersResponse(status="cleared")


@app.get("/profile", response_model=ProfileResponse)
def get_profile() -> ProfileResponse:
    """Return the profile for the current user.

    The user is hardcoded to the ``USER_ID`` environment variable. Returns the
    profile stored via ``PUT /profile`` (``source: "stored"``) if present,
    otherwise falls back to ``user_profile.json`` (``source: "default"``).
    """
    user_id = os.environ.get("USER_ID")
    if not user_id:
        raise HTTPException(status_code=500, detail="USER_ID is not set.")
    stored = profile_store.get_profile(user_id)
    source = "stored" if stored is not None else "default"
    profile = stored if stored is not None else profile_store.default_profile()
    logger.info("GET /profile (user_id=%s): source=%s", user_id, source)
    return ProfileResponse(user_id=user_id, source=source, profile=profile)


@app.put("/profile", response_model=ProfileResponse)
def update_profile(request: UpdateProfileRequest) -> ProfileResponse:
    """Overwrite the profile for the current user.

    The user is hardcoded to the ``USER_ID`` environment variable. The stored
    profile fully replaces any previous one and is used by subsequent
    ``POST /agent/gift`` runs, so profile edits change the agent's results.
    """
    user_id = os.environ.get("USER_ID")
    if not user_id:
        raise HTTPException(status_code=500, detail="USER_ID is not set.")
    profile_store.save_profile(user_id, request.profile)
    logger.info("PUT /profile (user_id=%s): stored profile", user_id)
    return ProfileResponse(user_id=user_id, source="stored", profile=request.profile)


@app.post("/card/fund", response_model=FundCardResponse)
def fund_card(request: FundCardRequest) -> FundCardResponse:
    """Fund the collateral account via the Rain cards API.

    Reads the API key and contract id from the environment (``API_KEY`` /
    ``COLLATERAL_CONTRACT_ID``); the caller only supplies the amount.
    """
    logger.info("POST /card/fund (amount=%s)", request.amount)
    api_key = os.environ.get("API_KEY")
    contract_id = os.environ.get("COLLATERAL_CONTRACT_ID")
    if not api_key:
        raise HTTPException(status_code=500, detail="API_KEY is not set.")
    if not contract_id:
        raise HTTPException(status_code=500, detail="COLLATERAL_CONTRACT_ID is not set.")

    payload = {
        "contractId": contract_id,
        "currency": _FUND_CURRENCY,
        "amount": request.amount,
    }
    try:
        response = httpx.post(
            _FUND_URL,
            headers={"Api-Key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Rain API request failed: {exc}") from exc

    if response.status_code >= 400:
        logger.warning("POST /card/fund: Rain API returned %d", response.status_code)
        raise HTTPException(
            status_code=502,
            detail=f"Rain API returned {response.status_code}: {response.text}",
        )

    try:
        result = response.json()
    except ValueError:
        result = {"raw": response.text}
    logger.info("POST /card/fund done: Rain API responded %d", response.status_code)
    return FundCardResponse(result=result)


@app.post("/card/scoped", response_model=ScopedCardResponse)
def issue_scoped_card(request: ScopedCardRequest) -> ScopedCardResponse:
    """Issue a single-use scoped card via the Rain issuing API.

    Reads the API key and (by default) the user id from the environment
    (``API_KEY`` / ``USER_ID``); the caller supplies the amount and a session id.
    """
    logger.info(
        "POST /card/scoped (amount_in_usd_cents=%s, user_id=%s)",
        request.amount_in_usd_cents,
        request.user_id or "<env>",
    )
    try:
        result = cards.issue_scoped_card(
            amount_in_usd_cents=request.amount_in_usd_cents,
            allowed_mccs=request.allowed_mccs,
            user_id=request.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "POST /card/scoped: Rain API returned %d", exc.response.status_code
        )
        raise HTTPException(
            status_code=502,
            detail=f"Rain API returned {exc.response.status_code}: {exc.response.text}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Rain API request failed: {exc}") from exc

    logger.info("POST /card/scoped done")
    return ScopedCardResponse(result=result)


@app.post(
    "/v1/simulate/transactions/authorize",
    response_model=AuthorizeTransactionResponse,
)
def simulate_authorize(
    request: AuthorizeTransactionRequest,
) -> AuthorizeTransactionResponse:
    """Simulate a card authorization via the Rain simulate API.

    Reads the API key from the environment (``API_KEY``) and forwards it as the
    ``Api-Key`` header; the tenant scope is resolved from that key. The card must
    be active and belong to the calling tenant — locked, canceled, or unactivated
    cards cannot be authorized.
    """
    logger.info(
        "POST /v1/simulate/transactions/authorize (card_id=%s, amount=%s, mcc=%s, decline=%s)",
        request.card_id,
        request.amount,
        request.merchant_category_code,
        request.decline_reason or "<none>",
    )
    try:
        result = cards.authorize_transaction(
            card_id=request.card_id,
            amount=request.amount,
            currency=request.currency,
            merchant_name=request.merchant_name,
            merchant_category_code=request.merchant_category_code,
            decline_reason=request.decline_reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "POST /v1/simulate/transactions/authorize: Rain API returned %d",
            exc.response.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Rain API returned {exc.response.status_code}: {exc.response.text}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Rain API request failed: {exc}") from exc

    logger.info("POST /v1/simulate/transactions/authorize done")
    return AuthorizeTransactionResponse(result=result)


@app.post(
    "/v1/simulate/transactions/{transaction_id}/settle",
    response_model=SettleTransactionResponse,
)
def simulate_settle(
    transaction_id: str,
    request: SettleTransactionRequest,
) -> SettleTransactionResponse:
    """Settle an existing authorized transaction via the Rain simulate API.

    Reads the API key from the environment (``API_KEY``) and forwards it as the
    ``Api-Key`` header; the tenant scope is resolved from that key. If no amount
    is supplied, the original authorization amount is settled.
    """
    logger.info(
        "POST /v1/simulate/transactions/%s/settle (amount=%s)",
        transaction_id,
        request.amount if request.amount is not None else "<original>",
    )
    try:
        result = cards.settle_transaction(
            transaction_id=transaction_id,
            amount=request.amount,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "POST /v1/simulate/transactions/%s/settle: Rain API returned %d",
            transaction_id,
            exc.response.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Rain API returned {exc.response.status_code}: {exc.response.text}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Rain API request failed: {exc}") from exc

    logger.info("POST /v1/simulate/transactions/%s/settle done", transaction_id)
    return SettleTransactionResponse(result=result)


def main() -> None:
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("AGENT_PORT", "8010"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
