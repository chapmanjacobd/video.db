from tests.utils import v_db
from xklb.lb import library as lb


def test_playlists(capsys):
    lb(["playlists", v_db])
    captured = capsys.readouterr().out.replace("\n", "")
    assert "duration" in captured
    assert len(captured) > 200


def test_playlists2(capsys):
    lb(["playlists", v_db, "data"])
    captured = capsys.readouterr().out.replace("\n", "")
    assert len(captured) > 200


def test_playlists3(capsys):
    lb(["playlists", v_db, "-pa"])
    captured = capsys.readouterr().out.replace("\n", "")
    assert "playlists_count" in captured
    assert "media_count" in captured
    assert len(captured) > 200
