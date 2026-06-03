#!/usr/bin/env python3
"""
Fujifilm X10 / X20 / X30 Deal Hunter
Monitors eBay, Reddit r/photomarket, Craigslist (LA/OC/Ventura), Swappa, and Mercari.
Sends rich Discord webhook alerts for new deals under $350.
Max budget: $350 | Home ZIP: 90278 (Redondo Beach, CA)
"""

import os
import json
import re
import time
import hashlib
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
SEEN_FILE = "seen_listings.json"
MAX_BUDGET = 350
HOME_ZIP = "90278"
HOME_CITY = "Redondo Beach, CA"

# Fair market prices in current (2026) hyperinflated market
# Tiers: exceptional < great < good < fair  (all are still deals vs retail)
MARKET_PRICES = {
    "x10": {"exceptional": 85,  "great": 115, "good": 150, "fair": 190},
    "x20": {"exceptional": 110, "great": 145, "good": 185, "fair": 230},
    "x30": {"exceptional": 140, "great": 185, "good": 230, "fair": 285},
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

# Keywords that suggest local pickup in the ~50-mile radius of 90278
LOCAL_KEYWORDS = [
    "redondo beach", "torrance", "manhattan beach", "hermosa beach",
    "el segundo", "hawthorne", "gardena", "lawndale", "inglewood",
    "los angeles", " la ", "culver city", "santa monica", "venice",
    "long beach", "compton", "carson", "wilmington", "san pedro",
    "palos verdes", "rolling hills", "lomita", "rancho palos verdes",
    "lakewood", "downey", "lynwood", "paramount", "south gate",
    "burbank", "glendale", "pasadena", "alhambra", "monterey park",
    "west covina", "pomona", "ontario", "anaheim", "santa ana",
    "irvine", "orange", "fullerton", "yorba linda", "costa mesa",
    "newport beach", "huntington beach", "seal beach",
    "thousand oaks", "ventura", "oxnard", "camarillo", "simi valley",
    "chatsworth", "canoga park", "woodland hills", "van nuys",
    "sherman oaks", "studio city", "north hollywood", "hollywood",
    "west hollywood", "beverly hills", "brentwood", "westwood",
    "marina del rey", "playa del rey", "el monte", "san gabriel",
    "arcadia", "monrovia", "whittier", "pico rivera", "montebello",
    "cerritos", "bellflower", "norwalk", "socal", "so cal",
    "southern california", "south bay", "90278", "90277", "90266",
    "90254", "90503", "90504", "90505", "90501", "90502", "90248",
    "90249", "90250", "90260", "90401", "90405", "90291",
]

# Words that indicate it's an accessory, not a camera body
ACCESSORY_WORDS = [
    "charger", "battery", "case", "strap", "grip", "cable",
    "filter", "lens cap", "screen protector", "bag", "pouch",
    "card", "memory", "manual", "book", "mount", "adapter",
]


# ──────────────────────────────────────────────
# UTILITIES
# ──────────────────────────────────────────────

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
    # Keep last 3000 to avoid the file growing forever
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(seen)[-3000:], f, indent=2)


def url_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:16]


def detect_model(text: str) -> str | None:
    t = text.lower()
    # x30 must be checked first (longer/more specific)
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
    text = text.replace(",", "").replace(" ", " ")
    matches = re.findall(r"\$\s*(\d+(?:\.\d{1,2})?)", text)
    if not matches:
        # Try plain numbers near "usd" or at start of string
        matches = re.findall(r"(?:^|usd\s*)(\d{2,4}(?:\.\d{1,2})?)", text.lower())
    if matches:
        valid = [float(p) for p in matches if 20 <= float(p) <= 800]
        return min(valid) if valid else None
    return None


def is_local(text: str, location: str = "") -> bool:
    combined = (text + " " + location).lower()
    return any(kw in combined for kw in LOCAL_KEYWORDS)


def is_accessory(title: str) -> bool:
    t = title.lower()
    return any(word in t for word in ACCESSORY_WORDS)


