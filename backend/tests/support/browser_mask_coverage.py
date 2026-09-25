"""Probe the real edit admission and gallery coverage for a Playwright export.

Reads JSON with base64 ``image`` and ``mask`` from stdin; writes one JSON line
with the coverage persisted by the backend. No network gateway is contacted.
"""

import base64
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.app import main as backend_main
from backend.app.repositories.gallery import mutations, queries
from backend.tests.support.contract import _configure_runtime, _test_client, _wait_for_job


def main() -> None:
    payload = json.load(sys.stdin)
    image = base64.b64decode(payload["image"], validate=True)
    mask = base64.b64decode(payload["mask"], validate=True)
    image_id = "browser-mask-coverage"

    async def fake_edit_api(api_url, api_key, request, image_sources, api_preset_name=None,
                            progress=None, socks5_proxy=None, persist_gallery_entry=None,
                            mask_source=None, mask_coverage=None):
        entry = await mutations.add_to_gallery_async(
            image_bytes=image,
            image_id=image_id,
            prompt=request.prompt,
            size=request.size,
            filename=f"{image_id}.png",
            metadata={"api_path": "/v1/images/edits", "api_preset_name": api_preset_name,
                      "mask_coverage": mask_coverage},
        )
        return [entry]

    original = backend_main.proxy.call_image_edit_api
    with tempfile.TemporaryDirectory() as directory:
        _configure_runtime(Path(directory))
        backend_main.proxy.call_image_edit_api = fake_edit_api
        try:
            with _test_client() as client:
                response = client.post(
                    "/api/edits",
                    data={"prompt": "browser coverage probe", "model": "gpt-image-2"},
                    files={"image": ("source.png", image, "image/png"),
                           "mask": ("mask.png", mask, "image/png")},
                )
                if response.status_code != 202:
                    raise RuntimeError(f"edit admission failed: {response.status_code} {response.text}")
                job = _wait_for_job(client, response.json()["job_id"])
                if job["status"] != "success":
                    raise RuntimeError(f"edit job failed: {job}")
                coverage = queries.get_gallery_entry(image_id).mask_coverage
                print(json.dumps({"mask_coverage": coverage}))
        finally:
            backend_main.proxy.call_image_edit_api = original


if __name__ == "__main__":
    main()
