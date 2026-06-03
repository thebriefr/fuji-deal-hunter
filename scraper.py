#!/usr/bin/env python3
"""
Fujifilm X10 / X20 / X30 Deal Hunter  v3
=========================================
Active sources (reliable):
  - eBay (sellers ≥20 feedback)
  - Reddit r/photomarket, r/analog, r/fujifilm
  - Craigslist LA, Long Beach, OC, Ventura, Inland Empire
  - Fred Miranda Buy & Sell
  - Mercari (best-effort, JS-rendered)

Inactive/removed: Swappa, OfferUp, Poshmark, KEH
  (wrong audience, broken APIs, or prices above budget)

Budget   : $375 max
Home ZIP : 90278 (Redondo Beach, CA)
Alerts   : Discord webhook
"""

import os, json, re, time, hashlib
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

PDT = timezone(timedelta(hours=-7))

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
SEEN_FILE       = "seen_listings.json"
MAX_BUDGET      = 375
HOME_ZIP        = "90278"
HOME_CITY       = "Redondo Beach, CA"

# Price tiers — all within $350 budget. Steal = under $200 for all models.
MARKET_PRICES = {
    "x10": {"exceptional": 200, "great": 250, "good": 300, "fair": 375},
    "x20": {"exceptional": 200, "great": 250, "good": 300, "fair": 375},
    "x30": {"exceptional": 200, "great": 250, "good": 300, "fair": 375},
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ~50-mile radius keywords from 90278
LOCAL_KEYWORDS = [
    "redondo beach","torrance","manhattan beach","hermosa beach",
    "el segundo","hawthorne","gardena","lawndale","inglewood",
    "los angeles"," la ","culver city","santa monica","venice",
    "long beach","compton","carson","wilmington","san pedro",
    "palos verdes","rolling hills","lomita","rancho palos verdes",
    "lakewood","downey","lynwood","paramount","south gate",
    "burbank","glendale","pasadena","alhambra","monterey park",
    "west covina","pomona","ontario","anaheim","santa ana",
    "irvine","orange","fullerton","yorba linda","costa mesa",
    "newport beach","huntington beach","seal beach",
    "thousand oaks","ventura","oxnard","camarillo","simi valley",
    "chatsworth","canoga park","woodland hills","van nuys",
    "sherman oaks","studio city","north hollywood","hollywood",
    "west hollywood","beverly hills","brentwood","westwood",
    "marina del rey","playa del rey","el monte","san gabriel",
    "arcadia","monrovia","whittier","pico rivera","montebello",
    "cerritos","bellflower","norwalk","socal","so cal",
    "southern california","south bay","90278","90277","90266",
    "90254","90503","90504","90505","90501","90502","90248",
    "90249","90250","90260","90401","90405","90291",
]

ACCESSORY_WORDS = [
    "charger","battery","case","strap","grip","cable","filter",
    "lens cap","screen protector","bag","pouch","card","memory",
    "manual","book","mount","adapter","holster","skin","cover",
]

# Fred Miranda sticky/pinned posts to skip — these are board admin posts, not listings
FM_SKIP_TITLES = [
    "tips for safe transactions",
    "rules for the buy",
    "please read before posting",
    "forum rules",
    "sticky",
    "moderator",
    "welcome to",
    "how to post",
    "guidelines",
    "announcement",
]


# ─────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────

def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE) as f:
                data = json.load(f)
                return set(data if isinstance(data, list) else data.get("ids", []))
        except Exception:
            pass
    return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(seen)[-3000:], f, indent=2)


def url_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:16]


