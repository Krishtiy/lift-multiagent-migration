"""Tests for sandbox runner safety contracts and Docker parameter enforcement."""

import sys
import unittest
from unittest.mock import MagicMock

from lift.sandbox.runner import (
    DockerSandboxRunner,
    SandboxLimits,
    SandboxResult,
    SubprocessSandboxRunner,
)


class TestDockerSandboxParameters(unittest.TestCase):
    """Verify Docker container is configured with mandatory security limits."""

    def test_security_parameters_passed_to_docker(self):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.attrs = {"State": {"OOMKilled": False, "ExitCode": 0}}
        mock_container.logs.return_value = b"test output"
        mock_client.containers.create.return_value = mock_container

        runner = DockerSandboxRunner(client=mock_client)
        limits = SandboxLimits(
            memory_limit_mb=128,
            pids_limit=32,
            network_disabled=True,
            read_only_root=True,
            timeout_seconds=5.0,
        )

        result = runner.run(
            image="python:3.11",
            cmd=["python", "-c", "print('hello')"],
            limits=limits,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.stdout, "test output")

        # Verify docker creation kwargs had all security controls
        mock_client.containers.create.assert_called_once()
        _, kwargs = mock_client.containers.create.call_args
        self.assertEqual(kwargs["network_mode"], "none")
        self.assertEqual(kwargs["read_only"], True)
        self.assertEqual(kwargs["mem_limit"], "128m")
        self.assertEqual(kwargs["pids_limit"], 32)
        self.assertEqual(kwargs["cap_drop"], ["ALL"])

    def test_docker_timeout_kills_container(self):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.wait.side_effect = Exception("Read timed out")
        mock_container.attrs = {"State": {"OOMKilled": False, "ExitCode": -1}}
        mock_container.logs.return_value = b""
        mock_client.containers.create.return_value = mock_container

        runner = DockerSandboxRunner(client=mock_client)
        limits = SandboxLimits(timeout_seconds=1.0)

        result = runner.run(
            image="python:3.11",
            cmd=["python", "-c", "while True: pass"],
            limits=limits,
        )

        self.assertTrue(result.timed_out)
        mock_container.kill.assert_called_once()
        mock_container.remove.assert_called_once_with(force=True)

    def test_docker_oom_killed_flag(self):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 137}
        mock_container.attrs = {"State": {"OOMKilled": True, "ExitCode": 137}}
        mock_container.logs.return_value = b"Killed\n"
        mock_client.containers.create.return_value = mock_container

        runner = DockerSandboxRunner(client=mock_client)
        result = runner.run(
            image="python:3.11",
            cmd=["python", "-c", "a = ' ' * 10**9"],
            limits=SandboxLimits(memory_limit_mb=64),
        )

        self.assertTrue(result.oom_killed)
        self.assertEqual(result.exit_code, 137)


class TestSandboxSafetyExecution(unittest.TestCase):
    """Test safety constraints using the active runner."""

    def setUp(self):
        self.runner = SubprocessSandboxRunner()

    def test_infinite_loop_killed_by_timeout(self):
        """Safety Test 1: Infinite loop must be terminated by timeout cap."""
        limits = SandboxLimits(timeout_seconds=0.5)
        result = self.runner.run(
            image="local",
            cmd=[sys.executable, "-c", "import time; time.sleep(10)"],
            limits=limits,
        )
        self.assertTrue(result.timed_out)
        self.assertFalse(result.success)

    def test_output_truncation_limits_memory_leak(self):
        """Verify runaway stdout is truncated to prevent host memory exhaustion."""
        limits = SandboxLimits(timeout_seconds=5.0, max_output_bytes=500)
        result = self.runner.run(
            image="local",
            cmd=[sys.executable, "-c", "print('A' * 20000)"],
            limits=limits,
        )
        self.assertTrue(result.success)
        self.assertLessEqual(len(result.stdout), 500)

    def test_successful_execution(self):
        """Verify normal compliant command completes successfully."""
        result = self.runner.run(
            image="local",
            cmd=[sys.executable, "-c", "print('sandbox_ready')"],
        )
        self.assertTrue(result.success)
        self.assertIn("sandbox_ready", result.stdout)


if __name__ == "__main__":
    unittest.main()
