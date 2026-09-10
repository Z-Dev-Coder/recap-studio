"""
The pieces a cut is assembled from.

Each beat is cut to its own file and the files are then joined. They were
tidied away only when the join succeeded, so every stopped or failed cut left
its pieces behind -- 57MB found in one project, from a run interrupted days
earlier, in a folder nothing ever looks at again.
"""

import pytest

from ytdl.recap import video


def _parts(tmp_path, n=3):
    work = tmp_path / "_parts"
    work.mkdir()
    made = []
    for i in range(n):
        p = work / f"part_{i:03d}.mp4"
        p.write_bytes(b"clip")
        made.append(p)
    return work, made


def test_the_pieces_go_after_a_successful_join(tmp_path):
    work, parts = _parts(tmp_path)
    video._clear(work, parts)
    assert not work.exists()


def test_pieces_from_an_earlier_run_go_too(tmp_path):
    """A cut that died left files this run knows nothing about."""
    work, parts = _parts(tmp_path, 2)
    (work / "part_099.mp4").write_bytes(b"orphan")

    video._clear(work, parts)
    assert not work.exists(), "the folder is only empty if the stray went as well"


def test_clearing_an_already_clean_folder_is_not_an_error(tmp_path):
    video._clear(tmp_path / "gone", [])


def test_the_folder_survives_if_something_else_is_in_it(tmp_path):
    """Only this app's pieces are its to delete."""
    work, parts = _parts(tmp_path, 1)
    (work / "notes.txt").write_bytes(b"someone put this here")

    video._clear(work, parts)
    assert (work / "notes.txt").exists()
    assert not (work / "part_000.mp4").exists()
