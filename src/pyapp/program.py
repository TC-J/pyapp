import asyncio
import io
import os
import socket
import subprocess
from typing import List, Optional, Union, overload, Literal, Any, Dict

# ==========================================
# TYPE ALIASES & STRUCTURAL PAYLOADS
# ==========================================
PipeOutTarget = Union[str, bytes, os.PathLike, io.IOBase, socket.socket, int]
StderrTarget = Union[Literal["stdout", "devnull"], PipeOutTarget]

class ProcessResult:
    """Stores the structured results of a completed synchronous shell command execution."""
    def __init__(self, stdout: Union[str, bytes], stderr: Union[str, bytes], returncode: int, is_binary: bool):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.success = (returncode == 0)
        self.is_binary = is_binary

    def pipe(self, next_program: 'Program', *args: str, **kwargs) -> 'ProcessResult':
        """Fluent method to pipe this result's stdout into another sync program wrapper."""
        return next_program.run(*args, pipe_in=self.stdout, **kwargs)

    def pipe_err(self, next_program: 'Program', *args: str, **kwargs) -> 'ProcessResult':
        """Fluent method to pipe this result's stderr into another sync program wrapper."""
        return next_program.run(*args, pipe_in=self.stderr, **kwargs)

    def __repr__(self) -> str:
        return f"CommandResult(success={self.success}, returncode={self.returncode}, is_binary={self.is_binary})"


# ==========================================
# INTERNAL CONTEXT STREAM RESOLVER
# ==========================================
class StreamResolver:
    """Manages tracking, type checking, and safety-flushing for routed file and socket handles."""
    def __init__(self, binary: bool):
        self.binary = binary
        self.files_to_close: List[io.IOBase] = []
        self.sockets_to_close: List[io.IOBase] = []

    def resolve(self, target: Any, is_stderr: bool = False) -> Any:
        if target is None:
            return subprocess.PIPE
        if target == "stdout" and is_stderr:
            return subprocess.STDOUT
        if target == "devnull":
            return subprocess.DEVNULL
        if isinstance(target, (str, bytes, os.PathLike)):
            mode = 'wb' if self.binary else 'w'
            f = open(target, mode, encoding=None if self.binary else 'utf-8')
            self.files_to_close.append(f)
            return f
        if isinstance(target, socket.socket):
            mode = 'wb' if self.binary else 'w'
            sock_file = target.makefile(mode)
            self.sockets_to_close.append(sock_file)
            return sock_file
        if isinstance(target, (io.IOBase, int)):
            return target
        raise ValueError(f"Invalid stream routing target: {target}")

    def cleanup(self):
        for f in self.files_to_close:
            f.close()
        for s in self.sockets_to_close:
            s.flush()
            s.close()


# ==========================================
# INTERFACE 1: SYNCHRONOUS PROCESS ENGINE
# ==========================================
class Program:
    """A synchronous wrapper class to execute blocking shell pipelines with advanced routing capabilities."""
    def __init__(self, program_name: str, default_timeout: Optional[float] = None):
        self.program_name = program_name
        self.default_timeout = default_timeout

    @overload
    def run(
        self, *args: str, pipe_in: Optional[Union[str, bytes]] = None, 
        pipe_out: Optional[PipeOutTarget] = None,
        binary: Literal[False] = False, 
        stderr_redirect: Optional[StderrTarget] = None, 
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None, check: bool = False
    ) -> ProcessResult: ...

    @overload
    def run(
        self, *args: str, pipe_in: Optional[Union[str, bytes]] = None, 
        pipe_out: Optional[PipeOutTarget] = None,
        binary: Literal[True] = True, stderr_redirect: Optional[StderrTarget] = None, 
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None, check: bool = False
    ) -> ProcessResult: ...

    def run(
        self, *args: str, 
        pipe_in: Optional[Union[str, bytes]] = None, 
        pipe_out: Optional[PipeOutTarget] = None,
        binary: bool = False, stderr_redirect: Optional[StderrTarget] = None, 
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None, check: bool = False
    ) -> ProcessResult:
        
        full_command = [self.program_name] + list(args)
        chosen_timeout = timeout if timeout is not None else self.default_timeout
        resolver = StreamResolver(binary)

        stdout_stream = resolver.resolve(pipe_out, is_stderr=False)
        stderr_stream = resolver.resolve(stderr_redirect, is_stderr=True)

        current_env = os.environ.copy()
        if env:
            current_env.update(env)

        if pipe_in is not None and not binary and isinstance(pipe_in, bytes):
            pipe_in = pipe_in.decode('utf-8', errors='replace')

        try:
            process = subprocess.run(
                full_command,
                input=pipe_in,
                stdout=stdout_stream,
                stderr=stderr_stream,
                text=not binary,
                env=current_env,
                check=check,
                timeout=chosen_timeout
            )
            
            stdout_out = b"" if binary else f"[Stdout redirected]" if pipe_out else (process.stdout or "").strip()
            stderr_out = b"" if binary else f"[Stderr redirected]" if stderr_redirect else (process.stderr or "").strip()
            if pipe_out is None and binary: stdout_out = process.stdout
            if stderr_redirect is None and binary: stderr_out = process.stderr

            return ProcessResult(stdout_out, stderr_out, process.returncode, is_binary=binary)
            
        except subprocess.TimeoutExpired:
            err = f"Error: Command timed out after {chosen_timeout} seconds."
            return ProcessResult(b"" if binary else "", err.encode() if binary else err, -1, is_binary=binary)
        except FileNotFoundError:
            err = f"Error: Program '{self.program_name}' not found."
            return ProcessResult(b"" if binary else "", err.encode() if binary else err, -1, is_binary=binary)
        finally:
            resolver.cleanup()
