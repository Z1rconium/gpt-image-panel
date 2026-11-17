import asyncio
import json

from backend.tests.support.contract import *  # noqa: F403

from backend.app.core.utils import utc_now
from backend.app.services import gallery_job_sse


async def _collect_stream(body) -> list[dict]:
    raw = []
    async for chunk in body.chunks:
        raw.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
    frames = []
    for block in "".join(raw).split("\n\n"):
        if not block.strip() or block.lstrip().startswith(":"):
            continue
        fields = {}
        for line in block.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fields[k.strip()] = v.lstrip()
        if "data" in fields:
            frames.append({"event": fields.get("event"), "data": json.loads(fields["data"])})
    return frames


def test_gallery_feed_emits_event_to_active_subscriber():
    kind = "export"
    job_id = "job-sse-test"
    feed = gallery_job_sse.gallery_feed(kind)

    async def scenario():
        async with feed.subscribe(job_id, start_poller=False) as queue:
            feed.publish(
                job_id,
                {"event": "export", "data": {"job_id": job_id, "status": "running"}},
            )
            return queue.get_nowait()

    event = asyncio.run(scenario())
    assert event["event"] == "export"
    assert event["data"]["job_id"] == job_id
    assert event["data"]["status"] == "running"


def test_stream_gallery_job_emits_initial_and_terminal_update(tmp_path):
    _configure_runtime(tmp_path)
    ts0 = utc_now()
    ts1 = "2099-01-01T00:00:01+00:00"
    job_id = "gallery-stream-public-1"
    coordination_repo.create_gallery_job(
        job_id=job_id,
        kind="export",
        status="running",
        stage="zipping",
        message="Exporting",
        progress=20,
        requested_count=1,
        exported_count=0,
        missing_count=0,
        created_at=ts0,
        updated_at=ts0,
    )

    async def scenario():
        pushed = False

        async def is_disconnected():
            nonlocal pushed
            if not pushed:
                pushed = True
                gallery_job_sse.gallery_feed("export").publish(
                    job_id,
                    {
                        "event": "export",
                        "data": {
                            "job_id": job_id,
                            "status": "success",
                            "stage": "completed",
                            "progress": 100,
                            "updated_at": ts1,
                        },
                    },
                )
            return False

        body = await gallery_job_sse.stream_gallery_job(
            kind="export",
            job_id=job_id,
            client_ip="127.0.0.1",
            is_disconnected=is_disconnected,
            event_name="export",
            terminal_statuses={"success", "error"},
            payload_builder=lambda job: dict(job),
            not_found_detail="Gallery export job not found",
        )
        return await _collect_stream(body)

    frames = asyncio.run(scenario())
    assert [frame["data"]["status"] for frame in frames] == ["running", "success"]


def test_stream_gallery_job_resolves_kind_descriptor_defaults(tmp_path):
    _configure_runtime(tmp_path)
    ts0 = utc_now()
    job_id = "gallery-stream-descriptor-1"
    coordination_repo.create_gallery_job(
        job_id=job_id,
        kind="sync",
        status="success",
        stage="completed",
        message="Done",
        progress=100,
        total_count=1,
        compared_count=1,
        uploaded_count=1,
        created_at=ts0,
        updated_at=ts0,
        payload={"dry_run": False},
    )

    async def scenario():
        async def is_disconnected():
            return False

        body = await gallery_job_sse.stream_gallery_job(
            kind="sync",
            job_id=job_id,
            client_ip="127.0.0.1",
            is_disconnected=is_disconnected,
        )
        return await _collect_stream(body)

    frames = asyncio.run(scenario())
    assert len(frames) == 1
    assert frames[0]["event"] == "sync"
    assert frames[0]["data"]["status"] == "success"
    assert frames[0]["data"]["uploaded_count"] == 1
