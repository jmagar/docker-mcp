"""SSH key rotation and management utilities."""

import asyncio
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple
import hashlib
import json

import structlog

from docker_mcp.core.exceptions import DockerMCPError

logger = structlog.get_logger()


class SSHKeyRotationError(DockerMCPError):
    """SSH key rotation error."""
    pass


class SSHKeyManager:
    """Manages SSH key rotation and lifecycle."""
    
    def __init__(
        self,
        key_directory: str = "/etc/docker-mcp/ssh-keys",
        rotation_days: int = 90,
        key_type: str = "ed25519",
        key_bits: int = 4096
    ):
        """Initialize SSH key manager.
        
        Args:
            key_directory: Directory to store SSH keys
            rotation_days: Days before key rotation is required
            key_type: SSH key type (rsa, ed25519, ecdsa)
            key_bits: Key size in bits (for RSA)
        """
        self.key_directory = Path(key_directory)
        self.rotation_days = rotation_days
        self.key_type = key_type
        self.key_bits = key_bits
        
        # Create key directory if it doesn't exist
        self.key_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        
        # Metadata file for tracking key lifecycle
        self.metadata_file = self.key_directory / "key_metadata.json"
    
    def _load_metadata(self) -> dict:
        """Load key metadata from file.
        
        Returns:
            Key metadata dictionary
        """
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load metadata: {e}")
        return {}
    
    def _save_metadata(self, metadata: dict) -> None:
        """Save key metadata to file.
        
        Args:
            metadata: Metadata dictionary to save
        """
        try:
            with open(self.metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2, default=str)
            os.chmod(self.metadata_file, 0o600)
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")
    
    async def generate_key_pair(
        self,
        host_id: str,
        comment: str = ""
    ) -> Tuple[str, str]:
        """Generate a new SSH key pair for a host.
        
        Args:
            host_id: Host identifier
            comment: Key comment
            
        Returns:
            Tuple of (private_key_path, public_key_path)
            
        Raises:
            SSHKeyRotationError: If key generation fails
        """
        # Create host-specific key directory
        host_key_dir = self.key_directory / host_id
        host_key_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        
        # Generate timestamp for key naming
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        key_name = f"{host_id}_{timestamp}"
        
        private_key_path = host_key_dir / f"{key_name}"
        public_key_path = host_key_dir / f"{key_name}.pub"
        
        # Build ssh-keygen command
        cmd = [
            "ssh-keygen",
            "-t", self.key_type,
            "-f", str(private_key_path),
            "-N", "",  # No passphrase
            "-C", comment or f"{host_id}@docker-mcp"
        ]
        
        # Add key size for RSA
        if self.key_type == "rsa":
            cmd.extend(["-b", str(self.key_bits)])
        
        try:
            # Generate key pair
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True
                )
            )
            
            # Set secure permissions
            os.chmod(private_key_path, 0o600)
            os.chmod(public_key_path, 0o644)
            
            # Calculate key fingerprint
            fingerprint = await self._get_key_fingerprint(str(public_key_path))
            
            # Update metadata
            metadata = self._load_metadata()
            if host_id not in metadata:
                metadata[host_id] = {"keys": []}
            
            metadata[host_id]["keys"].append({
                "timestamp": timestamp,
                "created_at": datetime.utcnow().isoformat(),
                "fingerprint": fingerprint,
                "type": self.key_type,
                "active": True,
                "private_key": str(private_key_path),
                "public_key": str(public_key_path)
            })
            
            # Mark previous keys as inactive
            for key in metadata[host_id]["keys"][:-1]:
                key["active"] = False
            
            self._save_metadata(metadata)
            
            logger.info(
                "Generated new SSH key pair",
                host_id=host_id,
                key_type=self.key_type,
                fingerprint=fingerprint
            )
            
            return str(private_key_path), str(public_key_path)
            
        except subprocess.CalledProcessError as e:
            raise SSHKeyRotationError(f"Failed to generate SSH key: {e}") from e
    
    async def _get_key_fingerprint(self, public_key_path: str) -> str:
        """Get SSH key fingerprint.
        
        Args:
            public_key_path: Path to public key
            
        Returns:
            Key fingerprint
        """
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["ssh-keygen", "-l", "-f", public_key_path],
                    check=True,
                    capture_output=True,
                    text=True
                )
            )
            # Extract fingerprint from output
            parts = result.stdout.strip().split()
            if len(parts) >= 2:
                return parts[1]
            return ""
        except Exception:
            return ""
    
    async def rotate_key(
        self,
        host_id: str,
        hostname: str,
        username: str,
        current_key_path: Optional[str] = None
    ) -> Tuple[str, str]:
        """Rotate SSH key for a host.
        
        Args:
            host_id: Host identifier
            hostname: Target hostname
            username: SSH username
            current_key_path: Current key path for authentication
            
        Returns:
            Tuple of (new_private_key_path, new_public_key_path)
            
        Raises:
            SSHKeyRotationError: If rotation fails
        """
        # Generate new key pair
        new_private_key, new_public_key = await self.generate_key_pair(host_id)
        
        # Read new public key
        with open(new_public_key, 'r') as f:
            new_public_key_content = f.read().strip()
        
        # Deploy new public key to remote host
        if current_key_path:
            try:
                await self._deploy_public_key(
                    hostname,
                    username,
                    current_key_path,
                    new_public_key_content
                )
                
                # Verify new key works
                await self._verify_key_access(
                    hostname,
                    username,
                    new_private_key
                )
                
                # Archive old key
                await self._archive_old_key(host_id, current_key_path)
                
                logger.info(
                    "Successfully rotated SSH key",
                    host_id=host_id,
                    hostname=hostname
                )
                
            except Exception as e:
                # Rollback on failure
                os.unlink(new_private_key)
                os.unlink(new_public_key)
                raise SSHKeyRotationError(f"Key rotation failed: {e}") from e
        
        return new_private_key, new_public_key
    
    async def _deploy_public_key(
        self,
        hostname: str,
        username: str,
        current_key_path: str,
        public_key_content: str
    ) -> None:
        """Deploy public key to remote host.
        
        Args:
            hostname: Target hostname
            username: SSH username
            current_key_path: Current key for authentication
            public_key_content: New public key content
        """
        # Create temporary script for atomic key update
        script = f"""
#!/bin/bash
set -e
mkdir -p ~/.ssh
chmod 700 ~/.ssh
echo '{public_key_content}' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
# Remove duplicate keys
sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys
"""
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
            f.write(script)
            script_path = f.name
        
        try:
            # Deploy script via SSH
            ssh_cmd = [
                "ssh",
                "-i", current_key_path,
                "-o", "StrictHostKeyChecking=yes",
                "-o", "PasswordAuthentication=no",
                "-o", "BatchMode=yes",
                f"{username}@{hostname}",
                f"bash -s < {script_path}"
            ]
            
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ssh_cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
            )
        finally:
            os.unlink(script_path)
    
    async def _verify_key_access(
        self,
        hostname: str,
        username: str,
        key_path: str
    ) -> None:
        """Verify SSH access with new key.
        
        Args:
            hostname: Target hostname
            username: SSH username
            key_path: Key path to verify
            
        Raises:
            SSHKeyRotationError: If verification fails
        """
        ssh_cmd = [
            "ssh",
            "-i", key_path,
            "-o", "StrictHostKeyChecking=yes",
            "-o", "PasswordAuthentication=no",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            f"{username}@{hostname}",
            "echo 'OK'"
        ]
        
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ssh_cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15
                )
            )
            
            if "OK" not in result.stdout:
                raise SSHKeyRotationError("Key verification failed")
                
        except subprocess.CalledProcessError as e:
            raise SSHKeyRotationError(f"Cannot verify key access: {e}") from e
    
    async def _archive_old_key(self, host_id: str, old_key_path: str) -> None:
        """Archive old SSH key.
        
        Args:
            host_id: Host identifier
            old_key_path: Path to old key
        """
        archive_dir = self.key_directory / host_id / "archive"
        archive_dir.mkdir(exist_ok=True, mode=0o700)
        
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        old_key_name = Path(old_key_path).name
        
        # Move old keys to archive
        if Path(old_key_path).exists():
            archive_path = archive_dir / f"{timestamp}_{old_key_name}"
            Path(old_key_path).rename(archive_path)
            os.chmod(archive_path, 0o600)
            
        # Archive public key too
        old_public_key = f"{old_key_path}.pub"
        if Path(old_public_key).exists():
            archive_public = archive_dir / f"{timestamp}_{old_key_name}.pub"
            Path(old_public_key).rename(archive_public)
            os.chmod(archive_public, 0o644)
    
    def check_rotation_needed(self, host_id: str) -> bool:
        """Check if key rotation is needed for a host.
        
        Args:
            host_id: Host identifier
            
        Returns:
            True if rotation is needed
        """
        metadata = self._load_metadata()
        
        if host_id not in metadata:
            return True
        
        # Find active key
        active_keys = [
            k for k in metadata[host_id].get("keys", [])
            if k.get("active", False)
        ]
        
        if not active_keys:
            return True
        
        # Check age of active key
        latest_key = active_keys[-1]
        created_at = datetime.fromisoformat(latest_key["created_at"])
        age = datetime.utcnow() - created_at
        
        return age > timedelta(days=self.rotation_days)
    
    def get_active_key(self, host_id: str) -> Optional[str]:
        """Get active SSH key path for a host.
        
        Args:
            host_id: Host identifier
            
        Returns:
            Path to active private key or None
        """
        metadata = self._load_metadata()
        
        if host_id not in metadata:
            return None
        
        # Find active key
        active_keys = [
            k for k in metadata[host_id].get("keys", [])
            if k.get("active", False)
        ]
        
        if active_keys:
            return active_keys[-1].get("private_key")
        
        return None
    
    async def cleanup_old_keys(self, host_id: str, keep_count: int = 3) -> None:
        """Clean up old archived keys.
        
        Args:
            host_id: Host identifier
            keep_count: Number of archived keys to keep
        """
        archive_dir = self.key_directory / host_id / "archive"
        
        if not archive_dir.exists():
            return
        
        # Get all archived keys sorted by modification time
        archived_keys = sorted(
            archive_dir.glob("*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        # Keep only the specified number of recent keys
        for old_key in archived_keys[keep_count * 2:]:  # *2 for private and public keys
            try:
                old_key.unlink()
                logger.debug(f"Removed old archived key: {old_key}")
            except Exception as e:
                logger.warning(f"Failed to remove old key {old_key}: {e}")