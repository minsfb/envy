"""Protected research capacity; pure calculations over the retained journal.

This module does not certify research completion. The bundle must verify the
graph checkpoint authorizing release before asking the budget journal to record
it. Activity is attested model work, not only the duration of provider calls.
"""

import math


DEFAULTS = {"baseline_percent": 30, "followup_percent": 50,
            "extension_percent": 20, "minimum_followup_rounds": 2}
METRICS = ("active_minutes", "agent_minutes", "search_attempts",
           "read_attempts", "assessed_artifacts")
PURPOSES = ("research", "coordination", "repair", "coverage-review")
UNCLASSIFIED = "unclassified"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def config(contract):
    flag = contract.get("requirements", {}).get("protected_followups", False)
    require(type(flag) is bool, "protected_followups must be boolean")
    if not flag:
        require("research_allocation" not in contract,
                "research_allocation requires protected_followups")
        return None
    require(contract.get("requirements", {}).get("research_budget") is True,
            "protected_followups requires research_budget")
    value = contract.get("research_allocation", {})
    require(isinstance(value, dict) and set(value) <= set(DEFAULTS),
            "unknown research allocation fields")
    value = dict(DEFAULTS, **value)
    shares = [value[key] for key in DEFAULTS if key.endswith("_percent")]
    require(all(type(share) in (int, float) and math.isfinite(share) and share > 0
                for share in shares) and math.isclose(sum(shares), 100, rel_tol=0, abs_tol=1e-9),
            "research allocation shares must be positive and sum to 100")
    rounds = value["minimum_followup_rounds"]
    require(type(rounds) is int and 2 <= rounds <= 10,
            "minimum_followup_rounds must be an integer from 2 through 10")
    return value


def shares(configuration):
    rounds = configuration["minimum_followup_rounds"]
    result = {"baseline": configuration["baseline_percent"] / 100}
    result.update({"followup-" + str(i): configuration["followup_percent"] / 100 / rounds
                   for i in range(1, rounds + 1)})
    result["extension"] = configuration["extension_percent"] / 100
    return result


def union_minutes(intervals):
    merged = []
    for start, end in sorted(intervals):
        end = max(start, end)
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return sum(end - start for start, end in merged) / 60


def accounting(state, current):
    """Attribute observed events only; keep missing historical labels explicit."""
    active, intervals, charged, assessments = {}, [], [], set()
    unmeasured = []
    for event in state["events"]:
        kind = event["type"]
        if kind == "activity-start":
            active[event["actor"]] = event
        elif kind == "activity-stop":
            start = active.pop(event["actor"])
            intervals.append((start, event["at"]))
        elif kind in ("admit", "assess"):
            values = dict.fromkeys(METRICS, 0)
            if kind == "admit":
                values["search_attempts" if event["kind"] == "search" else "read_attempts"] = 1
            if kind == "assess" or (event["kind"] == "read" and event["assess"]):
                if event["source_id"] not in assessments:
                    assessments.add(event["source_id"])
                    values["assessed_artifacts"] = 1
            charged.append((event, values))
            if event.get("actor") not in active:
                unmeasured.append(event.get("id"))
    intervals.extend((start, current) for start in active.values())
    result = {"active_activity": {actor: {key: event.get(key, UNCLASSIFIED)
                                        for key in ("allocation", "purpose", "branch")}
                                  for actor, event in active.items()},
              "unmeasured_admission_events": unmeasured,
              "allocation_time_basis": "Each bucket reports its own interval union. Concurrent buckets may overlap; bucket active minutes do not add to global active minutes."}
    for dimension in ("actor", "purpose", "allocation", "branch"):
        groups = {}
        for start, end in intervals:
            key = start.get(dimension, UNCLASSIFIED)
            group = groups.setdefault(key, {"intervals": [], **dict.fromkeys(METRICS, 0)})
            group["intervals"].append((start["at"], end))
            group["agent_minutes"] += max(0, end - start["at"]) / 60
        for event, values in charged:
            key = event.get(dimension, UNCLASSIFIED)
            group = groups.setdefault(key, {"intervals": [], **dict.fromkeys(METRICS, 0)})
            for metric in METRICS:
                group[metric] += values[metric]
        for group in groups.values():
            group["active_minutes"] = union_minutes(group.pop("intervals"))
        result["by_activity_branch" if dimension == "branch" else "by_" + dimension] = groups
    return result


def activity_fields(configuration, allocation, purpose, branch):
    require(all(isinstance(value, str) and value.strip() for value in (allocation, purpose, branch)),
            "protected activity needs allocation, purpose and branch")
    require(allocation in shares(configuration), "unknown protected research allocation")
    require(purpose in PURPOSES, "unknown research activity purpose")
    require(purpose != "coverage-review" or allocation == "extension",
            "coverage-review uses extension allocation")
    return {"allocation": allocation, "purpose": purpose, "branch": branch}


def lineage(configuration, allocation, purpose, generation):
    if purpose == "coverage-review":
        require(allocation == "extension", "coverage-review uses extension allocation")
        return
    expected = ("baseline" if generation == 0 else
                "followup-" + str(generation) if generation <= configuration["minimum_followup_rounds"] else
                "extension")
    require(allocation == expected,
            "research allocation must follow its actual generation: " + expected)


