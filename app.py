"""
Epstein Corpus Explorer - Flask API Backend
Provides search, filtering, and data access for the D3 dashboard.
"""

import sqlite3
import json
import re
import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from functools import lru_cache
from contextlib import contextmanager

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

app = Flask(__name__, static_folder='.')
CORS(app)

# Database path - configurable via environment variable for deployment
DB_PATH = os.environ.get('DATABASE_PATH', './data/corpus.sqlite')

# Connection pool
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def highlight_text(text, query):
    """Highlight matching terms in text."""
    if not query:
        return text
    words = query.split()
    for word in words:
        # Case-insensitive highlight that preserves original case
        pattern = re.compile(f'({re.escape(word)})', re.IGNORECASE)
        text = pattern.sub(r'<mark>\1</mark>', text)
    return text

def generate_typo_variants(word):
    """
    Generate common typo variations of a word.
    Handles: missing letters, doubled letters, swapped letters, common substitutions.
    """
    variants = [word.lower()]
    word = word.lower()

    # Common letter substitutions (phonetic)
    substitutions = {
        'ph': 'f', 'f': 'ph',
        'gh': 'g', 'g': 'gh',
        'c': 'k', 'k': 'c',
        'c': 's', 's': 'c',
        'z': 's', 's': 'z',
        'x': 'ks', 'ks': 'x',
        'qu': 'kw', 'kw': 'qu',
        'tion': 'shun', 'shun': 'tion',
        'i': 'y', 'y': 'i',
        'ee': 'i', 'i': 'ee',
        'ei': 'ie', 'ie': 'ei',
        'ai': 'ay', 'ay': 'ai',
        'ou': 'ow', 'ow': 'ou',
    }

    # Generate variants with single character missing
    for i in range(len(word)):
        variant = word[:i] + word[i+1:]
        if len(variant) >= 3:
            variants.append(variant)

    # Generate variants with adjacent letters swapped
    for i in range(len(word) - 1):
        variant = word[:i] + word[i+1] + word[i] + word[i+2:]
        variants.append(variant)

    # Apply common substitutions
    for old, new in substitutions.items():
        if old in word:
            variants.append(word.replace(old, new, 1))

    return list(set(variants))[:10]  # Limit to prevent explosion

def build_fuzzy_pattern(query):
    """
    Build SQL LIKE patterns for fuzzy matching.
    More targeted than character-by-character matching.
    """
    patterns = []
    words = query.lower().split()

    for word in words:
        if len(word) < 3:
            patterns.append(f'%{word}%')
            continue

        # Main pattern: allow one character variation anywhere
        # E.g., "maxwell" -> "%m_xwell%" or "%ma_well%" etc.
        for i in range(len(word)):
            pattern = '%' + word[:i] + '_' + word[i+1:] + '%'
            patterns.append(pattern)

        # Also include exact match
        patterns.append(f'%{word}%')

        # Generate typo variants
        for variant in generate_typo_variants(word):
            patterns.append(f'%{variant}%')

    return list(set(patterns))

@app.route('/')
def index():
    return send_from_directory('.', 'dashboard.html')

@app.route('/api/stats')
def get_stats():
    """Get corpus statistics for dashboard header."""
    with get_db() as conn:
        cursor = conn.cursor()

        stats = {}

        # Total counts
        cursor.execute("SELECT COUNT(*) FROM docs")
        stats['total_docs'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM chunks")
        stats['total_chunks'] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT cluster_id) FROM chunks WHERE cluster_id IS NOT NULL")
        stats['total_clusters'] = cursor.fetchone()[0]

        # Get metadata
        cursor.execute("SELECT k, v FROM meta")
        stats['meta'] = {row['k']: row['v'] for row in cursor.fetchall()}

        return jsonify(stats)

