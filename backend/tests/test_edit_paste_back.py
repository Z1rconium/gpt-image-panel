from io import BytesIO

from PIL import Image

from backend.app.core import settings as config
from backend.app.services.edit_paste_back import paste_back_image


def _bytes(image: Image.Image, fmt: str = "PNG") -> bytes:
    output = BytesIO()
    image.save(output, format=fmt)
    return output.getvalue()


def _inputs(tmp_path, size=(128, 128)):
    primary = Image.new("RGB", size, (30, 40, 50))
    mask = Image.new("RGBA", size, (0, 0, 0, 255))
    for y in range(size[1] // 3, 2 * size[1] // 3):
        for x in range(size[0] // 3, 2 * size[0] // 3):
            mask.putpixel((x, y), (0, 0, 0, 0))
    primary_path = tmp_path / "primary.png"
    mask_path = tmp_path / "mask.png"
    primary.save(primary_path)
    mask.save(mask_path)
    return primary_path, mask_path


def test_paste_back_preserves_outside_and_takes_inside(tmp_path):
    primary, mask = _inputs(tmp_path)
    result = _bytes(Image.new("RGB", (128, 128), (30, 40, 50)))
    edited = Image.open(BytesIO(result)).copy()
    for y in range(43, 85):
        for x in range(43, 85):
            edited.putpixel((x, y), (200, 100, 80))
    outcome = paste_back_image(_bytes(edited), primary, mask)
    output = Image.open(BytesIO(outcome.image_bytes))
    assert outcome.status == "applied"
    assert output.getpixel((0, 0)) == (30, 40, 50)
    assert output.getpixel((64, 64)) == (200, 100, 80)
    assert 30 <= output.getpixel((42, 64))[0] <= 200
    for y in range(128):
        for x in range(128):
            if x < 25 or x > 103 or y < 25 or y > 103:
                assert output.getpixel((x, y)) == (30, 40, 50)


def test_paste_back_resizes_result_and_records_scale(tmp_path):
    primary, mask = _inputs(tmp_path)
    source = _bytes(Image.new("RGB", (64, 64), (30, 40, 50)))
    outcome = paste_back_image(source, primary, mask)
    assert outcome.status == "applied"
    assert outcome.metadata["paste_back_scale"] == 2
    assert Image.open(BytesIO(outcome.image_bytes)).size == (128, 128)


def test_paste_back_skips_aspect_and_drift_without_reencoding(tmp_path):
    primary, mask = _inputs(tmp_path)
    wrong_shape = _bytes(Image.new("RGB", (64, 80), (30, 40, 50)))
    outcome = paste_back_image(wrong_shape, primary, mask)
    assert outcome.status == "skipped:aspect_mismatch"
    assert outcome.image_bytes is wrong_shape
    changed = _bytes(Image.new("RGB", (128, 128), (200, 200, 200)))
    outcome = paste_back_image(changed, primary, mask)
    assert outcome.status == "skipped:keep_region_changed"
    assert outcome.image_bytes is changed


def test_paste_back_keeps_alpha_and_icc(tmp_path):
    primary, mask = _inputs(tmp_path)
    source = Image.new("RGBA", (128, 128), (30, 40, 50, 255))
    source.putpixel((64, 64), (200, 100, 80, 0))
    outcome = paste_back_image(_bytes(source), primary, mask, background="transparent")
    output = Image.open(BytesIO(outcome.image_bytes))
    assert outcome.status == "applied"
    assert output.mode == "RGBA"
    assert output.getpixel((64, 64)) == (200, 100, 80, 0)


def test_paste_back_skips_oversized_output(tmp_path, monkeypatch):
    primary, mask = _inputs(tmp_path)
    result = _bytes(Image.new("RGB", (128, 128), (30, 40, 50)))
    monkeypatch.setattr(config, "MAX_FILE_SIZE_MB", 0)
    outcome = paste_back_image(result, primary, mask)
    assert outcome.status == "skipped:too_large"
    assert outcome.image_bytes is result


def test_paste_back_keeps_primary_icc_profile(tmp_path):
    primary, mask = _inputs(tmp_path)
    profile = b"test-icc-profile"
    Image.new("RGB", (128, 128), (30, 40, 50)).save(primary, format="PNG", icc_profile=profile)
    result = _bytes(Image.new("RGB", (128, 128), (30, 40, 50)))
    outcome = paste_back_image(result, primary, mask)
    with Image.open(BytesIO(outcome.image_bytes)) as output:
        assert output.info["icc_profile"] == profile


def test_paste_back_with_jpeg_primary_and_png_result(tmp_path):
    primary, mask = _inputs(tmp_path)
    jpeg_path = tmp_path / "primary.jpg"
    Image.new("RGB", (128, 128), (30, 40, 50)).save(jpeg_path, format="JPEG", quality=100)
    result = _bytes(Image.new("RGB", (128, 128), (30, 40, 50)))
    outcome = paste_back_image(result, jpeg_path, mask)
    with Image.open(BytesIO(outcome.image_bytes)) as output:
        assert outcome.status == "applied"
        assert output.format == "PNG"
        assert output.size == (128, 128)


def test_paste_back_applies_when_kept_area_is_too_small_for_guard(tmp_path):
    primary, _ = _inputs(tmp_path)
    mask = tmp_path / "almost-all.png"
    image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    image.putpixel((0, 0), (0, 0, 0, 255))
    image.save(mask)
    result = _bytes(Image.new("RGB", (128, 128), (200, 100, 80)))
    outcome = paste_back_image(result, primary, mask)
    assert outcome.status == "applied"