def _base_capacities(configuration, limits):
    fractions = shares(configuration)
    result = {key: {} for key in fractions}
    for metric in METRICS:
        for key, fraction in fractions.items():
            if key != "extension":
                value = limits[metric] * fraction
                result[key][metric] = value if metric.endswith("minutes") else math.floor(value)
        result["extension"][metric] = limits[metric] - sum(
            values[metric] for key, values in result.items() if key != "extension")
    return result


def capacities(configuration, limits, state, totals):
    """Released reserves share one pool; no per-bucket duplication of credit.

    Each unreleased bucket may use its guarantee plus available shared capacity;
    its excess usage debits the pool, never another bucket's guarantee.
    A release freezes already-charged usage. Subsequent usage in that bucket
    competes with extension for the pool. Amendments adjust capacity, never the
    frozen or post-release usage. Assessment slots are charged once per identity;
    active time unions conservatively consume each overlapping bucket's share.
    """
    base = _base_capacities(configuration, limits)
    released = {event["allocation"]: event for event in state["events"]
                if event["type"] == "allocation-release"}
    require(set(released) <= set(base) - {"extension"}, "unknown released allocation")
    used = {key: totals["by_allocation"].get(key, dict.fromkeys(METRICS, 0)) for key in base}
    pool_cap, pool_spent = {}, {}
    for metric in METRICS:
        pool_cap[metric] = limits[metric] - sum(base[key][metric] for key in base
                                              if key != "extension" and key not in released)
        pool_cap[metric] -= sum(event["spent"][metric] for event in released.values())
        pool_spent[metric] = used["extension"][metric] + sum(
            max(0, used[key][metric] - (released[key]["spent"][metric] if key in released else base[key][metric]))
            for key in base if key != "extension")
    pool_remaining = {metric: pool_cap[metric] - pool_spent[metric] for metric in METRICS}
    buckets = {}
    for key in base:
        pooled = key == "extension" or key in released
        guaranteed = {metric: 0 if pooled else max(0, base[key][metric] - used[key][metric])
                      for metric in METRICS}
        remaining = {metric: guaranteed[metric] + pool_remaining[metric] for metric in METRICS}
        buckets[key] = {"funding": "shared-pool" if pooled else "protected-reserve-and-shared-pool",
                        "base_capacity": base[key], "spent": used[key], "remaining": remaining,
                        "guaranteed_remaining": guaranteed, "shared_available": dict(pool_remaining),
                        "released_at_checkpoint": released[key]["checkpoint"] if key in released else None}
    return {"buckets": buckets, "shared_pool": {"capacity": pool_cap, "spent": pool_spent,
                                                "remaining": pool_remaining},
            "allocation_policy": configuration,
            "overruns": [{"allocation": key, "metric": metric, "over_by": -value}
                         for key, bucket in buckets.items() for metric, value in bucket["remaining"].items()
                         if value < -1e-9]}


def remaining_seconds(allocation, limits, totals, capacity):
    """Clamp full activity against global clocks and concurrent shared borrowing.

    For each time metric, forecast each active bucket's exhaustion of its own
    reserve. All excess burns one shared pool. This avoids promising the same
    pool headroom independently to several simultaneously active buckets.
    """
    actors = totals["active_activity"]
    rates = {}
    for entry in actors.values():
        rates[entry["allocation"]] = rates.get(entry["allocation"], 0) + 1
    if allocation not in rates:
        rates[allocation] = 1
    remaining = min(limits["active_minutes"] - totals["active_minutes"],
                    (limits["agent_minutes"] - totals["agent_minutes"]) / sum(rates.values()))
    for metric in ("active_minutes", "agent_minutes"):
        pool = capacity["shared_pool"]["remaining"][metric]
        # Clock closure can overshoot shared capacity. Retain that debt in each
        # bucket's usable remainder, but do not freeze still-funded reserves.
        # With no shared headroom, forecasting permits no further borrowing.
        debt, pool = min(0, pool), max(0, pool)
        def forecast(duration):
            return sum(max(0, duration * (1 if metric == "active_minutes" else count)
                           - max(0, capacity["buckets"][key]["guaranteed_remaining"][metric] + debt))
                       for key, count in rates.items())
        if forecast(remaining) > pool:
            low, high = 0, remaining
            for _ in range(60):
                midpoint = (low + high) / 2
                if forecast(midpoint) <= pool:
                    low = midpoint
                else:
                    high = midpoint
            remaining = low
    return remaining * 60


def validate_amendment(old, new, state):
    before, after = config(old), config(new)
    require(not before or after, "cannot disable protected followups")
    spending = [event for event in state["events"]
                if event["type"] in ("activity-start", "admit", "assess", "allocation-release")]
    if before and spending:
        require(before == after, "cannot change allocation percentages or rounds after activity")
    if not before and after:
        require(not spending, "cannot introduce protected followups after unclassified spending")
    return after
