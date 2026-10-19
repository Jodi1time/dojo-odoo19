# -*- coding: utf-8 -*-
"""
Vector Store — pgvector-backed intent embeddings for fast semantic routing.

Stores OpenAI text-embedding-3-small vectors (1536 dims) for each intent schema.
find_similar() performs cosine similarity search to identify the top-K matching
intents for a user query, dramatically reducing LLM prompt size and cost.
"""

import json
import logging
import time

import requests

from odoo import api, fields, models, tools
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# OpenAI embedding model configuration
_EMBEDDING_MODEL = "text-embedding-3-small"
_EMBEDDING_DIMS = 1536
_EMBEDDING_BATCH_SIZE = 50  # OpenAI supports up to 2048 inputs per batch


class AiVectorStore(models.Model):
    _name = "ai.vector.store"
    _description = "Intent Vector Embedding Store"
    _order = "intent_type"

    intent_type = fields.Char(
        string="Intent Type",
        required=True,
        index=True,
    )
    domain_agent = fields.Char(
        string="Domain Agent",
        index=True,
        help="Agent domain this intent belongs to (e.g., attendance, enrollment, crm)",
    )
    text_content = fields.Text(
        string="Embedded Text",
        help="The text that was embedded (intent description + examples)",
    )
    embedding_model = fields.Char(
        string="Model",
        default=_EMBEDDING_MODEL,
    )
    last_embedded = fields.Datetime(
        string="Last Embedded",
        default=fields.Datetime.now,
    )

    _sql_constraints = [
        (
            "intent_type_unique",
            "UNIQUE(intent_type)",
            "Each intent type can only have one embedding.",
        ),
    ]

    # ─── Init: Create pgvector column + index ─────────────────────────────────
    def init(self):
        """
        Ensure pgvector extension is enabled and the embedding column + index exist.

        Called on module install/upgrade.  Runs raw SQL because Odoo's ORM
        does not natively support the ``vector`` column type.
        """
        cr = self.env.cr
        # Enable pgvector extension (idempotent)
        try:
            with cr.savepoint(flush=False):
                cr.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:
            _logger.warning(
                "Could not create pgvector extension — "
                "make sure the PostgreSQL server has pgvector installed. "
                "Vector similarity search will be unavailable."
            )
            return

        # Add the embedding column if it doesn't exist
        cr.execute("""
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = 'ai_vector_store'
               AND column_name = 'embedding'
        """)
        if not cr.fetchone():
            cr.execute(
                "ALTER TABLE ai_vector_store "
                f"ADD COLUMN embedding vector({_EMBEDDING_DIMS})"
            )
            _logger.info("Added vector(%s) column to ai_vector_store", _EMBEDDING_DIMS)

        # Create index for cosine similarity (idempotent)
        cr.execute("""
            SELECT indexname FROM pg_indexes
             WHERE tablename = 'ai_vector_store'
               AND indexname = 'idx_ai_vector_store_embedding'
        """)
        if not cr.fetchone():
            # ivfflat needs at least some rows; if table is empty, use hnsw
            cr.execute("SELECT count(*) FROM ai_vector_store")
            row_count = cr.fetchone()[0]
            if row_count >= 100:
                lists = max(row_count // 50, 1)
                cr.execute(f"""
                    CREATE INDEX idx_ai_vector_store_embedding
                        ON ai_vector_store
                     USING ivfflat (embedding vector_cosine_ops)
                      WITH (lists = {lists})
                """)
            else:
                # Use hnsw for small datasets — no training data required
                cr.execute("""
                    CREATE INDEX idx_ai_vector_store_embedding
                        ON ai_vector_store
                     USING hnsw (embedding vector_cosine_ops)
                """)
            _logger.info("Created vector similarity index on ai_vector_store")

    # ─── Embedding API (with retry + backoff for DNS/transient errors) ────────
    _EMBED_MAX_RETRIES = 3
    _EMBED_RETRY_BACKOFF = 1.0  # seconds, doubles each retry

    @api.model
    def _get_openai_api_key(self):
        """Retrieve the OpenAI API key from system parameters."""
        ICP = self.env["ir.config_parameter"].sudo()
        key = (
            ICP.get_str("openai.api_key")
            or ICP.get_str("elevenlabs_connector.openai_api_key")
            or ""
        )
        if not key:
            raise UserError(
                "OpenAI API key not configured. "
                "Set it in Settings → AI → Configuration."
            )
        return key

    @api.model
    def _request_with_retry(self, url, headers, payload, timeout=30):
        """
        POST to an HTTP endpoint with retry + exponential backoff.

        Retries on connection errors, DNS failures, and 5xx responses.
        Does NOT retry on 4xx (client errors) — those are permanent.

        Returns:
            dict: Parsed JSON response.

        Raises:
            UserError: After all retries exhausted or on 4xx errors.
        """
        last_error = None
        for attempt in range(self._EMBED_MAX_RETRIES):
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
                # Don't retry 4xx errors (bad request, auth, etc.)
                if 400 <= resp.status_code < 500:
                    resp.raise_for_status()

                # Retry 5xx errors
                if resp.status_code >= 500:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    _logger.warning(
                        "OpenAI API attempt %d/%d got %d — retrying",
                        attempt + 1,
                        self._EMBED_MAX_RETRIES,
                        resp.status_code,
                    )
                    if attempt < self._EMBED_MAX_RETRIES - 1:
                        time.sleep(self._EMBED_RETRY_BACKOFF * (2 ** attempt))
                    continue

                return resp.json()

            except requests.ConnectionError as e:
                last_error = e
                _logger.warning(
                    "OpenAI API attempt %d/%d connection error (DNS/network): %s",
                    attempt + 1,
                    self._EMBED_MAX_RETRIES,
                    e,
                )
            except requests.Timeout as e:
                last_error = e
                _logger.warning(
                    "OpenAI API attempt %d/%d timed out: %s",
                    attempt + 1,
                    self._EMBED_MAX_RETRIES,
                    e,
                )
            except requests.RequestException as e:
                # Non-retryable request error
                _logger.error("OpenAI API error (non-retryable): %s", e)
                raise UserError(f"Embedding API error: {e}") from e

            if attempt < self._EMBED_MAX_RETRIES - 1:
                time.sleep(self._EMBED_RETRY_BACKOFF * (2 ** attempt))

        raise UserError(
            f"Embedding API unreachable after {self._EMBED_MAX_RETRIES} attempts: {last_error}"
        )

    @api.model
    def embed_text(self, text):
        """
        Generate an embedding vector for the given text using OpenAI.

        Args:
            text: The text to embed.

        Returns:
            list[float]: 1536-dimensional embedding vector.
        """
        api_key = self._get_openai_api_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": _EMBEDDING_MODEL,
            "input": text,
        }
        data = self._request_with_retry(
            "https://api.openai.com/v1/embeddings",
            headers,
            payload,
            timeout=30,
        )
        return data["data"][0]["embedding"]

    @api.model
    def embed_batch(self, texts):
        """
        Generate embeddings for multiple texts in a single API call.

        Args:
            texts: list[str] — texts to embed (max 2048 per call).

        Returns:
            list[list[float]]: list of embedding vectors, same order as input.
        """
        if not texts:
            return []
        api_key = self._get_openai_api_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        all_embeddings = []
        for i in range(0, len(texts), _EMBEDDING_BATCH_SIZE):
            batch = texts[i : i + _EMBEDDING_BATCH_SIZE]
            payload = {
                "model": _EMBEDDING_MODEL,
                "input": batch,
            }
            data = self._request_with_retry(
                "https://api.openai.com/v1/embeddings",
                headers,
                payload,
                timeout=60,
            )
            # Sort by index to preserve order
            sorted_data = sorted(data["data"], key=lambda x: x["index"])
            all_embeddings.extend([d["embedding"] for d in sorted_data])
        return all_embeddings

    # ─── Similarity Search ────────────────────────────────────────────────────
    @api.model
    def find_similar(self, query_text, top_k=5, threshold=0.7):
        """
        Find the most similar intent types for a user query.

        Uses pgvector cosine distance (1 - cosine_similarity).
        Returns results sorted by similarity score descending.

        Args:
            query_text: The user's natural language query.
            top_k: Maximum number of results to return.
            threshold: Minimum similarity score (0.0-1.0).

        Returns:
            list[dict]: [
                {
                    "intent_type": str,
                    "domain_agent": str,
                    "similarity": float,
                    "text_content": str,
                },
                ...
            ]
        """
        # Check if vector column exists and has data
        cr = self.env.cr
        cr.execute("SELECT count(*) FROM ai_vector_store WHERE embedding IS NOT NULL")
        if cr.fetchone()[0] == 0:
            _logger.warning("No embeddings in vector store — falling back to full intent list")
            return []

        # Embed the query
        try:
            query_embedding = self.embed_text(query_text)
        except Exception as e:
            _logger.error("Failed to embed query: %s", e)
            return []

        # Convert to pgvector format
        embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        # Cosine distance search: distance = 1 - similarity
        max_distance = 1.0 - threshold
        cr.execute(
            """
            SELECT intent_type,
                   domain_agent,
                   text_content,
                   1 - (embedding <=> %s::vector) AS similarity
              FROM ai_vector_store
             WHERE embedding IS NOT NULL
               AND 1 - (embedding <=> %s::vector) >= %s
             ORDER BY embedding <=> %s::vector
             LIMIT %s
            """,
            (embedding_str, embedding_str, threshold, embedding_str, top_k),
        )
        results = []
        for row in cr.fetchall():
            results.append({
                "intent_type": row[0],
                "domain_agent": row[1],
                "text_content": row[2],
                "similarity": round(row[3], 4),
            })
        return results

    # ─── Rebuild Embeddings ───────────────────────────────────────────────────
    @api.model
    def rebuild_embeddings(self):
        """
        Rebuild all intent embeddings from the current intent schema data.

        For each active intent, constructs an embedding text from its
        description and example phrases, then upserts the vector.
        """
        t0 = time.time()
        IntentSchema = self.env["ai.intent.schema"]
        intents = IntentSchema.search([("active", "=", True)])

        if not intents:
            _logger.info("No active intents found — nothing to embed")
            return

        _logger.info("Rebuilding embeddings for %d intents...", len(intents))

        # Build text content for each intent
        texts = []
        intent_data = []
        for intent in intents:
            parts = [f"Intent: {intent.intent_type}"]
            if intent.name:
                parts.append(f"Name: {intent.name}")
            if intent.description:
                parts.append(f"Description: {intent.description}")
            if intent.example_phrases:
                examples = [
                    ex.strip()
                    for ex in intent.example_phrases.splitlines()
                    if ex.strip()
                ]
                if examples:
                    parts.append("Examples: " + " | ".join(examples[:6]))
            text = "\n".join(parts)
            texts.append(text)

            # Resolve domain_agent from the intent's agent relationship
            agent = self.env["ai.agent"].search(
                [("intent_ids", "in", intent.id)], limit=1
            )
            intent_data.append({
                "intent_type": intent.intent_type,
                "domain_agent": agent.domain if agent else False,
                "text_content": text,
            })

        # Batch embed
        try:
            embeddings = self.embed_batch(texts)
        except Exception as e:
            _logger.error("Batch embedding failed: %s", e)
            return

        # Upsert into ai_vector_store
        cr = self.env.cr
        for i, data in enumerate(intent_data):
            embedding_str = "[" + ",".join(str(v) for v in embeddings[i]) + "]"
            cr.execute(
                """
                INSERT INTO ai_vector_store (intent_type, domain_agent, text_content,
                                             embedding_model, embedding, last_embedded,
                                             create_uid, create_date, write_uid, write_date)
                VALUES (%s, %s, %s, %s, %s::vector, NOW(),
                        %s, NOW(), %s, NOW())
                ON CONFLICT (intent_type)
                DO UPDATE SET domain_agent   = EXCLUDED.domain_agent,
                              text_content   = EXCLUDED.text_content,
                              embedding      = EXCLUDED.embedding,
                              embedding_model = EXCLUDED.embedding_model,
                              last_embedded  = NOW(),
                              write_uid      = EXCLUDED.write_uid,
                              write_date     = NOW()
                """,
                (
                    data["intent_type"],
                    data["domain_agent"],
                    data["text_content"],
                    _EMBEDDING_MODEL,
                    embedding_str,
                    self.env.uid,
                    self.env.uid,
                ),
            )

        elapsed = time.time() - t0
        _logger.info(
            "Rebuilt %d intent embeddings in %.1fs (model: %s)",
            len(intents),
            elapsed,
            _EMBEDDING_MODEL,
        )

    @api.model
    def cron_rebuild_embeddings(self):
        """Cron entry point for rebuilding embeddings."""
        self.rebuild_embeddings()
