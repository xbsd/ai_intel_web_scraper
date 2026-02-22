"""Benchmark scraper for ClickBench, TSBS, and other performance comparison sources.

Fetches benchmark pages, extracts performance data tables and charts as text,
and identifies database names, query types, and execution times.
"""

import logging
import re
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
    normalize_url,
    save_records,
)

logger = logging.getLogger(__name__)


class BenchmarkScraper:
    """Scrapes benchmark and performance comparison pages."""

    def __init__(self, origin: str):
        self.origin = origin

    def scrape(self, benchmarks_config: list[dict], output_dir: str) -> list[SourceRecord]:
        """Scrape all benchmark sources.

        Args:
            benchmarks_config: List of benchmark config dicts, each with:
                name, url, scrape_method, max_depth (optional)
            output_dir: Directory for output JSON.

        Returns:
            List of SourceRecord objects.
        """
        rate_limiter = RateLimiter(min_delay=1.0)
        records = []

        for bench_config in benchmarks_config:
            name = bench_config.get("name", "unknown")
            url = bench_config.get("url", "")
            method = bench_config.get("scrape_method", "single_page")

            logger.info("Scraping benchmark: %s (%s)", name, url)

            if method == "single_page":
                result = self._scrape_single(url, name, rate_limiter)
                if result:
                    records.append(result)
            elif method == "crawl":
                max_depth = bench_config.get("max_depth", 2)
                crawled = self._scrape_crawl(url, name, max_depth, rate_limiter)
                records.extend(crawled)

        if records:
            save_records(records, output_dir, f"{self.origin}_benchmarks.json")

        logger.info("Scraped %d benchmark pages for %s", len(records), self.origin)
        return records

    def _scrape_single(
        self, url: str, name: str, rate_limiter: RateLimiter
    ) -> Optional[SourceRecord]:
        """Scrape a single benchmark page."""
        response = fetch_url(url, rate_limiter=rate_limiter)
        if not response:
            return None

        title, text = extract_content(response.text, "main", url)
        if not title:
            title, text = extract_content(response.text, "article", url)
        if not title:
            title, text = extract_content(response.text, "body", url)

        if not text.strip():
            logger.warning("No content extracted from benchmark page: %s", url)
            return None

        # Try to extract benchmark-specific data
        benchmark_data = self._extract_benchmark_data(text)

        return SourceRecord(
            id=generate_record_id(self.origin, "benchmark", url),
            origin=self.origin,
            source_type=SourceType.BENCHMARK,
            url=url,
            title=title or f"Benchmark: {name}",
            text=text,
            scraped_date=date.today(),
            credibility=Credibility.THIRD_PARTY,
            word_count=count_words(text),
            metadata={
                "benchmark_name": name,
                "benchmark_data": benchmark_data,
            },
        )

    def _scrape_crawl(
        self, base_url: str, name: str, max_depth: int, rate_limiter: RateLimiter,
        max_pages: int = 50,
    ) -> list[SourceRecord]:
        """Crawl a benchmark site for related pages."""
        from collections import deque

        visited = set()
        records = []
        queue: deque[tuple[str, int]] = deque()

        start = normalize_url(base_url)
        queue.append((start, 0))
        visited.add(start)

        while queue and len(records) < max_pages:
            url, depth = queue.popleft()

            response = fetch_url(url, rate_limiter=rate_limiter)
            if not response:
                continue

            title, text = extract_content(response.text, "article", url)
            if not text.strip():
                title, text = extract_content(response.text, "main", url)

            if text.strip():
                benchmark_data = self._extract_benchmark_data(text)
                record = SourceRecord(
                    id=generate_record_id(self.origin, "benchmark", url),
                    origin=self.origin,
                    source_type=SourceType.BENCHMARK,
                    url=url,
                    title=title or f"Benchmark: {name}",
                    text=text,
                    scraped_date=date.today(),
                    credibility=Credibility.THIRD_PARTY,
                    word_count=count_words(text),
                    metadata={
                        "benchmark_name": name,
                        "benchmark_data": benchmark_data,
                    },
                )
                records.append(record)

            if depth < max_depth:
                links = extract_links(response.text, url)
                for link in links:
                    norm = normalize_url(link)
                    if norm not in visited:
                        visited.add(norm)
                        queue.append((norm, depth + 1))

        return records

    def _extract_benchmark_data(self, text: str) -> dict:
        """Try to extract structured benchmark data from text.

        Looks for patterns like:
        - Database names (QuestDB, ClickHouse, KDB+, TimescaleDB, InfluxDB)
        - Performance numbers (X rows/sec, X ms, X GB/s)
        - Hardware specs (CPU, RAM, storage)
        """
        data = {
            "databases_mentioned": [],
            "performance_numbers": [],
            "hardware_specs": [],
        }

        # Known database names
        db_names = [
            "QuestDB", "ClickHouse", "KDB\\+", "KDB-X", "TimescaleDB",
            "InfluxDB", "DuckDB", "PostgreSQL", "MySQL", "MongoDB",
            "Druid", "Pinot", "CrateDB", "TDengine",
        ]
        for db in db_names:
            if re.search(db, text, re.IGNORECASE):
                data["databases_mentioned"].append(db.replace("\\+", "+"))

        # Performance numbers
        perf_patterns = [
            r"([\d,.]+)\s*(rows?/s(?:ec(?:ond)?)?|rows per second)",
            r"([\d,.]+)\s*(ms|millisecond|microsecond|μs|us|ns|nanosecond)",
            r"([\d,.]+)\s*(GB/s|MB/s|TB/s)",
            r"([\d,.]+)\s*(QPS|queries per second)",
            r"([\d,.]+)x\s*(faster|slower)",
        ]
        for pattern in perf_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                data["performance_numbers"].append(" ".join(match))

        # Hardware specs
        hw_patterns = [
            r"(\d+)\s*(CPU|core|vCPU)",
            r"(\d+)\s*(GB|TB)\s*(RAM|memory|disk|SSD|NVMe|storage)",
            r"(AWS|GCP|Azure)\s+(\w+\.\w+)",
        ]
        for pattern in hw_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                data["hardware_specs"].append(" ".join(match))

        return data


def scrape_benchmarks(competitor_config: dict, data_dir: str) -> list[SourceRecord]:
    """Top-level function to scrape benchmark sources for a competitor."""
    origin = competitor_config["short_name"]
    benchmarks_config = competitor_config.get("sources", {}).get("benchmarks", [])
    if not benchmarks_config:
        logger.info("No benchmark config for %s, skipping", origin)
        return []

    output_dir = f"{data_dir}/{origin}/benchmarks"
    scraper = BenchmarkScraper(origin)
    return scraper.scrape(benchmarks_config, output_dir)
