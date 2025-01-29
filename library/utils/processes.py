import contextlib, functools, importlib, json, multiprocessing, os, shlex, signal, subprocess, sys, threading
from contextlib import suppress
from pathlib import Path
from shutil import which
from typing import NoReturn

from library.data import unar_errors
from library.utils import consts, iterables, nums, path_utils, strings
from library.utils.log_utils import log


def exit_nicely(_signal, _frame) -> NoReturn:
    log.warning("\nExiting... (Ctrl+C)")
    raise SystemExit(130)


signal.signal(signal.SIGINT, exit_nicely)


def no_media_found() -> NoReturn:
    log.error("No media found")
    raise SystemExit(2)


def player_exit(completed_process) -> NoReturn:
    log.error("Player exited with code %s", completed_process.returncode)
    log.debug(shlex.join(completed_process.args))
    raise SystemExit(completed_process.returncode)


def exit_error(msg) -> NoReturn:
    log.error("ERROR: " + msg)
    raise SystemExit(1)


def timeout(time_str) -> None:
    max_seconds = nums.human_to_seconds(time_str) or 0

    def exit_timeout(_signal, _frame):
        print(f"\nReached timeout... ({max_seconds}s)")
        raise SystemExit(124)

    signal.signal(signal.SIGALRM, exit_timeout)
    signal.alarm(max_seconds)


sizeout_max = None
sizeout_total = 0


def sizeout(max_size: str, next_size: int) -> None:
    global sizeout_max
    global sizeout_total

    if sizeout_max is None:
        sizeout_max = nums.human_to_bytes(max_size)

    sizeout_total += next_size
    if sizeout_total > sizeout_max:
        print(f"\nReached sizeout... ({max_size})")
        raise SystemExit(124)


def with_timeout(seconds):  # noqa: ANN201
    def decorator(decorated):
        @functools.wraps(decorated)
        def inner(*args, **kwargs):
            pool = multiprocessing.Pool(1)
            async_result = pool.apply_async(decorated, args, kwargs)
            try:
                return async_result.get(seconds)
            finally:
                pool.close()

        return inner

    return decorator


def with_timeout_thread(seconds):  # noqa: ANN201
    def decorator(decorated):
        @functools.wraps(decorated)
        def inner(*args, **kwargs):
            event = threading.Event()  # task completed

            result = None

            def target():
                nonlocal result
                result = decorated(*args, **kwargs)
                event.set()

            thread = threading.Thread(target=target)
            thread.start()

            if event.wait(seconds):
                return result
            else:
                raise TimeoutError

        return inner

    return decorator


@contextlib.contextmanager
def timeout_thread(seconds):
    event = threading.Event()  # task completed
    result = None

    def target(func, *args, **kwargs):
        nonlocal result
        result = func(*args, **kwargs)
        event.set()

    def run(func, *args, **kwargs):
        thread = threading.Thread(target=target, args=(func, *args), kwargs=kwargs)
        thread.start()
        if event.wait(seconds):
            return result
        else:
            raise TimeoutError

    yield run


def os_bg_kwargs() -> dict:
    # prevent ctrl-c from affecting subprocesses first

    if hasattr(os, "setpgrp"):
        return {"start_new_session": True}
    else:
        # CREATE_NEW_PROCESS_GROUP = 0x00000200
        # DETACHED_PROCESS = 0x00000008
        # os_kwargs = dict(creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
        return {}


def cmd(
    *command, strict=True, cwd=None, quiet=True, error_verbosity=1, ignore_regexps=None, **kwargs
) -> subprocess.CompletedProcess:
    def print_std(s, is_success):
        if ignore_regexps is not None:
            s = "\n".join(line for line in s.splitlines() if not any(r.match(line) for r in ignore_regexps))

        s = s.strip()
        if s:
            if quiet and is_success:
                log.debug(s)
            elif consts.PYTEST_RUNNING:
                log.warning(s)
            elif error_verbosity == 0:
                log.debug(s)
            elif error_verbosity == 1:
                log.info(s)
            else:
                log.warning(s)
        return s

    try:
        r = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=cwd,
            errors=sys.getfilesystemencodeerrors(),
            **os_bg_kwargs(),
            **kwargs,
        )
    except UnicodeDecodeError:
        print(repr(command))
        raise

    log.debug(r.args)
    print_std(r.stdout, r.returncode == 0)
    print_std(r.stderr, r.returncode == 0)
    if r.returncode != 0:
        if error_verbosity == 0:
            log.debug("[%s] exited %s", shlex.join(command), r.returncode)
        elif error_verbosity == 1:
            log.info("[%s] exited %s", shlex.join(command), r.returncode)
        else:
            log.warning("[%s] exited %s", shlex.join(command), r.returncode)

        if strict:
            raise subprocess.CalledProcessError(r.returncode, shlex.join(command), r.stdout, r.stderr)

    return r


