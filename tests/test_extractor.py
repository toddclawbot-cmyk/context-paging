"""Tests for the structure extractor.

Per spec §4, the extractor must produce parseable facts only — signatures
from the AST, headings from the markdown tree, file paths from grep.
"""

import pytest

from src.extractor import (
    detect_language,
    extract,
    extract_bash,
    extract_code,
    extract_grep,
    extract_markdown,
)


# --- Language detection ---

def test_detect_python():
    assert detect_language("foo.py") == "python"

def test_detect_javascript():
    assert detect_language("foo.js") == "javascript"
    assert detect_language("foo.jsx") == "javascript"

def test_detect_typescript():
    assert detect_language("foo.ts") == "typescript"
    assert detect_language("foo.tsx") == "typescript"

def test_detect_go():
    assert detect_language("foo.go") == "go"

def test_detect_rust():
    assert detect_language("foo.rs") == "rust"

def test_detect_unknown():
    assert detect_language("foo.xyz") is None
    assert detect_language("") is None

def test_detect_markdown():
    assert detect_language("foo.md") == "markdown"


# --- Code extraction ---

def test_extract_code_python_finds_functions():
    content = '''
def foo(x: int) -> int:
    return x + 1

def bar(name: str) -> str:
    return f"Hello, {name}"
'''
    s = extract_code(content, "x.py")
    assert s["language"] == "python"
    assert "foo(int)" in s["exports"] or any("foo" in e for e in s["exports"])
    assert "bar(str)" in s["exports"] or any("bar" in e for e in s["exports"])


def test_extract_code_python_finds_imports():
    content = '''
import os
import sys
from typing import Optional, List
'''
    s = extract_code(content, "x.py")
    assert "os" in s["imports"]
    assert "sys" in s["imports"]
    assert "typing" in s["imports"]


def test_extract_code_python_finds_class():
    content = '''
class User:
    def __init__(self, name):
        self.name = name
'''
    s = extract_code(content, "x.py")
    assert "User" in s["class_names"]


def test_extract_code_go_finds_functions():
    content = '''
package main

func VerifyToken(token string) (*Claims, error) {
    return nil, nil
}

func RotateRefresh(token string) (string, error) {
    return "", nil
}
'''
    s = extract_code(content, "auth.go")
    assert s["language"] == "go"
    assert any("VerifyToken" in e for e in s["exports"])
    assert any("RotateRefresh" in e for e in s["exports"])


def test_extract_code_javascript_finds_functions():
    content = '''
function handleRequest(req, res) {
    return res.json({ok: true});
}

const handleError = (err) => {
    console.error(err);
};
'''
    s = extract_code(content, "server.js")
    assert s["language"] == "javascript"
    assert any("handleRequest" in e for e in s["exports"])


def test_extract_code_shape_summary():
    content = "def a(): pass\ndef b(): pass\ndef c(): pass\n"
    s = extract_code(content, "x.py")
    assert "3 funcs" in s["shape"]
    assert "lines" in s["shape"]


def test_extract_code_truncates_long_export_lists():
    content = "\n".join(f"def func_{i}(): pass" for i in range(50))
    s = extract_code(content, "x.py")
    # Should truncate at 8 entries
    exports_actual = [e for e in s["exports"] if not e.startswith("…")]
    assert len(exports_actual) <= 8
    # And include a "more" marker
    assert any(e.startswith("…") for e in s["exports"])


# --- Markdown extraction ---

def test_extract_markdown_counts_headings():
    content = '''# Title

## Section 1

## Section 2

### Subsection 2.1

## Section 3
'''
    s = extract_markdown(content)
    assert s["language"] == "markdown"
    assert s["headings"] == 5
    assert s["toc"][0] == "Title"
    assert "Section 1" in s["toc"]


def test_extract_markdown_handles_empty():
    s = extract_markdown("")
    assert s["headings"] == 0
    assert s["toc"] == []


# --- Grep extraction ---

def test_extract_grep_counts_matches():
    content = """src/foo.py:10:def bar(): pass
src/foo.py:20:def baz(): pass
src/baz.py:5:import bar
"""
    s = extract_grep(content, {"pattern": "def"})
    assert s["match_count"] == 3
    assert s["file_count"] == 2
    assert "src/foo.py" in s["files"]


# --- Bash extraction ---

def test_extract_bash_separates_stdout_stderr():
    content = "hello world\nSTDERR: error occurred\n"
    s = extract_bash(content, {"cmd": "ls", "exit_code": 0})
    assert "hello world" in s["stdout_head"]
    assert "error" in s["stderr_head"]


def test_extract_bash_just_stdout():
    content = "file1.txt\nfile2.txt\n"
    s = extract_bash(content, {"cmd": "ls", "exit_code": 0})
    assert "file1" in s["stdout_head"]
    assert s["stderr_head"] == ""


# --- Dispatcher ---

def test_extract_dispatches_read_python():
    content = "def foo(): pass\n"
    s = extract(content, tool="Read", args={"file_path": "x.py"}, path="x.py")
    assert s["language"] == "python"


def test_extract_dispatches_read_markdown():
    content = "# Title\n\n## Section\n"
    s = extract(content, tool="Read", args={"file_path": "x.md"}, path="x.md")
    assert s["language"] == "markdown"


def test_extract_dispatches_grep():
    content = "src/foo.py:1:match"
    s = extract(content, tool="Grep", args={"pattern": "match"})
    assert "match_count" in s


def test_extract_dispatches_bash():
    content = "output\n"
    s = extract(content, tool="Bash", args={"cmd": "echo"})
    assert "stdout_head" in s


def test_extract_fallback_unknown_tool():
    content = "just some text\n"
    s = extract(content, tool="UnknownTool")
    assert s["language"] == "text"