@app.route('/api/search')
def search():
    """
    Full-text search with optional fuzzy matching.
    Query params: q, fuzzy, limit, offset
    Supports exact phrase matching with quotes: "Bill Gates"
    """
    query = request.args.get('q', '').strip()
    fuzzy = request.args.get('fuzzy', 'false').lower() == 'true'
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))

    if not query:
        return jsonify({'results': [], 'total': 0, 'query': query})

    # Check if query is an exact phrase (in quotes)
    exact_phrase = query.startswith('"') and query.endswith('"')
    if exact_phrase:
        query = query[1:-1]  # Remove quotes

    with get_db() as conn:
        cursor = conn.cursor()
        results = []
        total = 0
        search_mode = 'exact_phrase' if exact_phrase else ('fuzzy' if fuzzy else 'keywords')

        try:
            if exact_phrase:
                # Exact phrase matching using LIKE
                like_pattern = f'%{query}%'

                cursor.execute("""
                    SELECT COUNT(*) FROM chunks WHERE text LIKE ?
                """, (like_pattern,))
                total = cursor.fetchone()[0]

                cursor.execute("""
                    SELECT uid, doc_id, source_file, text, cluster_id, token_count
                    FROM chunks WHERE text LIKE ?
                    LIMIT ? OFFSET ?
                """, (like_pattern, limit, offset))

            elif fuzzy:
                # Build fuzzy patterns for typo-tolerant search
                patterns = build_fuzzy_pattern(query)

                # Build OR condition for all patterns (limit to first 5 for performance)
                conditions = ' OR '.join(['LOWER(text) LIKE ?' for _ in patterns[:5]])

                # Count total (approximate - just use first pattern for speed)
                cursor.execute(f"""
                    SELECT COUNT(*) FROM chunks
                    WHERE {conditions}
                """, patterns[:5])
                total = cursor.fetchone()[0]

                # Get results
                cursor.execute(f"""
                    SELECT DISTINCT uid, doc_id, source_file, text, cluster_id, token_count
                    FROM chunks
                    WHERE {conditions}
                    LIMIT ? OFFSET ?
                """, patterns[:5] + [limit, offset])
            else:
                # Use FTS for keyword matching (faster, but splits on words)
                fts_query = ' OR '.join(query.split())

                # Count total
                cursor.execute("""
                    SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?
                """, (fts_query,))
                total = cursor.fetchone()[0]

                # Get results with join
                cursor.execute("""
                    SELECT c.uid, c.doc_id, c.source_file, c.text, c.cluster_id, c.token_count
                    FROM chunks c
                    INNER JOIN chunks_fts fts ON c.uid = fts.uid
                    WHERE chunks_fts MATCH ?
                    LIMIT ? OFFSET ?
                """, (fts_query, limit, offset))

            for row in cursor.fetchall():
                text = row['text'] or ''
                results.append({
                    'uid': row['uid'],
                    'doc_id': row['doc_id'],
                    'source_file': row['source_file'],
                    'text': text[:500] + ('...' if len(text) > 500 else ''),
                    'text_highlighted': highlight_text(text[:500], query) + ('...' if len(text) > 500 else ''),
                    'cluster_id': row['cluster_id'],
                    'token_count': row['token_count']
                })

        except Exception as e:
            # Fallback to simple LIKE search
            like_pattern = f'%{query}%'
            cursor.execute("""
                SELECT COUNT(*) FROM chunks WHERE text LIKE ?
            """, (like_pattern,))
            total = cursor.fetchone()[0]

            cursor.execute("""
                SELECT uid, doc_id, source_file, text, cluster_id, token_count
                FROM chunks WHERE text LIKE ?
                LIMIT ? OFFSET ?
            """, (like_pattern, limit, offset))

            for row in cursor.fetchall():
                text = row['text'] or ''
                results.append({
                    'uid': row['uid'],
                    'doc_id': row['doc_id'],
                    'source_file': row['source_file'],
                    'text': text[:500] + ('...' if len(text) > 500 else ''),
                    'text_highlighted': highlight_text(text[:500], query) + ('...' if len(text) > 500 else ''),
                    'cluster_id': row['cluster_id'],
                    'token_count': row['token_count']
                })

        # Determine search mode for frontend display
        if exact_phrase:
            search_mode = 'exact'
        elif fuzzy:
            search_mode = 'fuzzy'
        elif ' ' in query:
            search_mode = 'any_word'
        else:
            search_mode = 'standard'

        return jsonify({
            'results': results,
            'total': total,
            'query': query,
            'fuzzy': fuzzy,
            'search_mode': search_mode,
            'limit': limit,
            'offset': offset
        })

