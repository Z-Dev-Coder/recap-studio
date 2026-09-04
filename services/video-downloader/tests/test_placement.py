"""
A caption you can actually put somewhere.

The compositor clamps every picture inside the frame. A picture that IS the
frame therefore has exactly one possible position, so a caption drawn at full
frame width could be dragged anywhere on screen and would come out in the
middle every time.
"""

import pytest


def place(image_width: int, fraction: float, frame: int = 1080) -> float:
    """The compositor's own arithmetic, from burn_caption_images."""
    return min(max(fraction * frame - image_width / 2, 0), frame - image_width)


def test_a_full_width_picture_cannot_be_placed():
    assert place(1080, 0.25) == place(1080, 0.5) == place(1080, 0.8) == 0


def test_a_picture_sized_to_its_text_can_be():
    left, middle, right = place(529, 0.25), place(529, 0.5), place(529, 0.8)
    assert left < middle < right


def test_it_is_centred_on_the_point_it_was_dragged_to():
    width = 400
    assert place(width, 0.5) + width / 2 == pytest.approx(540)
    assert place(width, 0.25) + width / 2 == pytest.approx(270)


def test_it_never_hangs_off_an_edge():
    for fraction in (0.0, 0.02, 0.5, 0.98, 1.0):
        x = place(400, fraction)
        assert 0 <= x <= 1080 - 400
