"""Shared utilities for all scrapers: rate limiting, retry, text extraction, hashing."""

import hashlib
import logging
import re
import time
from datetime import date
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": "CompetitiveIntel/1.0 (competitive intelligence research bot)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


class RateLimiter:
    """Simple rate limiter that enforces minimum delay between requests."""

    def __init__(self, min_delay: float = 0.5):
        self.min_delay = min_delay
        self._last_request_time = 0.0

    def wait(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self._last_request_time = time.time()


def fetch_url(
    url: str,
    headers: Optional[dict] = None,
    timeout: int = 30,
    rate_limiter: Optional[RateLimiter] = None,
) -> Optional[requests.Response]:
    """Fetch a URL with retry logic and rate limiting.

    Returns None on any unrecoverable error (including exhausted retries).
    """
    try:
        return _fetch_url_with_retry(url, headers=headers, timeout=timeout, rate_limiter=rate_limiter)
    except Exception as e:
        logger.error("All retries exhausted for %s: %s", url, e)
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
)
def _fetch_url_with_retry(
    url: str,
    headers: Optional[dict] = None,
    timeout: int = 30,
    rate_limiter: Optional[RateLimiter] = None,
) -> Optional[requests.Response]:
    """Inner fetch with retry decorator."""
    if rate_limiter:
        rate_limiter.wait()

    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}
    try:
        response = requests.get(url, headers=merged_headers, timeout=timeout)
        if response.status_code == 404:
            logger.warning("404 Not Found: %s", url)
            return None
        response.raise_for_status()
        return response
    except requests.HTTPError as e:
        logger.error("HTTP error fetching %s: %s", url, e)
        return None


def normalize_url(url: str, base_url: Optional[str] = None) -> str:
    """Normalize a URL: resolve relative, remove fragments and trailing slashes."""
    if base_url:
        url = urljoin(base_url, url)
    parsed = urlparse(url)
    # Remove fragment, keep path without trailing slash for consistency
    path = parsed.path.rstrip("/") if parsed.path != "/" else "/"
    normalized = urlunparse(
        (parsed.scheme, parsed.netloc, path, parsed.params, "", "")
    )
    return normalized


def is_same_domain(url: str, base_url: str) -> bool:
    """Check if url belongs to the same domain as base_url."""
    return urlparse(url).netloc == urlparse(base_url).netloc


def is_html_url(url: str) -> bool:
    """Check if a URL likely points to an HTML page (not PDF, image, etc.)."""
    path = urlparse(url).path.lower()
    non_html_extensions = {
        ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
        ".zip", ".tar", ".gz", ".mp4", ".mp3", ".css", ".js",
        ".woff", ".woff2", ".ttf", ".eot", ".xml", ".json",
    }
    for ext in non_html_extensions:
        if path.endswith(ext):
            return False
    return True


def extract_content(
    html: str,
    content_selector: str = "article",
    url: str = "",
) -> tuple[str, str]:
    """Extract main content text and title from HTML.

    Returns (title, text) tuple.
    """
    soup = BeautifulSoup(html, "lxml")

    # Extract title
    title = ""
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    # Find main content area
    content_area = soup.select_one(content_selector)
    if not content_area:
        # Fallback: try common selectors
        for fallback in ["main", "article", "[role='main']", ".content", "#content"]:
            content_area = soup.select_one(fallback)
            if content_area:
                break

    if not content_area:
        content_area = soup.find("body")

    if not content_area:
        return title, ""

    # Remove unwanted elements
    for tag_name in ["nav", "header", "footer", "aside", "script", "style", "noscript"]:
        for tag in content_area.find_all(tag_name):
            tag.decompose()

    # Remove cookie banners, overlays, etc.
    for class_pattern in ["cookie", "banner", "popup", "modal", "overlay", "sidebar", "toc"]:
        for tag in content_area.find_all(class_=re.compile(class_pattern, re.I)):
            tag.decompose()

    # Extract text preserving structure
    text = _extract_structured_text(content_area)
    return title, text


