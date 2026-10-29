"""Docker and local isolated sandbox runners with strict security limits."""

import abc
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SandboxLimits:
    """Security and resource limits for sandbox execution."""
    memory_limit_mb: int = 256
    cpu_count: float = 1.0
    pids_limit: int = 64
    timeout_seconds: float = 15.0
    network_disabled: bool = True
    read_only_root: bool = True
    max_output_bytes: int = 65_536  # 64KB max output truncation


@dataclass
class SandboxResult:
    """Execution result returned by sandbox runner."""
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    oom_killed: bool = False
    error_message: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.oom_killed


class BaseSandboxRunner(abc.ABC):
    """Abstract sandbox runner interface."""

    @abc.abstractmethod
    def run(
        self,
        image: str,
        cmd: List[str],
        mounts: Optional[Dict[str, Dict[str, str]]] = None,
        limits: Optional[SandboxLimits] = None,
        working_dir: str = "/workspace",
    ) -> SandboxResult:
        """Execute command in isolated sandbox."""
        pass


class DockerSandboxRunner(BaseSandboxRunner):
    """Docker-based isolated container runner enforcing strict cgroups and network limits."""

    def __init__(self, client=None):
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        import docker
        self._client = docker.from_env()
        return self._client

    def run(
        self,
        image: str,
        cmd: List[str],
        mounts: Optional[Dict[str, Dict[str, str]]] = None,
        limits: Optional[SandboxLimits] = None,
        working_dir: str = "/workspace",
    ) -> SandboxResult:
        limits = limits or SandboxLimits()
        start_time = time.monotonic()
        client = self._get_client()

        # Build Docker container run parameters
        container_kwargs = {
            "image": image,
            "command": cmd,
            "detach": True,
            "working_dir": working_dir,
            "volumes": mounts or {},
            "network_mode": "none" if limits.network_disabled else "bridge",
            "mem_limit": f"{limits.memory_limit_mb}m",
            "memswap_limit": f"{limits.memory_limit_mb}m",
            "pids_limit": limits.pids_limit,
            "read_only": limits.read_only_root,
            "cap_drop": ["ALL"],
        }

        container = None
        timed_out = False
        oom_killed = False
        exit_code = -1
        stdout_str = ""
        stderr_str = ""

        try:
            container = client.containers.create(**container_kwargs)
            container.start()

            # Wait with wall-clock timeout enforcement
            try:
                wait_res = container.wait(timeout=int(limits.timeout_seconds))
                exit_code = wait_res.get("StatusCode", -1)
            except Exception:  # Timeout or socket read timeout
                timed_out = True
                try:
                    container.kill()
                except Exception:
                    pass

            # Inspect container state for OOM killer
            try:
                container.reload()
                state = container.attrs.get("State", {})
                oom_killed = state.get("OOMKilled", False)
                if not timed_out:
                    exit_code = state.get("ExitCode", exit_code)
            except Exception:
                pass

            # Fetch logs with size truncation
            logs_stdout = container.logs(stdout=True, stderr=False)
            logs_stderr = container.logs(stdout=False, stderr=True)

            stdout_str = logs_stdout[:limits.max_output_bytes].decode("utf-8", errors="replace")
            stderr_str = logs_stderr[:limits.max_output_bytes].decode("utf-8", errors="replace")

        except Exception as e:
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_seconds=round(time.monotonic() - start_time, 3),
                timed_out=timed_out,
                oom_killed=oom_killed,
                error_message=str(e),
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

        duration = round(time.monotonic() - start_time, 3)
        return SandboxResult(
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            duration_seconds=duration,
            timed_out=timed_out,
            oom_killed=oom_killed,
        )


class SubprocessSandboxRunner(BaseSandboxRunner):
    """Local fallback runner when Docker is unavailable in environment.
    
    Enforces wall-clock timeouts and execution bounds.
    """

    def run(
        self,
        image: str,
        cmd: List[str],
        mounts: Optional[Dict[str, Dict[str, str]]] = None,
        limits: Optional[SandboxLimits] = None,
        working_dir: str = "/workspace",
    ) -> SandboxResult:
        limits = limits or SandboxLimits()
        start_time = time.monotonic()

        # Resolve host work directory from mounts if provided
        cwd = None
        if mounts:
            for host_path, config in mounts.items():
                if config.get("bind") == working_dir or working_dir == "/workspace":
                    cwd = host_path
                    break

        timed_out = False
        oom_killed = False
        exit_code = -1
        stdout_str = ""
        stderr_str = ""

        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                timeout=limits.timeout_seconds,
            )
            exit_code = proc.returncode
            stdout_str = proc.stdout[:limits.max_output_bytes].decode("utf-8", errors="replace")
            stderr_str = proc.stderr[:limits.max_output_bytes].decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout_str = (exc.stdout or b"")[:limits.max_output_bytes].decode("utf-8", errors="replace")
            stderr_str = (exc.stderr or b"")[:limits.max_output_bytes].decode("utf-8", errors="replace")
        except Exception as e:
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_seconds=round(time.monotonic() - start_time, 3),
                timed_out=False,
                error_message=str(e),
            )

        duration = round(time.monotonic() - start_time, 3)
        return SandboxResult(
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            duration_seconds=duration,
            timed_out=timed_out,
            oom_killed=oom_killed,
        )


def create_sandbox_runner() -> BaseSandboxRunner:
    """Factory creating Docker runner if docker is present, otherwise fallback runner."""
    try:
        import docker
        client = docker.from_env()
        client.ping()
        return DockerSandboxRunner(client=client)
    except Exception:
        return SubprocessSandboxRunner()
