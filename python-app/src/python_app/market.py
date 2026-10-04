"""Market price calculation utilities.

This module intentionally keeps the first market model conservative:
- transaction totals are converted to per-unit prices;
- human-friendly amounts such as 1.5k / 1,500円 are accepted;
- level prices are estimated only from observations at that exact level;
- no unverified level-growth formula is invented;
- mutation multipliers are calculated from comparable normal/mutation prices.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Sequence
import re


@dataclass(frozen=True)
class PriceObservation:
    """One normalized price observation."""

    character_name: str
    level: int
    price_per_unit: float


@dataclass(frozen=True)
class MarketEstimate:
    """Calculated market estimate for one character/mutation at one level."""

    character_name: str
    level: int
    price: float
    transaction_count: int
    confidence: str
    is_estimated: bool
    mutation: str = "通常"


_AMOUNT_RE = re.compile(
    r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*([kKmMbB])?\s*(?:円|¥|$)?\s*$"
)


def parse_market_amount(value: str | int | float) -> float:
    """Parse a stored/user-facing amount such as 1500, 1,500円, or 1.5k."""
    if isinstance(value, bool):
        raise ValueError("金額が正しくありません。")

    if isinstance(value, (int, float)):
        amount = float(value)
    else:
        text = str(value).strip().replace(",", "")
        match = _AMOUNT_RE.fullmatch(text)
        if not match:
            raise ValueError(f"金額を解釈できません: {value!r}")
        amount = float(match.group(1))
        suffix = (match.group(2) or "").lower()
        amount *= {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)

    if amount < 0:
        raise ValueError("金額は0以上である必要があります。")
    return amount


def price_per_unit(total_amount: float | str, quantity: int) -> float:
    """Convert a total transaction price into a per-unit price."""
    if quantity <= 0:
        raise ValueError("個数は1以上である必要があります。")
    amount = parse_market_amount(total_amount)
    return amount / quantity


def _median_without_extreme_outliers(values: Sequence[float]) -> float:
    """Return a median after removing obvious extreme outliers."""
    if not values:
        raise ValueError("価格データがありません。")

    ordered = sorted(float(value) for value in values)
    if len(ordered) < 5:
        return float(median(ordered))

    q1 = float(median(ordered[: len(ordered) // 2]))
    q3 = float(median(ordered[(len(ordered) + 1) // 2 :]))
    iqr = q3 - q1
    if iqr <= 0:
        return float(median(ordered))

    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    filtered = [value for value in ordered if lower <= value <= upper]
    return float(median(filtered or ordered))


def confidence_for_count(count: int) -> str:
    """Map observation count to a simple, transparent confidence label."""
    if count >= 10:
        return "高"
    if count >= 5:
        return "中"
    if count >= 1:
        return "低"
    return "データ不足"


def estimate_market_price(
    observations: Sequence[PriceObservation],
    *,
    level: int,
    mutation: str = "通常",
) -> MarketEstimate:
    """Estimate the price at an exact level from matching observations.

    Higher/lower levels are deliberately not converted to another level.
    That prevents the bot from claiming a level-1 value without an
    evidence-backed level curve.
    """
    if level < 1:
        raise ValueError("レベルは1以上である必要があります。")
    if not observations:
        raise ValueError("価格データがありません。")

    matching = [
        observation.price_per_unit
        for observation in observations
        if observation.level == level
    ]
    if not matching:
        return MarketEstimate(
            character_name=observations[0].character_name,
            level=level,
            price=0.0,
            transaction_count=0,
            confidence="データ不足",
            is_estimated=True,
            mutation=mutation,
        )

    return MarketEstimate(
        character_name=observations[0].character_name,
        level=level,
        price=_median_without_extreme_outliers(matching),
        transaction_count=len(matching),
        confidence=confidence_for_count(len(matching)),
        is_estimated=False,
        mutation=mutation,
    )


def estimate_level_one_price(
    observations: Sequence[PriceObservation],
) -> MarketEstimate:
    """Estimate the level-1 market price from level-1 observations only."""
    return estimate_market_price(observations, level=1)


def _load_observations(
    rows,
) -> list[PriceObservation]:
    observations: list[PriceObservation] = []
    for row in rows:
        stored_name = str(row[0])
        level_text = str(row[1]).strip()
        try:
            level = int(level_text)
            quantity = int(row[2])
            amount = parse_market_amount(row[3])
        except (TypeError, ValueError):
            continue

        if level < 1 or quantity <= 0:
            continue

        observations.append(
            PriceObservation(
                character_name=stored_name,
                level=level,
                price_per_unit=price_per_unit(amount, quantity),
            )
        )
    return observations


def load_character_observations(
    connection,
    character_name: str,
) -> list[PriceObservation]:
    """Load usable verified non-set trades for one character."""
    rows = connection.execute(
        """
        SELECT character_name, level, quantity, total_amount
        FROM trades
        WHERE character_name = ?
          AND transaction_type != 'セット'
          AND is_verified = 1
          AND quantity > 0
        ORDER BY id
        """,
        (character_name,),
    ).fetchall()
    return _load_observations(rows)


def load_verified_character_observations(
    connection,
    character_name: str,
    mutation: str = "",
) -> list[PriceObservation]:
    """Load verified non-set observations for one character and mutation."""
    rows = connection.execute(
        """
        SELECT tc.character_name, tc.level, tc.quantity, t.total_amount
        FROM trade_characters AS tc
        JOIN trades AS t ON t.id = tc.trade_id
        WHERE tc.character_name = ?
          AND tc.mutation = ?
          AND tc.is_verified = 1
          AND t.is_verified = 1
          AND t.transaction_type != 'セット'
          AND tc.quantity > 0

        UNION ALL

        SELECT t.character_name, t.level, t.quantity, t.total_amount
        FROM trades AS t
        WHERE t.character_name = ?
          AND t.mutation = ?
          AND t.is_verified = 1
          AND t.transaction_type != 'セット'
          AND t.quantity > 0
          AND NOT EXISTS (
              SELECT 1
              FROM trade_characters AS existing
              WHERE existing.trade_id = t.id
          )

        ORDER BY 1
        """,
        (character_name, mutation, character_name, mutation),
    ).fetchall()
    return _load_observations(rows)


def calculate_mutation_multiplier(
    normal_price: float,
    mutation_price: float,
) -> float:
    """Calculate a character-specific mutation multiplier."""
    if normal_price <= 0:
        raise ValueError("通常価格は0より大きい必要があります。")
    if mutation_price < 0:
        raise ValueError("変異価格は0以上である必要があります。")
    return mutation_price / normal_price


def load_character_market_observations(
    connection,
    character_name: str,
) -> dict[str, list[PriceObservation]]:
    """Load verified non-set observations grouped by mutation."""
    rows = connection.execute(
        """
        SELECT tc.character_name, tc.mutation, tc.level, tc.quantity, t.total_amount
        FROM trade_characters AS tc
        JOIN trades AS t ON t.id = tc.trade_id
        WHERE tc.character_name = ?
          AND tc.is_verified = 1
          AND t.is_verified = 1
          AND t.transaction_type != 'セット'
          AND tc.quantity > 0
        UNION ALL
        SELECT t.character_name, t.mutation, t.level, t.quantity, t.total_amount
        FROM trades AS t
        WHERE t.character_name = ?
          AND t.is_verified = 1
          AND t.transaction_type != 'セット'
          AND t.quantity > 0
          AND NOT EXISTS (
              SELECT 1 FROM trade_characters AS existing
              WHERE existing.trade_id = t.id
          )
        """,
        (character_name, character_name),
    ).fetchall()
    grouped: dict[str, list[PriceObservation]] = {}
    for row in rows:
        try:
            level = int(str(row[2]).strip())
            quantity = int(row[3])
            amount = parse_market_amount(row[4])
        except (TypeError, ValueError):
            continue
        if level < 1 or quantity <= 0:
            continue
        mutation = str(row[1]).strip() or "通常"
        grouped.setdefault(mutation, []).append(
            PriceObservation(
                character_name=str(row[0]),
                level=level,
                price_per_unit=price_per_unit(amount, quantity),
            )
        )
    return grouped
