"""The code corpus is data somebody checked by hand. These tests hold the checks.

A transcription slip in `fbcreview/codes/` is the most expensive kind of bug this
product can have: every rule downstream is correct, and the answer is wrong on a
VERIFIED card. Table 506.2 shipped with every sprinklered value too small — S1
and SM carried at 3 x and 2 x the non-sprinklered factor instead of the printed
4 x and 3 x — and printed "within the 25,500 SF Table 506.2 allows" for an A-3,
Type III-B, sprinklered storey the code allows 38,000.

So the structural facts about a table are asserted here, not just its cells,
and each spot-check cites where the number was confirmed.
"""
from __future__ import annotations

import pytest

from fbcreview.codes import fbc2023 as C


def _rows():
    return [(k, v) for k, v in C._AREA.items() if v[0] != C.UNLIMITED]


@pytest.mark.parametrize("key,row", _rows(), ids=[f"{g}/{t}" for (g, t), _ in _rows()])
def test_table_506_2_sprinklered_columns_are_four_and_three_times_ns(key, row):
    ns, s1, sm = row
    assert s1 == 4 * ns, f"{key}: S1 {s1} is not 4 x NS {ns}"
    assert sm == 3 * ns, f"{key}: SM {sm} is not 3 x NS {ns}"


@pytest.mark.parametrize("group,ctype,sprinklered,stories,expected", [
    # ICC 2021 IBC Heights & Areas (EDUCODE 2024) worked example: Group B,
    # Type II-B, one storey, sprinklered → 92,000 from Table 506.2.
    ("B", "II-B", True, 1, 92000),
    # The Sculpted Hot Pilates building: A-3, III-B, one storey, sprinklered.
    ("A-3", "III-B", True, 1, 38000),
    ("A-3", "III-B", False, 1, 9500),
    ("A-3", "III-B", True, 2, 28500),
    ("S-1", "II-B", True, 1, 70000),
    ("M", "V-B", True, 3, 27000),
])
def test_table_506_2_spot_checks(group, ctype, sprinklered, stories, expected):
    assert C.area_limit_sf(group, ctype, sprinklered, stories) == expected


def test_a_3_and_a_1_rows_are_not_confused():
    """A-1 prints 8,500 for II-B and III-B; A-3 prints 9,500 for both."""
    assert C.area_limit_sf("A-3", "II-B", False) == 9500
    assert C.area_limit_sf("A-3", "III-B", False) == 9500


def test_table_504_3_heights_for_the_common_groups():
    assert C.height_limit_ft("A-3", "III-B", True) == 75
    assert C.height_limit_ft("B", "II-B", False) == 55
    assert C.height_limit_ft("B", "V-B", True) == 60


def test_the_dead_end_exception_does_not_reach_group_a():
    """1020.5 Exc. 2 names the groups it covers; Group A is not one of them."""
    assert C.dead_end_ft("A-3", True) == 20
    assert C.dead_end_ft("B", True) == 50
