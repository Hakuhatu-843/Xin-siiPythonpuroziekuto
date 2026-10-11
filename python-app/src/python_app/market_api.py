"""Client for the Cloudflare Worker market snapshot API."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MARKET_API_BASE_URL = "https://trade-value-api.hakuhatu-okuchi.workers.dev"
MARKET_SNAPSHOT_TIMEOUT_SECONDS = 5.0
_DIRECTIONS = {"↑", "↓", "→"}


class MarketAPIError(Exception):
    """A request or response error from the market snapshot API."""


@dataclass(frozen=True)
class MutationSnapshot:
    mutation: str
    multiplier: float | None
    sample_count: int
    direction: str


@dataclass(frozen=True)
class MarketSnapshot:
    character_number: str | None
    character_id: str
    character_name: str
    level1_value: float | None
    level_max_value: float | None
    demand_score: float | None
    sample_count: int
    level1_sample_count: int | None
    level_max_sample_count: int | None
    created_at: str
    level1_direction: str
    level_max_direction: str
    demand_direction: str
    has_previous: bool
    mutations: tuple[MutationSnapshot, ...]


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarketAPIError(f"Worker API の {field} が不正です。")
    return value


def _optional_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarketAPIError(f"Worker API の {field} が不正です。")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise MarketAPIError(f"Worker API の {field} が不正です。")
    return number


def _sample_count(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MarketAPIError(f"Worker API の {field} が不正です。")
    return value


def _optional_sample_count(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _sample_count(value, field)


def _direction(value: object, field: str) -> str:
    if not isinstance(value, str) or value not in _DIRECTIONS:
        raise MarketAPIError(f"Worker API の {field} が不正です。")
    return value


def _decode_snapshot(payload: object) -> MarketSnapshot:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise MarketAPIError("Worker API のレスポンス形式が不正です。")

    character_number = payload.get("characterNumber")
    if character_number is not None and (
        not isinstance(character_number, str)
        or re.fullmatch(r"\d{3}", character_number) is None
    ):
        raise MarketAPIError("Worker API の characterNumber が不正です。")

    changes = payload.get("changes")
    if not isinstance(changes, dict):
        raise MarketAPIError("Worker API の前回比較データが不正です。")

    has_previous = changes.get("hasPrevious")
    if not isinstance(has_previous, bool):
        raise MarketAPIError("Worker API の前回比較データが不正です。")

    raw_mutations = payload.get("mutations")
    if not isinstance(raw_mutations, list):
        raise MarketAPIError("Worker API の変異データが不正です。")

    mutations: list[MutationSnapshot] = []
    for row in raw_mutations:
        if not isinstance(row, dict):
            raise MarketAPIError("Worker API の変異データが不正です。")
        mutations.append(
            MutationSnapshot(
                mutation=_required_string(row.get("mutation"), "mutation"),
                multiplier=_optional_number(row.get("multiplier"), "multiplier"),
                sample_count=_sample_count(row.get("sampleCount"), "sampleCount"),
                direction=_direction(row.get("direction"), "direction"),
            )
        )

    demand_score = _optional_number(payload.get("demandScore"), "demandScore")
    if demand_score is not None and demand_score > 5:
        raise MarketAPIError("Worker API の demandScore が不正です。")

    return MarketSnapshot(
        character_number=character_number,
        character_id=_required_string(payload.get("characterId"), "characterId"),
        character_name=_required_string(payload.get("characterName"), "characterName"),
        level1_value=_optional_number(payload.get("level1Value"), "level1Value"),
        level_max_value=_optional_number(
            payload.get("levelMaxValue"), "levelMaxValue"
        ),
        demand_score=demand_score,
        sample_count=_sample_count(payload.get("sampleCount"), "sampleCount"),
        level1_sample_count=_optional_sample_count(
            payload.get("level1SampleCount"), "level1SampleCount"
        ),
        level_max_sample_count=_optional_sample_count(
            payload.get("levelMaxSampleCount"), "levelMaxSampleCount"
        ),
        created_at=_required_string(payload.get("createdAt"), "createdAt"),
        level1_direction=_direction(
            changes.get("level1Direction"), "level1Direction"
        ),
        level_max_direction=_direction(
            changes.get("levelMaxDirection"), "levelMaxDirection"
        ),
        demand_direction=_direction(changes.get("demandDirection"), "demandDirection"),
        has_previous=has_previous,
        mutations=tuple(mutations),
    )


def _describe_http_error(error: HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}

    detail = payload.get("error") if isinstance(payload, dict) else None
    if detail == "Character not found":
        return "このキャラクターはWorkerの市場データ対象にありません。"
    if detail == "Character number not found":
        return "未登録のキャラクター番号です。番号を確認してください。"
    if detail == "Market snapshot not found":
        return "市場スナップショットがまだありません。次回更新後に再度お試しください。"
    return f"Worker API が HTTP {error.code} を返しました。"


def get_market_snapshot(
    character_name: str | None = None,
    *,
    character_number: str | None = None,
    base_url: str = MARKET_API_BASE_URL,
    timeout: float = MARKET_SNAPSHOT_TIMEOUT_SECONDS,
) -> MarketSnapshot:
    """Fetch a persisted D1 market snapshot without calculating market values."""
    if (character_name is None) == (character_number is None):
        raise ValueError("キャラ名かキャラクター番号のどちらかを指定してください。")
    if character_number is not None:
        if re.fullmatch(r"\d{3}", character_number) is None:
            raise ValueError("キャラクター番号は3桁の数字で指定してください。")
        query = urlencode({"characterNumber": character_number})
    else:
        query = urlencode({"characterName": character_name})
    url = f"{base_url.rstrip('/')}/market-snapshot?{query}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "TradeValueDiscordBot/1.0",
        },
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise MarketAPIError(_describe_http_error(error)) from error
    except TimeoutError as error:
        raise MarketAPIError(
            "Worker APIへの接続がタイムアウトしました。時間をおいて再度お試しください。"
        ) from error
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise MarketAPIError(
                "Worker APIへの接続がタイムアウトしました。時間をおいて再度お試しください。"
            ) from error
        raise MarketAPIError(
            "Worker APIに接続できませんでした。ネットワークを確認して再度お試しください。"
        ) from error
    except OSError as error:
        raise MarketAPIError(
            "Worker APIに接続できませんでした。ネットワークを確認して再度お試しください。"
        ) from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MarketAPIError("Worker APIから不正なJSONレスポンスが返されました。") from error

    return _decode_snapshot(payload)


@dataclass(frozen=True)
class CharacterCatalogEntry:
    """A tradable character returned by the canonical Worker catalog."""

    character_number: str
    english_name: str
    japanese_name: str
    rarity: str


def get_characters(
    *,
    base_url: str = MARKET_API_BASE_URL,
    timeout: float = MARKET_SNAPSHOT_TIMEOUT_SECONDS,
) -> list[CharacterCatalogEntry]:
    """Fetch the canonical character-number list from the Worker catalog."""
    request = Request(
        f"{base_url.rstrip('/')}/characters",
        headers={
            "Accept": "application/json",
            "User-Agent": "TradeValueDiscordBot/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise MarketAPIError(
            f"Worker API が HTTP {error.code} を返しました（キャラ一覧）。"
        ) from error
    except TimeoutError as error:
        raise MarketAPIError(
            "Worker APIへの接続がタイムアウトしました。時間をおいて再度お試しください。"
        ) from error
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise MarketAPIError(
                "Worker APIへの接続がタイムアウトしました。時間をおいて再度お試しください。"
            ) from error
        raise MarketAPIError(
            "Worker APIに接続できませんでした。ネットワークを確認してください。"
        ) from error
    except OSError as error:
        raise MarketAPIError(
            "Worker APIに接続できませんでした。ネットワークを確認してください。"
        ) from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MarketAPIError("Worker APIから不正なJSONレスポンスが返されました。") from error

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise MarketAPIError("Worker APIのキャラ一覧レスポンスが不正です。")
    rows = payload.get("characters")
    if not isinstance(rows, list):
        raise MarketAPIError("Worker APIのキャラ一覧が不正です。")

    characters: list[CharacterCatalogEntry] = []
    seen_numbers: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise MarketAPIError("Worker APIのキャラ情報が不正です。")
        number = row.get("characterNumber")
        english_name = row.get("englishName")
        japanese_name = row.get("characterName")
        rarity = row.get("rarity")
        if (
            not isinstance(number, str)
            or re.fullmatch(r"\\d{3}", number) is None
            or number in seen_numbers
            or not isinstance(english_name, str)
            or not english_name.strip()
            or not isinstance(japanese_name, str)
            or not japanese_name.strip()
            or rarity not in {
                "common", "rare", "epic", "legend", "mythic",
                "secret", "ultimate", "emperor",
            }
        ):
            raise MarketAPIError("Worker APIのキャラ情報に不正な項目があります。")
        seen_numbers.add(number)
        characters.append(
            CharacterCatalogEntry(
                character_number=number,
                english_name=english_name.strip(),
                japanese_name=japanese_name.strip(),
                rarity=rarity,
            )
        )
    return sorted(characters, key=lambda item: item.character_number)