@app.route('/api/search/combined')
def search_combined():
    """
    Search with multiple terms combined with AND logic.
    Query params: terms (comma-separated), limit, offset, unique (dedupe by doc)
    Example: /api/search/combined?terms=Singapore,Bill Gates&limit=50
    """
    terms_param = request.args.get('terms', '').strip()
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))
    unique = request.args.get('unique', 'true').lower() == 'true'  # Default to unique

    if not terms_param:
        return jsonify({'results': [], 'total': 0, 'terms': []})

    # Parse comma-separated terms
    terms = [t.strip() for t in terms_param.split(',') if t.strip()]

    if not terms:
        return jsonify({'results': [], 'total': 0, 'terms': []})

    with get_db() as conn:
        cursor = conn.cursor()
        results = []

        # Build AND conditions - each term must appear in the text
        conditions = ' AND '.join(['text LIKE ?' for _ in terms])
        params = [f'%{term}%' for term in terms]

        if unique:
            # Count unique documents
            cursor.execute(f"""
                SELECT COUNT(DISTINCT doc_id) FROM chunks WHERE {conditions}
            """, params)
            total = cursor.fetchone()[0]

            # Get results and deduplicate in Python - fetch more to ensure we have enough unique docs
            # Prioritize shorter texts (cryptic emails are often more interesting)
            cursor.execute(f"""
                SELECT uid, doc_id, source_file, text, cluster_id, token_count
                FROM chunks WHERE {conditions}
                ORDER BY LENGTH(text) ASC
                LIMIT ? OFFSET ?
            """, params + [limit * 5, offset * 5])  # Fetch more to account for duplicates

            # Deduplicate by doc_id, keeping shortest/most cryptic text
            seen_docs = set()
            unique_results = []
            for row in cursor.fetchall():
                if row['doc_id'] not in seen_docs:
                    seen_docs.add(row['doc_id'])
                    unique_results.append(row)
                    if len(unique_results) >= limit:
                        break

            # Use the deduplicated results
            cursor_results = unique_results
        else:
            # Count total chunks
            cursor.execute(f"""
                SELECT COUNT(*) FROM chunks WHERE {conditions}
            """, params)
            total = cursor.fetchone()[0]

            # Get all matching chunks
            cursor.execute(f"""
                SELECT uid, doc_id, source_file, text, cluster_id, token_count
                FROM chunks WHERE {conditions}
                LIMIT ? OFFSET ?
            """, params + [limit, offset])
            cursor_results = cursor.fetchall()

        for row in cursor_results:
            text = row['text'] or ''
            # Highlight all terms
            highlighted = text[:500]
            for term in terms:
                highlighted = highlight_text(highlighted, term)

            results.append({
                'uid': row['uid'],
                'doc_id': row['doc_id'],
                'source_file': row['source_file'],
                'text': text[:500] + ('...' if len(text) > 500 else ''),
                'text_highlighted': highlighted + ('...' if len(text) > 500 else ''),
                'cluster_id': row['cluster_id'],
                'token_count': row['token_count']
            })

        return jsonify({
            'results': results,
            'total': total,
            'terms': terms,
            'search_mode': 'combined',
            'limit': limit,
            'offset': offset
        })