def analyze_deal(
    model: str | None,
    price: float | None,
    has_offers: bool = False,
    location_text: str = "",
) -> dict:
    base = {
        "model": f"Fujifilm {model.upper()}" if model else "Fujifilm X-series",
        "price": price,
        "deal_tier": "unknown",
        "verdict": "❓ No price listed",
        "emoji": "❓",
        "local": is_local(location_text),
        "offer_note": None,
    }

    if not price or not model:
        return base

    mp = MARKET_PRICES.get(model, {})
    if not mp:
        return base

    over_budget = price > MAX_BUDGET

    if price <= mp["exceptional"]:
        base.update(deal_tier="exceptional", verdict="🔥 STEAL — exceptional deal", emoji="🔥")
    elif price <= mp["great"]:
        base.update(deal_tier="great", verdict="⚡ GREAT DEAL — act fast", emoji="⚡")
    elif price <= mp["good"]:
        base.update(deal_tier="good", verdict="✅ Good deal", emoji="✅")
    elif price <= mp["fair"]:
        base.update(deal_tier="fair", verdict="👍 Fair market price", emoji="👍")
    elif not over_budget:
        base.update(deal_tier="high", verdict="💸 Above market (still in budget)", emoji="💸")
    else:
        base.update(deal_tier="over", verdict=f"❌ Over $350 budget", emoji="❌")

    # Negotiation probability logic
    if has_offers:
        target = mp["good"]
        if price > mp["fair"]:
            pct = round((price - target) / target * 100)
            base["offer_note"] = (
                f"💬 Listed ${price:.0f}, target price ~${target} — "
                f"**~75% chance an offer of ${target} is accepted** (listed {pct}% over fair value)"
            )
        elif price > mp["good"]:
            target2 = int(price * 0.90)
            base["offer_note"] = (
                f"💬 Accepts offers — try ${target2} (~50% probability accepted)"
            )
        else:
            base["offer_note"] = (
                f"💬 Already a good price with offers — could try ${int(price * 0.93)}"
            )

    return base


# ──────────────────────────────────────────────
# DISCORD
# ──────────────────────────────────────────────

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
    deal = listing.get("deal") or {}
    price = listing.get("price")
    color = TIER_COLORS.get(deal.get("deal_tier", "unknown"), 0x0099FF)

    price_str = f"${price:.0f}" if price else "Price not listed"

    fields = [
        {"name": "💰 Price", "value": price_str, "inline": True},
        {"name": "📷 Camera", "value": deal.get("model", "Fujifilm X-series"), "inline": True},
        {"name": "🏪 Source", "value": listing.get("source", "Unknown"), "inline": True},
        {"name": "🎯 Verdict", "value": deal.get("verdict", "—"), "inline": False},
    ]

    location = listing.get("location", "")
    if location:
        local_flag = " ← **📍 LOCAL PICKUP possible!**" if deal.get("local") else " (ships to 90278)"
        fields.append({"name": "📍 Location", "value": f"{location}{local_flag}", "inline": False})

    if deal.get("offer_note"):
        fields.append({"name": "🤝 Negotiation", "value": deal["offer_note"], "inline": False})

    # Market context footer
    model_key = (deal.get("model", "") or "").split()[-1].lower()
    mp = MARKET_PRICES.get(model_key, {})
    if mp:
        ctx = (
            f"🔥 Steal <${mp['exceptional']}  "
            f"⚡ Great <${mp['great']}  "
            f"✅ Good <${mp['good']}  "
            f"👍 Fair <${mp['fair']}"
        )
        fields.append({"name": "📊 Market Guide", "value": ctx, "inline": False})

    title = (listing.get("title") or "New Camera Listing")[:100]
    embed = {
        "title": f"{deal.get('emoji', '📷')} {title}",
        "url": listing.get("url", ""),
        "color": color,
        "fields": fields,
        "footer": {"text": f"Fuji Deal Hunter • {datetime.now().strftime('%b %d %Y, %I:%M %p PT')}"},
    }

    if listing.get("image"):
        embed["thumbnail"] = {"url": listing["image"]}
    if listing.get("description"):
        snip = listing["description"][:350]
        if len(listing["description"]) > 350:
            snip += "…"
        embed["description"] = snip

    payload = {"username": "📷 Fuji Deal Hunter", "embeds": [embed]}

    if not DISCORD_WEBHOOK:
        print(f"  [DRY RUN] Would alert: {title} — {price_str} ({listing.get('source')})")
        return

    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if r.status_code not in (200, 204):
            print(f"  Discord error {r.status_code}: {r.text[:200]}")
        time.sleep(1.2)  # Discord rate limit: ~50 req/sec, be safe
    except Exception as e:
        print(f"  Discord send error: {e}")


