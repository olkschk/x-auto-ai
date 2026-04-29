# X AUTO

Twitter (X) automation toolkit driven by Claude:

- **Learn the user's voice** — scrape posts, distill them into a style guide.
- **Auto-rewrite incoming content** — monitor X accounts and public Telegram channels; new posts get rewritten as tweets in the learned voice and sent to your Telegram bot for Accept / Cancel approval.
- **Auto-post** — approved tweets go up automatically.
- **AI Reply** — Chrome extension adds a button under every tweet in your feed that pre-fills the reply box with a generated answer.

No official X API: everything browser-driven via the `auth_token` cookie.

## Stack
- [Playwright](https://playwright.dev/python/) for browser automation
- [Anthropic Claude](https://www.anthropic.com/) `claude-haiku-4-5-20251001` (with prompt caching on long system prompts)
- MongoDB for the post corpus
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) for the approval flow
- FastAPI + a Manifest V3 Chrome extension for the AI Reply button

## Setup

1. Clone the repo and create a virtualenv:
   ```bash
   git clone https://github.com/olkschk/x-auto-ai
   cd x-auto-ai
   python -m venv venv && venv\Scripts\activate    # Windows
   # or:    python -m venv venv && source venv/bin/activate   (Linux/macOS)
   pip install -r requirements.txt
   playwright install chromium
   ```
2. Copy `.env.example` → `.env` and fill it in (see [Configuration](#configuration)).
3. Make sure MongoDB is running on `localhost:27017` (or change `MONGO_URI`).

## Configuration

| Variable | Default | What it is |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | required, from console.anthropic.com |
| `LLM_MODEL` | `claude-haiku-4-5-20251001` | any Claude model id |
| `X_AUTH_TOKEN` | — | required, `auth_token` cookie value from `x.com` (DevTools → Application → Cookies) |
| `X_HEADLESS` | `false` | run Playwright headless |
| `MONGO_URI` | `mongodb://localhost:27017` | |
| `MONGO_DB` | `twitter` | |
| `TELEGRAM_BOT_TOKEN` | — | required, create a bot via [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | — | required, your chat id from `https://api.telegram.org/bot<TOKEN>/getUpdates` after sending any message to the bot |
| `MONITOR_INTERVAL_SECONDS` | `120` | poll cadence |
| `TWEET_CHAR_LIMIT` | `280` | hard ceiling for generated posts; bump to `25000` if you have X Premium |
| `AUTOREPLY_HOST` / `AUTOREPLY_PORT` | `127.0.0.1` / `8765` | local FastAPI server for the Chrome extension |
| `LOG_LEVEL` | `INFO` | |

## How the parts fit together

```
                 ┌─────────────────────────────┐
                 │ last_user_posts.py          │   ──►  MongoDB twitter.posts
                 │  scrape an X user           │
                 └─────────────┬───────────────┘
                               │
                 ┌─────────────▼───────────────┐
                 │ create_rules.py             │   ──►  post_rules.md
                 │  Claude analyses style      │       (then clears MongoDB)
                 └─────────────────────────────┘

   Sources                Generation               Approval            Action
   ───────                ──────────               ────────            ──────
  ┌────────┐  poll  ┌──────────────────┐  send  ┌─────────────┐ accept ┌────────────┐
  │ X user ├───────►│ generate_similar │───────►│ Telegram    ├───────►│ autoposting│
  └────────┘        │  uses post_rules │        │ Accept/     │        │  posts to X│
  ┌────────┐  poll  │       .md        │        │ Cancel      │        └────────────┘
  │ TG ch. ├───────►│                  │        │             │
  └────────┘        └──────────────────┘        └─────────────┘

  Chrome extension  ──►  autoreply_server.py  ──►  Claude w/ instructions.md
   (button click)        (local FastAPI)            ──►  pasted into reply box
```

## Scripts

### 1. Scrape the last 100 posts of a user
```bash
python last_user_posts.py <username>
```
Saves the full text of each post into MongoDB (`twitter.posts`).

### 2. Generate post-writing rules from saved posts
```bash
python create_rules.py
```
Reads all posts from MongoDB, asks Claude to extract style/length/voice, writes `post_rules.md`. On success, the `posts` collection is cleared.

### 3. Run multiple monitors in parallel (recommended)
```bash
python run.py --x elonmusk --x sama --tg durov --tg breakingnews
```
Telegram allows only **one** long-polling client per bot token, so launching `monitor.py` and `tg_monitor.py` side-by-side will produce `Conflict: terminated by other getUpdates request`. `run.py` hosts a single bot and runs every monitor as an asyncio task.

- `--x USERNAME` and `--tg CHANNEL` are both repeatable.
- All X monitors share one Playwright browser context.
- All TG monitors share one HTTP client.
- Ctrl+C cleanly stops every loop, the bot, the browser, and the HTTP client.

### 4. Single-source equivalents
For one source at a time:
```bash
python monitor.py <username>            # one X account
python tg_monitor.py <channel>          # one public TG channel (bare, @name, or t.me URL)
```

### 5. Manual autopost (rarely needed standalone)
```bash
python autoposting.py "Tweet text here"
```

### 6. Chrome extension (AI Reply button)
1. Start the local server:
   ```bash
   python autoreply_server.py
   ```
2. Open `chrome://extensions`, enable Developer Mode, click "Load unpacked", select the `chrome_extension/` folder.
3. Open `x.com`. A "🤖 AI Reply" button appears under every tweet. Click it → the reply box gets pre-filled with the generated answer (you decide whether to send).

Edit `instructions.md` to change the reply style — restart the server for the changes to take effect.

## Project structure

```
.
├── core/
│   ├── config.py            # .env loader (strict + lenient modes)
│   ├── db.py                # MongoDB access (auto-cleans legacy indexes)
│   ├── llm.py               # Claude client w/ ephemeral prompt caching
│   ├── logger.py            # logging setup
│   ├── post_generator.py    # shared post-rewriting logic (length-aware, retries)
│   ├── telegram_bot.py      # ApprovalBot + chunked message splitter (>4096 chars)
│   ├── x_session.py         # Playwright + auth_token cookie, scrape helpers
│   ├── x_monitor.py         # X profile poll loop
│   └── tg_monitor.py        # TG channel scrape loop
├── chrome_extension/
│   ├── manifest.json        # MV3
│   ├── content.js           # injects the AI Reply button, fills DraftJS via execCommand
│   ├── styles.css
│   ├── options.html / options.js   # configure local server URL
├── last_user_posts.py       # scrape → MongoDB
├── create_rules.py          # MongoDB → post_rules.md
├── monitor.py               # single X source CLI
├── tg_monitor.py            # single TG source CLI
├── run.py                   # multi-source launcher (one bot)
├── autoposting.py           # publish_tweet(text)
├── autoreply_server.py      # FastAPI used by the Chrome extension
├── instructions.md          # autoreply style guide (you edit this)
├── .env.example
└── requirements.txt
```

## Notes & disclaimers

- **X ToS.** Browser automation against X may violate the platform's terms. Use on accounts you own and at your own risk.
- **One bot per process.** Don't run `monitor.py`, `tg_monitor.py`, and `run.py` simultaneously with the same `TELEGRAM_BOT_TOKEN` — they will fight for long-polling.
- All model output is generated in English; redirect via `instructions.md` and `post_rules.md` if you want a different language.
- `post_rules.md` is generated and gitignored — re-run `create_rules.py` whenever you want to refresh the voice profile.