@app.route('/api/suggest')
def suggest():
    """
    Get search suggestions for autocomplete.
    Extracts common terms from matching documents.
    """
    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return jsonify({'suggestions': []})

    with get_db() as conn:
        cursor = conn.cursor()

        # Get sample of matching texts
        cursor.execute("""
            SELECT text FROM chunks
            WHERE text LIKE ?
            LIMIT 100
        """, (f'%{query}%',))

        # Extract unique words that match the query prefix
        suggestions = set()
        pattern = re.compile(r'\b' + re.escape(query) + r'\w*', re.IGNORECASE)

        for row in cursor.fetchall():
            if row['text']:
                matches = pattern.findall(row['text'])
                suggestions.update(m.lower() for m in matches)

        # Sort by length and return top suggestions
        sorted_suggestions = sorted(suggestions, key=len)[:10]

        return jsonify({'suggestions': sorted_suggestions})

@app.route('/api/document/<doc_id>')
def get_document(doc_id):
    """Get full document with all its chunks."""
    with get_db() as conn:
        cursor = conn.cursor()

        # Get document metadata
        cursor.execute("""
            SELECT doc_id, meta_json FROM docs WHERE doc_id = ?
        """, (doc_id,))
        doc_row = cursor.fetchone()

        if not doc_row:
            return jsonify({'error': 'Document not found'}), 404

        meta = json.loads(doc_row['meta_json']) if doc_row['meta_json'] else {}

        # Get all chunks for this document
        cursor.execute("""
            SELECT uid, chunk_id, order_index, text, cluster_id, token_count
            FROM chunks
            WHERE doc_id = ?
            ORDER BY order_index
        """, (doc_id,))

        chunks = []
        full_text = []
        for row in cursor.fetchall():
            chunks.append({
                'uid': row['uid'],
                'chunk_id': row['chunk_id'],
                'order_index': row['order_index'],
                'text': row['text'],
                'cluster_id': row['cluster_id'],
                'token_count': row['token_count']
            })
            if row['text']:
                full_text.append(row['text'])

        return jsonify({
            'doc_id': doc_id,
            'meta': meta,
            'chunks': chunks,
            'full_text': '\n\n'.join(full_text),
            'chunk_count': len(chunks)
        })

@app.route('/api/clusters')
def get_clusters():
    """Get cluster summaries for visualization."""
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT cluster_id, n_chunks, prob_avg, bm25_density_avg, token_count_avg
            FROM cluster_summary
            ORDER BY n_chunks DESC
        """)

        clusters = []
        for row in cursor.fetchall():
            clusters.append({
                'cluster_id': row['cluster_id'],
                'n_chunks': row['n_chunks'],
                'prob_avg': row['prob_avg'],
                'bm25_density_avg': row['bm25_density_avg'],
                'token_count_avg': row['token_count_avg']
            })

        return jsonify({'clusters': clusters})

@app.route('/api/cluster/<int:cluster_id>')
def get_cluster_samples(cluster_id):
    """Get sample documents from a specific cluster."""
    limit = min(int(request.args.get('limit', 20)), 100)

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT uid, doc_id, source_file, text, cluster_prob
            FROM chunks
            WHERE cluster_id = ?
            ORDER BY cluster_prob DESC
            LIMIT ?
        """, (cluster_id, limit))

        samples = []
        for row in cursor.fetchall():
            text = row['text'] or ''
            samples.append({
                'uid': row['uid'],
                'doc_id': row['doc_id'],
                'source_file': row['source_file'],
                'text': text[:300] + ('...' if len(text) > 300 else ''),
                'cluster_prob': row['cluster_prob']
            })

        return jsonify({'cluster_id': cluster_id, 'samples': samples})

