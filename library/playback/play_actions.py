import argparse, os
from pathlib import Path

from library import usage
from library.createdb import tube_backend
from library.folders import big_dirs
from library.mediadb import db_history, db_media
from library.playback import media_player, media_printer
from library.tablefiles import mcda
from library.utils import arggroups, argparse_utils, consts, devices, file_utils, iterables, nums, processes, sqlgroups
from library.utils.consts import SC
from library.utils.log_utils import Timer, log


def parse_args(action, default_chromecast=None) -> argparse.Namespace:
    DEFAULT_PLAYER_ARGS_SUB = ["--speed=1"]
    DEFAULT_PLAYER_ARGS_NO_SUB = ["--speed=1.46"]

    parser = argparse_utils.ArgumentParser(usage=usage.play(action))
    arggroups.sql_fs(parser)
    arggroups.playback(parser)
    arggroups.post_actions(parser)
    arggroups.multiple_playback(parser)
    arggroups.clobber(parser)

    arggroups.group_folders(parser)
    arggroups.text_filtering(parser)
    arggroups.cluster_sort(parser)
    arggroups.regex_sort(parser)
    arggroups.related(parser)

    ordering = parser.add_argument_group("Ordering")
    ordering.add_argument(
        "--play-in-order",
        "-O",
        nargs="?",
        const="natural_ps",
        help="""Play media in order (for similarly named episodes)

-O [option]_[algorithm]_[field]
-O [algorithm]_[field]
-O [algorithm]
(-O [field] if fieldname does not match an algorithm name)

Options:

    - reverse: reverse the sort order
    - compat: treat characters like '⑦' as '7'

Algorithms:

    - natural: parse numbers as integers
    - os: sort similar to the OS File Explorer sorts. To improve non-alphanumeric sorting on Mac OS X and Linux it is necessary to install pyicu (perhaps via python3-icu -- https://gitlab.pyicu.org/main/pyicu#installing-pyicu)
    - path: use natsort "path" algorithm (https://natsort.readthedocs.io/en/stable/api.html#the-ns-enum)
    - human: use system locale
    - ignorecase: treat all case as equal
    - lowercase: sort lowercase first
    - signed: sort with an understanding of negative numbers
    - python: sort like default python

Fields:

    - path
    - parent
    - stem
    - title (or any other column field)
    - ps: parent, stem
    - pts: parent, title, stem

Examples:

-O             # natural algorithm and (parent, stem) fields
-O natural_ps  # natural algorithm and (parent, stem) fields

-O title       # natural algorithm (default algorithm) and title field
-O path        # path algorithm and (parent, stem) fields (default field)

-O path_ps     # path algorithm and (parent, stem) fields
-O path_path   # path algorithm and path field

If you prefer SQLite's ordering you can do this instead of -O
-s d/planet.earth.2024/ -u path
""",
    )
    ordering.add_argument(
        "--fetch-siblings",
        "--siblings",
        "-o",
        help="""If using --random you need to fetch sibling media to play the media in order:

--fetch-siblings each             # get the first result per directory, SQLite ordering
--fetch-siblings each -O          # get the first result per directory
--fetch-siblings if-audiobook -O  # get the first result per directory if 'audiobook' is in the path
--fetch-siblings always -O        # get 2,000 results per directory
""",
    )
    ordering.add_argument(
        "--re-rank",
        "--rerank",
        "-rr",
        action=argparse_utils.ArgparseDict,
        default={},
        metavar="COLUMN=WEIGHT",
        help="""Add key/value pairs re-rank sorting by multiple attributes (similar to MCDA)
-rr 'regex_sort=1 cluster_sort=1 -size=3'
will sort the playlist by taking into account size, regex, and clusters prioritizing size over the other two.

-u size desc -L 500 --cols path,title,size,time_modified -rs -rr 'regex_sort=2 time_modified=2 sort=1'
will get the 500 largest and then sort by regex_sort, time_modified, and the original sort (size desc)
""",
    )

    probabling = parser.add_argument_group("Probability")
    probabling.add_argument(
        "--subtitle-mix",
        type=float,
        default=consts.DEFAULT_SUBTITLE_MIX,
        help="Probability to play no-subtitle content",
    )
    probabling.add_argument(
        "--interdimensional-cable",
        "-4dtv",
        type=int,
        help="""Duration to play (in seconds) while changing the channel
--interdimensional-cable 40
-4dtv 40
You can open two terminals to replicate AMV Hell somewhat
library watch --volume 0 -4dtv 30
library listen -4dtv 30
""",
    )

    chromecast = parser.add_argument_group("Chromecast")
    chromecast.add_argument("--chromecast", "--cast", "-c", action="store_true", help="Cast to chromecast groups")
    chromecast.add_argument(
        "--chromecast-device",
        "--cast-to",
        "-t",
        default=default_chromecast or "",
        help="""--cast --cast-to "Office pair"
-ct "Office pair"  # equivalent

If you don't know the exact name of your chromecast group run `catt scan`
""",
    )
    chromecast.add_argument(
        "--cast-with-local", "-wl", action="store_true", help="Play music locally at the same time as chromecast"
    )

    player = parser.add_argument_group("Player")
    player.add_argument(
        "--player-args-sub",
        "--player-sub",
        nargs="*",
        default=DEFAULT_PLAYER_ARGS_SUB,
        help="Player arguments for videos with subtitles",
    )
    player.add_argument(
        "--player-args-no-sub",
        "--player-no-sub",
        nargs="*",
        default=DEFAULT_PLAYER_ARGS_NO_SUB,
        help="Player arguments for videos without subtitles",
    )
    player.add_argument(
        "--watch-later-directory", default=consts.DEFAULT_MPV_WATCH_LATER, help="Location of mpv watch-later directory"
    )
    player.add_argument(
        "--transcode",
        action="store_true",
        help="Attempt to transcode to a format that will work with chromecast or other players better",
    )
    player.add_argument(
        "--transcode-audio",
        action="store_true",
        help="Attempt to transcode to a format that will work with chromecast or other players better leaving any video streams AS-IS",
    )

    for i in range(0, 255):
        parser.add_argument(f"--cmd{i}", help=argparse.SUPPRESS)

    parser.add_argument("--safe", action="store_true", help="Skip generic URLs")
    parser.add_argument(
        "--refresh", "--exists", action="store_true", help="Check for deleted files before starting playqueue"
    )
    parser.add_argument(
        "--delete-unplayable", action="store_true", help="Delete from disk any media which does not open successfully"
    )
    arggroups.debug(parser)

    arggroups.database(parser)
    parser.add_argument("search", nargs="*")
    args = parser.parse_intermixed_args()
    for i in range(0, 255):
        if getattr(args, f"cmd{i}") is None:
            delattr(args, f"cmd{i}")
    arggroups.args_post(args, parser)

    arggroups.sql_fs_post(args)
    arggroups.playback_post(args)
    arggroups.post_actions_post(args)
    arggroups.multiple_playback_post(args)
    arggroups.group_folders_post(args)
    arggroups.regex_sort_post(args)

    if args.mpv_socket is None:
        if args.action in (SC.listen,):
            args.mpv_socket = consts.DEFAULT_MPV_LISTEN_SOCKET
        else:
            args.mpv_socket = consts.DEFAULT_MPV_WATCH_SOCKET

    if args.big_dirs:
        args.local_media_only = True

    if args.chromecast:
        from catt.api import CattDevice

        args.cc = CattDevice(args.chromecast_device, lazy=True)
        args.cc_ip = devices.get_ip_of_chromecast(args.chromecast_device)

    args.sock = None
    return args