def detect_model(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\bx[-\s]?30\b", t) or "x30" in t:
        return "x30"
    if re.search(r"\bx[-\s]?20\b", t) or "x20" in t:
        return "x20"
    if re.search(r"\bx[-\s]?10\b", t) or "x10" in t:
        return "x10"
    return None


def extract_price(text: str) -> float | None:
    if not text:
        return None
    text = text.replace(",", "")
    matches = re.findall(r"\$\s*(\d+(?:\.\d{1,2})?)", text)
    if not matches:
        matches = re.findall(r"(?:^|\s)(\d{2,3})(?:\.\d{2})?\s*(?:usd|obo|shipped|firm)?", text.lower())
    if matches:
        valid = [float(p) for p in matches if 25 <= float(p) <= 800]
        return min(valid) if valid else None
    return None


def is_local(text: str, location: str = "") -> bool:
    combined = (text + " " + location).lower()
    return any(kw in combined for kw in LOCAL_KEYWORDS)


def is_accessory(title: str) -> bool:
    t = title.lower()
    return any(w in t for w in ACCESSORY_WORDS)


def analyze_deal(
    model: str | None,
    price: float | None,
    has_offers: bool = False,
    location_text: str = "",
) -> dict:
    base = {
        "model":      f"Fujifilm {model.upper()}" if model else "Fujifilm X-series",
        "price":      price,
        "deal_tier":  "unknown",
        "verdict":    "❓ No price listed — check listing",
        "emoji":      "❓",
        "local":      is_local(location_text),
        "offer_note": None,
    }
    if not price or not model:
        return base

    mp = MARKET_PRICES.get(model, {})
    if not mp:
        return base

    if price <= mp["exceptional"]:
        base.update(deal_tier="exceptional", verdict="🔥 STEAL — exceptional deal", emoji="🔥")
    elif price <= mp["great"]:
        base.update(deal_tier="great", verdict="⚡ GREAT DEAL — act fast", emoji="⚡")
    elif price <= mp["good"]:
        base.update(deal_tier="good", verdict="✅ Good deal", emoji="✅")
    elif price <= mp["fair"]:
        base.update(deal_tier="fair", verdict="👍 Fair market price", emoji="👍")
    elif price <= MAX_BUDGET:
        base.update(deal_tier="high", verdict="💸 Above market (still in budget)", emoji="💸")
    else:
        base.update(deal_tier="over", verdict="❌ Over $350 budget", emoji="❌")

    # Negotiation probability
    if has_offers:
        target = mp["good"]
        if price > mp["fair"]:
            pct = round((price - target) / target * 100)
            base["offer_note"] = (
                f"💬 Try offering ${target} — **~75% chance accepted** "
                f"(listed {pct}% above fair value)"
            )
        elif price > mp["good"]:
            base["offer_note"] = (
                f"💬 Offer ${int(price * 0.90)} — ~50% probability accepted"
            )
        else:
            base["offer_note"] = (
                f"💬 Already a good price — could try ${int(price * 0.93)}"
            )
    return base


# ─────────────────────────────────────────────────────────────
# DISCORD
# ─────────────────────────────────────────────────────────────

TIER_COLORS = {
    "exceptional": 0xFF4500,
    "great":       0xFF8C00,
    "good":        0x00C853,
    "fair":        0xFFD600,
    "high":        0x9E9E9E,
    "over":        0x616161,
    "unknown":     0x0099FF,
}


def send_discord(listing: dict):
    deal  = listing.get("deal") or {}
    price = listing.get("price")
    color = TIER_COLORS.get(deal.get("deal_tier", "unknown"), 0x0099FF)
    price_str = f"${price:.0f}" if price else "Price not listed"

    fields = [
        {"name": "💰 Price",   "value": price_str,                             "inline": True},
        {"name": "📷 Camera",  "value": deal.get("model", "Fujifilm X-series"),"inline": True},
        {"name": "🏪 Source",  "value": listing.get("source", "Unknown"),      "inline": True},
        {"name": "🎯 Verdict", "value": deal.get("verdict", "—"),              "inline": False},
    ]

    loc = listing.get("location", "")
    if loc:
        flag = " ← **📍 LOCAL PICKUP!**" if deal.get("local") else " (ships to 90278)"
        fields.append({"name": "📍 Location", "value": f"{loc}{flag}", "inline": False})

    if deal.get("offer_note"):
        fields.append({"name": "🤝 Negotiation", "value": deal["offer_note"], "inline": False})

    model_key = (deal.get("model") or "").split()[-1].lower()
    mp = MARKET_PRICES.get(model_key, {})
    if mp:
        fields.append({
            "name": "📊 Market Guide",
            "value": (
                f"🔥 Steal <${mp['exceptional']}  "
                f"⚡ Great <${mp['great']}  "
                f"✅ Good <${mp['good']}  "
                f"👍 Fair <${mp['fair']}"
            ),
            "inline": False,
        })

    title = (listing.get("title") or "New Camera Listing")[:100]
    embed = {
        "title":  f"{deal.get('emoji','📷')} {title}",
        "url":    listing.get("url", ""),
        "color":  color,
        "fields": fields,
        "footer": {"text": f"Fuji Deal Hunter • {datetime.now(PDT).strftime('%b %d %Y, %I:%M %p')} PDT"},
    }
    if listing.get("image"):
        embed["thumbnail"] = {"url": listing["image"]}
    if listing.get("description"):
        snip = listing["description"][:350]
        embed["description"] = snip + ("…" if len(listing["description"]) > 350 else "")

    payload = {"username": "📷 Fuji Deal Hunter", "embeds": [embed]}

    if not DISCORD_WEBHOOK:
        print(f"  [DRY RUN] {title} — {price_str} ({listing.get('source')})")
        return

    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if r.status_code not in (200, 204):
            print(f"  Discord error {r.status_code}: {r.text[:200]}")
        time.sleep(1.2)
    except Exception as e:
        print(f"  Discord error: {e}")


def send_heartbeat():
    """Once-daily ping so you know the bot is alive."""
    if not DISCORD_WEBHOOK:
        return
    now = datetime.now(PDT)
    # Send once daily at ~8am PDT
    if now.hour != 8:
        return
    payload = {
        "username": "📷 Fuji Deal Hunter",
        "embeds": [{
            "title": "✅ Bot is running",
            "description": (
                f"Scanning 9 sources every 15 min for Fujifilm X10 / X20 / X30 under ${MAX_BUDGET}.\n"
                f"Home base: {HOME_CITY} ({HOME_ZIP})"
            ),
            "color": 0x00C853,
            "footer": {"text": now.strftime("%b %d %Y")},
        }],
    }
    try:
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# SCRAPERS
# ─────────────────────────────────────────────────────────────

# ── 1. eBay ──────────────────────────────────────────────────
# Reliability: 8/10
# Sellers filtered to ≥20 feedback. Largest inventory by far.
# Note: eBay occasionally serves CAPTCHA but rarely for simple searches.

def scrape_ebay() -> list:
    listings = []
    queries = [("fujifilm x10", "x10"), ("fujifilm x20", "x20"), ("fujifilm x30", "x30")]

    for query, hint_model in queries:
        url = (
            f"https://www.ebay.com/sch/i.html?"
            f"_nkw={quote_plus(query)}"
            f"&LH_ItemCondition=3000"   # Used
            f"&_sop=10"                 # Sort: newest first
            f"&_ipg=48"                 # 48 results per page
            f"&_minfdbk=20"             # Seller ≥20 feedback score
            f"&LH_Complete=0"           # Exclude completed/sold
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            soup = BeautifulSoup(resp.text, "html.parser")

            for item in soup.select("li.s-item"):
                try:
                    title_el = item.select_one(".s-item__title")
                    price_el = item.select_one(".s-item__price")
                    link_el  = item.select_one("a.s-item__link")
                    img_el   = item.select_one("img.s-item__image-img")
                    loc_el   = item.select_one(".s-item__location")
                    # Best offer: eBay puts it in the purchase options span
                    offer_el = item.select_one(".s-item__purchase-options-with-icon")

                    if not title_el or not link_el:
                        continue
                    title = title_el.get_text(strip=True)
                    if "Shop on eBay" in title or not title:
                        continue
                    if is_accessory(title):
                        continue

                    model = detect_model(title) or hint_model
                    href  = re.sub(r"\?.*", "", link_el.get("href", ""))
                    price = extract_price(price_el.get_text() if price_el else "")
                    loc   = loc_el.get_text(strip=True).replace("From ", "") if loc_el else ""

                    if price and price > MAX_BUDGET * 1.3:
                        continue

                    has_offers = bool(offer_el) or bool(
                        re.search(r"best offer|make offer", item.get_text(), re.I)
                    )

                    deal = analyze_deal(model, price, has_offers=has_offers,
                                        location_text=title + " " + loc)
                    listings.append({
                        "id":         url_id(href),
                        "title":      title,
                        "url":        href,
                        "price":      price,
                        "source":     "eBay",
                        "location":   loc,
                        "image":      (img_el.get("src") or img_el.get("data-src", "")) if img_el else "",
                        "has_offers": has_offers,
                        "deal":       deal,
                    })
                except Exception:
                    continue
            time.sleep(2.5)
        except Exception as e:
            print(f"  [eBay] {query}: {e}")
    return listings


# ── 2. Reddit ────────────────────────────────────────────────
# Reliability: 9/10
# Reddit JSON API is the most stable source. No auth needed.
# Scanning r/photomarket (primary), r/analog, r/fujifilm.

def scrape_reddit() -> list:
    listings = []
    seen_ids: set = set()

    searches = [
        # (subreddit, search_term)
        ("photomarket", "fujifilm x10"),
        ("photomarket", "fujifilm x20"),
        ("photomarket", "fujifilm x30"),
        ("photomarket", "fuji x10"),
        ("photomarket", "fuji x20"),
        ("photomarket", "fuji x30"),
        ("analog",      "fujifilm x10 fs"),
        ("analog",      "fujifilm x20 fs"),
        ("analog",      "fujifilm x30 fs"),
        ("fujifilm",    "x10 selling"),
        ("fujifilm",    "x20 selling"),
        ("fujifilm",    "x30 selling"),
    ]

    for subreddit, term in searches:
        url = (
            f"https://www.reddit.com/r/{subreddit}/search.json?"
            f"q={quote_plus(term)}&restrict_sr=1&sort=new&limit=25&t=month"
        )
        try:
            r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=15)
            posts = r.json().get("data", {}).get("children", [])

            for post in posts:
                p       = post.get("data", {})
                post_id = p.get("id", "")
                if post_id in seen_ids:
                    continue
                seen_ids.add(post_id)

                title    = p.get("title", "")
                selftext = p.get("selftext", "")
                flair    = p.get("link_flair_text") or ""

                # Skip WTB / want to buy
                if re.search(r"^\[?wtb\]?|\bwant to buy\b", title.lower()[:20]):
                    continue
                # On r/analog and r/fujifilm, require [FS] tag or "selling"
                if subreddit in ("analog", "fujifilm"):
                    if not re.search(r"\[fs\]|\bselling\b|\bfor sale\b", title.lower()):
                        continue

                model = detect_model(title + " " + selftext)
                if not model:
                    continue

                post_url   = f"https://reddit.com{p.get('permalink', '')}"
                price      = extract_price(title) or extract_price(selftext[:500])
                body_lower = (title + selftext).lower()
                has_offers = bool(re.search(r"\bobo\b|\bnegotiable\b|\boffers?\b", body_lower))

                deal = analyze_deal(model, price, has_offers=has_offers,
                                    location_text=title + " " + selftext[:300] + " " + flair)

                listings.append({
                    "id":          f"reddit_{post_id}",
                    "title":       title,
                    "url":         post_url,
                    "price":       price,
                    "source":      f"r/{subreddit}",
                    "location":    flair,
                    "description": selftext[:600] if selftext else None,
                    "has_offers":  has_offers,
                    "deal":        deal,
                })
            time.sleep(1.5)
        except Exception as e:
            print(f"  [Reddit r/{subreddit}] '{term}': {e}")

    return listings


# ── 3. Craigslist ────────────────────────────────────────────
# Reliability: 8/10
# 5 SoCal regions covering ~50mi from 90278.
# CL rarely blocks scrapers. Both old + new HTML layouts handled.

def scrape_craigslist() -> list:
    listings = []
    regions = [
        ("losangeles",   "Los Angeles, CA"),
        ("longbeach",    "Long Beach, CA"),
        ("orangecounty", "Orange County, CA"),
        ("ventura",      "Ventura County, CA"),
        ("inlandempire", "Inland Empire, CA"),
    ]
    for subdomain, region_name in regions:
        for query in ["fujifilm x10", "fujifilm x20", "fujifilm x30"]:
            url = (
                f"https://{subdomain}.craigslist.org/search/pho?"
                f"query={quote_plus(query)}&sort=date"
            )
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
                soup = BeautifulSoup(resp.text, "html.parser")

                # New CL layout
                items = soup.select("li.cl-search-result")
                # Fallback: old CL layout
                if not items:
                    items = soup.select(".result-row")

                for item in items[:20]:
                    try:
                        # New layout
                        title_el = item.select_one(".label") or item.select_one("a.cl-app-anchor .label")
                        link_el  = item.select_one("a.cl-app-anchor") or item.select_one("a.result-title")
                        price_el = item.select_one(".priceinfo") or item.select_one(".result-price")

                        if not link_el:
                            continue
                        title = (title_el or link_el).get_text(strip=True)
                        if is_accessory(title):
                            continue
                        model = detect_model(title)
                        if not model:
                            continue

                        href = link_el.get("href", "")
                        if not href.startswith("http"):
                            href = f"https://{subdomain}.craigslist.org{href}"

                        price = extract_price(price_el.get_text() if price_el else "")
                        if price and price > MAX_BUDGET * 1.3:
                            continue

                        deal = analyze_deal(model, price, location_text=region_name)
                        deal["local"] = True  # All CL = local pickup

                        listings.append({
                            "id":       url_id(href),
                            "title":    title,
                            "url":      href,
                            "price":    price,
                            "source":   f"Craigslist ({region_name})",
                            "location": region_name,
                            "deal":     deal,
                        })
                    except Exception:
                        continue
                time.sleep(2)
            except Exception as e:
                print(f"  [Craigslist] {subdomain}/{query}: {e}")
    return listings


# ── 4. Fred Miranda Buy & Sell ───────────────────────────────
# Reliability: 7/10
# Scrapes the B&S board listing pages directly (3 pages).
# FM is a trusted photography forum — sellers are vetted community members.
# Inventory for X10/X20/X30 is low (maybe 1–2/month) but very legitimate.

def scrape_fred_miranda() -> list:
    listings = []
    seen_ids: set = set()
    pages = [
        "https://www.fredmiranda.com/forum/board/10",
        "https://www.fredmiranda.com/forum/board/10/1",
        "https://www.fredmiranda.com/forum/board/10/2",
    ]
    for page_url in pages:
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            for link_el in soup.select("a[href*='/forum/topic/']"):
                try:
                    href = link_el.get("href", "")
                    if not href.startswith("http"):
                        href = "https://www.fredmiranda.com" + href

                    m = re.search(r"/topic/(\d+)", href)
                    if not m:
                        continue
                    tid = m.group(1)
                    if tid in seen_ids:
                        continue
                    seen_ids.add(tid)

                    title = link_el.get_text(strip=True)
                    if not title or len(title) < 5:
                        continue
                    if is_accessory(title):
                        continue

                    title_lower = title.lower()

                    # Skip sticky/pinned board admin posts
                    if any(s in title_lower for s in FM_SKIP_TITLES):
                        continue

                    # Skip WTB posts
                    if re.search(r"\[wtb\]|\bwtb\b|\bwant to buy\b", title_lower[:25]):
                        continue

                    # Must mention Fuji to be a relevant listing
                    if not re.search(r"\bfuji", title_lower):
                        continue

                    model = detect_model(title)
                    if not model:
                        continue

                    price      = extract_price(title)
                    has_offers = bool(re.search(r"\bobo\b|\boffers?\b|\bnegotiable\b", title.lower()))
                    deal       = analyze_deal(model, price, has_offers=has_offers, location_text=title)

                    listings.append({
                        "id":         f"fm_{tid}",
                        "title":      title,
                        "url":        href,
                        "price":      price,
                        "source":     "Fred Miranda B&S",
                        "has_offers": has_offers,
                        "deal":       deal,
                    })
                except Exception:
                    continue
            time.sleep(2)
        except Exception as e:
            print(f"  [Fred Miranda] {page_url}: {e}")
    return listings


# ── 5. Mercari ───────────────────────────────────────────────
# Reliability: 6/10
# Uses __NEXT_DATA__ JSON embedded in the page.
# Mercari changes their data structure occasionally — silently fails when they do.
# High inventory, lots of private sellers. Worth checking.

def scrape_mercari() -> list:
    listings = []
    for query in ["fujifilm x10", "fujifilm x20", "fujifilm x30"]:
        url = f"https://www.mercari.com/search/?keyword={quote_plus(query)}&status=on_sale"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
            script = soup.find("script", id="__NEXT_DATA__")
            if not script:
                continue

            data = json.loads(script.string or "{}")
            # Try multiple known data paths
            items = (
                data.get("props", {}).get("pageProps", {}).get("searchResult", {}).get("items")
                or data.get("props", {}).get("pageProps", {}).get("initialState", {})
                   .get("items", {}).get("data")
                or []
            )

            for item in items[:20]:
                try:
                    name = item.get("name") or item.get("itemName") or ""
                    if is_accessory(name):
                        continue
                    model = detect_model(name)
                    if not model:
                        continue

                    price   = float(item.get("price") or item.get("sellingPrice") or 0) or None
                    if price and price > MAX_BUDGET * 1.3:
                        continue

                    item_id = item.get("id") or item.get("itemId") or ""
                    href    = f"https://www.mercari.com/us/item/{item_id}/"
                    thumbs  = item.get("thumbnails") or item.get("photos") or []
                    img     = (thumbs[0] if isinstance(thumbs[0], str) else
                               thumbs[0].get("uri", "")) if thumbs else ""

                    deal = analyze_deal(model, price)
                    listings.append({
                        "id":     f"mercari_{item_id}",
                        "title":  name,
                        "url":    href,
                        "price":  price,
                        "source": "Mercari",
                        "image":  img,
                        "deal":   deal,
                    })
                except Exception:
                    continue
            time.sleep(2)
        except Exception as e:
            print(f"  [Mercari] '{query}': {e}")
    return listings


# ── 6. Swappa ────────────────────────────────────────────────
# Reliability: 5/10
# Uses the search URL (model-specific pages don't exist for older cameras).
# Swappa focuses on phones/newer gear so inventory is low, but legit when found.

def scrape_swappa() -> list:
    listings = []
    for query in ["fujifilm x10", "fujifilm x20", "fujifilm x30"]:
        url = f"https://swappa.com/listings/search?q={quote_plus(query)}&sort=listed_on_desc"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            # Try structured data first
            script = soup.find("script", type="application/ld+json")
            items_json = []
            if script:
                try:
                    ld = json.loads(script.string or "[]")
                    if isinstance(ld, list):
                        items_json = ld
                    elif isinstance(ld, dict):
                        items_json = ld.get("itemListElement", [])
                except Exception:
                    pass

            for entry in items_json[:10]:
                try:
                    item = entry.get("item", entry)
                    name  = item.get("name", "")
                    model = detect_model(name)
                    if not model or is_accessory(name):
                        continue
                    price_raw = item.get("offers", {}).get("price") or item.get("price")
                    price = float(price_raw) if price_raw else None
                    href  = item.get("url") or item.get("@id") or ""
                    deal  = analyze_deal(model, price)
                    listings.append({
                        "id":     url_id(href),
                        "title":  name,
                        "url":    href,
                        "price":  price,
                        "source": "Swappa",
                        "deal":   deal,
                    })
                except Exception:
                    continue

            # Fallback: HTML card scrape
            if not listings:
                for card in soup.select(".listing-card, .card, [data-listing-id]")[:10]:
                    try:
                        link_el  = card.select_one("a")
                        title_el = card.select_one("h2, h3, .title, strong")
                        price_el = card.select_one(".price, [class*='price']")
                        if not link_el:
                            continue
                        href  = link_el.get("href", "")
                        if not href.startswith("http"):
                            href = "https://swappa.com" + href
                        title = (title_el or link_el).get_text(strip=True)
                        model = detect_model(title)
                        if not model or is_accessory(title):
                            continue
                        price = extract_price(price_el.get_text() if price_el else "")
                        deal  = analyze_deal(model, price)
                        listings.append({
                            "id":     url_id(href),
                            "title":  title,
                            "url":    href,
                            "price":  price,
                            "source": "Swappa",
                            "deal":   deal,
                        })
                    except Exception:
                        continue
            time.sleep(2)
        except Exception as e:
            print(f"  [Swappa] '{query}': {e}")
    return listings


# ── 7. OfferUp ───────────────────────────────────────────────
# Reliability: 4/10
# JS-rendered site — tries the undocumented API endpoint with ZIP radius.
# Often fails silently; catches deals when it works.

def scrape_offerup() -> list:
    listings = []
    for query in ["fujifilm x10", "fujifilm x20", "fujifilm x30"]:
        # Try undocumented API endpoint
        api_url = (
            f"https://offerup.com/api/items/search/?"
            f"q={quote_plus(query)}&radius=50&postal_code={HOME_ZIP}&limit=20"
        )
        try:
            r = requests.get(api_url, headers={**HEADERS, "Accept": "application/json"}, timeout=15)
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}

            items = (data.get("data", {}).get("listings")
                     or data.get("items")
                     or data.get("results")
                     or [])

            for item in items[:15]:
                try:
                    name = item.get("title") or item.get("name") or ""
                    if is_accessory(name):
                        continue
                    model = detect_model(name)
                    if not model:
                        continue

                    # OfferUp price is in cents sometimes
                    price_raw = item.get("price") or item.get("asking_price") or {}
                    if isinstance(price_raw, dict):
                        price = float(price_raw.get("amount", 0)) / 100
                    elif isinstance(price_raw, (int, float)):
                        price = float(price_raw)
                        if price > 1000:   # likely in cents
                            price /= 100
                    else:
                        price = extract_price(str(price_raw))

                    if price and price > MAX_BUDGET * 1.3:
                        continue

                    item_id  = str(item.get("id") or item.get("listing_id") or "")
                    href     = item.get("url") or f"https://offerup.com/item/detail/{item_id}/"
                    location = item.get("location") or item.get("city") or ""

                    deal = analyze_deal(model, price, location_text=location)
                    listings.append({
                        "id":       f"ou_{item_id}",
                        "title":    name,
                        "url":      href,
                        "price":    price if price else None,
                        "source":   "OfferUp",
                        "location": location,
                        "deal":     deal,
                    })
                except Exception:
                    continue
            time.sleep(2)
        except Exception as e:
            print(f"  [OfferUp] '{query}': {e}")
    return listings


