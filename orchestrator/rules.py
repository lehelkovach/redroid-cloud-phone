"""Time-based follow-up rules for procedure runs.

A procedure is a straight line: open, type, swipe. Engagement is not — "message
a match an hour after it happens" is a rule that fires later, on state the
procedure produced.

Everything here is pure: it takes state and a clock and returns what is *due*.
Nothing sends. The caller sends, and only after approval, which is why
`due_followups` returns intents rather than performing them.

Policy this encodes (see docs/RUNTIME-SPLIT.md and the OSLO non-goals):

- One message per match, ever. No drip campaigns.
- Nothing before `delay_s` — the point is to not look like a bot.
- Outbound messaging is approval-gated; `plan_followups` marks every intent
  `needs_approval` until a caller passes `approve=True`.
- A per-run cap, so a bug cannot fan out across a whole match list.
"""

DEFAULT_DELAY_S = 3600
DEFAULT_MAX_PER_RUN = 3


class SwipeBudget:
    """Caps automated swiping. A runaway loop here is the abuse case."""

    def __init__(self, max_swipes=20, max_likes=10):
        if max_swipes < 0 or max_likes < 0:
            raise ValueError("budgets must be non-negative")
        self.max_swipes = max_swipes
        self.max_likes = max_likes
        self.swipes = 0
        self.likes = 0

    @property
    def exhausted(self):
        return self.swipes >= self.max_swipes or self.likes >= self.max_likes

    def remaining(self):
        return {
            "swipes": max(0, self.max_swipes - self.swipes),
            "likes": max(0, self.max_likes - self.likes),
        }

    def record(self, liked):
        if self.exhausted:
            raise RuntimeError("swipe budget exhausted")
        self.swipes += 1
        if liked:
            self.likes += 1


def due_followups(matches, now, delay_s=DEFAULT_DELAY_S, already_messaged=(),
                  max_per_run=DEFAULT_MAX_PER_RUN):
    """Matches old enough to warrant one follow-up.

    `matches` is a list of {"id": str, "matched_at": epoch_seconds}.
    Oldest first, capped, skipping anyone already messaged.
    """
    messaged = set(already_messaged)
    ready = [
        match for match in matches
        if match.get("id") not in messaged
        and now - float(match.get("matched_at", now)) >= delay_s
    ]
    ready.sort(key=lambda m: float(m.get("matched_at", 0)))
    return ready[:max_per_run]


def plan_followups(matches, now, template, delay_s=DEFAULT_DELAY_S,
                   already_messaged=(), max_per_run=DEFAULT_MAX_PER_RUN,
                   approve=False):
    """Turn due matches into message intents. Never sends."""
    if "{name}" not in template and "{id}" not in template:
        # A template with no address makes every match receive identical text,
        # which is the spam shape this rule exists to avoid.
        raise ValueError("template must personalize with {name} or {id}")

    intents = []
    for match in due_followups(matches, now, delay_s, already_messaged, max_per_run):
        match_id = match.get("id")
        intents.append({
            "to": match_id,
            "text": template.format(name=match.get("name", match_id), id=match_id),
            "waited_s": int(now - float(match.get("matched_at", now))),
            "needs_approval": not approve,
        })
    return intents
