"""
Stack Network Module

Network testing and performance estimation for Docker Compose stacks.
Handles SSH connectivity, network speed testing, and transfer time calculations.
"""

import asyncio
import subprocess
import time

import structlog

from ...core.config_loader import DockerHost
from ...utils import build_ssh_command, format_size


class StackNetwork:
    """Network testing and performance estimation for stack operations."""

    def __init__(self):
        self.logger = structlog.get_logger()

    async def test_network_connectivity(
        self, source_host: DockerHost, target_host: DockerHost
    ) -> tuple[bool, dict]:
        """Test network connectivity between source and target hosts.

        Args:
            source_host: Source host configuration
            target_host: Target host configuration

        Returns:
            Tuple of (connectivity_ok: bool, details: dict)
        """
        details = {
            "source_host": source_host.hostname,
            "target_host": target_host.hostname,
            "tests": {},
        }

        try:
            # Test 1: Basic SSH connectivity to both hosts
            ssh_tests = {}

            # Test source host SSH
            source_ssh_cmd = build_ssh_command(source_host) + ["echo 'SSH_OK'"]
            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(  # noqa: S603
                        source_ssh_cmd, capture_output=True, text=True, check=False, timeout=10
                    ),
                )
                ssh_tests["source_ssh"] = {
                    "success": result.returncode == 0 and "SSH_OK" in result.stdout,
                    "response_time": "< 10s",
                    "error": result.stderr if result.stderr else None,
                }
            except Exception as e:
                ssh_tests["source_ssh"] = {"success": False, "error": str(e)}

            # Test target host SSH
            target_ssh_cmd = build_ssh_command(target_host) + ["echo 'SSH_OK'"]
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(  # noqa: S603
                        target_ssh_cmd, capture_output=True, text=True, check=False, timeout=10
                    ),
                )
                ssh_tests["target_ssh"] = {
                    "success": result.returncode == 0 and "SSH_OK" in result.stdout,
                    "response_time": "< 10s",
                    "error": result.stderr if result.stderr else None,
                }
            except Exception as e:
                ssh_tests["target_ssh"] = {"success": False, "error": str(e)}

            details["tests"]["ssh_connectivity"] = ssh_tests

            # Test 2: Network speed test (small file transfer)
            speed_test = {}
            if ssh_tests["source_ssh"]["success"] and ssh_tests["target_ssh"]["success"]:
                try:
                    # Create a small test file on source (1MB)
                    create_test_file_cmd = source_ssh_cmd[:-1] + [
                        "dd if=/dev/zero of=/tmp/speed_test bs=1M count=1 2>/dev/null && echo 'FILE_CREATED'"  # noqa: S108
                    ]
                    result = await loop.run_in_executor(
                        None,
                        lambda: subprocess.run(  # noqa: S603
                            create_test_file_cmd,
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=15,
                        ),
                    )

                    if result.returncode == 0 and "FILE_CREATED" in result.stdout:
                        # Transfer the file using rsync
                        start_time = time.time()

                        rsync_test_cmd = source_ssh_cmd[:-1] + [
                            f"rsync -z /tmp/speed_test {target_host.user}@{target_host.hostname}:/tmp/speed_test_recv"  # noqa: S108
                            + (
                                f" -e 'ssh -i {target_host.identity_file}'"
                                if target_host.identity_file
                                else ""
                            )
                        ]

                        result = await loop.run_in_executor(
                            None,
                            lambda: subprocess.run(  # noqa: S603
                                rsync_test_cmd,
                                capture_output=True,
                                text=True,
                                check=False,
                                timeout=30,
                            ),
                        )

                        transfer_time = time.time() - start_time

                        if result.returncode == 0:
                            # Calculate rough network speed (1MB in transfer_time seconds)
                            mb_per_second = 1.0 / transfer_time if transfer_time > 0 else 0
                            mbps = mb_per_second * 8  # Convert MB/s to Mbps

                            speed_test = {
                                "success": True,
                                "transfer_time_seconds": transfer_time,
                                "estimated_speed": f"{mbps:.1f} Mbps",
                                "test_size": "1 MB",
                                "method": "rsync test",
                            }

                            # Cleanup test files
                            cleanup_source = source_ssh_cmd[:-1] + ["rm -f /tmp/speed_test"]  # noqa: S108
                            cleanup_target = target_ssh_cmd[:-1] + ["rm -f /tmp/speed_test_recv"]  # noqa: S108

                            await asyncio.gather(
                                loop.run_in_executor(
                                    None,
                                    lambda: subprocess.run(cleanup_source, check=False),  # noqa: S603
                                ),
                                loop.run_in_executor(
                                    None,
                                    lambda: subprocess.run(cleanup_target, check=False),  # noqa: S603
                                ),
                            )
                        else:
                            speed_test = {
                                "success": False,
                                "error": f"Rsync transfer failed: {result.stderr}",
                                "transfer_time_seconds": transfer_time,
                            }
                    else:
                        speed_test = {
                            "success": False,
                            "error": "Failed to create test file",
                            "create_result": result.stdout.strip(),
                        }

                except Exception as e:
                    speed_test = {"success": False, "error": f"Speed test failed: {str(e)}"}

            else:
                speed_test = {
                    "success": False,
                    "error": "SSH connectivity test failed, cannot perform speed test",
                }

            details["tests"]["network_speed"] = speed_test

            # Overall connectivity assessment
            connectivity_ok = ssh_tests.get("source_ssh", {}).get(
                "success", False
            ) and ssh_tests.get("target_ssh", {}).get("success", False)

            details["overall_connectivity"] = connectivity_ok

            return connectivity_ok, details

        except Exception as e:
            details["error"] = str(e)
            return False, details

    def estimate_transfer_time(
        self, data_size_bytes: int, network_speed_details: dict = None
    ) -> dict:
        """Estimate transfer time based on data size and network speed.

        Args:
            data_size_bytes: Size of data to transfer in bytes
            network_speed_details: Optional network speed test results

        Returns:
            Dict with transfer time estimates and details
        """
        estimates = {
            "data_size_bytes": data_size_bytes,
            "data_size_human": format_size(data_size_bytes),
            "compressed_size_bytes": int(data_size_bytes * 0.3),  # Assume 70% compression
            "compressed_size_human": format_size(int(data_size_bytes * 0.3)),
            "estimates": {},
        }

        # Use network speed if available, otherwise use standard estimates
        if network_speed_details and network_speed_details.get("success"):
            try:
                # Parse network speed (e.g., "50.2 Mbps")
                speed_str = network_speed_details.get("estimated_speed", "10.0 Mbps")
                speed_value = float(speed_str.split()[0])
                speed_unit = speed_str.split()[1] if len(speed_str.split()) > 1 else "Mbps"

                # Convert to bytes per second
                if speed_unit.lower() == "mbps":
                    bytes_per_second = (speed_value * 1_000_000) / 8  # Mbps to bytes/sec
                elif speed_unit.lower() == "gbps":
                    bytes_per_second = (speed_value * 1_000_000_000) / 8  # Gbps to bytes/sec
                else:
                    bytes_per_second = speed_value / 8  # Assume bps

                # Calculate transfer time for compressed data
                compressed_bytes = estimates["compressed_size_bytes"]
                if bytes_per_second > 0:
                    transfer_seconds = compressed_bytes / bytes_per_second

                    estimates["estimates"]["actual_network"] = {
                        "method": "measured",
                        "speed": speed_str,
                        "time_seconds": transfer_seconds,
                        "time_human": self.format_time(transfer_seconds),
                        "description": f"Based on actual network speed test ({speed_str})",
                    }

            except (ValueError, IndexError, TypeError):
                # Fall back to estimates if parsing fails
                pass

        # Always provide standard estimates for comparison
        standard_speeds = [
            ("10 Mbps", "Slow broadband", 10 * 1_000_000 / 8),
            ("100 Mbps", "Fast broadband", 100 * 1_000_000 / 8),
            ("1 Gbps", "Gigabit network", 1 * 1_000_000_000 / 8),
        ]

        compressed_bytes = estimates["compressed_size_bytes"]
        for speed_name, description, bytes_per_sec in standard_speeds:
            if bytes_per_sec > 0:
                transfer_seconds = compressed_bytes / bytes_per_sec
                estimates["estimates"][speed_name.replace(" ", "_").lower()] = {
                    "method": "estimate",
                    "speed": speed_name,
                    "time_seconds": transfer_seconds,
                    "time_human": self.format_time(transfer_seconds),
                    "description": description,
                }

        # Add overhead estimates (15-25% additional time for setup, verification, etc.)
        if estimates["estimates"]:
            for _estimate_key, estimate_data in estimates["estimates"].items():
                base_time = estimate_data["time_seconds"]
                with_overhead = base_time * 1.2  # 20% overhead
                estimate_data["time_with_overhead"] = with_overhead
                estimate_data["time_with_overhead_human"] = self.format_time(with_overhead)

        return estimates

    def format_time(self, seconds: float) -> str:
        """Format seconds into human-readable time string."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}m"
        elif seconds < 86400:
            hours = seconds / 3600
            return f"{hours:.1f}h"
        else:
            days = seconds / 86400
            return f"{days:.1f}d"

    async def measure_network_bandwidth(
        self, source_host: DockerHost, target_host: DockerHost, test_size_mb: int = 10
    ) -> dict:
        """Measure actual network bandwidth between hosts.

        Args:
            source_host: Source host configuration
            target_host: Target host configuration
            test_size_mb: Size of test file in MB (default: 10MB)

        Returns:
            Dict with bandwidth measurement results
        """
        result = {
            "success": False,
            "bandwidth_mbps": 0.0,
            "test_size_mb": test_size_mb,
            "transfer_time_seconds": 0.0,
            "error": None,
        }

        try:
            source_ssh_cmd = build_ssh_command(source_host)
            target_ssh_cmd = build_ssh_command(target_host)
            loop = asyncio.get_running_loop()

            # Create test file on source
            create_cmd = source_ssh_cmd + [
                f"dd if=/dev/zero of=/tmp/bandwidth_test bs=1M count={test_size_mb} 2>/dev/null && echo 'CREATED'"  # noqa: S108
            ]

            create_result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(  # noqa: S603
                    create_cmd, capture_output=True, text=True, check=False, timeout=30
                ),
            )

            if create_result.returncode != 0 or "CREATED" not in create_result.stdout:
                result["error"] = f"Failed to create test file: {create_result.stderr}"
                return result

            # Transfer file and measure time
            start_time = time.time()

            transfer_cmd = source_ssh_cmd + [
                f"rsync -z /tmp/bandwidth_test {target_host.user}@{target_host.hostname}:/tmp/bandwidth_test_recv"  # noqa: S108
                + (f" -e 'ssh -i {target_host.identity_file}'" if target_host.identity_file else "")
            ]

            transfer_result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(  # noqa: S603
                    transfer_cmd, capture_output=True, text=True, check=False, timeout=60
                ),
            )

            transfer_time = time.time() - start_time

            if transfer_result.returncode == 0:
                # Calculate bandwidth (MB/s to Mbps)
                mb_per_second = test_size_mb / transfer_time if transfer_time > 0 else 0
                mbps = mb_per_second * 8  # Convert MB/s to Mbps

                result.update(
                    {
                        "success": True,
                        "bandwidth_mbps": mbps,
                        "transfer_time_seconds": transfer_time,
                        "throughput_mb_per_sec": mb_per_second,
                    }
                )
            else:
                result["error"] = f"Transfer failed: {transfer_result.stderr}"

            # Cleanup test files
            cleanup_commands = [
                source_ssh_cmd + ["rm -f /tmp/bandwidth_test"],  # noqa: S108
                target_ssh_cmd + ["rm -f /tmp/bandwidth_test_recv"],  # noqa: S108
            ]

            await asyncio.gather(
                *[
                    loop.run_in_executor(
                        None,
                        lambda cmd=cmd: subprocess.run(cmd, check=False),  # noqa: S603
                    )
                    for cmd in cleanup_commands
                ]
            )

        except Exception as e:
            result["error"] = f"Bandwidth measurement failed: {str(e)}"

        return result
