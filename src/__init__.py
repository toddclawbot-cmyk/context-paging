"""
Context Paging — tool outputs become handles, not history.

A thin harness layer that intercepts tool results, writes them byte-exact
to a content-addressed disk store, and replaces them in the agent's
context with a structured stub. A `recall` tool lets the agent page
content back when needed.

See SPEC.pdf for the full design.
"""

from .extractor import extract, extract_code, extract_markdown, extract_grep, extract_bash
from .recall import recall, RecallResult, set_llm_extractor
from .store import StashStore, StashRecord, is_sensitive_path
from .stub import format_stub
from .summarizer import summarize, set_llm_summarizer
from .wrapper import ContextPager, InterceptorConfig, intercept_tool_result

__all__ = [
    "ContextPager",
    "InterceptorConfig",
    "RecallResult",
    "StashRecord",
    "StashStore",
    "extract",
    "extract_bash",
    "extract_code",
    "extract_grep",
    "extract_markdown",
    "format_stub",
    "intercept_tool_result",
    "is_sensitive_path",
    "recall",
    "set_llm_extractor",
    "set_llm_summarizer",
    "summarize",
]

__version__ = "0.1.0"
