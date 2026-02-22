"""Documentation site crawler.

Crawls documentation websites (KX docs, QuestDB docs, ClickHouse docs, etc.)
following internal links up to a configurable depth. Extracts main content,
preserves code blocks and tables, and produces SourceRecord objects.
"""

import logging
from collections import deque
from datetime import date
from typing import Optional

from schemas.source_record import Credibility, SourceRecord, SourceType
from scrapers.utils import (
    RateLimiter,
    count_words,
    extract_content,
    extract_links,
    fetch_url,
    generate_record_id,
    is_same_domain,
    normalize_url,
    save_records,
)

logger = logging.getLogger(__name__)


class DocsScraper:
    """Generic documentation site scraper.

    Works for any documentation site by accepting a CSS content selector
    and crawling internal links up to max_depth.
    """

    def __init__(self, origin: str, source_type: SourceType = SourceType.OFFICIAL_DOCS):
        self.origin = origin
        self.source_type = source_type

    def scrape(self, config: dict, output_dir: str) -> list[SourceRecord]:
        """Scrape a documentation source based on its config entry.

        Args:
            config: A single docs source config dict with keys:
                - id, base_url, scrape_method, content_selector, max_depth,
                  rate_limit_seconds, exclude_patterns (optional)
            output_dir: Directory to save raw JSON output.

        Returns:
            List of SourceRecord objects.
        """
        base_url = config["base_url"]
        method = config.get("scrape_method", "crawl")
        content_selector = config.get("content_selector", "article")
        max_depth = config.get("max_depth", 3)
        max_pages = config.get("max_pages", 200)
        rate_limit = config.get("rate_limit_seconds", 0.5)
        exclude_patterns = config.get("exclude_patterns", [])

        rate_limiter = RateLimiter(min_delay=rate_limit)

        if method == "single_page":
            records = self._scrape_single(base_url, content_selector, rate_limiter)
        else:
            records = self._crawl(
                base_url, content_selector, max_depth, rate_limiter, exclude_patterns,
                max_pages=max_pages,
            )

        if records:
            save_records(records, output_dir, f"{config['id']}.json")

        logger.info(
            "Scraped %d pages from %s (%s)", len(records), config["id"], base_url
        )
        return records

    def _scrape_single(
        self,
        url: str,
        content_selector: str,
        rate_limiter: RateLimiter,
    ) -> list[SourceRecord]:
        """Scrape a single page."""
        response = fetch_url(url, rate_limiter=rate_limiter)
        if not response:
            return []

        title, text = extract_content(response.text, content_selector, url)
        if not text.strip():
            logger.warning("No content extracted from %s", url)
            return []

        record = SourceRecord(
            id=generate_record_id(self.origin, self.source_type.value, url),
            origin=self.origin,
            source_type=self.source_type,
            url=url,
            title=title,
            text=text,
            scraped_date=date.today(),
            credibility=Credibility.OFFICIAL,
            word_count=count_words(text),
        )
        return [record]

    def _crawl(
        self,
        base_url: str,
        content_selector: str,
        max_depth: int,
        rate_limiter: RateLimiter,
        exclude_patterns: list[str],
        max_pages: int = 200,
    ) -> list[SourceRecord]:
        """Crawl a documentation site following internal links."""
        visited: set[str] = set()
        records: list[SourceRecord] = []

        # BFS queue: (url, depth)
        queue: deque[tuple[str, int]] = deque()
        start_url = normalize_url(base_url)
        queue.append((start_url, 0))
        visited.add(start_url)

        while queue and len(records) < max_pages:
            url, depth = queue.popleft()

            if self._should_exclude(url, exclude_patterns):
                continue

            response = fetch_url(url, rate_limiter=rate_limiter)
            if not response:
                continue

            title, text = extract_content(response.text, content_selector, url)
            if not text.strip():
                continue

            record = SourceRecord(
                id=generate_record_id(self.origin, self.source_type.value, url),
                origin=self.origin,
                source_type=self.source_type,
                url=url,
                title=title,
                text=text,
                scraped_date=date.today(),
                credibility=Credibility.OFFICIAL,
                word_count=count_words(text),
            )
            records.append(record)

            # Follow links if within depth limit
            if depth < max_depth:
                links = extract_links(response.text, url, content_selector)
                for link in links:
                    norm_link = normalize_url(link)
                    if (
                        norm_link not in visited
                        and is_same_domain(norm_link, base_url)
                        and not self._should_exclude(norm_link, exclude_patterns)
                    ):
                        visited.add(norm_link)
                        queue.append((norm_link, depth + 1))

            if len(records) % 50 == 0:
                logger.info("Crawled %d pages so far...", len(records))

        return records

    def _should_exclude(self, url: str, patterns: list[str]) -> bool:
        """Check if a URL matches any exclusion pattern."""
        for pattern in patterns:
            if pattern in url:
                return True
        return False


def scrape_docs(competitor_config: dict, data_dir: str) -> list[SourceRecord]:
    """Top-level function to scrape all docs sources for a competitor.

    Args:
        competitor_config: Full competitor config dict.
        data_dir: Base data directory (e.g., 'data/raw').

    Returns:
        All scraped SourceRecords across all doc sources.
    """
    origin = competitor_config["short_name"]
    sources = competitor_config.get("sources", {})
    docs_sources = sources.get("docs", [])
    product_sources = sources.get("product_pages", [])

    output_dir = f"{data_dir}/{origin}/docs"
    all_records = []

    # Scrape documentation pages
    docs_scraper = DocsScraper(origin, SourceType.OFFICIAL_DOCS)
    for source in docs_sources:
        records = docs_scraper.scrape(source, output_dir)
        all_records.extend(records)

    # Scrape product/comparison/case study pages
    product_scraper = DocsScraper(origin, SourceType.PRODUCT_PAGE)
    for source in product_sources:
        source_type = SourceType.PRODUCT_PAGE
        if "compare" in source.get("base_url", "").lower():
            source_type = SourceType.COMPARISON_PAGE
        elif "case-stud" in source.get("base_url", "").lower():
            source_type = SourceType.CASE_STUDY
        elif "resource" in source.get("base_url", "").lower():
            source_type = SourceType.WHITEPAPER

        scraper = DocsScraper(origin, source_type)
        records = scraper.scrape(source, output_dir)
        all_records.extend(records)

    logger.info("Total docs scraped for %s: %d", origin, len(all_records))
    return all_records
