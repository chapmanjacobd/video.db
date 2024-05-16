import sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from tests import utils
from tests.utils import connect_db_args, v_db
from xklb.createdb.fs_add import fs_add
from xklb.lb import library as lb
from xklb.mediadb import db_history, db_media
from xklb.playback.media_player import MediaPrefetcher
from xklb.playback.play_actions import watch as wt
from xklb.utils import consts
from xklb.utils.log_utils import log
from xklb.utils.objects import NoneSpace


@pytest.fixture
def media():
    return [
        {"path": "tests/data/test.mp4"},
        {"path": "tests/data/test.opus"},
        {"path": "tests/data/test.eng.vtt"},
        {"path": "tests/data/test.vtt"},
    ]


def test_prefetch(media):
    args = NoneSpace(
        prefetch=2,
        database=":memory:",
        prefix="",
        transcode=False,
        transcode_audio=False,
        folders=False,
        action=consts.SC.watch,
        verbose=2,
    )
    prep = MediaPrefetcher(args, media)

    assert prep.remaining == 4
    assert len(prep.media) == 4
    assert len(prep.futures) == 0

    prep.fetch()
    assert prep.remaining == 4
    assert len(prep.media) == 2
    assert len(prep.futures) == 2

    m = prep.get_m()
    assert m is not None
    assert m["path"] == utils.p("tests/data/test.mp4")
    assert prep.remaining == 3
    assert len(prep.media) == 1
    assert len(prep.futures) == 2

    m = prep.get_m()
    assert prep.remaining == 2
    assert len(prep.media) == 0
    assert len(prep.futures) == 2

    m = prep.get_m()
    assert prep.remaining == 1
    assert len(prep.media) == 0
    assert len(prep.futures) == 1

    m = prep.get_m()
    assert m is not None
    assert m["path"] == utils.p("tests/data/test.vtt")
    assert prep.remaining == 0
    assert len(prep.media) == 0
    assert len(prep.futures) == 0

    assert prep.get_m() is None
    assert prep.remaining == 0


def test_wt_help(capsys):
    wt_help_text = "usage:,where,sort,--duration".split(",")

    sys.argv = ["wt", "-h"]
    with pytest.raises(SystemExit):
        wt()
    captured = capsys.readouterr().out.replace("\n", "")
    for help_text in wt_help_text:
        assert help_text in captured

    with pytest.raises(SystemExit):
        lb(["wt", "-h"])
    captured = capsys.readouterr().out.replace("\n", "")
    for help_text in wt_help_text:
        assert help_text in captured


def test_wt_print(capsys):
    for lb_command in [
        ["wt", v_db, "-p"],
        ["pl", v_db],
    ]:
        lb(lb_command)
        captured = capsys.readouterr().out.replace("\n", "")
        assert "Agg" not in captured, f"Test failed for {lb_command}"

    for lb_command in [
        ["wt", v_db, "-p", "a"],
        ["wt", v_db, "-pa"],
        ["pl", v_db, "-pa"],
    ]:
        lb(lb_command)
        captured = capsys.readouterr().out.replace("\n", "")
        assert ("Agg" in captured) or ("extractor_key" in captured), f"Test failed for {lb_command}"


class TestFs(unittest.TestCase):
    @mock.patch("xklb.playback.media_player.single_player", return_value=SimpleNamespace(returncode=0))
    def test_lb_fs(self, play_mocked):
        for SC in ("watch", "wt"):
            lb([SC, v_db, "-w", "path like '%test.mp4'"])
            out = play_mocked.call_args[0][1]
            assert "test.mp4" in out["path"]
            assert out["duration"] == 12
            assert out["size"] == 136057

        sys.argv = ["wt", v_db, "-w", "path like '%test.mp4'"]
        wt()
        out = play_mocked.call_args[0][1]
        assert "test.mp4" in out["path"]
        assert out["duration"] == 12
        assert out["size"] == 136057

        a_db = "tests/data/audio.db"
        fs_add([a_db, "--audio", "tests/data/"])
        lb(["listen", a_db])
        out = play_mocked.call_args[0][1]
        assert "test" in out["path"]

    @mock.patch("xklb.playback.media_player.single_player", return_value=SimpleNamespace(returncode=0))
    def test_wt_sort(self, play_mocked):
        sys.argv = ["wt", v_db, "-u", "duration"]
        wt()
        out = play_mocked.call_args[0][1]
        assert out is not None

    @mock.patch("xklb.playback.media_player.single_player", return_value=SimpleNamespace(returncode=0))
    def test_wt_size(self, play_mocked):
        sys.argv = ["wt", v_db, "--size", "-1"]  # less than 1MB
        wt()
        out = play_mocked.call_args[0][1]
        assert out is not None

    @mock.patch("xklb.playback.media_player.single_player", return_value=SimpleNamespace(returncode=0))
    def test_undelete(self, _play_mocked):
        temp_dir = tempfile.TemporaryDirectory()

        t_db = str(Path(temp_dir.name, "test.db"))
        fs_add([t_db, "tests/data/"])
        args = connect_db_args(t_db)
        db_history.add(args, [str(Path("tests/data/test.mp4").resolve())])
        db_media.mark_media_deleted(args, [str(Path("tests/data/test.mp4").resolve())])
        fs_add([t_db, "tests/data/"])
        d = args.db.pop_dict("select * from media where path like '%test.mp4'")
        assert d["time_deleted"] == 0

        try:
            temp_dir.cleanup()
        except Exception as e:
            log.debug(e)
