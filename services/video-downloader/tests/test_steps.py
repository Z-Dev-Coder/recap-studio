"""
The diffusion step count reaching the model.

Ten steps is VoxCPM's default and the whole cost on a modest card: measured on
a 13.8s line, ten took 24s and six took 12s. A setting that is dropped on the
way through would look like it worked and change nothing.
"""

import pytest

from ytdl.recap import localtts


@pytest.fixture
def spoken(monkeypatch):
    seen = {}

    class Model:
        def generate(self, **kw):
            seen.update(kw)
            import numpy as np
            return np.zeros(24000, dtype="float32")

    monkeypatch.setattr(localtts, "load", lambda *a, **k: Model())
    monkeypatch.setattr(localtts, "available", lambda: True)
    return seen


def test_a_step_count_reaches_the_model(spoken):
    localtts.speak("ဒါဟာ စမ်းသပ်မှုပါ။", timesteps=6)
    assert spoken["inference_timesteps"] == 6


def test_no_step_count_leaves_the_default_alone(spoken):
    localtts.speak("ဒါဟာ စမ်းသပ်မှုပါ။")
    assert "inference_timesteps" not in spoken


def test_an_absurd_step_count_is_clamped(spoken):
    localtts.speak("ဒါဟာ စမ်းသပ်မှုပါ။", timesteps=200)
    assert spoken["inference_timesteps"] == 20
    localtts.speak("ဒါဟာ စမ်းသပ်မှုပါ။", timesteps=1)
    assert spoken["inference_timesteps"] == 4
