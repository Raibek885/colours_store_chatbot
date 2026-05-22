from typing import Any

from catalog_db import count_promotions, list_promotions


class PromotionTools:
    def list_current_promotions(self, limit: int = 8) -> dict[str, Any]:
        promotions = list_promotions(limit=limit)
        return {
            "promotions_count_in_db": count_promotions(),
            "promotions": promotions,
            "freshness_note": (
                "Promotions are parsed from the public promotions page. "
                "If ends_at is empty, do not claim the promotion is guaranteed active; "
                "say it is published on the site and recommend checking details."
            ),
        }