def cmd_detach(*command, **kwargs) -> subprocess.CompletedProcess:
    # https://stackoverflow.com/questions/62521658/python-subprocess-detach-a-process
    # After lb closes, the detached process becomes daemonized (ie. not connected to the terminal so they won't show up in the shell command `jobs`)
    # If you shut down your computer often, you may want to open: `watch progress -wc ffmpeg` in another terminal so that you don't forget many things are in the background
    # If using with ffmpeg remember to include ffmpeg's flag `-nostdin` in the command when calling this function
    stdout = subprocess.DEVNULL
    stderr = subprocess.DEVNULL
    stdin = subprocess.DEVNULL

    command = iterables.conform(command)
    if command[0] in ["fish", "bash"]:
        command = command[0:2] + [shlex.join(command[2:])]
    proc = subprocess.Popen(
        command, stdin=stdin, stdout=stdout, stderr=stderr, close_fds=True, **os_bg_kwargs(), **kwargs
    )
    log.debug("pid %s cmd: %s", proc.pid, command)
    return subprocess.CompletedProcess(command, 0, "Detached command is async")


def cmd_interactive(*command, strict=True) -> subprocess.CompletedProcess:
    return_code = os.spawnvpe(os.P_WAIT, command[0], command, os.environ)

    if return_code != 0:
        msg = f"[{shlex.join(command)}] exited {return_code}"
        if strict:
            raise RuntimeError(msg)
        else:
            log.info(msg)

    return subprocess.CompletedProcess(command, return_code, "Interactive command")


def Pclose(process) -> subprocess.CompletedProcess:  # noqa: N802
    try:
        stdout, stderr = process.communicate(input)
    except subprocess.TimeoutExpired as exc:
        log.debug("subprocess.TimeoutExpired")
        process.kill()
        if consts.IS_WINDOWS:
            exc.stdout, exc.stderr = process.communicate()
        else:
            process.wait()
        raise
    except Exception as e:
        log.debug(e)
        process.kill()
        raise
    return_code = process.poll()
    return subprocess.CompletedProcess(process.args, return_code, stdout, stderr)


def load_or_install_modules(module_list):
    for modules in module_list:
        loaded = False
        for module_name in modules:
            try:
                importlib.import_module(module_name)
            except ImportError:
                pass
            else:
                loaded = True
                break

        if not loaded:
            for module_name in modules:
                try:
                    subprocess.check_call([sys.executable, "-m", "pip", "install", module_name])
                except subprocess.CalledProcessError:
                    pass
                else:
                    try:
                        importlib.import_module(module_name)
                    except ImportError:
                        pass
                    else:
                        loaded = True
                        break

        if not loaded:
            print(f"None of the python packages {modules} could be imported or installed")


class UnplayableFile(RuntimeError):
    pass


def is_album_art(s):
    from yt_dlp.utils import traverse_obj

    return traverse_obj(s, ["disposition", "attached_pic"]) == 1


class FFProbe:
    def __init__(self, path, *args):
        args = [
            "ffprobe",
            "-hide_banner",
            "-rw_timeout",
            "100000000",
            "-timeout",
            "45000000",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            "-of",
            "json",
            *args,
            path,
        ]
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        out, err = p.communicate()
        if p.returncode != 0:
            log.info("ffprobe %s out %s error %s", p.returncode, out, err)
            if p.returncode == -2:
                raise KeyboardInterrupt
            elif p.returncode == 127:  # Cannot open shared object file
                raise RuntimeError
            elif p.returncode == -6:  # Too many open files
                raise OSError
            else:
                raise UnplayableFile(out, err)
        d = strings.safe_json_loads(out.decode("utf-8"))

        self.path = path

        self.streams = d.get("streams")
        self.chapters = d.get("chapters")
        self.format = d.get("format")

        self.video_streams = []
        self.audio_streams = []
        self.subtitle_streams = []
        self.album_art_streams = []
        self.other_streams = []

        for s in self.streams:
            if "codec_type" not in s:
                continue
            elif s["codec_type"] == "video":
                if is_album_art(s):
                    self.album_art_streams.append(s)
                else:
                    self.video_streams.append(s)
            elif s["codec_type"] == "audio":
                self.audio_streams.append(s)
            elif s["codec_type"] == "subtitle":
                self.subtitle_streams.append(s)
            else:
                self.other_streams.append(s)

        self.has_video = len(self.video_streams) > 0
        self.has_audio = len(self.audio_streams) > 0

        self.duration = None
        with suppress(KeyError):
            self.duration = float(self.format["duration"])
        if self.duration is None:
            with suppress(IndexError, KeyError):
                self.duration = float(self.audio_streams[0]["duration"])
        if self.duration is None:
            with suppress(IndexError, KeyError):
                self.duration = float(self.video_streams[0]["duration"])

        if self.duration and self.duration > 0:
            start = nums.safe_float(self.format.get("start_time")) or 0
            if start > 0:
                end = nums.safe_float(self.format.get("end_time")) or 0
                if end > 0:
                    self.duration = start - end
                else:
                    self.duration -= start

        self.fps = iterables.safe_unpack(
            [
                self.parse_framerate(s.get("avg_frame_rate"))
                for s in self.streams
                if s.get("avg_frame_rate") is not None and "/0" not in s.get("avg_frame_rate")
            ]
            + [
                self.parse_framerate(s.get("r_frame_rate"))
                for s in self.streams
                if s.get("r_frame_rate") is not None and "/0" not in s.get("r_frame_rate")
            ],
        )

    @staticmethod
    def parse_framerate(string) -> float | None:
        top, bot = string.split("/")
        bot = float(bot)
        if bot == 0:
            return None
        return float(top) / bot


