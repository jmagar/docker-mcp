"""
Comprehensive tests for SSH config parser functionality.

Tests SSH config file parsing, host validation, and Docker host conversion.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from docker_mcp.core.config_loader import DockerHost
from docker_mcp.core.ssh_config_parser import SSHConfigEntry, SSHConfigParser


class TestSSHConfigEntry:
    """Test SSHConfigEntry creation and validation."""

    def test_ssh_config_entry_creation(self):
        """Test creating SSH config entry with basic properties."""
        entry = SSHConfigEntry("test-host")
        
        assert entry.name == "test-host"
        assert entry.hostname is None
        assert entry.user is None
        assert entry.port == 22
        assert entry.identity_file is None
        assert entry.other_options == {}

    def test_ssh_config_entry_to_docker_host_minimal(self):
        """Test converting minimal SSH entry to DockerHost."""
        entry = SSHConfigEntry("test-server")
        entry.hostname = "192.168.1.100"
        
        docker_host = entry.to_docker_host()
        
        assert isinstance(docker_host, DockerHost)
        assert docker_host.hostname == "192.168.1.100"
        assert docker_host.user == "root"  # Default user
        assert docker_host.port == 22
        assert docker_host.identity_file is None
        assert docker_host.description == "Imported from SSH config"
        assert docker_host.tags == ["imported", "ssh-config"]
        assert docker_host.enabled is True

    def test_ssh_config_entry_to_docker_host_complete(self):
        """Test converting complete SSH entry to DockerHost."""
        entry = SSHConfigEntry("production-server")
        entry.hostname = "prod.example.com"
        entry.user = "deploy"
        entry.port = 2222
        entry.identity_file = "/home/user/.ssh/prod_key"
        
        docker_host = entry.to_docker_host()
        
        assert docker_host.hostname == "prod.example.com"
        assert docker_host.user == "deploy"
        assert docker_host.port == 2222
        assert docker_host.identity_file == "/home/user/.ssh/prod_key"
        assert docker_host.description == "Imported from SSH config"
        assert docker_host.tags == ["imported", "ssh-config"]

    def test_ssh_config_entry_to_docker_host_no_hostname(self):
        """Test converting SSH entry without explicit hostname."""
        entry = SSHConfigEntry("web-server")
        entry.user = "admin"
        
        docker_host = entry.to_docker_host()
        
        # Should use name as hostname when hostname is None
        assert docker_host.hostname == "web-server"
        assert docker_host.user == "admin"

    def test_ssh_config_entry_is_valid_normal_host(self):
        """Test validation of normal SSH host entries."""
        entry = SSHConfigEntry("valid-host")
        entry.hostname = "example.com"
        entry.user = "user"
        
        assert entry.is_valid() is True

    def test_ssh_config_entry_is_valid_no_hostname(self):
        """Test validation of entry without explicit hostname."""
        entry = SSHConfigEntry("server.example.com")
        entry.user = "deploy"
        
        # Valid because hostname defaults to name
        assert entry.is_valid() is True

    def test_ssh_config_entry_is_valid_wildcard_entries(self):
        """Test validation rejects wildcard entries."""
        # Test asterisk wildcard
        entry1 = SSHConfigEntry("*.example.com")
        assert entry1.is_valid() is False
        
        # Test question mark wildcard
        entry2 = SSHConfigEntry("server?.example.com")
        assert entry2.is_valid() is False

    def test_ssh_config_entry_is_valid_localhost_entries(self):
        """Test validation rejects localhost entries."""
        # Test localhost name
        entry1 = SSHConfigEntry("localhost")
        assert entry1.is_valid() is False
        
        # Test 127.0.0.1
        entry2 = SSHConfigEntry("web-server")
        entry2.hostname = "127.0.0.1"
        assert entry2.is_valid() is False
        
        # Test IPv6 localhost
        entry3 = SSHConfigEntry("ipv6-local")
        entry3.hostname = "::1"
        assert entry3.is_valid() is False

    def test_ssh_config_entry_repr(self):
        """Test string representation of SSH config entry."""
        entry = SSHConfigEntry("test-host")
        entry.hostname = "example.com"
        entry.user = "testuser"
        entry.port = 2222
        
        repr_str = repr(entry)
        
        assert "SSHConfigEntry" in repr_str
        assert "test-host" in repr_str
        assert "example.com" in repr_str
        assert "testuser" in repr_str
        assert "2222" in repr_str


class TestSSHConfigParser:
    """Test SSH config file parsing functionality."""

    def test_ssh_config_parser_init_default_path(self):
        """Test SSH config parser initialization with default path."""
        parser = SSHConfigParser()
        
        expected_path = Path.home() / ".ssh" / "config"
        assert parser.config_path == expected_path

    def test_ssh_config_parser_init_custom_path(self):
        """Test SSH config parser initialization with custom path."""
        custom_path = "/custom/ssh/config"
        parser = SSHConfigParser(custom_path)
        
        assert parser.config_path == Path(custom_path)

    def test_ssh_config_parser_init_path_object(self):
        """Test SSH config parser initialization with Path object."""
        path_obj = Path("/tmp/ssh_config")
        parser = SSHConfigParser(path_obj)
        
        assert parser.config_path == path_obj

    def test_parse_file_not_found(self):
        """Test parsing when SSH config file doesn't exist."""
        parser = SSHConfigParser("/nonexistent/ssh/config")
        
        with pytest.raises(FileNotFoundError) as exc_info:
            parser.parse()
        
        assert "SSH config file not found" in str(exc_info.value)

    def test_parse_simple_config(self):
        """Test parsing simple SSH config with one host."""
        config_content = """
Host web-server
    HostName 192.168.1.100
    User deploy
    Port 2222
    IdentityFile ~/.ssh/web_server_key
"""
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".config", delete=False) as f:
            f.write(config_content)
            config_path = f.name
        
        try:
            parser = SSHConfigParser(config_path)
            entries = parser.parse()
            
            assert len(entries) == 1
            assert "web-server" in entries
            
            entry = entries["web-server"]
            assert entry.name == "web-server"
            assert entry.hostname == "192.168.1.100"
            assert entry.user == "deploy"
            assert entry.port == 2222
            assert entry.identity_file == str(Path("~/.ssh/web_server_key").expanduser())
            
        finally:
            Path(config_path).unlink()

    def test_parse_multiple_hosts(self):
        """Test parsing SSH config with multiple hosts."""
        config_content = """
# Production servers
Host web-prod
    HostName web.prod.example.com
    User www-data
    Port 22

Host db-prod
    HostName db.prod.example.com
    User postgres
    Port 5432

# Development server
Host dev-server
    HostName dev.example.com
    User developer
"""
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".config", delete=False) as f:
            f.write(config_content)
            config_path = f.name
        
        try:
            parser = SSHConfigParser(config_path)
            entries = parser.parse()
            
            assert len(entries) == 3
            assert "web-prod" in entries
            assert "db-prod" in entries
            assert "dev-server" in entries
            
            # Check web-prod details
            web_entry = entries["web-prod"]
            assert web_entry.hostname == "web.prod.example.com"
            assert web_entry.user == "www-data"
            assert web_entry.port == 22
            
            # Check db-prod details
            db_entry = entries["db-prod"]
            assert db_entry.hostname == "db.prod.example.com"
            assert db_entry.user == "postgres"
            assert db_entry.port == 5432
            
        finally:
            Path(config_path).unlink()

    def test_parse_with_comments_and_empty_lines(self):
        """Test parsing SSH config with comments and empty lines."""
        config_content = """
# This is a comment
    
Host test-host
    # Another comment
    HostName test.example.com
    
    User testuser  # Inline comment would be included in value
    Port 22

# Final comment
"""
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".config", delete=False) as f:
            f.write(config_content)
            config_path = f.name
        
        try:
            parser = SSHConfigParser(config_path)
            entries = parser.parse()
            
            assert len(entries) == 1
            entry = entries["test-host"]
            assert entry.hostname == "test.example.com"
            assert entry.user == "testuser  # Inline comment would be included in value"
            
        finally:
            Path(config_path).unlink()

    def test_parse_invalid_lines_skipped(self):
        """Test parsing SSH config with malformed lines that get skipped."""
        config_content = """
Host valid-host
    HostName example.com
    User testuser
    InvalidLineWithoutValue
    Port 22

Host another-host
    HostName another.com
"""
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".config", delete=False) as f:
            f.write(config_content)
            config_path = f.name
        
        try:
            parser = SSHConfigParser(config_path)
            entries = parser.parse()
            
            # Should still parse valid entries despite invalid lines
            assert len(entries) == 2
            assert "valid-host" in entries
            assert "another-host" in entries
            
            # Check that valid host was parsed correctly
            entry = entries["valid-host"]
            assert entry.hostname == "example.com"
            assert entry.user == "testuser"
            assert entry.port == 22
            
        finally:
            Path(config_path).unlink()

    def test_parse_key_value_formats(self):
        """Test parsing different SSH config key-value formats."""
        config_content = """
Host format-test
    HostName example.com
    User testuser
    Port 2222
    IdentityFile /path/to/key
    CustomOption value with spaces
"""
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".config", delete=False) as f:
            f.write(config_content)
            config_path = f.name
        
        try:
            parser = SSHConfigParser(config_path)
            entries = parser.parse()
            
            entry = entries["format-test"]
            assert entry.hostname == "example.com"
            assert entry.user == "testuser"  # Standard format
            assert entry.port == 2222
            assert entry.identity_file == "/path/to/key"
            assert entry.other_options["CustomOption"] == "value with spaces"
            
        finally:
            Path(config_path).unlink()

    def test_parse_invalid_port_ignored(self):
        """Test parsing SSH config with invalid port numbers."""
        config_content = """
Host port-test
    HostName example.com
    User testuser
    Port invalid-port
    IdentityFile /path/to/key
"""
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".config", delete=False) as f:
            f.write(config_content)
            config_path = f.name
        
        try:
            parser = SSHConfigParser(config_path)
            entries = parser.parse()
            
            entry = entries["port-test"]
            assert entry.port == 22  # Should remain default when invalid
            assert entry.hostname == "example.com"
            assert entry.user == "testuser"
            
        finally:
            Path(config_path).unlink()

    def test_parse_wildcard_hosts_excluded(self):
        """Test that wildcard hosts are parsed but excluded from results."""
        config_content = """
Host *.example.com
    User wildcard-user
    Port 2222

Host valid-host
    HostName example.com
    User normal-user

Host server?.domain.com
    User another-wildcard
"""
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".config", delete=False) as f:
            f.write(config_content)
            config_path = f.name
        
        try:
            parser = SSHConfigParser(config_path)
            entries = parser.parse()
            
            # Only valid-host should be included (wildcard hosts are invalid)
            assert len(entries) == 1
            assert "valid-host" in entries
            assert "*.example.com" not in entries
            assert "server?.domain.com" not in entries
            
        finally:
            Path(config_path).unlink()

    def test_get_importable_hosts(self):
        """Test getting list of importable hosts."""
        config_content = """
Host prod-web
    HostName web.prod.example.com
    User www-data

Host localhost
    HostName 127.0.0.1
    User local

Host *.wildcard
    HostName wildcard.example.com
    User wild

Host valid-server
    HostName server.example.com
    User admin
"""
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".config", delete=False) as f:
            f.write(config_content)
            config_path = f.name
        
        try:
            parser = SSHConfigParser(config_path)
            importable_hosts = parser.get_importable_hosts()
            
            # Should only include valid, non-localhost, non-wildcard hosts
            assert len(importable_hosts) == 2
            
            # Should be sorted by name
            host_names = [host.name for host in importable_hosts]
            assert host_names == ["prod-web", "valid-server"]
            
            # Check that hosts are properly configured
            prod_host = next(h for h in importable_hosts if h.name == "prod-web")
            assert prod_host.hostname == "web.prod.example.com"
            assert prod_host.user == "www-data"
            
        finally:
            Path(config_path).unlink()

    def test_get_importable_hosts_file_not_found(self):
        """Test getting importable hosts when config file doesn't exist."""
        parser = SSHConfigParser("/nonexistent/config")
        
        with pytest.raises(FileNotFoundError):
            parser.get_importable_hosts()

    def test_validate_config_file_valid(self):
        """Test validating a valid SSH config file."""
        config_content = """
Host web-server
    HostName example.com
    User deploy

Host db-server
    HostName db.example.com
    User postgres
"""
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".config", delete=False) as f:
            f.write(config_content)
            config_path = f.name
        
        try:
            parser = SSHConfigParser(config_path)
            is_valid, message = parser.validate_config_file()
            
            assert is_valid is True
            assert "2 valid SSH host entries" in message
            
        finally:
            Path(config_path).unlink()

    def test_validate_config_file_not_found(self):
        """Test validating non-existent SSH config file."""
        parser = SSHConfigParser("/nonexistent/config")
        
        is_valid, message = parser.validate_config_file()
        
        assert is_valid is False
        assert "SSH config file not found" in message

    def test_validate_config_file_no_valid_entries(self):
        """Test validating SSH config file with no valid entries."""
        config_content = """
Host *.wildcard
    HostName wildcard.example.com

Host localhost
    HostName 127.0.0.1
"""
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".config", delete=False) as f:
            f.write(config_content)
            config_path = f.name
        
        try:
            parser = SSHConfigParser(config_path)
            is_valid, message = parser.validate_config_file()
            
            assert is_valid is False
            assert "No valid SSH host entries found" in message
            
        finally:
            Path(config_path).unlink()

    def test_validate_config_file_parse_error(self):
        """Test validating SSH config file with parse errors."""
        # Create a parser with a path that exists but can't be read
        parser = SSHConfigParser("/tmp/test_config")
        
        # Mock the exists method and open to simulate a permission error
        with patch("pathlib.Path.exists", return_value=True):
            with patch("builtins.open", side_effect=PermissionError("Permission denied")):
                is_valid, message = parser.validate_config_file()
                
                assert is_valid is False
                assert "Error parsing SSH config" in message