# ── 8. Poshmark ──────────────────────────────────────────────
# Reliability: 4/10
# Heavily JS-rendered. Cameras appear occasionally (retro/film aesthetic crowd).
# Will miss many listings but catches some. Low effort to include.

def scrape_poshmark() -> list:
    listings = []
    for query in ["fujifilm x10", "fujifilm x20", "fujifilm x30"]:
        url = f"https://poshmark.com/search?query={quote_plus(query)}&type=listings&src=dir"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            # Try __NEXT_DATA__
            script = soup.find("script", id="__NEXT_DATA__")
            items = []
            if script:
                try:
                    data  = json.loads(script.string or "{}")
                    items = (data.get("props", {})
                                 .get("pageProps", {})
                                 .get("searchResult", {})
                                 .get("data", {})
                                 .get("search", {})
                                 .get("results", []))
                except Exception:
                    pass

            # HTML fallback
            if not items:
                for card in soup.select(".card--small, [data-et-name='listing']")[:15]:
                    try:
                        link_el  = card.select_one("a")
                        title_el = card.select_one(".title, h4")
                        price_el = card.select_one(".price")
                        if not link_el:
                            continue
                        href  = link_el.get("href", "")
                        if not href.startswith("http"):
                            href = "https://poshmark.com" + href
                        title = (title_el or link_el).get_text(strip=True)
                        model = detect_model(title)
                        if not model or is_accessory(title):
                            continue
                        price = extract_price(price_el.get_text() if price_el else "")
                        if price and price > MAX_BUDGET * 1.3:
                            continue
                        deal = analyze_deal(model, price)
                        listings.append({
                            "id": url_id(href), "title": title, "url": href,
                            "price": price, "source": "Poshmark", "deal": deal,
                        })
                    except Exception:
                        continue

            for item in items[:15]:
                try:
                    title = item.get("title") or item.get("description") or ""
                    model = detect_model(title)
                    if not model or is_accessory(title):
                        continue
                    price_raw = item.get("price") or item.get("listing_price") or 0
                    price = float(re.sub(r"[^\d.]", "", str(price_raw))) if price_raw else None
                    if price and price > MAX_BUDGET * 1.3:
                        continue
                    item_id = item.get("id") or item.get("listing_id") or ""
                    slug    = item.get("slug") or item_id
                    href    = f"https://poshmark.com/listing/{slug}"
                    img     = (item.get("cover_shot") or {}).get("url_small") or ""
                    deal = analyze_deal(model, price)
                    listings.append({
                        "id": f"pm_{item_id}", "title": title, "url": href,
                        "price": price, "source": "Poshmark", "image": img, "deal": deal,
                    })
                except Exception:
                    continue
            time.sleep(2)
        except Exception as e:
            print(f"  [Poshmark] '{query}': {e}")
    return listings


