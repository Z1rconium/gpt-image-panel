ACTIVE_GENERATE_JOB_STATUSES: frozenset[str] = frozenset({"queued", "running"})
ERROR_GENERATE_JOB_STATUSES: frozenset[str] = frozenset(
    {"partial_failure", "error", "upstream_error"}
)
# Independent bound for checkpoint-based takeovers: a leased unit that already
# holds a remote checkpoint may be reclaimed past the generation attempt limit
# this many times before it is interrupted, so polling recovery is never
# mistaken for a new upstream generation. Kept internal on purpose.
IMAGE_JOB_UNIT_MAX_RECOVERIES = 5
