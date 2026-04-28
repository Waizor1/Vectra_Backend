from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import httpx

from bloobcat.settings import platega_settings


PLATEGA_PROVIDER = "platega"
PLATEGA_STATUS_PENDING = "PENDING"
PLATEGA_STATUS_CONFIRMED = "CONFIRMED"
PLATEGA_STATUS_CANCELED = "CANCELED"
PLATEGA_STATUS_CHARGEBACK = "CHARGEBACK"
PLATEGA_STATUS_CHARGEBACKED = "CHARGEBACKED"

PLATEGA_TO_INTERNAL_STATUS = {
    PLATEGA_STATUS_PENDING: "pending",
    PLATEGA_STATUS_CONFIRMED: "succeeded",
    PLATEGA_STATUS_CANCELED: "canceled",
    PLATEGA_STATUS_CHARGEBACK: "refunded",
    PLATEGA_STATUS_CHARGEBACKED: "refunded",
}


class PlategaConfigError(RuntimeError):
    """Raised when Platega is selected but required credentials are absent."""


class PlategaAPIError(RuntimeError):
    """Raised for Platega API/network failures without exposing credentials."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class PlategaCreateResult:
    transaction_id: str
    status: str
    redirect_url: str
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PlategaStatusResult:
    transaction_id: str
    status: str
    amount: float | None
    currency: str | None
    payload: str | None
    raw: dict[str, Any]


def normalize_platega_status(status: Any) -> str:
    return str(status or "").strip().upper()


def map_platega_status_to_internal(status: Any) -> str:
    return PLATEGA_TO_INTERNAL_STATUS.get(normalize_platega_status(status), "pending")


def parse_platega_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return dict(payload)
    if payload is None:
        return {}
    text = str(payload).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class PlategaClient:
    def __init__(
        self,
        *,
        merchant_id: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        payment_method: int | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.merchant_id = (merchant_id or platega_settings.merchant_id or "").strip()
        configured_secret = (
            platega_settings.secret_key.get_secret_value()
            if platega_settings.secret_key
            else ""
        )
        self.secret_key = (secret_key or configured_secret or "").strip()
        self.base_url = (base_url or platega_settings.base_url).rstrip("/")
        self.payment_method = (
            payment_method
            if payment_method is not None
            else platega_settings.payment_method
        )
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        if not self.merchant_id or not self.secret_key:
            raise PlategaConfigError("Platega credentials are not configured")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-MerchantId": self.merchant_id,
            "X-Secret": self.secret_key,
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def create_transaction(
        self,
        *,
        amount: float,
        currency: str,
        description: str,
        return_url: str,
        failed_url: str,
        payload: str,
    ) -> PlategaCreateResult:
        body: dict[str, Any] = {
            "paymentDetails": {
                "amount": float(amount),
                "currency": str(currency).upper(),
            },
            "description": description,
            "return": return_url,
            "failedUrl": failed_url,
            "payload": payload,
        }
        endpoint = "/v2/transaction/process"
        if self.payment_method is not None:
            body["paymentMethod"] = int(self.payment_method)
            endpoint = "/transaction/process"

        data = await self._request_json("POST", endpoint, json_body=body)
        transaction_id = str(data.get("transactionId") or data.get("id") or "").strip()
        redirect_url = str(data.get("redirect") or data.get("url") or "").strip()
        status = normalize_platega_status(data.get("status"))
        if not transaction_id or not redirect_url:
            raise PlategaAPIError("Platega create response is missing transaction link")
        return PlategaCreateResult(
            transaction_id=transaction_id,
            status=status or PLATEGA_STATUS_PENDING,
            redirect_url=redirect_url,
            raw=data,
        )

    async def get_transaction_status(self, transaction_id: str) -> PlategaStatusResult:
        clean_id = str(transaction_id or "").strip()
        if not clean_id:
            raise PlategaAPIError("Platega transaction id is empty")
        data = await self._request_json("GET", f"/transaction/{clean_id}")
        payment_details = data.get("paymentDetails")
        if not isinstance(payment_details, dict):
            payment_details = {}
        amount = payment_details.get("amount")
        try:
            parsed_amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            parsed_amount = None
        return PlategaStatusResult(
            transaction_id=str(data.get("id") or clean_id),
            status=normalize_platega_status(data.get("status")),
            amount=parsed_amount,
            currency=(
                str(payment_details.get("currency")).upper()
                if payment_details.get("currency") is not None
                else None
            ),
            payload=data.get("payload"),
            raw=data,
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.request(
                    method,
                    self._url(path),
                    headers=self._headers(),
                    json=json_body,
                )
        except PlategaConfigError:
            raise
        except httpx.TimeoutException as exc:
            raise PlategaAPIError("Platega request timed out") from exc
        except httpx.HTTPError as exc:
            raise PlategaAPIError("Platega request failed") from exc

        if response.status_code >= 400:
            raise PlategaAPIError(
                "Platega API returned an error",
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise PlategaAPIError("Platega API returned non-JSON response") from exc
        if not isinstance(data, dict):
            raise PlategaAPIError("Platega API returned unexpected response")
        return data
