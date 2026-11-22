from __future__ import annotations

import pandas as pd
import pytest

from nutriscore import schema
from nutriscore.splits import assign_splits, split_frame, stratified_sample
from tests.helpers.synthetic import make_clean_frame


def test_split_is_deterministic_and_independent_of_row_order() -> None:
    codes = pd.Series([f"{i:013d}" for i in range(2_000)])
    first = assign_splits(codes)
    shuffled = codes.sample(frac=1, random_state=3)
    second = assign_splits(shuffled).sort_index()
    assert first.equals(second)


def test_same_product_keeps_its_split_in_a_new_dataset_version() -> None:
    v1 = make_clean_frame(n_rows=500, seed=4)
    v2 = pd.concat([v1.sample(frac=0.5, random_state=1), make_clean_frame(n_rows=300, seed=5)])
    s1 = dict(zip(v1[schema.ID_COLUMN], assign_splits(v1[schema.ID_COLUMN]), strict=True))
    s2 = dict(zip(v2[schema.ID_COLUMN], assign_splits(v2[schema.ID_COLUMN]), strict=True))
    shared = set(s1) & set(s2)
    assert shared
    assert all(s1[code] == s2[code] for code in shared)


def test_split_fractions_are_70_15_15() -> None:
    codes = pd.Series([f"{i:013d}" for i in range(20_000)])
    shares = assign_splits(codes).value_counts(normalize=True)
    assert shares["train"] == pytest.approx(0.70, abs=0.02)
    assert shares["val"] == pytest.approx(0.15, abs=0.02)
    assert shares["test"] == pytest.approx(0.15, abs=0.02)


def test_hash_split_preserves_class_proportions() -> None:
    frame = make_clean_frame(n_rows=10_000, seed=6)
    overall = frame[schema.TARGET].value_counts(normalize=True)
    for part in split_frame(frame).values():
        shares = part[schema.TARGET].value_counts(normalize=True)
        assert (shares - overall).abs().max() < 0.03


def test_stratified_sample_keeps_proportions_and_size() -> None:
    frame = make_clean_frame(n_rows=5_000, seed=8)
    sample = stratified_sample(frame, 1_000, seed=1)
    assert len(sample) == 1_000
    diff = (
        sample[schema.TARGET].value_counts(normalize=True)
        - frame[schema.TARGET].value_counts(normalize=True)
    ).abs()
    assert diff.max() < 0.01
    assert len(stratified_sample(frame, 10_000, seed=1)) == len(frame)
