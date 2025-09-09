#!/usr/bin/env python3
"""
Fix the formatting issues from the previous asyncio fix script.
"""

import glob
import re
from pathlib import Path


def fix_asyncio_formatting(file_path):
    """Fix asyncio.to_thread formatting issues."""
    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    original_content = content

    # Fix broken asyncio.to_thread formatting
    # Pattern: Find malformed asyncio.to_thread calls and fix them
    broken_pattern = (
        r"await asyncio\.to_thread\(\s*subprocess\.run,\s*#\s*nosec\s*B603\s*([^)]+?)\s*\)"
    )

    def fix_formatting(match):
        args_part = match.group(1).strip()
        # Clean up the arguments and format properly
        return f"await asyncio.to_thread(\n                subprocess.run,  # nosec B603\n                {args_part}\n            )"

    content = re.sub(broken_pattern, fix_formatting, content, flags=re.DOTALL)

    # Fix double indentation issues
    content = re.sub(
        r"(\s+)subprocess\.run,\s*#\s*nosec\s*B603\s*\n\s+(.+)",
        r"\1subprocess.run,  # nosec B603\n\1\2",
        content,
    )

    # Write back if changes were made
    if content != original_content:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    return False


def main():
    """Fix all Python files."""
    base_dir = Path(__file__).parent / "docker_mcp"
    python_files = glob.glob(str(base_dir / "**/*.py"), recursive=True)

    fixed_files = 0
    print("🔧 Fixing asyncio.to_thread formatting...")

    for file_path in python_files:
        try:
            if fix_asyncio_formatting(file_path):
                fixed_files += 1
                print(f"✅ Fixed: {file_path}")
        except Exception as e:
            print(f"❌ Error: {file_path}: {e}")

    print(f"📊 Fixed {fixed_files} files")


if __name__ == "__main__":
    main()
