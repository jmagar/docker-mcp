#!/usr/bin/env python3
"""
Script to fix all asyncio.get_event_loop() patterns to modern asyncio.to_thread()
and asyncio.get_running_loop() patterns.

This addresses the critical Python 3.10+ compatibility issues identified in PR review.
"""

import glob
import os
import re
from pathlib import Path


def fix_asyncio_patterns(file_path):
    """Fix all asyncio patterns in a single file."""
    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    original_content = content
    changes_made = []

    # Pattern 1: Simple get_event_loop() usage
    # Before: loop = asyncio.get_event_loop()
    # After: loop = asyncio.get_running_loop()
    pattern1 = r"(\s+)loop\s*=\s*asyncio\.get_event_loop\(\)"
    if re.search(pattern1, content):
        content = re.sub(pattern1, r"\1loop = asyncio.get_running_loop()", content)
        changes_made.append("get_event_loop() -> get_running_loop()")

    # Pattern 2: get_event_loop().time()
    # Before: asyncio.get_event_loop().time()
    # After: time.time() (need to add import time)
    pattern2 = r"asyncio\.get_event_loop\(\)\.time\(\)"
    if re.search(pattern2, content):
        content = re.sub(pattern2, "time.time()", content)
        # Add time import if not present
        if "import time" not in content and "from time import" not in content:
            import_pattern = r"(import asyncio\n)"
            content = re.sub(import_pattern, r"\1import time\n", content)
        changes_made.append("get_event_loop().time() -> time.time()")

    # Pattern 3: Complex run_in_executor patterns with subprocess.run
    # This is the most common pattern that needs fixing
    executor_pattern = r"await asyncio\.get_event_loop\(\)\.run_in_executor\(\s*None,\s*lambda[^:]*:\s*subprocess\.run\(\s*#\s*nosec\s*B603([^}]+?)\),?\s*\)"

    def replace_executor_lambda(match):
        # Extract the subprocess.run arguments
        args_part = match.group(1)
        # Clean up the arguments and rebuild as asyncio.to_thread
        return f"await asyncio.to_thread(\n                subprocess.run,  # nosec B603{args_part}\n            )"

    if re.search(executor_pattern, content, re.DOTALL):
        content = re.sub(executor_pattern, replace_executor_lambda, content, flags=re.DOTALL)
        changes_made.append("run_in_executor with lambda -> asyncio.to_thread")

    # Pattern 4: Docker SDK calls with run_in_executor
    # Before: await loop.run_in_executor(None, lambda: client.containers.list(all=True))
    # After: await asyncio.to_thread(client.containers.list, all=True)
    docker_pattern = r"await\s+(?:asyncio\.get_event_loop\(\)\.run_in_executor|loop\.run_in_executor)\(\s*None,\s*lambda:\s*([^.]+)\.([^(]+)\(([^)]*)\)\s*\)"

    def replace_docker_lambda(match):
        client = match.group(1)
        method = match.group(2)
        args = match.group(3)
        if args.strip():
            return f"await asyncio.to_thread({client}.{method}, {args})"
        else:
            return f"await asyncio.to_thread({client}.{method})"

    if re.search(docker_pattern, content):
        content = re.sub(docker_pattern, replace_docker_lambda, content)
        changes_made.append("Docker SDK executor -> asyncio.to_thread")

    # Write back if changes were made
    if content != original_content:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return changes_made

    return []


def main():
    """Fix all Python files in the docker_mcp directory."""
    base_dir = Path(__file__).parent / "docker_mcp"

    # Find all Python files
    python_files = []
    for pattern in ["**/*.py"]:
        python_files.extend(glob.glob(str(base_dir / pattern), recursive=True))

    total_files_changed = 0
    total_changes = 0

    print("🔧 Fixing asyncio patterns for Python 3.10+ compatibility...")
    print(f"📁 Scanning {len(python_files)} Python files...")

    for file_path in python_files:
        try:
            changes = fix_asyncio_patterns(file_path)
            if changes:
                total_files_changed += 1
                total_changes += len(changes)
                rel_path = os.path.relpath(file_path)
                print(f"✅ {rel_path}:")
                for change in changes:
                    print(f"   - {change}")
        except Exception as e:
            print(f"❌ Error processing {file_path}: {e}")

    print("\n🎯 Summary:")
    print(f"   Files changed: {total_files_changed}")
    print(f"   Total changes: {total_changes}")
    print(f"   Status: {'✅ Complete' if total_changes > 0 else '⚠️  No patterns found'}")


if __name__ == "__main__":
    main()
