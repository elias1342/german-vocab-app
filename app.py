try:
    from flask import Flask, render_template, request, jsonify
except ImportError:
    import subprocess, sys
    print("Installing required packages...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "flask", "openai", "-q"])
    from flask import Flask, render_template, request, jsonify

import openai as openai_lib
import os, re, json, sqlite3, threading, webbrowser
from contextlib import contextmanager
from datetime import datetime, date, timedelta

app = Flask(__name__)

# ── .env loader (must run before env-var reads below) ────────────────────────

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))

# ── Database config ───────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL")
# SQLite fallback path (only used when DATABASE_URL is not set)
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "searches.db")


def _sql(query):
    """Convert SQLite-style ? placeholders to PostgreSQL %s."""
    if DATABASE_URL:
        return query.replace("?", "%s")
    return query


@contextmanager
def _db():
    """Yield an active DB cursor; commit on clean exit, rollback on error."""
    if DATABASE_URL:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
    else:
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db():
    with _db() as c:
        if DATABASE_URL:
            # PostgreSQL: SERIAL for auto-increment, include all_trans upfront
            c.execute(
                """CREATE TABLE IF NOT EXISTS searches (
                    id        SERIAL PRIMARY KEY,
                    word      TEXT   NOT NULL,
                    top_trans TEXT,
                    all_trans TEXT,
                    ts        TEXT   NOT NULL
                )"""
            )
            # Safe no-op if column already exists (handles schema migration)
            c.execute(
                "ALTER TABLE searches ADD COLUMN IF NOT EXISTS all_trans TEXT"
            )
        else:
            # SQLite: AUTOINCREMENT syntax
            c.execute(
                """CREATE TABLE IF NOT EXISTS searches (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    word      TEXT    NOT NULL,
                    top_trans TEXT,
                    ts        TEXT    NOT NULL
                )"""
            )
            try:
                c.execute("ALTER TABLE searches ADD COLUMN all_trans TEXT")
            except Exception:
                pass  # column already exists in existing DB
        c.execute(
            """CREATE TABLE IF NOT EXISTS word_schedule (
                word        TEXT    PRIMARY KEY,
                interval    INTEGER NOT NULL DEFAULT 1,
                next_review TEXT    NOT NULL
            )"""
        )


# ── OpenAI client (lazy) ──────────────────────────────────────────────────────

_client = None

def get_client():
    global _client
    if _client is None:
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set in your .env file.")
        _client = openai_lib.OpenAI(api_key=key)
    return _client


# ── Lookup ────────────────────────────────────────────────────────────────────

def _make_prompt(word):
    return (
        f'You are a professional German-English lexicographer.\n'
        f'For the English word "{word}", return ONLY a valid JSON object — no markdown, no code fences:\n'
        '{\n'
        '  "etymology": "Brief origin and history of the word (1-3 sentences)",\n'
        '  "translations": [\n'
        '    {\n'
        '      "german": "German word or phrase",\n'
        '      "context": "usage label, e.g. everyday, formal, informal, technical, colloquial, literary, archaic, regional",\n'
        f'      "example_en": "A natural English sentence using \\"{word}\\"",\n'
        '      "example_de": "German translation of that sentence"\n'
        '    }\n'
        '  ]\n'
        '}\n'
        'Rules:\n'
        '- Provide up to 5 translations, ordered most-to-least common\n'
        '- Each translation must have a distinct meaning or register\n'
        '- Return ONLY the JSON object, nothing else'
    )

def lookup_word(word):
    resp = get_client().chat.completions.create(
        model="gpt-4o",
        max_tokens=1500,
        messages=[{"role": "user", "content": _make_prompt(word)}],
    )
    text = resp.choices[0].message.content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    data = json.loads(text)
    translations = [
        {
            "german":     (t.get("german")     or "").strip(),
            "context":    (t.get("context")    or "").strip(),
            "example_en": (t.get("example_en") or "").strip(),
            "example_de": (t.get("example_de") or "").strip(),
        }
        for t in data.get("translations", [])[:5]
        if (t.get("german") or "").strip()
    ]
    return {
        "etymology":    str(data.get("etymology") or "").strip(),
        "translations": translations,
    }


# ── SM-2 (simplified) ─────────────────────────────────────────────────────────

def _next_interval(current, score):
    if score == 100:
        return max(1, current) * 2
    elif score > 0:
        return max(1, current) + 1
    else:
        return 1


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    from flask import make_response
    r = make_response(render_template("index.html"))
    r.headers["Cache-Control"] = "no-store"
    return r


