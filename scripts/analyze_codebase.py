#!/usr/bin/env python3
"""
Codebase analysis script for detecting duplicates and unreferenced code.

This script analyzes the codebase to find:
- Duplicate files
- Duplicate code blocks
- Unreferenced modules/imports
"""

import argparse
import ast
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def should_skip_module(module_name: str) -> bool:
    """Check if a module should be skipped from orphan detection.

    Args:
        module_name: Full module name to check

    Returns:
        True if module should be skipped (known false positive)
    """
    # Skip __init__ files (package initializers)
    if module_name.endswith(".__init__"):
        return True

    # Skip settings modules (often imported dynamically)
    if module_name.endswith(".settings"):
        return True

    # Skip prompts package and its submodules (used dynamically)
    if module_name == "docker_mcp.prompts" or module_name.startswith("docker_mcp.prompts."):
        return True

    return False


def get_file_hash(filepath: Path) -> str:
    """Calculate SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def extract_code_blocks(filepath: Path, min_lines: int = 5) -> list[tuple[int, int, str]]:
    """Extract significant code blocks from a Python file.

    Returns list of (start_line, end_line, normalized_code) tuples.
    """
    blocks = []
    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        tree = ast.parse(content)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                if hasattr(node, "lineno") and hasattr(node, "end_lineno"):
                    if node.end_lineno is not None and node.end_lineno - node.lineno >= min_lines:
                        # Extract and normalize the code block
                        lines = content.split("\n")[node.lineno - 1 : node.end_lineno]
                        normalized = "\n".join(line.strip() for line in lines if line.strip())
                        blocks.append((node.lineno, node.end_lineno, normalized))
    except (SyntaxError, UnicodeDecodeError):
        pass

    return blocks


def find_duplicate_files(root_dir: Path, exclude_patterns: list[str]) -> dict[str, list[str]]:
    """Find duplicate files based on content hash."""
    file_hashes: dict[str, str] = {}
    duplicates: dict[str, list[str]] = {}

    for py_file in root_dir.rglob("*.py"):
        # Skip excluded paths
        if any(pattern in str(py_file) for pattern in exclude_patterns):
            continue

        file_hash = get_file_hash(py_file)
        relative_path = str(py_file.relative_to(root_dir))

        if file_hash in file_hashes:
            if file_hash not in duplicates:
                duplicates[file_hash] = [file_hashes[file_hash]]
            duplicates[file_hash].append(relative_path)
        else:
            file_hashes[file_hash] = relative_path

    return duplicates


def find_duplicate_blocks(
    root_dir: Path, exclude_patterns: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Find duplicate code blocks across files."""
    block_hashes: dict[str, dict[str, Any]] = {}
    duplicates: dict[str, list[dict[str, Any]]] = {}

    for py_file in root_dir.rglob("*.py"):
        # Skip excluded paths
        if any(pattern in str(py_file) for pattern in exclude_patterns):
            continue

        relative_path = str(py_file.relative_to(root_dir))
        blocks = extract_code_blocks(py_file)

        for start_line, end_line, code in blocks:
            code_hash = hashlib.sha256(code.encode()).hexdigest()

            block_info = {
                "file": relative_path,
                "start_line": start_line,
                "end_line": end_line,
                "lines": end_line - start_line + 1,
            }

            if code_hash in block_hashes:
                if code_hash not in duplicates:
                    duplicates[code_hash] = [block_hashes[code_hash]]
                duplicates[code_hash].append(block_info)
            else:
                block_hashes[code_hash] = block_info

    return duplicates


def _extract_imports_from_file(
    py_file: Path, package_name: str, imported_modules: set[str]
) -> None:
    """Extract imports from a Python file."""
    try:
        with open(py_file, encoding="utf-8") as f:
            content = f.read()

        tree = ast.parse(content)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(package_name):
                        imported_modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith(package_name):
                    imported_modules.add(node.module)
                    # Also add the specific imports
                    for alias in node.names:
                        if alias.name != "*":
                            full_name = f"{node.module}.{alias.name}"
                            imported_modules.add(full_name)
    except (SyntaxError, UnicodeDecodeError):
        pass


def _process_python_file(
    py_file: Path,
    root_dir: Path,
    package_name: str,
    exclude_patterns: list[str],
    all_modules: set[str],
    imported_modules: set[str],
) -> None:
    """Process a single Python file for module and import collection."""
    # Skip excluded paths
    if any(pattern in str(py_file) for pattern in exclude_patterns):
        return

    relative_path = py_file.relative_to(root_dir)

    # Convert file path to module name
    module_parts = list(relative_path.parts[:-1]) + [relative_path.stem]
    if module_parts[0] == package_name:
        module_name = ".".join(module_parts)

        # Skip known false positives
        if not should_skip_module(module_name):
            all_modules.add(module_name)

    # Find imports in the file
    _extract_imports_from_file(py_file, package_name, imported_modules)


