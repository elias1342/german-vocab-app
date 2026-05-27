# EN→DE Dictionary & Vocabulary Trainer

A web app for learning German vocabulary. Look up English words to get German translations with context and examples, then reinforce them with a spaced-repetition quiz game.

**Live app:** https://german-vocab-app-production.up.railway.app

## Features

- **Translation lookup** — powered by GPT-4o. Each word returns up to 5 German translations ordered by frequency, each with a usage label (everyday, formal, technical, etc.) and an example sentence in both English and German, plus etymology.
- **Vocabulary game** — quiz yourself on words from your search history. Answers are scored on a sliding scale: exact match scores 100, close matches score partial credit via edit distance, wrong answers score 0.
- **Spaced repetition (SM-2)** — correct answers double the review interval, partial credit adds one day, wrong answers reset to tomorrow. A badge on the Practice tab shows how many words are due today.
- **Persistent history** — every lookup is saved to a PostgreSQL database (Supabase) and visible in a history table.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, Flask |
| Database | PostgreSQL (Supabase) with SQLite fallback for local dev |
| AI | OpenAI API (GPT-4o) |
| Server | Waitress (WSGI) |
| Hosting | Railway |

## Running Locally

**Requirements:** Python 3.10+

1. Clone the repository:
   ```bash
   git clone https://github.com/elias1342/german-vocab-app.git
   cd german-vocab-app
   ```

2. Install dependencies:
   ```bash
   pip install flask openai
   ```

3. Create a `.env` file in the project root:
   ```
   OPENAI_API_KEY=your_openai_api_key_here
   ```

4. Start the app:
   ```bash
   python app.py
   ```

The app opens automatically at `http://127.0.0.1:5000`. Search history is stored in a local `searches.db` SQLite file — no database setup required.

To use PostgreSQL locally instead of SQLite, add `DATABASE_URL=your_postgres_url` to your `.env` file.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI API key for GPT-4o lookups |
| `DATABASE_URL` | No | PostgreSQL connection string. Falls back to local SQLite if not set. |
| `PORT` | No | Port to listen on. Set automatically by Railway. |

## Deployment

The app is configured for Railway out of the box. Push to the `master` branch to trigger a redeploy.

Required Railway variables: `OPENAI_API_KEY`, `DATABASE_URL` (Supabase Session Pooler URL).