@app.route("/api/lookup", methods=["POST"])
def lookup():
    word = (request.get_json(silent=True) or {}).get("word", "").strip().lower()
    if not word:
        return jsonify(error="Please enter a word"), 400

    try:
        result = lookup_word(word)
    except RuntimeError as e:
        return jsonify(error=str(e)), 500
    except json.JSONDecodeError:
        return jsonify(error="Unexpected response format. Please try again."), 500
    except Exception as e:
        return jsonify(error=f"Lookup failed: {e}"), 500

    top    = result["translations"][0]["german"] if result["translations"] else ""
    all_de = ", ".join(t["german"] for t in result["translations"] if t["german"])
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _db() as c:
        c.execute(
            _sql("INSERT INTO searches (word, top_trans, all_trans, ts) VALUES (?,?,?,?)"),
            (word, top, all_de, ts),
        )

    return jsonify(word=word, etymology=result["etymology"], translations=result["translations"])


@app.route("/api/game/words")
def game_words():
    today = date.today().isoformat()
    with _db() as c:
        c.execute(
            _sql("""SELECT s.word, s.all_trans, s.top_trans,
                          COALESCE(ws.interval, 1) AS interval
                   FROM searches s
                   JOIN (SELECT word, MAX(id) AS maxid FROM searches GROUP BY word) latest
                        ON s.id = latest.maxid
                   LEFT JOIN word_schedule ws ON ws.word = s.word
                   WHERE COALESCE(s.all_trans, s.top_trans, '') != ''
                     AND (ws.word IS NULL OR ws.next_review <= ?)
                   ORDER BY COALESCE(ws.next_review, ?) ASC"""),
            (today, today),
        )
        rows = c.fetchall()
    words = []
    for word, all_trans, top_trans, interval in rows:
        trans_str    = all_trans or top_trans or ""
        translations = [t.strip() for t in trans_str.split(",") if t.strip()]
        if translations:
            words.append({"word": word, "translations": translations, "interval": interval})
    return jsonify(words)


@app.route("/api/game/schedule", methods=["POST"])
def update_schedule():
    body  = request.get_json(silent=True) or {}
    word  = (body.get("word") or "").strip().lower()
    score = int(body.get("score", 0))
    if not word:
        return jsonify(error="word required"), 400

    today = date.today()
    with _db() as c:
        c.execute(_sql("SELECT interval FROM word_schedule WHERE word = ?"), (word,))
        row      = c.fetchone()
        current  = row[0] if row else 1
        new_int  = _next_interval(current, score)
        new_date = (today + timedelta(days=new_int)).isoformat()
        if row:
            c.execute(
                _sql("UPDATE word_schedule SET interval=?, next_review=? WHERE word=?"),
                (new_int, new_date, word),
            )
        else:
            c.execute(
                _sql("INSERT INTO word_schedule (word, interval, next_review) VALUES (?,?,?)"),
                (word, new_int, new_date),
            )
    return jsonify(interval=new_int, next_review=new_date)


@app.route("/api/due/count")
def due_count():
    today = date.today().isoformat()
    with _db() as c:
        c.execute(
            _sql("""SELECT COUNT(*) FROM (
                     SELECT s.word
                     FROM searches s
                     JOIN (SELECT word, MAX(id) AS maxid FROM searches GROUP BY word) latest
                          ON s.id = latest.maxid
                     LEFT JOIN word_schedule ws ON ws.word = s.word
                     WHERE COALESCE(s.all_trans, s.top_trans, '') != ''
                       AND (ws.word IS NULL OR ws.next_review <= ?)
                   ) AS sub"""),
            (today,),
        )
        row = c.fetchone()
    return jsonify({"count": row[0]})


@app.route("/api/game/next-session")
def next_session():
    today = date.today().isoformat()
    with _db() as c:
        c.execute(
            _sql("SELECT MIN(next_review) FROM word_schedule WHERE next_review > ?"),
            (today,),
        )
        row       = c.fetchone()
        next_date = row[0] if row and row[0] else None
        count     = 0
        if next_date:
            c.execute(
                _sql("SELECT COUNT(*) FROM word_schedule WHERE next_review = ?"),
                (next_date,),
            )
            count = c.fetchone()[0]
    return jsonify({"next_date": next_date, "count": count})


@app.route("/api/history")
def history():
    with _db() as c:
        c.execute(
            "SELECT word, all_trans, top_trans, ts FROM searches ORDER BY id DESC LIMIT 100"
        )
        rows = c.fetchall()
    return jsonify([
        {"word": r[0], "translations": r[1] or r[2] or "—", "timestamp": r[3]}
        for r in rows
    ])


# ── Startup ───────────────────────────────────────────────────────────────────

init_db()

# ── Launch (local dev only) ───────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if "PORT" not in os.environ:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"EN->DE Dictionary running at http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
