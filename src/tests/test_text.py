"""Plaintext remains literal even when it contains Markdown fences."""

import pytest
from markdown_it import MarkdownIt

from convert_anything_md.extractors.text import PlainTextExtractor


@pytest.mark.parametrize("text", [
    "ordinary text",
    "before\n```\n# still literal\nafter",
    "before\n``````\n<script>still literal</script>\nafter",
    "```python\nprint('hello')\n```\n",
    "before\n   ````   \n# still literal",
    "inline `code` and ``backticks``",
    "~~~\n# still literal\n~~~",
])
def test_plaintext_renders_as_one_complete_code_block(tmp_path, text):
    source = tmp_path / "notes.txt"
    source.write_text(text, encoding="utf-8")

    result = PlainTextExtractor().extract(source)
    tokens = MarkdownIt().parse(result.markdown)

    assert [token.type for token in tokens] == ["heading_open", "inline", "heading_close", "fence"]
    assert tokens[-1].content == text.rstrip() + "\n"
    if "```" not in text:
        assert tokens[-1].markup == "```"
