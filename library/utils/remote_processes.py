import os, select, shlex, socketserver, subprocess, sys, threading
from contextlib import suppress
from pathlib import Path

from library.utils import consts, remote_processes
from library.utils.log_utils import log


def cmd(
    ssh,  # paramiko.SSHClient
    *command,
    local_files=None,
    cleanup_local_files=True,
    strict=True,
    quiet=True,
    error_verbosity=1,
    ignore_regexps=None,
    cwd=None,
    pty=False,
    **kwargs,
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

    if local_files:
        with ssh.open_sftp() as sftp:
            sftp = ssh.open_sftp()

            tempdir = remote_processes.ssh_tempdir(ssh) or "."
            library_dir = os.path.join(tempdir, "library_cmd_files")
            r = remote_processes.cmd(ssh, "mkdir", "-p", library_dir)

            for file in local_files:
                remote_temp_file = os.path.join(library_dir, Path(file).name)
                sftp.put(file, remote_temp_file)
                command = [remote_temp_file if s == file else s for s in command]

    command = shlex.join(str(s) for s in command)
    if cwd:
        command = "cd " + shlex.quote(cwd) + "; " + command

    log_command = command
    host = getattr(ssh, "host", None)
    if host:
        log_command = " ".join(["ssh", host, log_command])

    if pty:
        # interactive stdin/stdout forwarding
        transport = ssh.get_transport()
        channel = transport.open_session()
        channel.get_pty()  # nice!
        channel.exec_command(command)

        # local stdin to remote stdin
        import threading

        def forward_stdin():
            try:
                while not channel.closed:
                    if select.select([sys.stdin], [], [], 0.1)[0]:
                        data = sys.stdin.read(1)  # one character at a time
                        if not data:
                            break
                        channel.send(data)  # Send to remote stdin
            except Exception as e:
                log.warning(f"Error forwarding stdin: {e}")

        # forward stdin
        stdin_thread = threading.Thread(target=forward_stdin)
        stdin_thread.daemon = True
        stdin_thread.start()

        # Remote stdout to local
        stdout = b""
        while not channel.closed:
            if channel.recv_ready():
                data = channel.recv(4096)
                if not data:
                    break
                stdout += data
                sys.stdout.write(data.decode())
                sys.stdout.flush()

            if channel.recv_stderr_ready():
                stderr_data = channel.recv_stderr(4096)
                if stderr_data:
                    sys.stderr.write(stderr_data.decode())
                    sys.stderr.flush()

        returncode = channel.recv_exit_status()
        channel.close()
        r = subprocess.CompletedProcess(log_command, returncode, "Interactive command")
    else:
        _stdin, stdout, stderr = ssh.exec_command(command, **kwargs)

        returncode = stdout.channel.recv_exit_status()
        r = subprocess.CompletedProcess(log_command, returncode, stdout.read().decode(), stderr.read().decode())

    if cleanup_local_files and local_files:
        with ssh.open_sftp() as sftp:
            for file in local_files:
                remote_temp_file = os.path.join(library_dir, Path(file).name)
                with suppress(FileNotFoundError):
                    sftp.remove(remote_temp_file)

    log.debug(r.args)
    print_std(r.stdout, r.returncode == 0)
    print_std(r.stderr, r.returncode == 0)
    if r.returncode != 0:
        if error_verbosity == 0:
            log.debug("[%s] exited %s", command, r.returncode)
        elif error_verbosity == 1:
            log.info("[%s] exited %s", command, r.returncode)
        else:
            log.warning("[%s] exited %s", command, r.returncode)

        if strict:
            r.check_returncode()

    return r


def ssh_tempdir(ssh):
    r = remote_processes.cmd(ssh, "sh", "-c", "echo $XDG_RUNTIME_DIR")
    tempdir = r.stdout.strip()
    if tempdir:
        return tempdir

    r = remote_processes.cmd(ssh, "python3", "-c", "import tempfile; print(tempfile.gettempdir())")
    tempdir = r.stdout.strip()
    if tempdir:
        return tempdir


class PortForwardServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class PortForwardHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            chan = self.ssh_transport.open_channel(  # type: ignore
                "direct-tcpip", (self.chain_host, self.chain_port), self.request.getpeername()  # type: ignore
            )
        except Exception as e:
            log.error("Incoming request to %s:%d failed: %s", self.chain_host, self.chain_port, repr(e))  # type: ignore
            return
        if chan is None:
            log.error("Incoming request to %s:%d was rejected by the SSH server", self.chain_host, self.chain_port)  # type: ignore
            return

        log.debug(
            "Connected! Tunnel open %r -> %r -> %r",
            self.request.getpeername(),
            chan.getpeername(),
            (self.chain_host, self.chain_port),  # type: ignore
        )
        while True:
            r, w, x = select.select([self.request, chan], [], [])
            if self.request in r:
                data = self.request.recv(1024)
                if len(data) == 0:
                    break
                chan.send(data)
            if chan in r:
                data = chan.recv(1024)
                if len(data) == 0:
                    break
                self.request.send(data)

        peername = self.request.getpeername()
        chan.close()
        self.request.close()
        log.debug("Tunnel closed from %r", peername)


class SSHForwarder:
    def __init__(self, ssh_client, remote_port, local_port):
        # with SSHForwarder(ssh, remote_port, local_port):

        self.ssh_client = ssh_client
        self.remote_port = remote_port
        self.local_port = local_port
        self.server = None

    def __enter__(self):
        transport = self.ssh_client.get_transport()
        if not transport:
            raise Exception("SSH transport is not available")

        class SubHandler(PortForwardHandler):
            chain_host = "localhost"
            chain_port = self.remote_port
            ssh_transport = transport

        self.server = PortForwardServer(("", self.local_port), SubHandler)
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.daemon = True
        self.server_thread.start()
        log.debug("Port forwarding %s to localhost:%s started", self.remote_port, self.local_port)

    def __exit__(self, exc_type, exc_value, traceback):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
