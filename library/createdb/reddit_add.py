import argparse
import datetime as dt
import sys
from functools import partial
from itertools import takewhile
from typing import TYPE_CHECKING, Any

import prawcore

from library import usage
from library.mediadb import db_media, db_playlists
from library.utils import arggroups, argparse_utils, consts, db_utils, iterables, objects
from library.utils.log_utils import log

PRAW_SETUP_INSTRUCTIONS = r"""
You will need your Reddit user login info, client id, and secret.
See https://www.reddit.com/wiki/api for client id / secret.

Then create a praw.ini file:

[DEFAULT]
check_for_updates=False
[bot1]
client_id=
client_secret=
username=
password=
user_agent="test_script by u/YOUR_USERNAME_HERE"

And save it in the following location:

- Linux or Mac OS: ~/.config/praw.ini
- Windows: %APPDATA%\praw.ini

More details: https://praw.readthedocs.io/en/stable/getting_started/configuration/prawini.html
"""


if TYPE_CHECKING:
    import praw


def parse_args(action, usage) -> argparse.Namespace:
    parser = argparse_utils.ArgumentParser(usage=usage)
    parser.add_argument("--limit", default=1000, type=int)
    parser.add_argument("--lookback", default=4, type=int, help="Number of days to look back")
    parser.add_argument("--praw-site", default="bot1")

    parser.add_argument("--subreddits", action="store_true")
    parser.add_argument("--redditors", action="store_true")

    parser.add_argument("--force", "-f", action="store_true")
    arggroups.debug(parser)
    arggroups.database(parser)
    if action == consts.SC.reddit_add:
        arggroups.paths_or_stdin(parser)
    args = parser.parse_intermixed_args()
    arggroups.args_post(args, parser, create_db=action == consts.SC.reddit_add)

    if action == consts.SC.reddit_add:
        args.paths = iterables.conform(args.paths)

    try:
        import praw

        args.reddit = praw.Reddit(args.praw_site, config_interpolation="basic")
    except Exception as e:
        print(PRAW_SETUP_INSTRUCTIONS)
        raise SystemExit(e) from e

    return args