# ──────────────────────────────────────────────
# SCRAPERS
# ──────────────────────────────────────────────

def scrape_ebay() -> list:
    """eBay — nationwide + local. Sellers must have ≥20 feedback (legitimacy filter)."""
    listings = []
    queries = [
        ("fujifilm x10", "x10"),
        ("fujifilm x20", "x20"),
        ("fujifilm x30", "x30"),
    ]
    for query, hint_model in queries:
        # Used condition (3000), sorted newest, min 20 feedback (_minfdbk=20)
        url = (
            f"https://www.ebay.com/sch/i.html?"
            f"_nkw={quote_plus(query)}"
            f"&LH_ItemCondition=3000"
            f"&_sop=10"
            f"&_ipg=48"
            f"&_minfdbk=20"   # minimum 20 feedback score
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.select("li.s-item")
            for item in items[:30]:
                try:
                    title_el = item.select_one(".s-item__title")
                    price_el = item.select_one(".s-item__price")
                    link_el  = item.select_one("a.s-item__link")
                    img_el   = item.select_one("img.s-item__image-img")
                    loc_el   = item.select_one(".s-item__location")

                    if not title_el or not link_el:
                        continue

                    title = title_el.get_text(strip=True)
                    if "Shop on eBay" in title or not title:
                        continue
                    if is_accessory(title):
                        continue

                    model = detect_model(title) or hint_model
                    href  = link_el.get("href", "").split("?")[0]
                    price = extract_price(price_el.get_text() if price_el else "")
                    loc   = loc_el.get_text(strip=True) if loc_el else ""

                    if price and price > MAX_BUDGET * 1.3:
                        continue

                    # eBay "Best Offer" detection
                    has_offers = bool(item.select_one(".s-item__purchaseOptionsWithIcon"))

                    deal = analyze_deal(model, price, has_offers=has_offers,
                                        location_text=title + " " + loc)

                    listings.append({
                        "id":       url_id(href),
                        "title":    title,
                        "url":      href,
                        "price":    price,
                        "source":   "eBay",
                        "location": loc,
                        "image":    img_el.get("src") or img_el.get("data-src", "") if img_el else "",
                        "has_offers": has_offers,
                        "deal":     deal,
                    })
                except Exception:
                    continue
            time.sleep(2)
        except Exception as e:
            print(f"  [eBay] Error for '{query}': {e}")
    return listings


def scrape_reddit() -> list:
    """Reddit r/photomarket — JSON API, no auth required."""
    listings = []
    terms = ["fujifilm x10", "fujifilm x20", "fujifilm x30",
             "fuji x10", "fuji x20", "fuji x30"]

    seen_ids = set()
    for term in terms:
        url = (
            f"https://www.reddit.com/r/photomarket/search.json?"
            f"q={quote_plus(term)}&restrict_sr=1&sort=new&limit=25&t=month"
        )
        try:
            r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=15)
            posts = r.json().get("data", {}).get("children", [])
            for post in posts:
                p = post.get("data", {})
                post_id = p.get("id", "")
                if post_id in seen_ids:
                    continue
                seen_ids.add(post_id)

                title    = p.get("title", "")
                selftext = p.get("selftext", "")
                flair    = p.get("link_flair_text") or ""

                # Skip WTB (want-to-buy) posts
                if re.search(r"\[wtb\]|\bwtb\b", title.lower()[:15]):
                    continue

                model = detect_model(title + " " + selftext)
                if not model:
                    continue

                post_url  = f"https://reddit.com{p.get('permalink', '')}"
                price     = extract_price(title) or extract_price(selftext)
                body_lower = (title + selftext).lower()
                has_offers = bool(re.search(r"\bobo\b|\boffers?\b|\bnegotiable\b", body_lower))

                deal = analyze_deal(model, price, has_offers=has_offers,
                                    location_text=title + " " + selftext + " " + flair)

                listings.append({
                    "id":          f"reddit_{post_id}",
                    "title":       title,
                    "url":         post_url,
                    "price":       price,
                    "source":      "r/photomarket",
                    "location":    flair,
                    "description": selftext[:600] if selftext else None,
                    "has_offers":  has_offers,
                    "deal":        deal,
                })
            time.sleep(1.5)
        except Exception as e:
            print(f"  [Reddit] Error for '{term}': {e}")
    return listings


