"""Market price calculation utilities."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Sequence


@dataclass(frozen=True)
class PriceObservation:
    """One normalized price observation."""

    character_name: str
    level: int
    price_per_unit: float


@dataclass(frozen=True)
class MarketEstimate:
    """Calculated market estimate for one character."""

    character_name: str
    level: int
    price: float
    transaction_count: int
    confidence: str
    is_estimated: bool


def price_per_unit(total_amount: float, quantity: int) -> float:
    """Convert a total transaction price into a per-unit price."""
    if quantity <= 0:
        raise ValueError("個数は1以上である必要があります。")
    if total_amount < 0:
        raise ValueError("金額は0以上である必要があります。")

    return total_amount / quantity


def _median_without_extreme_outliers(
    values: Sequence[float],
) -> float:
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

    filtered = [
        value for value in ordered
        if lower <= value <= upper
    ]

    if not filtered:
        filtered = ordered

    return float(median(filtered))


def estimate_level_one_price(
    observations: Sequence[PriceObservation],
) -> MarketEstimate:
    """
    Estimate the level-1 market price.

    Level 1 observations are used directly.
    Higher-level observations are currently kept separate until
    enough data exists to safely estimate the level curve.
    """
    if not observations:
        raise ValueError("価格データがありません。")

    level_one = [
        observation.price_per_unit
        for observation in observations
        if observation.level == 1
    ]

    if level_one:
        price = _median_without_extreme_outliers(level_one)
        count = len(level_one)

        if count >= 10:
            confidence = "高"
        elif count >= 5:
            confidence = "中"
        else:
            confidence = "低"

        return MarketEstimate(
            character_name=observations[0].character_name,
            level=1,
            price=price,
            transaction_count=count,
            confidence=confidence,
            is_estimated=False,
        )

    return MarketEstimate(
        character_name=observations[0].character_name,
        level=1,
        price=0.0,
        transaction_count=0,
        confidence="データ不足",
        is_estimated=True,
    )


def load_character_observations(
    connection,
    character_name: str,
) -> list[PriceObservation]:
    """Load usable non-set trades for one character from SQLite."""
    rows = connection.execute(
        """
        SELECT character_name, level, quantity, total_amount
        FROM trades
        WHERE character_name = ?
          AND transaction_type != 'セット'
          AND is_verified = 1
          AND quantity > 0
          AND total_amount >= 0
        ORDER BY id
        """,
        (character_name,),
    ).fetchall()

    observations: list[PriceObservation] = []

    for row in rows:
        stored_name = str(row[0])
        level_text = str(row[1]).strip()

        try:
            level = int(level_text)
        except (TypeError, ValueError):
            continue

        if level < 1:
            continue

        quantity = int(row[2])
        total_amount = float(row[3])

        if quantity <= 0 or total_amount < 0:
            continue

        observations.append(
            PriceObservation(
                character_name=stored_name,
                level=level,
                price_per_unit=price_per_unit(
                    total_amount,
                    quantity,
                ),
            )
        )

    return observations
def load_verified_character_observations(
    connection,
    character_name: str,
    mutation: str = "",
) -> list[PriceObservation]:
    """Load verified non-set observations for one character and mutation."""
    rows = connection.execute(
        """
        SELECT
            tc.character_name,
            tc.level,
            tc.quantity,
            t.total_amount
        FROM trade_characters AS tc
        JOIN trades AS t
          ON t.id = tc.trade_id
        WHERE tc.character_name = ?
          AND tc.mutation = ?
          AND tc.is_verified = 1
          AND t.is_verified = 1
          AND t.transaction_type != 'セット'
          AND tc.quantity > 0
          AND t.total_amount >= 0
        ORDER BY t.id, tc.position
        """,
        (character_name, mutation),
    ).fetchall()

    observations: list[PriceObservation] = []

    for row in rows:
        stored_name = str(row[0])
        level_text = str(row[1]).strip()

        try:
            level = int(level_text)
        except (TypeError, ValueError):
            continue

        if level < 1:
            continue

        quantity = int(row[2])
        total_amount = float(row[3])

        if quantity <= 0 or total_amount < 0:
            continue

        observations.append(
            PriceObservation(
                character_name=stored_name,
                level=level,
                price_per_unit=price_per_unit(
                    total_amount,
                    quantity,
                ),
            )
        )

    return observations


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