"""
Catherine Devlin's reddit-to-sqlite project was very helpful in understanding
how to work with praw. Any lines of code which can be attributed to that person
should be.

https://github.com/catherinedevlin/reddit-to-sqlite

MIT License

Copyright (c) 2021 Catherine Devlin, Jacob Chapman

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def created_since(row, target_sec_utc: int | None) -> bool:
    result = (not target_sec_utc) or (row.created_utc >= target_sec_utc)
    return result


def legalize(val):
    import praw

    if isinstance(val, praw.models.reddit.base.RedditBase):  # type: ignore
        return str(val)
    if isinstance(val, praw.models.reddit.poll.PollData):  # type: ignore
        d = val.__dict__
        d.pop("_reddit", None)
        return d
    return val


def _parent_ids_interpreted(dct: dict[str, Any]) -> dict[str, Any]:
    if not dct.get("parent_id"):
        return dct

    prefix = dct["parent_id"][:3]
    dct["parent_clean_id"] = dct["parent_id"][3:]
    if prefix == "t1_":
        dct["parent_comment_id"] = dct["parent_clean_id"]
    elif prefix == "t3_":
        dct["parent_post_id"] = dct["parent_clean_id"]
    return dct


def saveable(item) -> dict[str, Any]:
    result = {k: legalize(v) for k, v in item.__dict__.items() if not k.startswith("_")}
    return _parent_ids_interpreted(result)


def slim_post_data(d: dict, subreddit=None) -> dict:
    skip_domains = ["linktr.ee", "twitter.com", "t.me", "patreon", "onlyfans", "fans.ly", "file-upload", "file-link"]
    url = d.get("url")
    if not url or any(domain in url for domain in skip_domains):
        d["url"] = d.get("url_overridden_by_dest")

    selftext = d.get("selftext")
    if selftext and selftext in (
        "[deleted]",
        "[removed]",
        "[ Removed by reddit in response to a copyright notice. ]",
        "[ Removed by reddit on account of violating the [content policy](/help/contentpolicy). ]",
    ):
        selftext = None

    return {
        "path": url,
        "author": d.get("author"),
        "author_flair_text": d.get("author_flair_text"),
        "time_created": d.get("created_utc"),
        "time_modified": int(d.get("edited") or 0),
        "is_over_18": d.get("over_18"),
        "is_crosspostable": d.get("is_crosspostable"),
        "is_original_content": d.get("is_original_content"),
        "is_video": d.get("is_video"),
        "link_flair_text": d.get("link_flair_text"),
        "num_comments": d.get("num_comments"),
        "num_crossposts": d.get("num_crossposts"),
        "score": d.get("score"),
        "upvote_ratio": d.get("upvote_ratio"),
        "selftext": selftext,
        "title": d.get("title"),
        "total_awards_received": d.get("total_awards_received"),
        "subreddit": subreddit,
    }


def save_post(args, post_dict, subreddit_path) -> None:
    slim_dict = objects.dict_filter_bool(slim_post_data(post_dict, subreddit_path))

    if slim_dict:
        already_downloaded_path = args.db.pop(
            "SELECT path FROM media WHERE webpath =?",
            [slim_dict["path"]],
            ignore_errors=["no such column", "no such table"],
        )
        if already_downloaded_path:
            slim_dict["path"] = already_downloaded_path

        slim_dict = {
            **slim_dict,
            "time_downloaded": 0,
            "time_deleted": 0,
        }

        if post_dict.get("author_is_blocked") == 1:
            pass
        elif "selftext" in slim_dict:
            args.db["reddit_posts"].upsert(slim_dict, pk=["path"], alter=True)
        else:
            db_media.add(args, slim_dict)


def since_last_created(args, playlist_path):
    latest_post_utc = args.db.pop(
        """
        select max(time_created)
        from (
            select time_created, playlists_id from reddit_posts
            UNION ALL
            select time_created, playlists_id from media
        )
        where playlists_id = (select id from playlists where path = ?)
    """,
        [playlist_path],
        ignore_errors=["no such column", "no such table"],
    )
    if latest_post_utc:
        get_since = dt.datetime.fromtimestamp(latest_post_utc, tz=dt.timezone.utc).astimezone() - dt.timedelta(
            days=args.lookback
        )
        get_since = int(get_since.timestamp())
        log.info("[%s]: Getting posts since timestamp %s", playlist_path, get_since)
    else:
        get_since = 0
        log.info("[%s]: Getting posts since the dawn of time...", playlist_path)

    _takewhile = partial(created_since, target_sec_utc=get_since)
    return _takewhile


def redditor_new(args, redditor_dict) -> None:
    user_path, user_name = redditor_dict.values()
    user: praw.reddit.Redditor = args.reddit.redditor(user_name)

    _takewhile = since_last_created(args, user_path)
    log.info("[%s]: Getting new posts", user_path)

    for s in takewhile(_takewhile, user.submissions.new(limit=args.limit)):
        s.time_created = s.created_utc
        save_post(args, saveable(s), user_path)


def subreddit_new(args, subreddit_dict) -> None:
    subreddit_path, subreddit_name = subreddit_dict.values()
    subreddit: praw.reddit.Subreddit = args.reddit.subreddit(subreddit_name)

    _takewhile = since_last_created(args, subreddit_path)
    log.info("[%s]: Getting new posts", subreddit_path)
    for idx, post in enumerate(takewhile(_takewhile, subreddit.new(limit=args.limit))):
        post_dict = saveable(post)

        if idx == 0:
            db_playlists._add(
                args,
                objects.dict_filter_bool(
                    {
                        "path": subreddit_path,
                        "subscribers": post_dict.pop("subreddit_subscribers", None),
                        "visibility": post_dict.pop("subreddit_type", None),
                        "time_modified": consts.now(),
                    },
                ),
            )

        save_post(args, post_dict, subreddit_path)


def subreddit_top(args, subreddit_dict) -> None:
    subreddit_path, subreddit_name = subreddit_dict.values()

    subreddit: praw.reddit.Subreddit = args.reddit.subreddit(subreddit_name)

    time_filters = ["all", "year", "month", "week", "day", "hour"]
    if args.lookback in [1, 2]:
        time_filters.reverse()
    time_filters = time_filters[: args.lookback]

    _takewhile = since_last_created(args, subreddit_path)
    for time_filter in time_filters:
        log.info("[%s]: Getting top posts for time_filter '%s'", subreddit, time_filter)
        for post in takewhile(_takewhile, subreddit.top(time_filter, limit=args.limit)):
            save_post(args, saveable(post), subreddit_path)


skip_errors = (prawcore.exceptions.NotFound, prawcore.exceptions.Forbidden, prawcore.exceptions.Redirect)


def reddit_add(args=None) -> None:
    if args:
        sys.argv = ["lb", *args]

    args = parse_args(consts.SC.reddit_add, usage=usage.reddit_add)

    for path in args.paths:
        path = path.lower()
        subreddit_matches = consts.REGEX_SUBREDDIT.match(path)
        redditor_matches = consts.REGEX_REDDITOR.match(path)
        extractor_key = "reddit_praw"
        name = path
        if subreddit_matches:
            extractor_key = "reddit_praw_subreddit"
            name = iterables.conform(subreddit_matches.groups()).pop()
        elif redditor_matches:
            extractor_key = "reddit_praw_redditor"
            name = iterables.conform(redditor_matches.groups()).pop()
        elif args.subreddits:
            extractor_key = "reddit_praw_subreddit"
            path = f"https://old.reddit.com/r/{name}/"
        elif args.redditors:
            extractor_key = "reddit_praw_redditor"
            path = f"https://old.reddit.com/user/{name}/"
        else:
            log.error(f"[{path}]: Skipping unknown URL")
            continue

        if db_playlists.get_id(args, path) is None or args.force:
            try:
                if extractor_key == "reddit_praw_redditor":
                    redditor_new(args, {"path": path, "name": name})
                elif extractor_key == "reddit_praw_subreddit":
                    subreddit_new(args, {"path": path, "name": name})
                    subreddit_top(args, {"path": path, "name": name})
            except skip_errors as e:
                log.error("[%s] skipping: %s", name, e)

        db_playlists._add(
            args,
            objects.dict_filter_bool(
                {
                    "path": path,
                    "extractor_playlist_id": name,
                    "extractor_config": args.extractor_config,
                    "extractor_key": extractor_key,
                    "time_modified": consts.now(),
                    "time_deleted": 0,
                },
            ),
        )

    if not args.db["media"].detect_fts():
        db_utils.optimize(args)


def reddit_update(args=None) -> None:
    if args:
        sys.argv = ["lb", *args]

    args = parse_args(consts.SC.reddit_update, usage=usage.reddit_update)
    reddit_playlists = db_playlists.get_all(
        args,
        "extractor_key, path, extractor_playlist_id, extractor_config",
        sql_filters=['AND extractor_key in ("reddit_praw_subreddit","reddit_praw_redditor")'],
    )

    for playlist in reddit_playlists:
        extractor_key = playlist["extractor_key"]
        path = playlist["path"]
        name = playlist["extractor_playlist_id"]

        try:
            if extractor_key == "reddit_praw_subreddit":
                subreddit_new(args, {"path": path, "name": name})
                subreddit_top(args, {"path": path, "name": name})
            elif extractor_key == "reddit_praw_redditor":
                redditor_new(args, {"path": path, "name": name})

            db_playlists.decrease_update_delay(args, playlist["path"])
        except skip_errors as e:
            db_playlists.increase_update_delay(args, playlist["path"])
            log.error("[%s] skipping: %s", name, e)
            continue
