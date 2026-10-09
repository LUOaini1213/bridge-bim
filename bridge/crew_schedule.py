"""Deterministic feasible crew allocation; neither policy is a global optimizer.

Only placement work reserves a crew. Curing and acceptance holds do not reserve
it. Wall-time intervals span the intervening nonworking shifts, where this
calendar cannot schedule another placement either.
"""


class CrewPool:
    def __init__(self, calendar, counts, origin, strategy):
        if strategy not in ("fixed_route", "earliest_gap"):
            raise ValueError("unknown crew strategy: " + str(strategy))
        self.calendar, self.counts = calendar, counts
        self.origin, self.strategy, self.intervals = origin, strategy, {}

    def reserve(self, deck, work_class, earliest, hours, task_id):
        """Return start, finish, crew ID and explicit allocation wait reasons."""
        key = (deck, work_class)
        crews = self.intervals.setdefault(key, [[] for _ in range(self.counts[deck][work_class])])
        if self.strategy == "fixed_route":
            # Keep the original span-order route and its availability tie-break.
            index = min(range(len(crews)), key=lambda i: (crews[i][-1][1] if crews[i] else self.origin, i))
            tail = crews[index][-1] if crews[index] else (self.origin, self.origin, "")
            start = self.calendar.start(max(earliest, tail[1]))
            reasons = ["fixed_route: wait for " + tail[2]] if tail[2] and tail[1] > earliest else []
        else:
            candidates = []
            for index, reservations in enumerate(crews):
                start, reasons = self.calendar.start(max(earliest, self.origin)), []
                for occupied_start, occupied_end, predecessor in reservations:
                    finish = self.calendar.end(start, hours)
                    if finish <= occupied_start:
                        break
                    if start < occupied_end and finish > occupied_start:
                        start = self.calendar.start(occupied_end)
                        reasons.append("earliest_gap: occupied by " + predecessor)
                candidates.append((start, index, reasons))
            start, index, reasons = min(candidates, key=lambda v: (v[0], v[1]))
        if start > earliest and not reasons:
            reasons = ["calendar: next working shift"]
        finish = self.calendar.end(start, hours)
        crews[index].append((start, finish, task_id))
        crews[index].sort(key=lambda v: (v[0], v[1], v[2]))
        return start, finish, "%s:%s:%d" % (deck, work_class, index + 1), reasons
