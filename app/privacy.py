"""Differential-privacy primitives.

Each query releases a count with independent Laplace noise of scale
``1 / epsilon``. A process-wide :class:`~random.Random` guarded by a lock is
used so noise draws are thread-safe under the FastAPI thread pool while the
global RNG state stays seeded once from operating-system entropy.
"""

from __future__ import annotations

import math
import random
import threading

_rng = random.Random()
_rng_lock = threading.Lock()


def laplace_noise(scale: float) -> float:
    """Draw one independent sample from Laplace(0, ``scale``).

    Implemented via inverse-CDF sampling::

        X = -scale * sgn(U) * ln(1 - 2|U|),  U ~ Uniform(-1/2, 1/2)

    which is equivalent to ``scale * ln(U1 / U2)`` for two independent
    uniform variables but needs only one random draw.
    """
    if scale <= 0:
        raise ValueError("Laplace scale must be strictly positive")
    with _rng_lock:
        u = _rng.random() - 0.5  # (-0.5, 0.5)
    sign = -1.0 if u < 0 else 1.0
    return sign * scale * math.log(1.0 - 2.0 * abs(u))
