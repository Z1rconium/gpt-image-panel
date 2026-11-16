"""Single owner for parent image-job terminal transitions and post-terminal cleanup."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from ..core.errors import DomainError, NotFoundError
from ..repositories.db.execution import run_db_operation
from ..repositories.image_jobs import (
    cancel_generate_job_tx,
    finalize_parent_job_from_units,
    release_edit_source_reservation,
)
from .job_persistence import publish_generate_job_row_async
from .job_queue import cleanup_parent_edit_sources, trim_generate_jobs

TerminalSourcePolicy = Literal["delete", "release_only"]
DeriveParentUpdate = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None]


@dataclass(frozen=True)
class FinalizeFromUnitsOutcome:
    """Derive and write a terminal parent update from unit rows in one transaction."""

    derive: DeriveParentUpdate


@dataclass(frozen=True)
class CancelJobOutcome:
    """Cancel a parent job and its active units in one transaction."""

    message: str


TerminalOutcome = FinalizeFromUnitsOutcome | CancelJobOutcome


async def _write_terminal_outcome(
    job_id: str,
    outcome: TerminalOutcome,
) -> tuple[dict[str, Any] | None, bool]:
    if isinstance(outcome, FinalizeFromUnitsOutcome):
        return await run_db_operation(
            finalize_parent_job_from_units,
            job_id,
            derive=outcome.derive,
            metric_name="finalize_parent_image_job",
            critical=True,
        )
    if isinstance(outcome, CancelJobOutcome):
        return await run_db_operation(
            cancel_generate_job_tx,
            job_id,
            outcome.message,
            metric_name="cancel_generate_job",
            critical=True,
        )
    raise TypeError(f"Unsupported terminal outcome: {type(outcome).__name__}")


async def _settle_edit_sources(
    job_id: str,
    sources: TerminalSourcePolicy,
) -> None:
    if sources == "release_only":
        await run_db_operation(
            release_edit_source_reservation,
            job_id,
            metric_name="release_edit_source_reservation",
        )
        return
    await run_db_operation(
        cleanup_parent_edit_sources,
        job_id,
        metric_name="cleanup_parent_edit_sources",
    )


async def settle_terminal(
    job_id: str,
    outcome: TerminalOutcome,
    *,
    sources: TerminalSourcePolicy,
    dispatch_webhook: bool = True,
    operation: str | None = None,
    require_transition: bool = False,
) -> dict[str, Any] | None:
    """Apply a critical terminal write, publish the row, trim history, and settle sources."""
    row, transitioned = await _write_terminal_outcome(job_id, outcome)
    if row is None:
        if require_transition:
            raise NotFoundError("Generation job not found")
        return None
    if require_transition and not transitioned:
        raise DomainError("Generation job already finished", status_code=409)

    job = await publish_generate_job_row_async(
        row,
        dispatch_webhook=bool(dispatch_webhook and transitioned),
    )
    await run_db_operation(trim_generate_jobs, metric_name="trim_generate_jobs")
    effective_operation = str(operation or row.get("operation") or "generation")
    if effective_operation == "edit":
        await _settle_edit_sources(job_id, sources)
    return job