def filter_episodic(args, media: list[dict]) -> list[dict]:
    parent_dict = {}
    for m in media:
        path = Path(m["path"])
        parent_path = path.parent
        parent_dict.setdefault(parent_path, 0)
        parent_dict[parent_path] += 1

    filtered_media = []
    for m in media:
        path = Path(m["path"])
        parent_path = path.parent

        siblings = parent_dict[parent_path]

        if not args.folder_counts(siblings):
            continue
        else:
            filtered_media.append(m)

    return filtered_media


def history_sort(args, media) -> list[dict]:
    if "s" in args.partial:  # skip; only play unseen
        previously_watched_paths = [m["path"] for m in media if m["time_first_played"]]
        return [m for m in media if m["path"] not in previously_watched_paths]

    def mpv_progress(m):
        playhead = m.get("playhead")
        duration = m.get("duration")
        if not playhead:
            return float("-inf")
        if not duration:
            return float("-inf")

        if "p" in args.partial and "t" in args.partial:
            return (duration / playhead) * -(duration - playhead)  # weighted remaining
        elif "t" in args.partial:
            return -(duration - playhead)  # time remaining
        else:
            return playhead / duration  # percent remaining

    def sorting_hat():
        if "f" in args.partial:  # first-viewed
            return lambda m: m.get("time_first_played") or 0
        elif "p" in args.partial or "t" in args.partial:  # sort by remaining duration
            return mpv_progress

        return lambda m: m.get("time_last_played") or m.get("time_first_played") or 0

    reverse_chronology = True
    if "o" in args.partial:  # oldest first
        reverse_chronology = False

    key = sorting_hat()
    if args.print:
        reverse_chronology = not reverse_chronology

    media = sorted(media, key=key, reverse=reverse_chronology)

    if args.offset:
        media = media[int(args.offset) :]

    return media


