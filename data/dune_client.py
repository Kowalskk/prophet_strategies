"""
PROPHET STRATEGIES
Dune Analytics API client — handles queries, execution, pagination
"""
from __future__ import annotations
import logging
import os
import time
from typing import Any, Iterator, Optional
import requests

logger = logging.getLogger(__name__)

DUNE_API_BASE = "https://api.dune.com/api/v1"


class DuneClient:
    """Client for Dune Analytics API with rate limiting and pagination."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("DUNE_API_KEY")
        if not self.api_key:
            raise ValueError("DUNE_API_KEY not set. Add to .env or environment.")
        self.session = requests.Session()
        self.session.headers.update({
            "x-dune-api-key": self.api_key,
            "Content-Type": "application/json",
        })

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def execute_query(self, query_id: int, params: Optional[dict] = None) -> str:
        """Trigger a query execution. Returns execution_id."""
        url = f"{DUNE_API_BASE}/query/{query_id}/execute"
        body = {"query_parameters": params or {}}
        resp = self.session.post(url, json=body)
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Dune API Error: {resp.text}")
            raise e
        execution_id = resp.json()["execution_id"]
        logger.info(f"Query {query_id} triggered → execution_id={execution_id}")
        return execution_id

    def wait_for_execution(self, execution_id: str, poll_interval: int = 5, timeout: int = 600) -> dict:
        """Poll until execution is complete. Returns status dict."""
        url = f"{DUNE_API_BASE}/execution/{execution_id}/status"
        start = time.time()
        while True:
            resp = self.session.get(url)
            resp.raise_for_status()
            data = resp.json()
            state = data.get("state", "")
            logger.debug(f"Execution {execution_id}: {state}")

            if state == "QUERY_STATE_COMPLETED":
                logger.info(f"Execution {execution_id} completed.")
                return data
            elif state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
                raise RuntimeError(f"Execution {execution_id} failed: {state}\n{data}")
            
            if time.time() - start > timeout:
                raise TimeoutError(f"Execution {execution_id} timed out after {timeout}s")
            
            time.sleep(poll_interval)

    def get_results(self, execution_id: str, limit: int = 25000, offset: int = 0) -> dict:
        """Fetch a page of results."""
        url = f"{DUNE_API_BASE}/execution/{execution_id}/results"
        params = {"limit": limit, "offset": offset}
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def iter_results(self, execution_id: str, page_size: int = 25000) -> Iterator[list[dict]]:
        """Iterate over all result pages. Yields list of row dicts per page."""
        offset = 0
        total_rows = None

        while True:
            data = self.get_results(execution_id, limit=page_size, offset=offset)
            rows = data.get("result", {}).get("rows", [])

            if total_rows is None:
                metadata = data.get("result", {}).get("metadata", {})
                total_rows = metadata.get("total_row_count", 0)
                logger.info(f"Total rows in execution {execution_id}: {total_rows:,}")

            if not rows:
                break

            yield rows
            offset += len(rows)

            if offset >= total_rows:
                break

            logger.info(f"Fetched {offset:,}/{total_rows:,} rows...")
            time.sleep(0.5)  # Be polite to the API

    # ------------------------------------------------------------------
    # Execute SQL directly (CRUD query)
    # ------------------------------------------------------------------

    def execute_sql(self, sql: str, query_name: str = "prophet_temp") -> str:
        """Create a one-off query and execute it. Returns execution_id."""
        # Create query
        url = f"{DUNE_API_BASE}/query"
        body = {
            "name": query_name,
            "query_sql": sql,
            "is_private": True,
        }
        resp = self.session.post(url, json=body)
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Dune API Error: {resp.text}")
            raise e
        query_id = resp.json()["query_id"]
        logger.info(f"Created query {query_id}: {query_name}")

        # Execute it
        return self.execute_query(query_id)

    # ------------------------------------------------------------------
    # Convenience: run and collect all rows
    # ------------------------------------------------------------------

    def run_query_and_collect(
        self,
        query_id: int,
        params: Optional[dict] = None,
        page_size: int = 25000,
    ) -> list[dict]:
        """Execute query, wait, collect all rows. Returns list of dicts."""
        execution_id = self.execute_query(query_id, params)
        self.wait_for_execution(execution_id)
        
        all_rows: list[dict] = []
        for page in self.iter_results(execution_id, page_size=page_size):
            all_rows.extend(page)
        
        logger.info(f"Collected {len(all_rows):,} total rows from query {query_id}")
        return all_rows

    def run_sql_and_collect(
        self,
        sql: str,
        query_name: str = "prophet_temp",
        page_size: int = 25000,
    ) -> list[dict]:
        """Create temp query, execute, collect, return rows."""
        execution_id = self.execute_sql(sql, query_name)
        self.wait_for_execution(execution_id)
        
        all_rows: list[dict] = []
        for page in self.iter_results(execution_id, page_size=page_size):
            all_rows.extend(page)
        
        return all_rows
