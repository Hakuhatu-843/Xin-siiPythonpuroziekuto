"""Authenticated client for reviewing Web-submitted Worker trades."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

WORKER_API_BASE_URL = "https://trade-value-api.hakuhatu-okuchi.workers.dev"
REQUEST_TIMEOUT_SECONDS = 5.0
REVIEW_PAGE_SIZE = 50
_ReviewRecord = TypeVar("_ReviewRecord")


class TradeReviewAPIError(Exception):
    """A request or response error from the Worker review API."""


@dataclass(frozen=True)
class ReviewTradeItem:
    character_id: str
    character_number: str | None
    character_name: str | None
    rarity: str
    level: int
    mutation: str
    quantity: int


@dataclass(frozen=True)
class ReviewTrade:
    trade_id: int
    trade_type: str
    total_price: int
    status: str
    submitted_at: str
    items: tuple[ReviewTradeItem, ...]


@dataclass(frozen=True)
class ReviewExchange:
    exchange_id: int
    exchange_description: str
    status: str
    submitted_at: str
    items: tuple[ReviewTradeItem, ...]


def _decode_json(data: bytes) -> object:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TradeReviewAPIError("Worker APIから不正なJSONレスポンスが返されました。") from error


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TradeReviewAPIError(f"Worker API の {field} が不正です。")
    return value


def _decode_trade(value: object) -> ReviewTrade:
    if not isinstance(value, dict):
        raise TradeReviewAPIError("Worker API の取引データが不正です。")
    trade_id = value.get("id")
    total_price = value.get("totalPrice")
    if (
        isinstance(trade_id, bool)
        or not isinstance(trade_id, int)
        or trade_id < 1
        or isinstance(total_price, bool)
        or not isinstance(total_price, int)
        or total_price < 1
    ):
        raise TradeReviewAPIError("Worker API の取引データが不正です。")
    status = _required_string(value.get("status"), "status")
    if status != "pending":
        raise TradeReviewAPIError("未審査ではない取引が審査一覧に含まれています。")
    raw_items = value.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 3:
        raise TradeReviewAPIError("Worker API の取引明細が不正です。")

    return ReviewTrade(
        trade_id=trade_id,
        trade_type=_required_string(value.get("tradeType"), "tradeType"),
        total_price=total_price,
        status=status,
        submitted_at=_required_string(value.get("submittedAt"), "submittedAt"),
        items=tuple(_decode_trade_item(raw) for raw in raw_items),
    )


def _request(
    path: str,
    *,
    admin_key: str,
    base_url: str,
    payload: dict[str, object] | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> object:
    headers = {
        "Accept": "application/json",
        "X-Admin-Key": admin_key,
        "User-Agent": "TradeValueDiscordBot/1.0",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(f"{base_url.rstrip('/')}{path}", data=data, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            decoded = _decode_json(response.read())
    except HTTPError as error:
        if error.code == 401:
            raise TradeReviewAPIError(
                "Worker管理APIの認証に失敗しました。運用担当者に確認してください。"
            ) from error
        if error.code == 409:
            raise TradeReviewAPIError("この審査対象はすでに審査されています。") from error
        if error.code == 404:
            raise TradeReviewAPIError("審査対象が見つかりません。") from error
        if error.code >= 500:
            raise TradeReviewAPIError("Worker APIでエラーが発生しました。") from error
        raise TradeReviewAPIError(f"Worker API が HTTP {error.code} を返しました。") from error
    except TimeoutError as error:
        raise TradeReviewAPIError("Worker APIへの接続がタイムアウトしました。") from error
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise TradeReviewAPIError("Worker APIへの接続がタイムアウトしました。") from error
        raise TradeReviewAPIError("Worker APIに接続できませんでした。") from error
    except OSError as error:
        raise TradeReviewAPIError("Worker APIに接続できませんでした。") from error
    if not isinstance(decoded, dict) or decoded.get("ok") is not True:
        raise TradeReviewAPIError("Worker API のレスポンス形式が不正です。")
    return decoded


def _get_pending_pages(
    path: str,
    response_key: str,
    decode_record: Callable[[object], _ReviewRecord],
    record_id: Callable[[_ReviewRecord], int],
    *,
    admin_key: str,
    base_url: str,
    timeout: float,
) -> tuple[_ReviewRecord, ...]:
    records: list[_ReviewRecord] = []
    cursor = 0
    through_cursor: int | None = None
    while True:
        through_query = (
            f"&through={through_cursor}" if through_cursor is not None else ""
        )
        payload = _request(
            f"{path}?status=pending&limit={REVIEW_PAGE_SIZE}&after={cursor}"
            f"{through_query}",
            admin_key=admin_key,
            base_url=base_url,
            timeout=timeout,
        )
        raw_records = payload.get(response_key)
        if not isinstance(raw_records, list) or len(raw_records) > REVIEW_PAGE_SIZE:
            raise TradeReviewAPIError("Worker API の審査一覧が不正です。")
        if "hasMore" not in payload or "nextCursor" not in payload:
            raise TradeReviewAPIError("Worker API がページングに対応していません。")

        page = tuple(decode_record(value) for value in raw_records)
        page_ids = tuple(record_id(record) for record in page)
        response_through = payload.get("throughCursor")
        if (
            isinstance(response_through, bool)
            or not isinstance(response_through, int)
            or response_through < cursor
            or any(record_id > response_through for record_id in page_ids)
        ):
            raise TradeReviewAPIError("Worker API のページカーソルが不正です。")
        if through_cursor is None:
            through_cursor = response_through
        elif response_through != through_cursor:
            raise TradeReviewAPIError("Worker API のページカーソルが不正です。")
        if any(
            current <= previous
            for previous, current in zip((cursor, *page_ids), page_ids)
        ):
            raise TradeReviewAPIError("Worker API のページ順序が不正です。")
        records.extend(page)

        has_more = payload.get("hasMore", False)
        if not isinstance(has_more, bool):
            raise TradeReviewAPIError("Worker API のページ情報が不正です。")
        if not has_more:
            if payload.get("nextCursor") is not None:
                raise TradeReviewAPIError("Worker API のページカーソルが不正です。")
            return tuple(records)

        next_cursor = payload.get("nextCursor")
        if (
            not page
            or isinstance(next_cursor, bool)
            or not isinstance(next_cursor, int)
            or next_cursor <= cursor
            or next_cursor != page_ids[-1]
        ):
            raise TradeReviewAPIError("Worker API のページカーソルが不正です。")
        cursor = next_cursor


def get_pending_trades(
    *,
    admin_key: str,
    base_url: str = WORKER_API_BASE_URL,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> tuple[ReviewTrade, ...]:
    if not admin_key.strip():
        raise TradeReviewAPIError("Worker管理APIの認証情報が設定されていません。")
    return _get_pending_pages(
        "/trades",
        "trades",
        _decode_trade,
        lambda trade: trade.trade_id,
        admin_key=admin_key,
        base_url=base_url,
        timeout=timeout,
    )


def get_pending_exchanges(
    *,
    admin_key: str,
    base_url: str = WORKER_API_BASE_URL,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> tuple[ReviewExchange, ...]:
    if not admin_key.strip():
        raise TradeReviewAPIError("Worker管理APIの認証情報が設定されていません。")
    return _get_pending_pages(
        "/exchanges",
        "exchanges",
        _decode_exchange,
        lambda exchange: exchange.exchange_id,
        admin_key=admin_key,
        base_url=base_url,
        timeout=timeout,
    )


def _decode_exchange(value: object) -> ReviewExchange:
    if not isinstance(value, dict):
        raise TradeReviewAPIError("Worker API の交換データが不正です。")
    exchange_id = value.get("id")
    if (
        isinstance(exchange_id, bool)
        or not isinstance(exchange_id, int)
        or exchange_id < 1
    ):
        raise TradeReviewAPIError("Worker API の交換データが不正です。")
    if value.get("status") != "pending":
        raise TradeReviewAPIError("未審査ではない交換が審査一覧に含まれています。")
    description = _required_string(
        value.get("exchangeDescription"), "exchangeDescription"
    )
    if len(description.strip()) > 1000:
        raise TradeReviewAPIError("Worker API の交換内容が不正です。")
    raw_items = value.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 3:
        raise TradeReviewAPIError("Worker API の交換対象データが不正です。")
    return ReviewExchange(
        exchange_id=exchange_id,
        exchange_description=description,
        status="pending",
        submitted_at=_required_string(value.get("submittedAt"), "submittedAt"),
        items=tuple(_decode_trade_item(item) for item in raw_items),
    )


def _decode_trade_item(raw: object) -> ReviewTradeItem:
    if not isinstance(raw, dict):
        raise TradeReviewAPIError("Worker API の取引明細が不正です。")
    level = raw.get("level")
    quantity = raw.get("quantity")
    if (
        isinstance(level, bool)
        or not isinstance(level, int)
        or not 1 <= level <= 301
        or isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or quantity < 1
    ):
        raise TradeReviewAPIError("Worker API の取引明細が不正です。")
    character_number = raw.get("characterNumber")
    if character_number is not None and (
        not isinstance(character_number, str)
        or len(character_number) != 3
        or not character_number.isascii()
        or not character_number.isdigit()
    ):
        raise TradeReviewAPIError("Worker API の characterNumber が不正です。")
    character_name = raw.get("characterName")
    if character_name is not None and (
        not isinstance(character_name, str) or not character_name.strip()
    ):
        raise TradeReviewAPIError("Worker API の characterName が不正です。")
    return ReviewTradeItem(
        character_id=_required_string(raw.get("characterId"), "characterId"),
        character_number=character_number,
        character_name=character_name,
        rarity=_required_string(raw.get("rarity"), "rarity"),
        level=level,
        mutation=_required_string(raw.get("mutation"), "mutation"),
        quantity=quantity,
    )


def update_trade_status(
    trade_id: int,
    status: str,
    *,
    reviewer_id: str,
    admin_key: str,
    base_url: str = WORKER_API_BASE_URL,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> None:
    if trade_id < 1 or status not in {"approved", "rejected"}:
        raise ValueError("A valid trade ID and terminal review status are required.")
    if not reviewer_id.strip() or not admin_key.strip():
        raise TradeReviewAPIError("審査担当者またはWorker認証情報が設定されていません。")
    _request(
        f"/trades/{trade_id}/status",
        admin_key=admin_key,
        base_url=base_url,
        payload={"status": status, "reviewerId": reviewer_id},
        timeout=timeout,
    )


def update_exchange_status(
    exchange_id: int,
    status: str,
    *,
    reviewer_id: str,
    admin_key: str,
    base_url: str = WORKER_API_BASE_URL,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> None:
    if exchange_id < 1 or status not in {"approved", "rejected"}:
        raise ValueError("A valid exchange ID and terminal review status are required.")
    if not reviewer_id.strip() or not admin_key.strip():
        raise TradeReviewAPIError("審査担当者またはWorker認証情報が設定されていません。")
    _request(
        f"/exchanges/{exchange_id}/status",
        admin_key=admin_key,
        base_url=base_url,
        payload={"status": status, "reviewerId": reviewer_id},
        timeout=timeout,
    )