def scrape_craigslist() -> list:
    """Craigslist — 5 regions within ~50mi of Redondo Beach."""
    listings = []

    # (subdomain, display name) — all within reasonable range of 90278
    regions = [
        ("losangeles",  "Los Angeles, CA"),
        ("longbeach",   "Long Beach, CA"),
        ("orangecounty","Orange County, CA"),
        ("ventura",     "Ventura County, CA"),
        ("inlandempire","Inland Empire, CA"),
    ]

    for subdomain, region_name in regions:
        for query in ["fujifilm x10", "fujifilm x20", "fujifilm x30"]:
            url = (
                f"https://{subdomain}.craigslist.org/search/pho?"
                f"query={quote_plus(query)}&sort=date&condition=10&condition=20&condition=30"
            )
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
                soup = BeautifulSoup(resp.text, "html.parser")

                # Handle both old and new CL layout
                items = (soup.select("li.cl-search-result") or
                         soup.select(".result-row"))

                for item in items[:15]:
                    try:
                        # New CL
                        title_el = (item.select_one(".label") or
                                    item.select_one(".result-title"))
                        link_el  = (item.select_one("a.cl-app-anchor") or
                                    item.select_one("a.result-title"))
                        price_el = (item.select_one(".priceinfo") or
                                    item.select_one(".result-price"))

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
                        deal["local"] = True  # All CL results are local pickup

                        listings.append({
                            "id":       url_id(href),
                            "title":    title,
                            "url":      href,
                            "price":    price,
                            "source":   f"Craigslist ({region_name})",
                            "location": region_name,
                            "is_local": True,
                            "deal":     deal,
                        })
                    except Exception:
                        continue
                time.sleep(2)
            except Exception as e:
                print(f"  [Craigslist] Error {subdomain}/{query}: {e}")
    return listings


def scrape_swappa() -> list:
    """Swappa — dedicated camera marketplace."""
    listings = []
    searches = [
        ("https://swappa.com/buy/fujifilm-x10", "x10"),
        ("https://swappa.com/buy/fujifilm-x20", "x20"),
        ("https://swappa.com/buy/fujifilm-x30", "x30"),
    ]
    for url, model in searches:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            # Swappa listing cards vary by page version
            items = (soup.select(".listing-card") or
                     soup.select("[data-listing-id]") or
                     soup.select(".panel-body a[href*='/listing/']"))

            for item in items[:12]:
                try:
                    link_el  = item if item.name == "a" else item.select_one("a")
                    if not link_el:
                        continue

                    href = link_el.get("href", "")
                    if not href.startswith("http"):
                        href = "https://swappa.com" + href

                    title_el = item.select_one("h2, h3, .title, strong")
                    price_el = item.select_one(".price, .listing-price, [class*='price']")

                    title = title_el.get_text(strip=True) if title_el else f"Fujifilm {model.upper()}"
                    price = extract_price(price_el.get_text() if price_el else "")

                    if price and price > MAX_BUDGET * 1.3:
                        continue

                    deal = analyze_deal(model, price)

                    listings.append({
                        "id":    url_id(href),
                        "title": title or f"Fujifilm {model.upper()} listing",
                        "url":   href,
                        "price": price,
                        "source": "Swappa",
                        "deal":  deal,
                    })
                except Exception:
                    continue
            time.sleep(2)
        except Exception as e:
            print(f"  [Swappa] Error {url}: {e}")
    return listings


