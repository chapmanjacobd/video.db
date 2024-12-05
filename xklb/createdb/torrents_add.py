import concurrent.futures, os, statistics
from pathlib import Path
from urllib.parse import urlparse

from torrentool.api import Torrent

from xklb import usage
from xklb.mediadb import db_playlists
from xklb.utils import arg_utils, arggroups, argparse_utils, consts, db_utils, iterables, nums, objects, printing
from xklb.utils.log_utils import log


def parse_args():
    parser = argparse_utils.ArgumentParser(usage=usage.torrents_add)

    arggroups.debug(parser)

    arggroups.database(parser)
    arggroups.paths_or_stdin(parser)
    args = parser.parse_args()
    arggroups.args_post(args, parser, create_db=True)
    return args


def get_tracker(torrent: Torrent):
    if torrent.announce_urls is None:
        return torrent.source

    log.debug(torrent.announce_urls)

    for tracker in iterables.flatten(torrent.announce_urls):
        url = urlparse(tracker)
        domain = ".".join(url.netloc.rsplit(":")[0].rsplit(".", 2)[-2:]).lower()
        return domain

    return torrent.source


def extract_metadata(path):
    torrent = Torrent.from_file(path)

    file_sizes = [f.length for f in torrent.files]

    stat = os.stat(path, follow_symlinks=False)

    return {
        "path": path,
        "webpath": iterables.safe_unpack(*torrent.webseeds, *torrent.httpseeds),
        "title": torrent.name,
        "tracker": get_tracker(torrent),
        "time_uploaded": nums.safe_int(torrent._struct.get("creation date")),
        "time_created": int(stat.st_ctime),
        "time_modified": int(stat.st_mtime) or consts.now(),
        "time_deleted": 0,
        "time_downloaded": 0,
        "size": sum(file_sizes),
        "size_avg": statistics.mean(file_sizes),
        "size_median": statistics.median(file_sizes),
        "file_count": len(torrent.files),
        "src": torrent.source,
        "is_private": torrent.private,
        "comment": torrent.comment,
        "author": torrent.created_by,
        "info_hash": torrent.info_hash,
        "files": [
            {
                "path": f.name,
                "size": f.length,
            }
            for f in torrent.files
        ],
    }


def torrents_add():
    args = parse_args()

    scanned_set = set(arg_utils.gen_paths(args, default_exts=(".torrent",)))

    try:
        pl_columns = db_utils.columns(args, "playlists")

        existing_set = {
            d["path"]
            for d in args.db.query(
                f"""select path from playlists
                where 1=1
                    {'AND coalesce(time_deleted, 0)=0' if 'time_deleted' in pl_columns else ''}
                """,
            )
        }
    except Exception as e:
        log.debug(e)
        paths = list(scanned_set)
    else:
        paths = list(scanned_set - existing_set)

        deleted_files = list(existing_set - scanned_set)
        if len(list(scanned_set)) > 1 and len(deleted_files) > 1:
            deleted_files = [p for p in deleted_files if not Path(p).exists()]
            deleted_count = db_playlists.mark_media_deleted(args, deleted_files)
            if deleted_count > 0:
                print(f"Marking", deleted_count, "orphaned metadata records as deleted")

    num_paths = len(paths)
    start_time = consts.now()
    with concurrent.futures.ProcessPoolExecutor() as ex:
        futures = [ex.submit(extract_metadata, path) for path in paths]

        for idx, future in enumerate(concurrent.futures.as_completed(futures)):
            try:
                torrent_info = future.result()
            except Exception:
                log.exception(idx)
                if args.verbose >= consts.LOG_DEBUG:
                    raise
            else:
                percent = (idx + 1) / num_paths * 100
                eta = printing.eta(idx + 1, num_paths, start_time=start_time) if num_paths > 2 else ""
                printing.print_overwrite(
                    f"[{torrent_info['path']}] Extracting metadata {idx + 1} of {num_paths} ({percent:3.1f}%) {eta}",
                    flush=True,
                )

                files = torrent_info.pop("files")
                log.debug(torrent_info)

                playlists_id = db_playlists._add(args, objects.dict_filter_bool(torrent_info))
                files = [file | {"playlists_id": playlists_id} for file in files]
                args.db["media"].insert_all(files, pk=["playlists_id", "path"], alter=True, replace=True)
    print()

    if not args.db["media"].detect_fts():
        db_utils.optimize(args)
