from ddgs import DDGS
import re


def read_webpage(url: str) -> str:

    try:
        result = DDGS().extract(
            url,
            fmt="text_markdown"
        )

        if not result:
            return ""

        content = result.get("content", "")

        return clean_webpage(content)

    except Exception as e:
        return f"Web reader error: {e}"


def clean_webpage(text: str, max_chars: int = 12000) -> str:

    if not text:
        return ""

    text = text.replace("\r", "")

    # Remove excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove link-reference definitions such as:
    # [1]: https://example.com
    text = re.sub(r"^\[\d+\]:.*$", "", text, flags=re.MULTILINE)

    # Remove excessive blank lines again
    text = re.sub(r"\n{3,}", "\n\n", text)

    text = text.strip()

    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[content truncated]"

    return text
