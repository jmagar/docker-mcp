#!/usr/bin/env python3
"""
Final cleanup of remaining run_in_executor patterns to asyncio.to_thread
"""

import re
import subprocess
from pathlib import Path


def fix_file_comprehensive(file_path: Path) -> bool:
    """Fix asyncio patterns in a single file comprehensively."""
    try:
        content = file_path.read_text()
        original_content = content

        # Pattern 1: Simple single-line lambda patterns
        pattern1 = re.compile(
            r'(\s*)loop = asyncio\.get_running_loop\(\)\s*\n'
            r'(\s*)([^=]*?result.*?= await loop\.run_in_executor\(\s*\n)'
            r'(\s*)None,\s*\n'
            r'(\s*)lambda:\s*(.*?)  # nosec B603([^\n]*)\n'
            r'(\s*)\)',
            re.MULTILINE | re.DOTALL
        )

        def replace_pattern1(match):
            indent = match.group(2)
            result_part = match.group(3).strip()
            subprocess_call = match.group(6)
            comment = match.group(7) or ""

            return (
                f'{indent}{result_part}\n'
                f'{indent}    {subprocess_call}  # nosec B603{comment}\n'
                f'{indent})'
            ).replace('loop.run_in_executor(', 'asyncio.to_thread(')

        content = pattern1.sub(replace_pattern1, content)

        # Pattern 2: Multi-line subprocess patterns
        pattern2 = re.compile(
            r'(\s*)loop = asyncio\.get_running_loop\(\)\s*\n'
            r'(\s*)([^=]*?result.*?= await loop\.run_in_executor\(\s*\n)'
            r'(\s*)None,\s*\n'
            r'(\s*)lambda:\s*subprocess\.run\(\s*(#[^\n]*)?\s*\n'
            r'(.*?)'
            r'(\s*)\),\s*(#[^\n]*)?\s*\n'
            r'(\s*)\)',
            re.MULTILINE | re.DOTALL
        )

        def replace_pattern2(match):
            indent = match.group(2)
            result_part = match.group(3).strip()
            comment1 = match.group(5) or ""
            middle_content = match.group(6)
            comment2 = match.group(8) or ""

            return (
                f'{indent}{result_part}\n'
                f'{indent}    subprocess.run,  {comment1}\n'
                f'{middle_content}'
                f'{indent})'
            ).replace('loop.run_in_executor(', 'asyncio.to_thread(')

        content = pattern2.sub(replace_pattern2, content)

        # Pattern 3: Direct loop.run_in_executor calls (for cleanup operations)
        pattern3 = re.compile(
            r'(\s*)loop\.run_in_executor\(\s*\n'
            r'(\s*)None,\s*\n'
            r'(\s*)lambda:\s*(.*?)(?:,\s*(#[^\n]*)?)?\s*\n'
            r'(\s*)\)',
            re.MULTILINE | re.DOTALL
        )

        def replace_pattern3(match):
            indent = match.group(1)
            call_content = match.group(4)
            comment = match.group(5) or ""

            return (
                f'{indent}asyncio.to_thread(\n'
                f'{indent}    {call_content}  {comment}\n'
                f'{indent})'
            )

        content = pattern3.sub(replace_pattern3, content)

        # Remove orphaned get_running_loop calls
        pattern4 = re.compile(r'(\s*)loop = asyncio\.get_running_loop\(\)\s*\n')

        # Only remove if not followed by run_in_executor
        lines = content.split('\n')
        cleaned_lines = []
        skip_next_empty = False

        for i, line in enumerate(lines):
            if 'loop = asyncio.get_running_loop()' in line:
                # Check next few lines for run_in_executor
                found_executor = False
                for j in range(i + 1, min(i + 5, len(lines))):
                    if 'loop.run_in_executor' in lines[j]:
                        found_executor = True
                        break

                if not found_executor:
                    # Skip this line (it's orphaned)
                    skip_next_empty = True
                    continue
            elif skip_next_empty and line.strip() == '':
                # Skip empty line after removed get_running_loop
                skip_next_empty = False
                continue

            skip_next_empty = False
            cleaned_lines.append(line)

        content = '\n'.join(cleaned_lines)

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
        "docker_mcp/services/stack/network.py",
        "docker_mcp/core/migration/verification.py",
        "docker_mcp/tools/logs.py",
        "docker_mcp/tools/stacks.py",
    ]

    fixed_count = 0
    total_files = len(files_to_fix)

    for file_str in files_to_fix:
        file_path = Path(file_str)
        if file_path.exists():
            if fix_file_comprehensive(file_path):
                fixed_count += 1
        else:
            print(f"✗ File not found: {file_path}")

    print(f"\nSummary: Fixed {fixed_count}/{total_files} files")

    # Run ruff format to clean up formatting
    print("Running ruff format to fix formatting...")
    try:
        subprocess.run(["uv", "run", "ruff", "format", "docker_mcp/"], check=True)
        print("✓ Formatting completed")
    except subprocess.CalledProcessError as e:
        print(f"✗ Formatting failed: {e}")

if __name__ == "__main__":
    main()