def file_or_folder_media(args, paths):
    media = []
    for path in paths:
        p = Path(path).resolve()
        if p.is_file():
            media.extend([{"path": str(p)}])
        elif p.is_dir():
            if args.folder_glob:
                media.extend([{"path": s} for s in file_utils.fast_glob(p)])
            elif args.action in SC.watch:
                media.extend([{"path": s} for s in file_utils.rglob(str(p), consts.VIDEO_EXTENSIONS, args.exclude)[0]])
            elif args.action == SC.listen:
                media.extend(
                    [{"path": s} for s in file_utils.rglob(str(p), consts.AUDIO_ONLY_EXTENSIONS, args.exclude)[0]]
                )
            elif args.action in SC.view:
                media.extend([{"path": s} for s in file_utils.rglob(str(p), consts.IMAGE_EXTENSIONS, args.exclude)[0]])
            elif args.action in SC.read:
                media.extend(
                    [{"path": s} for s in file_utils.rglob(str(p), consts.TEXTRACT_EXTENSIONS, args.exclude)[0]]
                )
            else:
                media.extend([{"path": s} for s in file_utils.rglob(str(p), exclude=args.exclude)[0]])
    return media


def folder_media(args, media):
    media = big_dirs.group_files_by_parent(args, media)
    media = big_dirs.process_big_dirs(args, media)
    media = iterables.list_dict_filter_bool(media, keep_0=False)
    return media


def filter_total_size(media, max_size):
    total_size = 0
    for m in media:
        if total_size + m["size"] <= max_size:
            total_size += m["size"]
            yield m
        else:
            break