# ── 9. KEH Camera ────────────────────────────────────────────
# Reliability: 8/10
# Professional used camera dealer. Prices are higher than private sellers
# but 100% legitimate with condition grading (LN, EX, BGN etc.).
# Worth knowing when a deal appears — they sell fast.

def scrape_keh() -> list:
    listings = []
    for query in ["fujifilm x10", "fujifilm x20", "fujifilm x30"]:
        url = f"https://www.keh.com/search#q={quote_plus(query)}&sort=newest"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            # KEH embeds product data in script tags
            script = soup.find("script", string=re.compile(r'"products"'))
            if script:
                try:
                    raw = re.search(r'\{.*\}', script.string, re.DOTALL)
                    if raw:
                        data  = json.loads(raw.group())
                        prods = data.get("products") or []
                        for p in prods[:15]:
                            name  = p.get("name") or p.get("title") or ""
                            model = detect_model(name)
                            if not model or is_accessory(name):
                                continue
                            price = float(p.get("price") or p.get("salePrice") or 0) or None
                            if price and price > MAX_BUDGET * 1.3:
                                continue
                            slug  = p.get("url") or p.get("slug") or ""
                            href  = f"https://www.keh.com{slug}" if not slug.startswith("http") else slug
                            grade = p.get("grade") or p.get("condition") or ""
                            title = f"{name} [{grade}]" if grade else name

                            deal = analyze_deal(model, price)
                            listings.append({
                                "id":     url_id(href),
                                "title":  title,
                                "url":    href,
                                "price":  price,
                                "source": "KEH Camera",
                                "deal":   deal,
                            })
                except Exception:
                    pass

            # HTML fallback
            if not listings:
                for card in soup.select(".product-tile, .product-card, [data-analytics-id]")[:15]:
                    try:
                        link_el  = card.select_one("a")
                        title_el = card.select_one(".product-name, h3, h4")
                        price_el = card.select_one(".price, .product-price")
                        if not link_el:
                            continue
                        href  = link_el.get("href", "")
                        if not href.startswith("http"):
                            href = "https://www.keh.com" + href
                        title = (title_el or link_el).get_text(strip=True)
                        model = detect_model(title)
                        if not model or is_accessory(title):
                            continue
                        price = extract_price(price_el.get_text() if price_el else "")
                        if price and price > MAX_BUDGET * 1.3:
                            continue
                        deal = analyze_deal(model, price)
                        listings.append({
                            "id": url_id(href), "title": title, "url": href,
                            "price": price, "source": "KEH Camera", "deal": deal,
                        })
                    except Exception:
                        continue
            time.sleep(2)
        except Exception as e:
            print(f"  [KEH] '{query}': {e}")
    return listings


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def deduplicate(listings: list) -> list:
    seen: set = set()
    out = []
    for l in listings:
        lid = l.get("id", "")
        if lid and lid not in seen:
            seen.add(lid)
            out.append(l)
    return out