def unar_out_path(archive_path):
    output_path = str(Path(archive_path).with_suffix(""))
    if output_path.endswith(tuple(f".{ext}" for ext in consts.ARCHIVE_EXTENSIONS)):
        output_path = str(Path(output_path).with_suffix(""))
    return output_path


def lsar(archive_path):
    # TODO: seems a little slow. maybe compare perf with 7z or https://github.com/wummel/patool

    if not which("lsar"):
        log.error("[%s]: The 'lsar' command is not available. Install 'unar' to check archives", archive_path)
        return []

    try:
        lsar_output = cmd("lsar", "-json", archive_path, error_verbosity=2)
    except subprocess.CalledProcessError:
        return []
    try:
        lsar_json = strings.safe_json_loads(lsar_output.stdout)
    except json.JSONDecodeError:
        log.warning("[%s]: Error parsing lsar output as JSON: %s", archive_path, lsar_output)
        return []

    if lsar_json.get("lsarError"):
        log.warning("[%s]: lsar error %s", archive_path, lsar_json["lsarError"])

    lsar_contents = lsar_json.get("lsarContents", [])
    lsar_contents = [d for d in lsar_contents if not d.get("XADIsDirectory")]
    if len(lsar_contents) == 0:
        log.info("[%s]: archive empty", archive_path)
        return []

    ar_size = os.stat(archive_path).st_size
    unar_out = unar_out_path(archive_path)
    archive_info_list = []
    for entry in lsar_contents:
        archive_info_list.append(
            {
                "archive_path": archive_path,
                "path": os.path.join(unar_out, entry.get("XADFileName")),
                "compressed_size": entry.get("XADCompressedSize") or ar_size // len(lsar_contents),
                "size": entry.get("XADFileSize"),
            }
        )

    return archive_info_list


def unar_delete(archive_path):
    output_path = unar_out_path(archive_path)
    if not os.path.exists(output_path):
        os.makedirs(output_path)

    lsar_output = cmd("lsar", "-json", archive_path)
    try:
        lsar_json = strings.safe_json_loads(lsar_output.stdout)
    except json.JSONDecodeError:
        log.warning("[%s]: Error parsing lsar output as JSON: %s", archive_path, lsar_output)
        return
    part_files = lsar_json["lsarProperties"]["XADVolumes"]

    original_stats = os.stat(archive_path)

    try:
        cmd("unar", "-quiet", "-force-rename", "-no-directory", "-output-directory", output_path, archive_path)
    except subprocess.CalledProcessError as e:
        error_log = e.stderr.splitlines()
        is_unsupported = any(unar_errors.unsupported_error.match(l) for l in error_log)
        is_file_error = any(unar_errors.file_error.match(l) for l in error_log)
        is_env_error = any(unar_errors.environment_error.match(l) for l in error_log)

        if is_env_error:
            raise
        elif is_unsupported:
            log.error(
                "[%s]: Skipping unsupported archive. %s",
                archive_path,
                e.stderr.replace("Use the -p option to provide one.", "Use unar -p to extract."),
            )
            return
        elif is_file_error:
            pass  # delete archive
        else:
            raise

    path_utils.folder_utime(output_path, (original_stats.st_atime, original_stats.st_mtime))

    try:
        for part_file in part_files:
            part_file_path = os.path.join(os.path.dirname(archive_path), part_file)
            os.unlink(part_file_path)
    except Exception as e:
        log.warning("Error deleting files: %s %s", e, part_files)

    return output_path


def fzf_select(items, multi=True):
    input_text = "\n".join(reversed(items))

    fzf_command = ["fzf", "--bind", "ctrl-a:toggle-all"]
    if multi:
        fzf_command += ["--multi"]

    try:
        result = subprocess.run(fzf_command, input=input_text, text=True, capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        if e.returncode == 130:  # no selection
            return []
        raise

    selected_items = result.stdout.strip().splitlines()
    return selected_items
