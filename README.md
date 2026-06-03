# 📷 Fujifilm X10 / X20 / X30 Deal Hunter

Automatically scans 8 marketplaces every hour and sends Discord alerts for new deals under $350.
Runs 100% free on GitHub Actions — nothing runs on your computer.

---

## What It Monitors

| Source | Type | Notes |
|---|---|---|
| **eBay** | Nationwide + local | Sellers must have ≥20 feedback (legitimacy filter) |
| **Reddit r/photomarket** | Nationwide | Skips WTB posts, detects OBO/negotiable |
| **Craigslist LA** | Local pickup | Los Angeles area |
| **Craigslist Long Beach** | Local pickup | Covers south bay / 90278 directly |
| **Craigslist Orange County** | Local pickup | Anaheim, Irvine, Huntington Beach etc. |
| **Craigslist Ventura County** | Local pickup | Oxnard, Ventura, Thousand Oaks |
| **Craigslist Inland Empire** | Local pickup | Ontario, Riverside (edge of 50mi) |
| **Swappa** | Nationwide | Camera-focused marketplace |
| **Mercari** | Nationwide | High-volume C2C |
| **OfferUp** | 50mi / ZIP 90278 | Local + national, filtered by radius |
| **Fred Miranda Buy & Sell** | Nationwide | Trusted photography community forum |
| **Poshmark** | Nationwide | Occasional retro/film camera finds |
| **Facebook Marketplace** | ⚠️ Manual only | Requires login — see note below |

**Facebook Marketplace note:** FB cannot be scraped without a logged-in account.
Search manually: https://www.facebook.com/marketplace/search/?query=fujifilm+x10
Set location to Redondo Beach, CA and radius to 50 miles.

---

## Deal Rating System

Prices calibrated to the current (2026) hyperinflated used camera market.

| Rating | X10 | X20 | X30 |
|---|---|---|---|
| 🔥 Steal | < $85 | < $110 | < $140 |
| ⚡ Great | < $115 | < $145 | < $185 |
| ✅ Good | < $150 | < $185 | < $230 |
| 👍 Fair | < $190 | < $230 | < $285 |
| 💸 Above market | $190–$350 | $230–$350 | $285–$350 |
| ❌ Over budget | > $350 | > $350 | > $350 |

Listings above market but under $350 still get alerted if they're local pickup.
No-price listings always get alerted (often hidden deals).

**Negotiation logic:** When a listing accepts offers, the bot estimates probability of acceptance based on how far the asking price is above fair value.

---

## Setup (15 minutes, free)

### Step 1 — Create the GitHub repo

1. Go to https://github.com/new
2. Name it something like `fuji-deal-hunter`
3. Set visibility to **Public** (recommended — gives you unlimited free Actions minutes)
   - Private repos get 2,000 free minutes/month. At hourly runs, that lasts ~40 days.
4. Click **Create repository**

### Step 2 — Upload the files

Upload all 4 files to the root of your repo:
- `scraper.py`
- `requirements.txt`
- `seen_listings.json`
- `README.md` (optional)

Also upload the `.github/workflows/deal-hunter.yml` file — you'll need to create the `.github/workflows/` folder path when uploading.

**Easiest way:** Use GitHub's web interface → "Add file" → "Upload files", or use GitHub Desktop.

### Step 3 — Create a Discord webhook

1. Open Discord, go to the server/channel where you want alerts
2. Click the channel's gear icon → **Integrations** → **Webhooks** → **New Webhook**
3. Name it "Fuji Deal Hunter", optionally set an avatar
4. Click **Copy Webhook URL** — save this, you'll need it next

### Step 4 — Add the webhook as a GitHub secret

1. In your GitHub repo, go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `DISCORD_WEBHOOK_URL`
4. Value: paste your Discord webhook URL
5. Click **Add secret**

### Step 5 — Enable GitHub Actions

1. Go to the **Actions** tab in your repo
2. If prompted, click **Enable GitHub Actions**
3. You should see "Fujifilm Deal Hunter" in the workflow list

### Step 6 — Test it manually

1. In the Actions tab, click **Fujifilm Deal Hunter**
2. Click **Run workflow** → **Run workflow**
3. Watch the run — it should complete in ~2 minutes
4. Check your Discord channel for alerts

---

## How It Works

```
Every hour (GitHub Actions cron)
    │
    ├── Scrape eBay, Reddit, 5x Craigslist, Swappa,
    │   Mercari, OfferUp, Fred Miranda, Poshmark
    │
    ├── Filter: used Fujifilm X10/X20/X30 only
    │   Skip accessories, WTB posts, >$350 listings
    │   eBay: skip sellers with <20 feedback
    │
    ├── Analyze each new listing:
    │   - Deal tier (steal/great/good/fair/above market)
    │   - Local pickup check (50mi from 90278)
    │   - Negotiation probability if "offers accepted"
    │
    ├── Compare against seen_listings.json (dedup)
    │
    ├── Send Discord alert for each new deal
    │
    └── Commit updated seen_listings.json to repo
```

State is stored in `seen_listings.json` (committed back to the repo after each run). This means you'll never get the same listing twice, even across days.

---

## Adjusting the Schedule

Edit `.github/workflows/deal-hunter.yml`:

```yaml
# Every hour (default, works for public + private repos)
- cron: "0 * * * *"

# Every 30 minutes (recommended for public repos)
- cron: "*/30 * * * *"

# Every 2 hours (most conservative, definitely within free tier)
- cron: "0 */2 * * *"
```

---

## Adjusting Deal Thresholds or Budget

Edit the top of `scraper.py`:

```python
MAX_BUDGET = 350   # Change your max price here

MARKET_PRICES = {
    "x10": {"exceptional": 85,  "great": 115, "good": 150, "fair": 190},
    "x20": {"exceptional": 110, "great": 145, "good": 185, "fair": 230},
    "x30": {"exceptional": 140, "great": 185, "good": 230, "fair": 285},
}
```

---

## Cost

**$0.** GitHub Actions is free for public repos (unlimited minutes). Discord webhooks are free.
Nothing runs on your computer. The repo uses ~10KB of storage for state.
