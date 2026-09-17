import asyncio

from backend.app.services import gallery_job_sse


def test_publish_gallery_job_sse_emits_event_to_active_subscriber():
    kind = "export"
    job_id = "job-sse-test"
    queue: asyncio.Queue = asyncio.Queue()
    gallery_job_sse._get_gallery_job_subscribers(kind)[job_id] = {queue}
    try:
        gallery_job_sse._publish_gallery_job_sse(
            {"kind": kind, "job_id": job_id, "status": "running"}
        )
    finally:
        gallery_job_sse._get_gallery_job_subscribers(kind).pop(job_id, None)

    event = queue.get_nowait()
    assert event["event"] == "export"
    assert event["data"]["job_id"] == job_id
    assert event["data"]["status"] == "running"