def find_unreferenced_modules(root_dir: Path, exclude_patterns: list[str]) -> dict[str, Any]:
    """Find potentially unreferenced modules."""
    all_modules: set[str] = set()
    imported_modules: set[str] = set()
    package_name = "docker_mcp"

    # Process all Python files
    for py_file in root_dir.rglob("*.py"):
        _process_python_file(
            py_file, root_dir, package_name, exclude_patterns, all_modules, imported_modules
        )

    # Find unreferenced modules
    unreferenced = all_modules - imported_modules

    # Filter out main/cli modules (entry points)
    unreferenced = {
        m for m in unreferenced if not m.endswith(".__main__") and not m.endswith(".main")
    }

    return {
        "total_modules": len(all_modules),
        "imported_modules": len(imported_modules),
        "unreferenced_modules": sorted(unreferenced),
        "count": len(unreferenced),
    }


def generate_markdown_report(duplicates: dict[str, Any], output_file: str) -> None:
    """Generate markdown report of findings."""
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("# Code Health: Duplicates and Dead Code Analysis\n\n")
        f.write("## Summary\n\n")

        # Summary stats
        f.write(f"- Duplicate files: {duplicates['duplicates']['files']}\n")
        f.write(f"- Duplicate code blocks: {duplicates['duplicates']['blocks']}\n")
        f.write(f"- Potentially unreferenced modules: {duplicates['unreferenced']['count']}\n\n")

        # Duplicate files
        f.write("## Duplicate Files\n\n")
        if duplicates["duplicate_files"]:
            for hash_val, files in duplicates["duplicate_files"].items():
                f.write(f"### Group (hash: {hash_val[:8]}...)\n")
                for file in files:
                    f.write(f"- {file}\n")
                f.write("\n")
        else:
            f.write("No duplicate files found.\n\n")

        # Duplicate blocks
        f.write("## Duplicate Code Blocks\n\n")
        if duplicates["duplicate_blocks"]:
            for hash_val, blocks in duplicates["duplicate_blocks"].items():
                f.write(f"### Block (hash: {hash_val[:8]}...)\n")
                for block in blocks:
                    f.write(
                        f"- {block['file']}:{block['start_line']}-{block['end_line']} ({block['lines']} lines)\n"
                    )
                f.write("\n")
        else:
            f.write("No duplicate code blocks found.\n\n")

        # Unreferenced modules
        f.write("## Potentially Unreferenced Modules\n\n")
        if duplicates["unreferenced"]["unreferenced_modules"]:
            for module in duplicates["unreferenced"]["unreferenced_modules"]:
                f.write(f"- {module}\n")
        else:
            f.write("No unreferenced modules found.\n")

        f.write("\n## Analysis Scope\n\n")
        f.write("- **Root**: .\n")
        f.write("- **Include**: **/*.py\n")
        f.write("- **Exclude**: .venv, build, dist, node_modules, **/__pycache__/**\n")
        f.write("- **Filters**: __init__ files, settings modules, prompts package\n")


async def main():
    parser = argparse.ArgumentParser(description="Analyze codebase for duplicates and dead code")
    parser.add_argument(
        "--out", default="CODE_HEALTH_DUPLICATES_DEAD_CODE.md", help="Output markdown file"
    )
    parser.add_argument("--json", help="Output JSON file for CI integration")
    args = parser.parse_args()

    root_dir = Path(".")
    exclude_patterns = [".venv", "build", "dist", "node_modules", "__pycache__", ".git", "tests"]

    # Find duplicates and unreferenced code
    duplicate_files = find_duplicate_files(root_dir, exclude_patterns)
    duplicate_blocks = find_duplicate_blocks(root_dir, exclude_patterns)
    unreferenced = find_unreferenced_modules(root_dir, exclude_patterns)

    # Prepare results
    results: dict[str, Any] = {
        "duplicates": {"files": len(duplicate_files), "blocks": len(duplicate_blocks)},
        "duplicate_files": duplicate_files,
        "duplicate_blocks": duplicate_blocks,
        "unreferenced": unreferenced,
    }

    # Generate markdown report
    generate_markdown_report(results, args.out)

    # Generate JSON report if requested
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, indent=2)

    # Exit with non-zero if issues found
    total_issues = (
        results["duplicates"]["files"]
        + results["duplicates"]["blocks"]
        + results["unreferenced"]["count"]
    )
    sys.exit(1 if total_issues > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