def scrape_mercari() -> list:
    """Mercari — high volume C2C marketplace."""
    listings = []
    queries = ["fujifilm x10", "fujifilm x20", "fujifilm x30"]

    for query in queries:
        url = f"https://www.mercari.com/search/?keyword={quote_plus(query)}&status=on_sale"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            # Mercari embeds data in __NEXT_DATA__ JSON
            script = soup.find("script", id="__NEXT_DATA__")
            if not script:
                continue

            data = json.loads(script.string or "{}")
            items = (data.get("props", {})
                        .get("pageProps", {})
                        .get("searchResult", {})
                        .get("items", []))

            for item in items[:15]:
                try:
                    name     = item.get("name", "")
                    if is_accessory(name):
                        continue

                    model = detect_model(name)
                    if not model:
                        continue

                    price   = item.get("price") or 0
                    item_id = item.get("id", "")
                    href    = f"https://www.mercari.com/us/item/{item_id}/"
                    thumbs  = item.get("thumbnails") or []
                    img     = thumbs[0] if thumbs else ""

                    if price and price > MAX_BUDGET * 1.3:
                        continue

                    deal = analyze_deal(model, float(price) if price else None)

                    listings.append({
                        "id":    f"mercari_{item_id}",
                        "title": name,
                        "url":   href,
                        "price": float(price) if price else None,
                        "source": "Mercari",
                        "image": img,
                        "deal":  deal,
                    })
                except Exception:
                    continue
            time.sleep(2)
        except Exception as e:
            print(f"  [Mercari] Error '{query}': {e}")
    return listings


def scrape_fred_miranda() -> list:
    """Fred Miranda Buy & Sell forum — trusted photography community."""
    listings = []
    # Board 10 = Buy & Sell, sorted by newest post
    base_url = "https://www.fredmiranda.com/forum/board/10"
    search_url = "https://www.fredmiranda.com/forum/search.php"
    terms = ["fujifilm x10", "fujifilm x20", "fujifilm x30", "fuji x10", "fuji x20", "fuji x30"]

    seen_ids: set = set()
    for term in terms:
        try:
            resp = requests.get(
                search_url,
                params={"q": term, "t": "post", "sort": "newest", "board": 10},
                headers=HEADERS,
                timeout=15,
            )
            soup = BeautifulSoup(resp.text, "html.parser")

            rows = soup.select("tr.search-result, .threadlist tr, tr[id^='t']")
            if not rows:
                # Try generic link scan for FS posts
                rows = soup.select("a[href*='/forum/topic/']")

            for row in rows[:20]:
                try:
                    link_el = row if row.name == "a" else row.select_one("a[href*='/forum/topic/']")
                    if not link_el:
                        continue

                    href = link_el.get("href", "")
                    if not href.startswith("http"):
                        href = "https://www.fredmiranda.com" + href

                    topic_id = re.search(r"/topic/(\d+)", href)
                    if not topic_id:
                        continue
                    tid = topic_id.group(1)
                    if tid in seen_ids:
                        continue
                    seen_ids.add(tid)

                    title = link_el.get_text(strip=True)
                    model = detect_model(title + " " + term)
                    if not model:
                        continue
                    if is_accessory(title):
                        continue
                    # Skip WTB
                    if re.search(r"\bwtb\b|\bwant to buy\b", title.lower()[:20]):
                        continue

                    price = extract_price(title)
                    has_offers = "obo" in title.lower() or "offer" in title.lower()

                    deal = analyze_deal(model, price, has_offers=has_offers,
                                        location_text=title)

                    listings.append({
                        "id":    f"fm_{tid}",
                        "title": title,
                        "url":   href,
                        "price": price,
                        "source": "Fred Miranda B&S",
                        "has_offers": has_offers,
                        "deal":  deal,
                    })
                except Exception:
                    continue
            time.sleep(2)
        except Exception as e:
            print(f"  [Fred Miranda] Error '{term}': {e}")
    return listings


