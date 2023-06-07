import argparse, sys
from pathlib import Path

from xklb import db, tube_backend, usage, utils
from xklb.consts import SC
from xklb.utils import log


def parse_args(action, usage) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="library " + action,
        usage=usage,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--dl-config",
        "-dl-config",
        nargs=1,
        action=utils.ArgparseDict,
        default={},
        metavar="KEY=VALUE",
        help="Add key/value pairs to override or extend default downloader configuration",
    )
    parser.add_argument("--download-archive", default="~/.local/share/yt_archive.txt")
    parser.add_argument("--safe", "-safe", action="store_true", help="Skip generic URLs")
    parser.add_argument("--no-sanitize", "-s", action="store_true", help="Don't sanitize some common URL parameters")
    parser.add_argument("--extra", "-extra", action="store_true", help="Get full metadata (takes a lot longer)")
    parser.add_argument("--playlist-files", action="store_true", help="Read playlists from text files")
    parser.add_argument(
        "--force", action="store_true", help="Fetch metadata for paths even if they are already in the media table"
    )
    parser.add_argument("--subs", action="store_true")
    parser.add_argument("--auto-subs", "--autosubs", action="store_true")
    parser.add_argument("--subtitle-languages", "--subtitle-language", "--sl", action=utils.ArgparseList)
    parser.add_argument("--extra-media-data", default={})
    parser.add_argument("--extra-playlist-data", default={})
    parser.add_argument("--ignore-errors", "--ignoreerrors", "-i", "-f", action="store_true", help=argparse.SUPPRESS)

    if action in (SC.tubeadd, SC.tubeupdate):
        parser.add_argument("--category", "-c", help=argparse.SUPPRESS)

    parser.add_argument("--timeout", "-T", help="Quit after x minutes")
    parser.add_argument("--verbose", "-v", action="count", default=0)
    parser.add_argument("--db", "-db", help=argparse.SUPPRESS)

    parser.add_argument("database")
    if action == SC.tubeadd:
        parser.add_argument("playlists", nargs="+", help=argparse.SUPPRESS)

    args = parser.parse_args()
    args.action = action

    if args.db:
        args.database = args.db
    if action == SC.tubeadd:
        Path(args.database).touch()
    args.db = db.connect(args)

    if hasattr(args, "playlists"):
        args.playlists = list(set(s.strip() for s in args.playlists))
        if not args.no_sanitize:
            args.playlists = [utils.sanitize_url(args, p) for p in args.playlists]
        args.playlists = utils.conform(args.playlists)

    utils.timeout(args.timeout)

    log.info(utils.dict_filter_bool(args.__dict__))
    return args


def tube_add(args=None) -> None:
    if args:
        sys.argv = ["tubeadd", *args]

    args = parse_args(SC.tubeadd, usage=usage.tubeadd)
    if args.playlist_files:
        args.playlists = list(utils.flatten([Path(p).read_text().splitlines() for p in args.playlists]))

    tables = args.db.table_names()
    known_playlists = []
    if "media" in tables and not args.force:
        m_columns = args.db["media"].columns_dict
        known_playlists = list(
            set(
                d["path"]
                for d in args.db.query(
                    (
                        "SELECT path from media"
                        + " WHERE path in ("
                        + ",".join(["?"] * len(args.playlists))
                        + f") or {'web' if 'webpath' in m_columns else ''}path in ("
                        + ",".join(["?"] * len(args.playlists))
                        + ")"
                        + " UNION ALL"
                        + " SELECT path from playlists"
                    ),
                    [*args.playlists, *args.playlists],
                )
            )
        )

    for path in args.playlists:
        if args.safe and not tube_backend.is_supported(path):
            log.info("[%s]: Skipping unsupported playlist (safe_mode)", path)
            continue

        if path in known_playlists:
            log.info("[%s]: Already added. Skipping!", path)
            continue

        tube_backend.process_playlist(args, path, tube_backend.tube_opts(args))

        if args.extra:
            log.warning("[%s]: Getting extra metadata", path)
            tube_backend.get_extra_metadata(args, path)

    LARGE_NUMBER = 100_000
    if not args.db["media"].detect_fts() or tube_backend.added_media_count > LARGE_NUMBER:
        db.optimize(args)


def tube_update(args=None) -> None:
    if args:
        sys.argv = ["tubeupdate", *args]

    args = parse_args(SC.tubeupdate, usage=usage.tubeupdate)
    playlists = db.get_playlists(args, constrain=True)
    tube_backend.update_playlists(args, playlists)
