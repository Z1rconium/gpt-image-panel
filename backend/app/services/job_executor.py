"""Stable entry points for image workers and parent-job aggregation."""

from .image_job_aggregate import aggregate_parent_image_job
from .image_unit_execution import ImageUnitExecutionContext

__all__ = ["aggregate_parent_image_job", "run_claimed_image_unit"]


async def run_claimed_image_unit(unit: dict, worker_id: str):
    await ImageUnitExecutionContext(unit, worker_id).execute()