def scrape_poshmark() -> list:
    """Poshmark — occasional camera finds, especially film/retro gear."""
    listings = []
    queries = ["fujifilm x10", "fujifilm x20", "fujifilm x30"]

    for query in queries:
        url = f"https://poshmark.com/search?query={quote_plus(query)}&type=listings&src=dir"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            # Poshmark embeds data in a script
            script = soup.find("script", id="__NEXT_DATA__") or \
                     soup.find("script", string=re.compile(r'"listings"'))

            items = []
            if script:
                try:
                    data = json.loads(script.string or "{}")
                    items = (data.get("props", {})
                                 .get("pageProps", {})
                                 .get("searchResult", {})
                                 .get("data", {})
                                 .get("search", {})
                                 .get("results", []))
                except Exception:
                    pass

            # Fallback: HTML card scrape
            if not items:
                cards = soup.select(".card--small, [data-et-name='listing']")
                for card in cards[:15]:
                    try:
                        title_el = card.select_one(".title, .item__details h4")
                        price_el = card.select_one(".price, .listing__ipad-price")
                        link_el  = card.select_one("a")
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
                            "id":    url_id(href),
                            "title": title,
                            "url":   href,
                            "price": price,
                            "source": "Poshmark",
                            "deal":  deal,
                        })
                    except Exception:
                        continue

            # Process structured data
            for item in items[:15]:
                try:
                    title = item.get("title") or item.get("description") or ""
                    model = detect_model(title)
                    if not model or is_accessory(title):
                        continue

                    price_raw = item.get("price") or item.get("listing_price") or {}
                    price = (float(price_raw) if isinstance(price_raw, (int, float))
                             else float(str(price_raw).replace("$", "").strip() or 0) or None)

                    if price and price > MAX_BUDGET * 1.3:
                        continue

                    item_id = item.get("id") or item.get("listing_id") or ""
                    slug    = item.get("slug") or item_id
                    href    = f"https://poshmark.com/listing/{slug}"
                    img     = item.get("cover_shot", {}).get("url_small") or ""

                    deal = analyze_deal(model, price)
                    listings.append({
                        "id":    f"pm_{item_id}",
                        "title": title,
                        "url":   href,
                        "price": price,
                        "source": "Poshmark",
                        "image": img,
                        "deal":  deal,
                    })
                except Exception:
                    continue

            time.sleep(2)
        except Exception as e:
            print(f"  [Poshmark] Error '{query}': {e}")
    return listings


