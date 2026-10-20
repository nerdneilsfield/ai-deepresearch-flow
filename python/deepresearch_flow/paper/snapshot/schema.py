from __future__ import annotations

import sqlite3

from deepresearch_flow.paper.snapshot.common import _column_exists


def init_snapshot_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON;")
    # Snapshot writers include the pipeline publisher.  Keep the existing WAL
    # mode, but make short cross-process waits explicit instead of relying on
    # sqlite's process-default timeout.
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA journal_mode=WAL;")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS snapshot_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pipeline_publication_receipt (
          job_id TEXT PRIMARY KEY,
          bundle_digest TEXT NOT NULL,
          paper_id TEXT NOT NULL,
          published_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pipeline_publication_receipt_paper_id
          ON pipeline_publication_receipt(paper_id);

        CREATE TABLE IF NOT EXISTS paper (
          paper_id TEXT PRIMARY KEY,
          paper_key TEXT NOT NULL,
          paper_key_type TEXT NOT NULL,
          doi TEXT,
          title TEXT NOT NULL,
          year TEXT NOT NULL,
          month TEXT NOT NULL,
          publication_date TEXT NOT NULL,
          venue TEXT NOT NULL,
          preferred_summary_template TEXT NOT NULL,
          summary_preview TEXT NOT NULL,
          paper_index INTEGER NOT NULL DEFAULT 0,
          source_hash TEXT,
          output_language TEXT,
          provider TEXT,
          model TEXT,
          prompt_template TEXT,
          extracted_at TEXT,
          pdf_content_hash TEXT,
          source_md_content_hash TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_paper_key ON paper(paper_key);
        CREATE INDEX IF NOT EXISTS idx_paper_year ON paper(year);
        CREATE INDEX IF NOT EXISTS idx_paper_month ON paper(month);
        CREATE INDEX IF NOT EXISTS idx_paper_venue ON paper(venue);
        CREATE INDEX IF NOT EXISTS idx_paper_index ON paper(paper_index);
        CREATE INDEX IF NOT EXISTS idx_paper_source_hash ON paper(source_hash);
        CREATE INDEX IF NOT EXISTS idx_paper_preferred_summary_template ON paper(preferred_summary_template);

        CREATE TABLE IF NOT EXISTS paper_summary (
          paper_id TEXT NOT NULL,
          template_tag TEXT NOT NULL,
          resource_path TEXT,
          content_hash TEXT,
          PRIMARY KEY (paper_id, template_tag),
          FOREIGN KEY (paper_id) REFERENCES paper(paper_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_paper_summary_template ON paper_summary(template_tag);

        CREATE TABLE IF NOT EXISTS paper_translation (
          paper_id TEXT NOT NULL,
          lang TEXT NOT NULL,
          md_content_hash TEXT NOT NULL,
          PRIMARY KEY (paper_id, lang),
          FOREIGN KEY (paper_id) REFERENCES paper(paper_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS semantic_staging (
          staging_id INTEGER PRIMARY KEY AUTOINCREMENT,
          doc_id TEXT NOT NULL,
          template_tag TEXT NOT NULL,
          group_hash TEXT NOT NULL,
          part_index INTEGER NOT NULL,
          part_count INTEGER NOT NULL,
          chunk_data TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE(doc_id, template_tag, group_hash, part_index)
        );

        CREATE TABLE IF NOT EXISTS paper_bibtex (
          paper_id TEXT PRIMARY KEY,
          bibtex_raw TEXT NOT NULL,
          bibtex_key TEXT,
          entry_type TEXT,
          FOREIGN KEY (paper_id) REFERENCES paper(paper_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS paper_key_alias (
          paper_key TEXT PRIMARY KEY,
          paper_id TEXT NOT NULL,
          paper_key_type TEXT NOT NULL,
          meta_fingerprint TEXT,
          FOREIGN KEY (paper_id) REFERENCES paper(paper_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_paper_key_alias_paper_id ON paper_key_alias(paper_id);

        CREATE TABLE IF NOT EXISTS author (
          author_id INTEGER PRIMARY KEY,
          value TEXT NOT NULL UNIQUE,
          paper_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS paper_author (
          paper_id TEXT NOT NULL,
          author_id INTEGER NOT NULL,
          PRIMARY KEY (paper_id, author_id),
          FOREIGN KEY (paper_id) REFERENCES paper(paper_id) ON DELETE CASCADE,
          FOREIGN KEY (author_id) REFERENCES author(author_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_paper_author_author_id ON paper_author(author_id);

        CREATE TABLE IF NOT EXISTS keyword (
          keyword_id INTEGER PRIMARY KEY,
          value TEXT NOT NULL UNIQUE,
          paper_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS paper_keyword (
          paper_id TEXT NOT NULL,
          keyword_id INTEGER NOT NULL,
          PRIMARY KEY (paper_id, keyword_id),
          FOREIGN KEY (paper_id) REFERENCES paper(paper_id) ON DELETE CASCADE,
          FOREIGN KEY (keyword_id) REFERENCES keyword(keyword_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_paper_keyword_keyword_id ON paper_keyword(keyword_id);

        CREATE TABLE IF NOT EXISTS institution (
          institution_id INTEGER PRIMARY KEY,
          value TEXT NOT NULL UNIQUE,
          paper_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS paper_institution (
          paper_id TEXT NOT NULL,
          institution_id INTEGER NOT NULL,
          PRIMARY KEY (paper_id, institution_id),
          FOREIGN KEY (paper_id) REFERENCES paper(paper_id) ON DELETE CASCADE,
          FOREIGN KEY (institution_id) REFERENCES institution(institution_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_paper_institution_institution_id ON paper_institution(institution_id);

        CREATE TABLE IF NOT EXISTS tag (
          tag_id INTEGER PRIMARY KEY,
          value TEXT NOT NULL UNIQUE,
          paper_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS paper_tag (
          paper_id TEXT NOT NULL,
          tag_id INTEGER NOT NULL,
          PRIMARY KEY (paper_id, tag_id),
          FOREIGN KEY (paper_id) REFERENCES paper(paper_id) ON DELETE CASCADE,
          FOREIGN KEY (tag_id) REFERENCES tag(tag_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_paper_tag_tag_id ON paper_tag(tag_id);

        CREATE TABLE IF NOT EXISTS venue (
          venue_id INTEGER PRIMARY KEY,
          value TEXT NOT NULL UNIQUE,
          paper_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS paper_venue (
          paper_id TEXT NOT NULL,
          venue_id INTEGER NOT NULL,
          PRIMARY KEY (paper_id, venue_id),
          FOREIGN KEY (paper_id) REFERENCES paper(paper_id) ON DELETE CASCADE,
          FOREIGN KEY (venue_id) REFERENCES venue(venue_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_paper_venue_venue_id ON paper_venue(venue_id);

        CREATE TABLE IF NOT EXISTS facet_node (
          node_id INTEGER PRIMARY KEY,
          facet_type TEXT NOT NULL,
          value TEXT NOT NULL,
          paper_count INTEGER NOT NULL DEFAULT 0,
          UNIQUE(facet_type, value)
        );
        CREATE INDEX IF NOT EXISTS idx_facet_node_type ON facet_node(facet_type);
        CREATE INDEX IF NOT EXISTS idx_facet_node_value ON facet_node(value);

        CREATE TABLE IF NOT EXISTS paper_facet (
          paper_id TEXT NOT NULL,
          node_id INTEGER NOT NULL,
          PRIMARY KEY (paper_id, node_id),
          FOREIGN KEY (paper_id) REFERENCES paper(paper_id) ON DELETE CASCADE,
          FOREIGN KEY (node_id) REFERENCES facet_node(node_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_paper_facet_node_id ON paper_facet(node_id);

        CREATE TABLE IF NOT EXISTS facet_edge (
          node_id_a INTEGER NOT NULL,
          node_id_b INTEGER NOT NULL,
          paper_count INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (node_id_a, node_id_b),
          FOREIGN KEY (node_id_a) REFERENCES facet_node(node_id) ON DELETE CASCADE,
          FOREIGN KEY (node_id_b) REFERENCES facet_node(node_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_facet_edge_a ON facet_edge(node_id_a);
        CREATE INDEX IF NOT EXISTS idx_facet_edge_b ON facet_edge(node_id_b);

        CREATE TABLE IF NOT EXISTS year_count (
          year TEXT PRIMARY KEY,
          paper_count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS month_count (
          month TEXT PRIMARY KEY,
          paper_count INTEGER NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts USING fts5(
          paper_id UNINDEXED,
          title,
          summary,
          source,
          translated,
          metadata,
          tokenize='unicode61'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts_trigram USING fts5(
          paper_id UNINDEXED,
          title,
          venue,
          tokenize='trigram'
        );
        """
    )

    # Backward-compatible migrations for older snapshots.
    if not _column_exists(conn, "paper", "doi"):
        conn.execute("ALTER TABLE paper ADD COLUMN doi TEXT")
    if not _column_exists(conn, "paper_summary", "resource_path"):
        conn.execute("ALTER TABLE paper_summary ADD COLUMN resource_path TEXT")
    if not _column_exists(conn, "paper_summary", "content_hash"):
        conn.execute("ALTER TABLE paper_summary ADD COLUMN content_hash TEXT")


_FACET_COUNT_TABLES = (
    ("author", "author_id", "paper_author"),
    ("keyword", "keyword_id", "paper_keyword"),
    ("institution", "institution_id", "paper_institution"),
    ("tag", "tag_id", "paper_tag"),
    ("venue", "venue_id", "paper_venue"),
    ("facet_node", "node_id", "paper_facet"),
)


def recompute_facet_counts(conn: sqlite3.Connection) -> None:
    """Refresh paper_count columns, writing only the rows whose count changed."""
    for table, id_col, join_table in _FACET_COUNT_TABLES:
        conn.execute(
            f"""
            UPDATE {table} SET paper_count = counts.n
            FROM (SELECT {id_col} AS id, COUNT(*) AS n FROM {join_table} GROUP BY {id_col}) AS counts
            WHERE {table}.{id_col} = counts.id AND {table}.paper_count IS NOT counts.n
            """
        )
        conn.execute(
            f"""
            UPDATE {table} SET paper_count = 0
            WHERE paper_count IS NOT 0
              AND NOT EXISTS (SELECT 1 FROM {join_table} WHERE {join_table}.{id_col} = {table}.{id_col})
            """
        )

    for table, column in (("year_count", "year"), ("month_count", "month")):
        conn.execute(
            f"""
            INSERT INTO {table}({column}, paper_count)
            SELECT {column}, COUNT(*) AS n FROM paper WHERE true GROUP BY {column}
            ON CONFLICT({column}) DO UPDATE SET paper_count = excluded.paper_count
            WHERE {table}.paper_count IS NOT excluded.paper_count
            """
        )
        conn.execute(
            f"""
            DELETE FROM {table}
            WHERE NOT EXISTS (SELECT 1 FROM paper WHERE paper.{column} = {table}.{column})
            """
        )


def recompute_facet_edges(conn: sqlite3.Connection) -> None:
    """Rebuild facet_edge co-occurrence counts from paper_facet."""
    conn.execute("DELETE FROM facet_edge;")
    conn.execute(
        """
        INSERT INTO facet_edge(node_id_a, node_id_b, paper_count)
        SELECT a.node_id, b.node_id, COUNT(*) AS paper_count
        FROM paper_facet a
        JOIN paper_facet b ON a.paper_id = b.paper_id AND a.node_id < b.node_id
        GROUP BY a.node_id, b.node_id
        """
    )


def recompute_paper_index(conn: sqlite3.Connection) -> None:
    """Renumber paper_index by paper_id, writing only rows whose number changed."""
    conn.execute(
        """
        WITH ordered AS (
          SELECT paper_id, ROW_NUMBER() OVER (ORDER BY paper_id ASC) AS idx
          FROM paper
        )
        UPDATE paper SET paper_index = ordered.idx
        FROM ordered
        WHERE paper.paper_id = ordered.paper_id AND paper.paper_index IS NOT ordered.idx
        """
    )