def _extract_structured_text(element: Tag) -> str:
    """Extract text from an element, preserving code blocks and tables."""
    parts = []

    for child in element.children:
        if isinstance(child, str):
            text = child.strip()
            if text:
                parts.append(text)
            continue

        if not isinstance(child, Tag):
            continue

        tag = child.name

        # Preserve code blocks
        if tag == "pre" or (tag == "code" and child.parent and child.parent.name == "pre"):
            lang = ""
            if child.get("class"):
                for cls in child.get("class", []):
                    if cls.startswith("language-"):
                        lang = cls.replace("language-", "")
                        break
            code_text = child.get_text()
            parts.append(f"\n```{lang}\n{code_text}\n```\n")

        # Preserve tables
        elif tag == "table":
            parts.append(_extract_table(child))

        # Headings
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            prefix = "#" * level
            parts.append(f"\n{prefix} {child.get_text(strip=True)}\n")

        # Lists
        elif tag in ("ul", "ol"):
            for li in child.find_all("li", recursive=False):
                parts.append(f"- {li.get_text(strip=True)}")

        # Paragraphs and divs — recurse
        elif tag in ("p", "div", "section", "article", "main", "blockquote"):
            inner = _extract_structured_text(child)
            if inner.strip():
                parts.append(inner)

        else:
            text = child.get_text(strip=True)
            if text:
                parts.append(text)

    return "\n".join(parts)


def _extract_table(table: Tag) -> str:
    """Extract a table as markdown."""
    rows = []
    for tr in table.find_all("tr"):
        cells = []
        for cell in tr.find_all(["th", "td"]):
            cells.append(cell.get_text(strip=True))
        if cells:
            rows.append("| " + " | ".join(cells) + " |")

    if not rows:
        return ""

    # Add separator after header row
    if len(rows) > 1:
        num_cols = rows[0].count("|") - 1
        separator = "| " + " | ".join(["---"] * num_cols) + " |"
        rows.insert(1, separator)

    return "\n" + "\n".join(rows) + "\n"


def generate_record_id(origin: str, source_type: str, url: str) -> str:
    """Generate a deterministic unique ID for a source record."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
    return f"{origin}-{source_type}-{url_hash}"


def count_words(text: str) -> int:
    """Count words in text."""
    return len(text.split())


def extract_date_from_text(text: str) -> Optional[date]:
    """Try to extract a date from text (common blog/article date formats)."""
    patterns = [
        r"(\d{4}-\d{2}-\d{2})",  # 2024-01-15
        r"(\w+ \d{1,2},? \d{4})",  # January 15, 2024
        r"(\d{1,2} \w+ \d{4})",  # 15 January 2024
    ]
    for pattern in patterns:
        match = re.search(pattern, text[:500])  # Only check beginning
        if match:
            try:
                from dateutil.parser import parse as dateparse

                return dateparse(match.group(1)).date()
            except (ValueError, ImportError):
                pass
    return None


def extract_links(html: str, base_url: str, content_selector: str = "body") -> list[str]:
    """Extract all internal links from a page within the content area."""
    soup = BeautifulSoup(html, "lxml")
    content = soup.select_one(content_selector) or soup.find("body")
    if not content:
        return []

    links = []
    for a_tag in content.find_all("a", href=True):
        href = a_tag["href"]
        full_url = normalize_url(href, base_url)
        if is_same_domain(full_url, base_url) and is_html_url(full_url):
            links.append(full_url)

    return list(set(links))


def save_records(records: list, output_dir: str, filename: str):
    """Save a list of Pydantic model instances to a JSON file."""
    import orjson
    from pathlib import Path

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    filepath = output_path / filename
    data = [r.model_dump(mode="json") for r in records]
    filepath.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
    logger.info("Saved %d records to %s", len(records), filepath)
    return filepath


def load_records(filepath: str) -> list[dict]:
    """Load records from a JSON file."""
    import orjson
    from pathlib import Path

    path = Path(filepath)
    if not path.exists():
        return []
    return orjson.loads(path.read_bytes())
