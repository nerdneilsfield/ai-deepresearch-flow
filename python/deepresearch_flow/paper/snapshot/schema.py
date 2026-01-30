from __future__ import annotations

import sqlite3


def init_snapshot_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA journal_mode=WAL;")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS snapshot_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS paper (
          paper_id TEXT PRIMARY KEY,
          paper_key TEXT NOT NULL,
          paper_key_type TEXT NOT NULL,
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

        CREATE TABLE IF NOT EXISTS paper_summary (
          paper_id TEXT NOT NULL,
          template_tag TEXT NOT NULL,
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


def recompute_facet_counts(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE author SET paper_count = (SELECT COUNT(*) FROM paper_author WHERE author_id = author.author_id);"
    )
    conn.execute(
        "UPDATE keyword SET paper_count = (SELECT COUNT(*) FROM paper_keyword WHERE keyword_id = keyword.keyword_id);"
    )
    conn.execute(
        "UPDATE institution SET paper_count = (SELECT COUNT(*) FROM paper_institution WHERE institution_id = institution.institution_id);"
    )
    conn.execute(
        "UPDATE tag SET paper_count = (SELECT COUNT(*) FROM paper_tag WHERE tag_id = tag.tag_id);"
    )
    conn.execute(
        "UPDATE venue SET paper_count = (SELECT COUNT(*) FROM paper_venue WHERE venue_id = venue.venue_id);"
    )
    conn.execute(
        "UPDATE facet_node SET paper_count = (SELECT COUNT(*) FROM paper_facet WHERE node_id = facet_node.node_id);"
    )

    conn.execute("DELETE FROM year_count;")
    conn.execute(
        """
        INSERT INTO year_count(year, paper_count)
        SELECT year, COUNT(*) AS paper_count
        FROM paper
        GROUP BY year
        """
    )

    conn.execute("DELETE FROM month_count;")
    conn.execute(
        """
        INSERT INTO month_count(month, paper_count)
        SELECT month, COUNT(*) AS paper_count
        FROM paper
        GROUP BY month
        """
    )


def recompute_paper_index(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        WITH ordered AS (
          SELECT paper_id, ROW_NUMBER() OVER (ORDER BY paper_id ASC) AS idx
          FROM paper
        )
        UPDATE paper
        SET paper_index = (SELECT idx FROM ordered WHERE ordered.paper_id = paper.paper_id);
        """
    )
