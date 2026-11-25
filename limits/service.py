"""How often something may be done, with short bursts (the GCRA algorithm)."""

import math
from dataclasses import dataclass

from django.db import connection

# One statement, so that requests at the same time cannot all be the last allowed.
TAKE = """
INSERT INTO limits_bucket AS b (name, clear_at)
VALUES (%(name)s, clock_timestamp() + %(step)s * interval '1 second')
ON CONFLICT (name) DO UPDATE
SET clear_at = greatest(b.clear_at, clock_timestamp())
    + %(step)s * interval '1 second'
WHERE b.clear_at <= clock_timestamp() + %(room)s * interval '1 second'
RETURNING 1
"""
WAIT = """
SELECT extract(epoch FROM clear_at - clock_timestamp()) - %(room)s
FROM limits_bucket WHERE name = %(name)s
"""


class TooMany(Exception):
    def __init__(self, wait: int) -> None:
        super().__init__(f"Wait {wait} s.")
        self.wait = wait


@dataclass(frozen=True)
class Limit:
    per_second: float
    burst: int

    def take(self, name: str) -> None:
        step = 1 / self.per_second
        asked: dict[str, str | float] = {
            "name": name,
            "step": step,
            "room": (self.burst - 1) * step,
        }
        with connection.cursor() as cursor:
            cursor.execute(TAKE, asked)
            if cursor.fetchone():
                return
            cursor.execute(WAIT, asked)
            row = cursor.fetchone()
        raise TooMany(max(math.ceil(row[0]), 1) if row else 1)