class TestSSHConfigParserIntegration:
    """Integration tests for SSH config parser functionality."""

    def test_full_workflow_ssh_to_docker_hosts(self):
        """Test complete workflow: parse SSH config and convert to Docker hosts."""
        config_content = """
# Production environment
Host web-prod
    HostName web.production.example.com
    User deploy
    Port 22
    IdentityFile ~/.ssh/production_key

Host db-prod
    HostName db.production.example.com
    User postgres
    Port 5432

# Development environment  
Host dev-server
    HostName dev.example.com
    User developer
    Port 2222

# Should be excluded
Host *.example.com
    User wildcard

Host localhost
    HostName 127.0.0.1
"""
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".config", delete=False) as f:
            f.write(config_content)
            config_path = f.name
        
        try:
            # Parse SSH config
            parser = SSHConfigParser(config_path)
            importable_hosts = parser.get_importable_hosts()
            
            # Convert to Docker hosts
            docker_hosts = []
            for ssh_entry in importable_hosts:
                docker_host = ssh_entry.to_docker_host()
                docker_hosts.append(docker_host)
            
            # Verify results
            assert len(docker_hosts) == 3
            
            # Check each Docker host
            host_dict = {host.hostname: host for host in docker_hosts}
            
            # Web production server
            web_host = host_dict["web.production.example.com"]
            assert web_host.user == "deploy"
            assert web_host.port == 22
            assert web_host.identity_file == str(Path("~/.ssh/production_key").expanduser())
            assert "imported" in web_host.tags
            assert "ssh-config" in web_host.tags
            
            # DB production server
            db_host = host_dict["db.production.example.com"]
            assert db_host.user == "postgres"
            assert db_host.port == 5432
            
            # Development server
            dev_host = host_dict["dev.example.com"]
            assert dev_host.user == "developer"
            assert dev_host.port == 2222
            
            # All should be enabled and have import tags
            for host in docker_hosts:
                assert host.enabled is True
                assert host.description == "Imported from SSH config"
                assert "imported" in host.tags
                assert "ssh-config" in host.tags
                
        finally:
            Path(config_path).unlink()

    def test_edge_cases_and_error_handling(self):
        """Test edge cases and error handling in SSH config parsing."""
        config_content = """
# Empty host entry (should be skipped)
Host

# Host with only name
Host name-only-host

# Host with minimal valid config
Host minimal-host
    HostName minimal.example.com

# Host with all options
Host complete-host
    HostName complete.example.com
    User complete-user
    Port 443
    IdentityFile /complete/path/key
    CustomOption1 value1
    CustomOption2 value with multiple words
"""
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".config", delete=False) as f:
            f.write(config_content)
            config_path = f.name
        
        try:
            parser = SSHConfigParser(config_path)
            entries = parser.parse()
            
            # Should handle edge cases gracefully
            assert "name-only-host" in entries
            assert "minimal-host" in entries
            assert "complete-host" in entries
            
            # Check name-only host defaults
            name_only = entries["name-only-host"]
            assert name_only.hostname is None  # No explicit hostname
            assert name_only.user is None      # No explicit user
            assert name_only.is_valid() is True  # Still valid
            
            # Check complete host
            complete = entries["complete-host"]
            assert complete.hostname == "complete.example.com"
            assert complete.user == "complete-user"
            assert complete.port == 443
            assert complete.identity_file == "/complete/path/key"
            assert complete.other_options["CustomOption1"] == "value1"
            assert complete.other_options["CustomOption2"] == "value with multiple words"
            
        finally:
            Path(config_path).unlink()