def process_playqueue(args) -> None:
    db_history.create(args)

    if args.action == SC.filesystem:
        query, bindings = sqlgroups.fs_sql(args, args.limit)
    else:
        query, bindings = sqlgroups.media_sql(args)

    t = Timer()
    if args.playlists:
        args.playlists = [p if p.startswith("http") else str(Path(p).resolve()) for p in args.playlists]
        media = db_media.get_playlist_media(args, args.playlists)
    else:
        media = list(args.db.query(query, bindings))
        log.debug("len(media_sql) = %s", len(media))
    log.debug("query: %s", t.elapsed())

    if args.fetch_siblings:
        media = db_media.get_sibling_media(args, media)

    if args.folder_counts:
        media = filter_episodic(args, media)
        log.debug("utils.filter_episodic: %s", t.elapsed())

    if not media:
        if not args.include:
            processes.no_media_found()

        path = " ".join(args.include)
        media = db_media.get_playlist_media(args, [path])
        if not media:
            media = db_media.get_dir_media(args, [path])
        if not media:
            media = file_or_folder_media(args, [path])
        if not media:
            processes.no_media_found()

    if args.safe:
        media = [d for d in media if tube_backend.is_supported(d["path"]) or Path(d["path"]).exists()]
        log.debug("tube_backend.is_supported: %s", t.elapsed())

    if args.related >= consts.RELATED:
        media = db_media.get_related_media(args, media[0])
        log.debug("player.get_related_media: %s", t.elapsed())

    if args.big_dirs:
        media_keyed = {d["path"]: d for d in media}
        folders = big_dirs.group_files_by_parents(args, media)
        folders = big_dirs.process_big_dirs(args, folders)
        folders = mcda.group_sort_by(args, folders)
        log.debug("process_bigdirs: %s", t.elapsed())
        folders = list(reversed([d["path"] for d in folders]))
        if "limit" in args.defaults:
            media = db_media.get_dir_media(args, folders)
            log.debug("get_dir_media: %s", t.elapsed())
        else:
            media = []
            media_set = set()
            for folder in folders:
                if len(folder) == 1:
                    continue

                for key in media_keyed:
                    if key in media_set:
                        continue

                    if os.sep not in key.replace(folder, "") and key.startswith(folder):
                        media_set.add(key)
                        media.append(media_keyed[key])
            log.debug("double for loop compare_block_strings: %s", t.elapsed())

    if args.partial:
        media = history_sort(args, media)
        log.debug("utils.history_sort: %s", t.elapsed())

    if getattr(args, "refresh", False):
        marked = db_media.mark_media_deleted(
            args, [d["path"] for d in media if d and d["path"] and not Path(d["path"]).exists()]
        )
        if marked > 0:
            log.warning(f"Marked {marked} metadata records as deleted")
            args.refresh = False
            return process_playqueue(args)
    elif args.folders:
        media = folder_media(args, media)
    elif args.folder_glob:
        media = ({"path": s} for m in media for s in file_utils.fast_glob(Path(m["path"]).parent, args.folder_glob))

    if args.play_in_order:
        media = db_media.natsort_media(args, media)

    if args.re_rank:
        import numpy as np
        import pandas as pd

        from library.utils import pd_utils

        df = pd.DataFrame(media)
        df = pd_utils.from_dict_add_path_rank(df, media, "sort")

        if args.regex_sort:
            from library.text import regex_sort

            sorted_media = regex_sort.sort_dicts(args, media)
            df = pd_utils.from_dict_add_path_rank(df, sorted_media, "regex_sort")
            log.debug("regex-sort: %s", t.elapsed())
        elif args.cluster_sort:
            from library.text import cluster_sort

            sorted_media = cluster_sort.sort_dicts(args, media)
            df = pd_utils.from_dict_add_path_rank(df, sorted_media, "cluster_sort")
            log.debug("cluster-sort: %s", t.elapsed())

        column_weights = {
            k.lstrip("-"): {
                "direction": "desc" if k.startswith("-") else "asc",
                "weight": v or 1,
            }
            for k, v in args.re_rank.items()
        }
        df = pd_utils.rank_dataframe(df, column_weights)
        media = df.replace({np.nan: None}).to_dict(orient="records")
        log.debug("re-rank: %s", t.elapsed())
    elif args.regex_sort:
        from library.text import regex_sort

        media = regex_sort.sort_dicts(args, media)
        log.debug("regex-sort: %s", t.elapsed())
    elif args.cluster_sort:
        from library.text import cluster_sort

        media = cluster_sort.sort_dicts(args, media)
        log.debug("cluster-sort: %s", t.elapsed())

    if args.timeout_size:
        max_size = nums.human_to_bytes(args.timeout_size)
        media = filter_total_size(media, max_size)

    if not media:
        processes.no_media_found()
    if any(
        [
            args.print,
            args.delete_files,
            args.delete_rows,
            args.mark_deleted,
            args.mark_watched,
        ]
    ):
        media_printer.media_printer(args, media)
        return None
    else:
        media_player.play_list(args, media)
        return None


def filesystem() -> None:
    args = parse_args(SC.filesystem)
    process_playqueue(args)


def media() -> None:
    args = parse_args(SC.media)
    process_playqueue(args)


def watch() -> None:
    args = parse_args(SC.watch, default_chromecast="Living Room TV")
    process_playqueue(args)


def listen() -> None:
    args = parse_args(SC.listen, default_chromecast="Xylo and Orchestra")
    process_playqueue(args)


def read() -> None:
    args = parse_args(SC.read)
    process_playqueue(args)


def view() -> None:
    args = parse_args(SC.view)
    process_playqueue(args)
