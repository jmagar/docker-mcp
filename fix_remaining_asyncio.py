#!/usr/bin/env python3
"""
Convert remaining run_in_executor patterns to asyncio.to_thread
"""

import re
import subprocess
from pathlib import Path


def fix_file(file_path: Path) -> bool:
    """Fix asyncio patterns in a single file."""
    try:
        content = file_path.read_text()
        original_content = content

        # Pattern 1: loop = asyncio.get_running_loop() followed by loop.run_in_executor
        pattern1 = re.compile(
            r"(\s*)loop = asyncio\.get_running_loop\(\)\s*\n"
            r"(\s*)result = await loop\.run_in_executor\(\s*\n"
            r"(\s*)None,\s*\n"
            r"(\s*)(lambda: subprocess\.run\([^)]+\)),\s*(#[^\n]*)?\s*\n"
            r"(\s*)\)",
            re.MULTILINE | re.DOTALL,
        )

        def replace_pattern1(match):
            indent1 = match.group(1)
            indent2 = match.group(2)
            lambda_call = match.group(5)
            comment = match.group(6) or ""

            # Extract subprocess.run call from lambda
            lambda_match = re.match(r"lambda: (subprocess\.run\([^)]+\))", lambda_call)
            if lambda_match:
                subprocess_call = lambda_match.group(1)
                return f"{indent2}result = await asyncio.to_thread(\n{indent2}    {subprocess_call}  {comment}\n{indent2})"
            return match.group(0)

        content = pattern1.sub(replace_pattern1, content)

        # Pattern 2: More complex cases with multiline subprocess calls
        pattern2 = re.compile(
            r"(\s*)loop = asyncio\.get_running_loop\(\)\s*\n"
            r"(\s*)result = await loop\.run_in_executor\(\s*\n"
            r"(\s*)None,\s*\n"
            r"(\s*)lambda[^:]*:\s*subprocess\.run\(\s*(#[^\n]*)?\s*\n"
            r"(.*?)"
            r"(\s*)\),\s*(#[^\n]*)?\s*\n"
            r"(\s*)\)",
            re.MULTILINE | re.DOTALL,
        )

        def replace_pattern2(match):
            indent1 = match.group(1)
            indent2 = match.group(2)
            comment1 = match.group(5) or ""
            middle_content = match.group(6)
            comment2 = match.group(8) or ""

            return f"{indent2}result = await asyncio.to_thread(\n{indent2}    subprocess.run,  {comment1}\n{middle_content}{indent2})"

        content = pattern2.sub(replace_pattern2, content)

        if content != original_content:
            file_path.write_text(content)
            print(f"✓ Fixed: {file_path}")
            return True
        else:
            print(f"- No changes needed: {file_path}")
            return False

    except Exception as e:
        print(f"✗ Error fixing {file_path}: {e}")
        return False


def main():
    """Fix all files with remaining asyncio patterns."""
    files_to_fix = [
        "docker_mcp/core/migration/verification.py",
        "docker_mcp/services/stack/migration_executor.py",
        "docker_mcp/services/stack/validation.py",
        "docker_mcp/services/stack/network.py",
        "docker_mcp/tools/logs.py",
        "docker_mcp/tools/stacks.py",
    ]

    fixed_count = 0
    total_files = len(files_to_fix)

    for file_str in files_to_fix:
        file_path = Path(file_str)
        if file_path.exists():
            if fix_file(file_path):
                fixed_count += 1
        else:
            print(f"✗ File not found: {file_path}")

    print(f"\nSummary: Fixed {fixed_count}/{total_files} files")

    # Run ruff format to clean up formatting
    print("Running ruff format to fix formatting...")
    try:
        subprocess.run(["uv", "run", "ruff", "format", "."], check=True)
        print("✓ Formatting completed")
    except subprocess.CalledProcessError as e:
        print(f"✗ Formatting failed: {e}")


if __name__ == "__main__":
    main()
