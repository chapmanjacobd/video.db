import argparse, os, re, tempfile
from pathlib import Path
from shlex import quote
from typing import List

import ffmpeg, pysubs2
from joblib import Parallel, delayed

from xklb.utils import cmd, flatten, remove_text_inside_brackets, remove_whitespaace
from xklb.utils_paths import get_media_files, youtube_dl_id

SUBTITLE_FORMATS = "vtt|srt|ssa|ass|sub|idx|psb|smi|ssf|usf"
IMAGE_SUBTITLE_CODECS = ["dvbsub", "dvdsub", "pgssub", "xsub", "dvb_subtitle", "dvd_subtitle", "hdmv_pgs_subtitle"]


def extract(video_file, stream_index):
    temp_vtt = tempfile.mktemp(".vtt")

    ffmpeg.input(video_file).output(temp_vtt, map="0:" + str(stream_index)).run(quiet=True)
    return temp_vtt


def subs_to_text(paths: List[str]):
    def read_sub(path):
        if Path(path).suffix != ".srt":
            temp_srt = tempfile.mktemp(".srt")
            ffmpeg.input(path).output(temp_srt).run(quiet=True)
            path = temp_srt

        try:
            return [
                remove_text_inside_brackets(caption.text.replace(r"\N", " ").replace(r"\n", " ").replace("\n", " "))
                for caption in pysubs2.load(path, format_="srt")
            ]
        except NotImplementedError:
            return []

    subtitles = " ".join(list(dict.fromkeys(flatten([read_sub(path) for path in paths]))))
    return remove_whitespaace(subtitles)


def has_internal_subtitle(file):
    internal_sub = cmd(
        f"ffmpeg -hide_banner -nostdin -i {quote(str(file))} -c copy -map 0:s:0 -frames:s 1 -f null - -v 0",
        strict=False,
        shell=True,
    ).returncode
    if internal_sub == 0:
        return True


def get_external(file):
    p = Path(file)
    for suffix in p.suffixes:
        subtitles = [
            str(p)
            for p in p.parent.glob(p.stem.removesuffix(suffix) + "*")
            if p.suffix[1:] in SUBTITLE_FORMATS.split("|")
        ]

        if len(subtitles) > 0:
            return subtitles

    return []


def has_external_subtitle(file):
    file = Path(file)

    if any(
        [
            file.with_suffix("." + ext).exists()
            or file.with_suffix(".en." + ext).exists()
            or file.with_suffix(".eng." + ext).exists()
            for ext in SUBTITLE_FORMATS.split("|")
        ]
    ):
        return True

    if len(file.stem) <= 13:
        return False

    FORMATSUB_REGEX = re.compile(rf".*\.({SUBTITLE_FORMATS})")
    for globbed in file.parent.glob(file.stem[:-12] + r".*"):
        match = FORMATSUB_REGEX.match(str(globbed))
        if match:
            return True

    return False


def get(args, file):
    try:
        if has_internal_subtitle(file) or has_external_subtitle(file):
            return
    except Exception:
        pass

    try:
        yt_video_id = youtube_dl_id(file)
    except Exception:
        print(file)
        return

    run_subliminal = not args.youtube_only
    run_youtube = args.youtube_only  # for new videos I already have yt-dlp get the subtitle

    if run_youtube and len(yt_video_id) > 0:
        print(yt_video_id)
        cmd(
            (
                "yt-dlp --sub-lang 'en,EN,en.*,en-*,EN.*,EN-*eng,ENG,english,English,ENGLISH'"
                " --embed-subs --compat-options no-keep-subs --write-sub --write-auto-sub"
                " --no-download-archive --skip-download --limit-rate 10K"
                f" https://youtu.be/{yt_video_id}"
            ),
            cwd=str(Path(file).parent),
            strict=False,
        )

    if run_subliminal:
        print("Downloading subtitles:", file)
        cmd(
            "subliminal",
            "--opensubtitles",
            os.environ["OPEN_SUBTITLE_CREDENTIALS"],
            "download",
            "-l",
            "en",
            file,
            # strict=False
        )


def main():
    parser = argparse.ArgumentParser(prog="lb subtitle")
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--youtube-only", action="store_true")
    parser.add_argument("--subliminal-only", action="store_true")
    parser.add_argument("--verbose", "-v", action="count", default=0)
    args = parser.parse_args()

    video_files = get_media_files(args)

    Parallel(n_jobs=6 if args.verbose == 0 else 1)(delayed(get)(args, file) for file in video_files)


if __name__ == "__main__":
    main()