@app.route('/api/cluster/<int:cluster_id>/preview')
def get_cluster_preview(cluster_id):
    """Get a quick preview of cluster contents for tooltips."""
    with get_db() as conn:
        cursor = conn.cursor()

        # Get 3 short representative samples
        cursor.execute("""
            SELECT text FROM chunks
            WHERE cluster_id = ? AND text IS NOT NULL AND LENGTH(text) > 50
            ORDER BY cluster_prob DESC
            LIMIT 3
        """, (cluster_id,))

        previews = []
        for row in cursor.fetchall():
            text = row[0]
            # Clean and truncate
            text = re.sub(r'\s+', ' ', text).strip()
            # Remove common boilerplate
            text = re.sub(r'please note.*?privileged.*?$', '', text, flags=re.IGNORECASE|re.DOTALL)
            text = re.sub(r'EFTA.*?\d+', '', text)
            text = text[:150].strip()
            if text:
                previews.append(text + '...')

        # Get cluster size
        cursor.execute("""
            SELECT n_chunks FROM cluster_summary WHERE cluster_id = ?
        """, (cluster_id,))
        row = cursor.fetchone()
        n_chunks = row[0] if row else 0

        return jsonify({
            'cluster_id': cluster_id,
            'n_chunks': n_chunks,
            'previews': previews
        })

@app.route('/api/random')
def get_random_samples():
    """Get random document samples for exploration."""
    limit = min(int(request.args.get('limit', 10)), 50)

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT uid, doc_id, source_file, text, cluster_id
            FROM chunks
            ORDER BY RANDOM()
            LIMIT ?
        """, (limit,))

        samples = []
        for row in cursor.fetchall():
            text = row['text'] or ''
            samples.append({
                'uid': row['uid'],
                'doc_id': row['doc_id'],
                'source_file': row['source_file'],
                'text': text[:400] + ('...' if len(text) > 400 else ''),
                'cluster_id': row['cluster_id']
            })

        return jsonify({'samples': samples})

@app.route('/api/source-files')
def get_source_files():
    """Get list of unique source file patterns."""
    with get_db() as conn:
        cursor = conn.cursor()

        # Get sample of source files to understand structure
        cursor.execute("""
            SELECT DISTINCT source_file FROM chunks LIMIT 1000
        """)

        files = [row['source_file'] for row in cursor.fetchall()]

        # Extract folder patterns
        folders = set()
        for f in files:
            if f:
                parts = f.split('/')
                if len(parts) > 2:
                    folders.add('/'.join(parts[:-1]))

        return jsonify({
            'sample_files': files[:100],
            'folders': sorted(folders)[:50]
        })

# Pre-computed list of notable people to search for
NOTABLE_PEOPLE = [
    'Bill Gates', 'Elon Musk', 'Leon Black', 'Ehud Barak', 'Larry Summers',
    'Reid Hoffman', 'Peter Thiel', 'Woody Allen', 'Bill Clinton', 'Donald Trump',
    'Prince Andrew', 'Ghislaine Maxwell', 'Les Wexner', 'Alan Dershowitz',
    'Naomi Campbell', 'Stephen Hawking', 'Kevin Spacey', 'Sergey Brin',
    'Marvin Minsky', 'George Mitchell', 'Glenn Dubin', 'Eva Andersson-Dubin',
    'Jean-Luc Brunel', 'Sarah Kellen', 'Nadia Marcinkova', 'Virginia Giuffre',
    'Maria Farmer', 'Courtney Wild', 'Alexander Acosta', 'Cyrus Vance'
]

# Pre-computed list of key locations
KEY_LOCATIONS = [
    'Palm Beach', 'New York', 'Paris', 'Virgin Islands', 'Zorro Ranch',
    'Little St James', 'Singapore', 'Hong Kong', 'London', 'Manhattan',
    'Florida', 'New Mexico', 'Caribbean', 'St. Thomas', 'Ohio',
    'Upper East Side', 'East 71st Street', 'Harvard', 'MIT', 'JPMorgan',
    'Deutsche Bank', 'Bear Stearns', 'Victoria\'s Secret', 'Wexner Foundation'
]

@app.route('/api/people')
def get_people():
    """
    Return list of notable people found in corpus with mention counts.
    Pre-computed list includes key figures associated with the Epstein case.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        people_counts = []

        for person in NOTABLE_PEOPLE:
            # Use case-insensitive search for each person
            # Search for full name and also last name for common references
            like_pattern = f'%{person}%'

            cursor.execute("""
                SELECT COUNT(*) FROM chunks WHERE LOWER(text) LIKE LOWER(?)
            """, (like_pattern,))
            count = cursor.fetchone()[0]

            if count > 0:
                people_counts.append({
                    'name': person,
                    'count': count
                })

        # Sort by count descending
        people_counts.sort(key=lambda x: x['count'], reverse=True)

        return jsonify({
            'people': people_counts,
            'total_people': len(people_counts)
        })

