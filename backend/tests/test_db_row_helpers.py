"""Characterize the shared row conversions and their established import names."""

import pytest

from backend.app.repositories import db
from backend.app.repositories.db import rows


@pytest.mark.parametrize(
    "normalize",
    [
        rows._normalize_gallery_favorite,
        rows._normalize_prompt_snippet_favorite,
        db._normalize_gallery_favorite,
        db._normalize_prompt_snippet_favorite,
    ],
    ids=["gallery", "snippet", "gallery-facade", "snippet-facade"],
)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, 1),
        (False, 0),
        (0, 0),
        (1, 1),
        (2, 1),
        (-1, 1),
        (-42, 1),
        (None, 0),
        ("", 0),
        (" \t\n", 0),
        ("1", 1),
        ("true", 1),
        ("yes", 1),
        ("on", 1),
        ("favorite", 1),
        ("favorited", 1),
        (" \tTrUe\n", 1),
        (" YES ", 1),
        (" ON ", 1),
        (" FAVORITE ", 1),
        (" FAVorited ", 1),
        ("0", 0),
        ("false", 0),
        ("no", 0),
        ("off", 0),
        ("unfavorite", 0),
        ("unfavorited", 0),
        ("y", 0),
        ("n", 0),
        ("2", 0),
        ("-1", 0),
        ("unknown", 0),
        (0.0, 0),
        (1.0, 0),
        (-1.5, 0),
        ([], 0),
        ([1], 0),
        ({}, 0),
        ({"favorite": True}, 0),
    ],
)
def test_favorite_normalization(normalize, value, expected):
    result = normalize(value)
    assert result == expected
    assert type(result) is int


@pytest.mark.parametrize(
    "escape",
    [
        rows._like_contains_param,
        rows._like_prompt_snippet_query,
        db._like_prompt_snippet_query,
    ],
    ids=["gallery", "snippet", "snippet-facade"],
)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", "%%"),
        ("plain", "%plain%"),
        (" a\tb\n ", "% a\tb\n %"),
        ("图像", "%图像%"),
        ("/", "%/%"),
        ("\\", "%\\\\%"),
        ("%", "%\\%%"),
        ("_", "%\\_%"),
        ("%_", "%\\%\\_%"),
        ("\\%_", "%\\\\\\%\\_%"),
        ("%_\\", "%\\%\\_\\\\%"),
        ("\\\\", "%\\\\\\\\%"),
        ("100%_done\\path/end", "%100\\%\\_done\\\\path/end%"),
        ("'\"", "%'\"%"),
    ],
)
def test_like_contains_escaping(escape, value, expected):
    assert escape(value) == expected
