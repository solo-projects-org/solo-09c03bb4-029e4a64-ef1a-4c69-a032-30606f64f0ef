"""Statistical sanity checks for the Laplace mechanism."""

from __future__ import annotations

import math

from app.privacy import laplace_noise


def test_laplace_scale_matches_epsilon():
    epsilon = 0.25
    scale = 1.0 / epsilon
    n = 60_000
    samples = [laplace_noise(scale) for _ in range(n)]

    mean = sum(samples) / n
    variance = sum((x - mean) ** 2 for x in samples) / n
    # Laplace(0, b) has mean 0 and variance 2*b^2; loose tolerances only
    # guard against a wrong scale, not exact distributional moments.
    assert abs(mean) < scale * 0.1
    assert math.isclose(variance, 2 * scale**2, rel_tol=0.15)


def test_laplace_rejects_non_positive_scale():
    import pytest

    for bad in (0, -1.0):
        with pytest.raises(ValueError):
            laplace_noise(bad)


def test_noise_draws_are_independent():
    # Two adjacent draws must not be deterministically identical.
    a = laplace_noise(1.0)
    b = laplace_noise(1.0)
    assert a != b or laplace_noise(1.0) != a