@app.route('/api/places')
def get_places():
    """
    Return list of key locations with mention counts.
    Includes locations relevant to the Epstein case.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        places_counts = []

        for place in KEY_LOCATIONS:
            like_pattern = f'%{place}%'

            cursor.execute("""
                SELECT COUNT(*) FROM chunks WHERE LOWER(text) LIKE LOWER(?)
            """, (like_pattern,))
            count = cursor.fetchone()[0]

            if count > 0:
                places_counts.append({
                    'name': place,
                    'count': count
                })

        # Sort by count descending
        places_counts.sort(key=lambda x: x['count'], reverse=True)

        return jsonify({
            'places': places_counts,
            'total_places': len(places_counts)
        })

@app.route('/api/timeline')
def get_timeline():
    """
    Return document counts by year for visualization.
    Extracts years from text content and returns {year: count} data.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # Get all text chunks to extract years
        cursor.execute("""
            SELECT text FROM chunks WHERE text IS NOT NULL
        """)

        year_counts = {}
        year_pattern = re.compile(r'\b(19[89]\d|20[0-2]\d)\b')  # Match years 1980-2029

        for row in cursor.fetchall():
            text = row['text'] or ''
            years_found = year_pattern.findall(text)
            for year in years_found:
                year_int = int(year)
                # Filter to reasonable range (1990-2025)
                if 1990 <= year_int <= 2025:
                    year_counts[year_int] = year_counts.get(year_int, 0) + 1

        # Convert to sorted list format for easier visualization
        timeline_data = [
            {'year': year, 'count': count}
            for year, count in sorted(year_counts.items())
        ]

        return jsonify({
            'timeline': timeline_data,
            'year_counts': year_counts,
            'total_years': len(year_counts)
        })

@app.route('/api/connections/<name>')
def get_connections(name):
    """
    Return documents mentioning a specific person.
    Supports limit/offset pagination.
    """
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))

    if not name:
        return jsonify({'error': 'Name parameter is required'}), 400

    with get_db() as conn:
        cursor = conn.cursor()

        like_pattern = f'%{name}%'

        # Get total count
        cursor.execute("""
            SELECT COUNT(*) FROM chunks WHERE LOWER(text) LIKE LOWER(?)
        """, (like_pattern,))
        total = cursor.fetchone()[0]

        # Get paginated results
        cursor.execute("""
            SELECT uid, doc_id, source_file, text, cluster_id, token_count
            FROM chunks
            WHERE LOWER(text) LIKE LOWER(?)
            LIMIT ? OFFSET ?
        """, (like_pattern, limit, offset))

        results = []
        for row in cursor.fetchall():
            text = row['text'] or ''
            results.append({
                'uid': row['uid'],
                'doc_id': row['doc_id'],
                'source_file': row['source_file'],
                'text': text[:500] + ('...' if len(text) > 500 else ''),
                'text_highlighted': highlight_text(text[:500], name) + ('...' if len(text) > 500 else ''),
                'cluster_id': row['cluster_id'],
                'token_count': row['token_count']
            })

        return jsonify({
            'name': name,
            'results': results,
            'total': total,
            'limit': limit,
            'offset': offset
        })

if __name__ == '__main__':
    print("Starting Epstein Corpus Explorer API...")
    print(f"Database: {DB_PATH}")
    print("Open http://localhost:5001 in your browser")
    app.run(host='0.0.0.0', port=5001, debug=True)