def should_alert(listing: dict) -> bool:
    deal = listing.get("deal") or {}
    tier = deal.get("deal_tier", "unknown")
    if tier == "over":
        return False
    if tier in ("exceptional", "great", "good", "fair", "unknown"):
        return True
    if tier == "high" and deal.get("local"):
        return True   # Above market but local — still worth knowing
    return False


def main():
    print(f"\n{'='*60}")
    print(f"  Fujifilm Deal Hunter v2")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Budget: ${MAX_BUDGET} | Home: {HOME_CITY}")
    print(f"{'='*60}\n")

    send_heartbeat()

    seen = load_seen()

    scrapers = [
        ("eBay (≥20 reviews)",    scrape_ebay),
        ("Reddit (3 subreddits)", scrape_reddit),
        ("Craigslist (5 regions)",scrape_craigslist),
        ("Fred Miranda B&S",      scrape_fred_miranda),
        ("Mercari",               scrape_mercari),
    ]
    # NOTE — Facebook Marketplace requires login and cannot be scraped.
    # Search manually: https://www.facebook.com/marketplace/search/?query=fujifilm+x10
    # Set location: Redondo Beach, CA | Radius: 50 miles

    all_listings = []
    for name, fn in scrapers:
        print(f"Scanning {name}...")
        try:
            results = fn()
            print(f"  → {len(results)} listings found")
            all_listings.extend(results)
        except Exception as e:
            print(f"  [ERROR] {name} crashed: {e}")

    all_listings = deduplicate(all_listings)
    print(f"\nTotal unique: {len(all_listings)}")

    alert_count = 0
    for listing in all_listings:
        lid = listing.get("id", "")
        if not lid or lid in seen:
            continue
        seen.add(lid)
        if should_alert(listing):
            send_discord(listing)
            tier = (listing.get("deal") or {}).get("deal_tier", "?")
            print(f"  ✓ [{tier}] {listing.get('title','')[:55]} — "
                  f"${listing.get('price') or '?'} ({listing.get('source')})")
            alert_count += 1

    save_seen(seen)
    print(f"\nDone — {alert_count} alert(s) sent\n")


if __name__ == "__main__":
    main()
