#!/usr/bin/python3
from library import usage
from library.mediafiles import torrents_start
from library.utils import arggroups, argparse_utils, consts, iterables, printing, processes, strings
from library.utils.path_utils import domain_from_url


def parse_args():
    parser = argparse_utils.ArgumentParser(usage=usage.torrents_info)
    arggroups.qBittorrent(parser)
    arggroups.capability_soft_delete(parser)
    arggroups.capability_delete(parser)
    arggroups.debug(parser)

    parser.add_argument("--file-search", "-s", nargs="+", help="The file path substring to search for")

    parser.add_argument("torrent_search", nargs="*", help="The info_hash, name, or save_path substring to search for")
    args = parser.parse_args()
    return args


def qbt_get_tracker(qbt_client, torrent):
    tracker = torrent.tracker
    if not tracker:
        tracker = iterables.safe_unpack(
            tr.url for tr in qbt_client.torrents_trackers(torrent.hash) if tr.url.startswith("http")
        )
    return domain_from_url(tracker)


def torrents_info():
    args = parse_args()

    qbt_client = torrents_start.start_qBittorrent(args)
    all_torrents = qbt_client.torrents_info()

    if args.torrent_search or args.file_search:
        torrents = [t for t in all_torrents if strings.glob_match(args.torrent_search, [t.name, t.save_path, t.hash])]

        if args.file_search:
            torrents = [t for t in torrents if strings.glob_match(args.file_search, [f.name for f in t.files])]

        if not torrents:
            processes.no_media_found()

        torrents = sorted(torrents, key=lambda t: -t.time_active)
        for torrent in torrents:
            printing.extended_view(torrent)

            files = torrent.files
            if args.file_search:
                files = [f for f in torrent.files if strings.glob_match(args.file_search, [f.name])]

            if args.verbose >= consts.LOG_INFO:
                printing.extended_view(files)

            if len(torrent.files) == 1:
                print("1 file")
            elif args.file_search:
                print(len(files), "files of", len(torrent.files), "matched")
            else:
                print(len(torrent.files), "total files")
            print()

        print(len(torrents), "matched torrents")

        torrent_hashes = [t.hash for t in torrents]
        if args.mark_deleted:
            qbt_client.torrents_add_tags(tags="library-delete", torrent_hashes=torrent_hashes)
        elif args.delete_files:
            qbt_client.torrents_delete(delete_files=True, torrent_hashes=torrent_hashes)
        elif args.delete_rows:
            qbt_client.torrents_delete(delete_files=False, torrent_hashes=torrent_hashes)
        return

    interesting_states = [
        "stoppedUP",
        "queuedUP",
        "stoppedDL",
        "forcedMetaDL",
        "metaDL",
        "forcedDL",
        "stalledDL",
        # 'forcedUP', 'stalledUP', 'uploading',  # not very interesting
        "downloading",
        "missingFiles",
        "error",
    ]

    torrents_by_state = {}
    for torrent in all_torrents:
        torrents_by_state.setdefault(torrent.state, []).append(torrent)

    if args.verbose >= consts.LOG_INFO:
        torrents_by_tracker = {}
        for torrent in all_torrents:
            torrents_by_tracker.setdefault(qbt_get_tracker(qbt_client, torrent), []).append(torrent)

        tbl = []
        for state in {*interesting_states} - {"uploading", "downloading"}:
            torrents = torrents_by_state.get(state)
            if not torrents:
                continue

            torrents = sorted(torrents, key=lambda t: (-t.seen_complete, t.time_active))

            tbl.extend(
                [
                    {
                        "state": state,
                        "name": printing.path_fill(t.name, width=76),
                        "seen_complete": strings.relative_datetime(t.seen_complete) if t.seen_complete > 0 else None,
                        "last_activity": strings.relative_datetime(t.last_activity),
                        "time_active": strings.duration(t.time_active),
                        "num_seeds": f"{t.num_seeds} ({t.num_complete})",
                        # 'num_leechs': f"{t.num_leechs} ({t.num_incomplete})",
                        # 'comment': t.comment,
                    }
                    for t in torrents
                ]
            )
        if tbl:
            printing.table(tbl)
            print()

        tbl = []
        for state in ["downloading", "missingFiles", "error"]:
            torrents = torrents_by_state.get(state)
            if not torrents:
                continue

            torrents = sorted(
                torrents, key=lambda t: (t.amount_left == t.total_size, t.eta, t.amount_left), reverse=True
            )

            tbl.extend(
                [
                    {
                        "state": state,
                        "name": printing.path_fill(t.name, width=76),
                        "progress": strings.safe_percent(t.progress),
                        "eta": strings.duration(t.eta) if t.eta < 8640000 else None,
                        "remaining": strings.file_size(t.amount_left),
                    }
                    for t in torrents
                ]
            )
        if tbl:
            printing.table(tbl)
            print()

        trackers = []
        for tracker, torrents in torrents_by_tracker.items():
            torrents = [t for t in torrents if args.verbose >= 2 or t.state not in ("stoppedDL",)]
            remaining = sum(t.amount_left for t in torrents)
            if remaining or args.verbose >= 2:
                trackers.append(
                    {
                        "tracker": tracker,
                        "count": len(torrents),
                        "size": sum(t.total_size for t in torrents),
                        "remaining": remaining,
                        "file_count": sum(len(t.files) for t in torrents) if args.verbose >= 2 else None,  # a bit slow
                    }
                )
        if trackers:
            trackers = sorted(trackers, key=lambda d: (d["remaining"], d["size"]))
            trackers = [
                {
                    **d,
                    "size": strings.file_size(d["size"]),
                    "remaining": strings.file_size(d["remaining"]) if d["remaining"] else None,
                }
                for d in trackers
            ]
            printing.table(iterables.list_dict_filter_bool(trackers))
            print()

    categories = []
    for state, torrents in torrents_by_state.items():
        remaining = sum(t.amount_left for t in torrents)
        categories.append(
            {
                "state": state,
                "count": len(torrents),
                "size": strings.file_size(sum(t.total_size for t in torrents)),
                "remaining": strings.file_size(remaining) if remaining else None,
                "file_count": sum(len(t.files) for t in torrents) if args.verbose >= 2 else None,  # a bit slow
            }
        )

    categories = sorted(
        categories,
        key=lambda d: (
            d["state"].endswith(("missingFiles", "error")),
            d["state"].endswith(("downloading", "DL")),
            iterables.safe_index(interesting_states, d["state"]),
        ),
    )
    printing.table(iterables.list_dict_filter_bool(categories))
    print()

    transfer = qbt_client.transfer_info()
    print(transfer.connection_status.upper())

    dl_speed = strings.file_size(transfer.dl_info_speed)
    dl_limit = f"[{strings.file_size(transfer.dl_rate_limit)}/s]" if transfer.dl_rate_limit > 0 else ""
    dl_d = strings.file_size(transfer.dl_info_data)
    print(f"DL {dl_speed}/s {dl_limit} ({dl_d})")

    up_speed = strings.file_size(transfer.up_info_speed)
    up_limit = f"[{strings.file_size(transfer.up_rate_limit)}/s]" if transfer.up_rate_limit > 0 else ""
    up_d = strings.file_size(transfer.up_info_data)
    print(f"UP {up_speed}/s {up_limit} ({up_d})")