def scrape_offerup() -> list:
    """OfferUp — local + national. Good for SoCal pickups."""
    listings = []
    queries = ["fujifilm x10", "fujifilm x20", "fujifilm x30"]

    for query in queries:
        # OfferUp search with ZIP radius
        url = (
            f"https://offerup.com/search/?"
            f"q={quote_plus(query)}"
            f"&radius=50"
            f"&zip={HOME_ZIP}"
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")

            # OfferUp embeds listing data in script tags
            scripts = soup.find_all("script", type="application/json")
            for sc in scripts:
                try:
                    data = json.loads(sc.string or "{}")
                    # Walk common data shapes
                    items = (data.get("items") or
                             data.get("listings") or
                             data.get("data", {}).get("items") or [])
                    for item in items[:15]:
                        try:
                            name  = item.get("title") or item.get("name") or ""
                            if is_accessory(name):
                                continue
                            model = detect_model(name)
                            if not model:
                                continue

                            price_raw = item.get("price") or item.get("asking_price") or {}
                            price = (float(price_raw) if isinstance(price_raw, (int, float))
                                     else float(price_raw.get("amount", 0)) / 100
                                     if isinstance(price_raw, dict) else None)

                            if price and price > MAX_BUDGET * 1.3:
                                continue

                            item_id = item.get("id") or item.get("listing_id") or ""
                            href    = (item.get("url") or
                                       f"https://offerup.com/item/detail/{item_id}/")
                            location = (item.get("location") or
                                        item.get("city") or "")

                            deal = analyze_deal(model, price, location_text=location)

                            listings.append({
                                "id":       f"offerup_{item_id}",
                                "title":    name,
                                "url":      href,
                                "price":    price,
                                "source":   "OfferUp",
                                "location": location,
                                "deal":     deal,
                            })
                        except Exception:
                            continue
                except Exception:
                    continue
            time.sleep(2)
        except Exception as e:
            print(f"  [OfferUp] Error '{query}': {e}")
    return listings


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

def deduplicate(listings: list) -> list:
    seen_ids = set()
    out = []
    for l in listings:
        lid = l.get("id", "")
        if lid and lid not in seen_ids:
            seen_ids.add(lid)
            out.append(l)
    return out


def should_alert(listing: dict) -> bool:
    """Decide if a listing is worth a Discord notification."""
    deal = listing.get("deal") or {}
    tier = deal.get("deal_tier", "unknown")

    if tier == "over":
        return False  # Over $350 — skip
    if tier in ("exceptional", "great", "good", "fair"):
        return True   # Always alert good deals
    if tier == "high" and deal.get("local"):
        return True   # Above market but local pickup — still worth knowing
    if tier == "unknown":
        return True   # No price listed — could be a hidden gem
    return False


def main():
    print(f"\n{'='*60}")
    print(f"  Fujifilm Deal Hunter — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Budget: ${MAX_BUDGET} | Home: {HOME_CITY}")
    print(f"{'='*60}\n")

    seen = load_seen()
    all_listings = []

    scrapers = [
        ("eBay (≥20 reviews)",      scrape_ebay),
        ("Reddit r/photomarket",     scrape_reddit),
        ("Craigslist (5 regions)",   scrape_craigslist),
        ("Swappa",                   scrape_swappa),
        ("Mercari",                  scrape_mercari),
        ("OfferUp (50mi/90278)",     scrape_offerup),
        ("Fred Miranda B&S",         scrape_fred_miranda),
        ("Poshmark",                 scrape_poshmark),
    ]
    # NOTE: Facebook Marketplace requires login — cannot be scraped automatically.
    # Manually search: https://www.facebook.com/marketplace/search/?query=fujifilm+x10
    # for local SoCal listings. Set location to Redondo Beach, 50mi radius.

    for name, fn in scrapers:
        print(f"Scanning {name}...")
        try:
            results = fn()
            print(f"  → {len(results)} listings found")
            all_listings.extend(results)
        except Exception as e:
            print(f"  [ERROR] {name} scraper crashed: {e}")

    all_listings = deduplicate(all_listings)
    print(f"\nTotal unique listings: {len(all_listings)}")

    alert_count = 0
    for listing in all_listings:
        lid = listing.get("id", "")
        if not lid or lid in seen:
            continue  # Already alerted

        seen.add(lid)

        if should_alert(listing):
            send_discord(listing)
            tier = (listing.get("deal") or {}).get("deal_tier", "?")
            print(f"  ✓ Alerted: [{tier}] {listing.get('title','')[:60]} — "
                  f"${listing.get('price') or '?'} ({listing.get('source')})")
            alert_count += 1

    save_seen(seen)
    print(f"\n✅ Done — {alert_count} new alert(s) sent\n")


if __name__ == "__main__":
    main()
