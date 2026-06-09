import os
import re
import asyncio
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
from cachetools import TTLCache
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Cache: 7 days (trailers don't change)
trailer_cache = TTLCache(maxsize=10000, ttl=604800)

TMDB_API_KEY    = os.getenv("TMDB_API_KEY")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")   # NEW: YouTube Data API v3
LANGUAGE_PREF_1 = os.getenv("LANGUAGE_PREF_1", "telugu").lower()
LANGUAGE_PREF_2 = os.getenv("LANGUAGE_PREF_2", "english").lower()

GEMINI_MODEL   = "gemini-2.0-flash"
GEMINI_API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"

print(f"\n🎬 Nuvio Trailer Proxy v5.0 - GEMINI + YOUTUBE API")
print(f"   Lang1={LANGUAGE_PREF_1}  Lang2={LANGUAGE_PREF_2}")
print(f"   Gemini : {'✅' if GEMINI_API_KEY  else '❌ missing'}")
print(f"   YouTube: {'✅' if YOUTUBE_API_KEY else '❌ missing (will use scraper fallback)'}\n")


# ═══════════════════════════════════════════════
#  GEMINI  — ask for exact search queries
# ═══════════════════════════════════════════════

async def gemini_get_search_queries(
    movie_name: str,
    year: str,
    lang1: str,
    lang2: str,
    original_language: str,
    content_type: str,
    client: httpx.AsyncClient,
) -> dict:
    """
    Ask Gemini to produce the BEST YouTube search query for each language tier.
    Returns: { "lang1": "...", "lang2": "...", "original": "...", "generic": "..." }
    Falls back to hand-crafted queries if Gemini is unavailable.
    """
    fallback = _fallback_queries(movie_name, year, lang1, lang2, original_language, content_type)

    if not GEMINI_API_KEY:
        return fallback

    prompt = f"""You are a YouTube trailer search expert.

Movie details:
  Title          : {movie_name}
  Year           : {year}
  Content type   : {content_type}
  Original lang  : {original_language}
  Preference 1   : {lang1}
  Preference 2   : {lang2}

Generate EXACTLY 4 YouTube search queries — one per line, no labels, no numbers, no extra text.
Line 1 → Best query to find the official {lang1} trailer (dubbed or original).
Line 2 → Best query to find the official {lang2} trailer (dubbed or original).
Line 3 → Best query to find the official {original_language} / original trailer.
Line 4 → Generic best query (any language, official trailer).

Rules:
- Use the SHORT movie title (drop subtitle after colon/dash) unless the subtitle is famous.
- Always include the year and the word "trailer".
- Add language name when not English (e.g. "Telugu trailer", "Hindi official trailer").
- If the movie has a well-known dubbed title in that language, use it.
- Keep each query under 10 words.
- Return ONLY the 4 lines."""

    try:
        resp = await client.post(
            f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 200},
            },
            timeout=8,
        )
        if resp.status_code != 200:
            logger.warning(f"Gemini {resp.status_code}")
            return fallback

        raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        lines = [l.strip().strip('"').strip("'") for l in raw.splitlines() if l.strip()]

        if len(lines) >= 4:
            queries = {
                "lang1":    lines[0],
                "lang2":    lines[1],
                "original": lines[2],
                "generic":  lines[3],
            }
            logger.info(f"🤖 Gemini queries: {queries}")
            return queries

    except Exception as e:
        logger.error(f"Gemini error: {e}")

    return fallback


def _clean_title(title: str) -> str:
    short = re.split(r"[:\-–—]", title)[0].strip()
    return short if len(short) >= 3 else title


def _fallback_queries(
    movie_name, year, lang1, lang2, original_language, content_type
) -> dict:
    short = _clean_title(movie_name)
    suffix = " series" if content_type == "series" else ""
    return {
        "lang1":    f"{short} {year} {lang1} official trailer{suffix}",
        "lang2":    f"{short} {year} {lang2} official trailer{suffix}",
        "original": f"{short} {year} official trailer{suffix}",
        "generic":  f"{movie_name} {year} trailer",
    }


# ═══════════════════════════════════════════════
#  YOUTUBE DATA API v3  — instant, no scraping
# ═══════════════════════════════════════════════

async def youtube_search(query: str, client: httpx.AsyncClient) -> str | None:
    """
    Search YouTube Data API v3 for the query.
    Returns the first video ID that looks like a real trailer, or None.
    """
    if not YOUTUBE_API_KEY:
        return await youtube_scraper_fallback(query, client)

    try:
        resp = await client.get(
            YOUTUBE_SEARCH_URL,
            params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": 5,
                "key": YOUTUBE_API_KEY,
                "videoCategoryId": "1",   # Film & Animation
                "relevanceLanguage": "en",
            },
            timeout=6,
        )
        if resp.status_code != 200:
            logger.warning(f"YouTube API {resp.status_code}: {resp.text[:200]}")
            return await youtube_scraper_fallback(query, client)

        items = resp.json().get("items", [])
        for item in items:
            vid_id = item["id"].get("videoId", "")
            title  = item["snippet"].get("title", "")
            if vid_id and "trailer" in title.lower():
                logger.info(f"   ▶ YT API hit: [{vid_id}] {title}")
                return vid_id

        # If no result has "trailer" in title, return first result anyway
        if items:
            vid_id = items[0]["id"].get("videoId", "")
            title  = items[0]["snippet"].get("title", "")
            logger.info(f"   ▶ YT API (no trailer word): [{vid_id}] {title}")
            return vid_id if vid_id else None

    except Exception as e:
        logger.error(f"YouTube API error: {e}")
        return await youtube_scraper_fallback(query, client)

    return None


async def youtube_scraper_fallback(query: str, client: httpx.AsyncClient) -> str | None:
    """
    Fallback: scrape YouTube search page when no API key.
    Extracts first videoId from the initial data JSON.
    """
    try:
        encoded = query.replace(" ", "+")
        resp = await client.get(
            f"https://www.youtube.com/results?search_query={encoded}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; NuvioProxy/5.0)"},
            timeout=8,
            follow_redirects=True,
        )
        # Extract videoIds from ytInitialData
        matches = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', resp.text)
        if matches:
            vid_id = matches[0]
            logger.info(f"   ▶ Scraper hit: {vid_id}")
            return vid_id
    except Exception as e:
        logger.error(f"Scraper error: {e}")
    return None


# ═══════════════════════════════════════════════
#  MAIN TRAILER LOGIC
# ═══════════════════════════════════════════════

async def get_accurate_trailer(
    movie_name: str,
    year: str,
    lang1: str,
    lang2: str,
    original_language: str,
    content_type: str,
    client: httpx.AsyncClient,
) -> str | None:
    logger.info(f"🎯 {movie_name} ({year}) [{original_language}]")

    # Step 1: Gemini generates all 4 queries in ONE API call (fast)
    queries = await gemini_get_search_queries(
        movie_name, year, lang1, lang2, original_language, content_type, client
    )

    # Step 2: Search in priority order — stop as soon as one is found
    order = [
        ("lang1",    lang1,             queries["lang1"]),
        ("lang2",    lang2,             queries["lang2"]),
        ("original", original_language, queries["original"]),
        ("generic",  "any",             queries["generic"]),
    ]

    for tier, lang, query in order:
        logger.info(f"   [{tier.upper()}] {query}")
        vid = await youtube_search(query, client)
        if vid:
            logger.info(f"   ✅ Found ({tier}={lang}): {vid}")
            return vid
        logger.info(f"   ❌ Not found for {tier}")

    logger.warning("   ⚠️  No trailer found at any tier")
    return None


# ═══════════════════════════════════════════════
#  TMDB
# ═══════════════════════════════════════════════

async def get_extended_tmdb_data(
    content_type: str, tmdb_id: int, client: httpx.AsyncClient
) -> dict:
    try:
        base = "movie" if content_type == "movie" else "tv"
        resp = await client.get(
            f"https://api.themoviedb.org/3/{base}/{tmdb_id}"
            f"?api_key={TMDB_API_KEY}&append_to_response=credits,external_ids",
            timeout=8,
        )
        return resp.json() if resp.status_code == 200 else {}
    except Exception as e:
        logger.error(f"TMDB extended: {e}")
        return {}


def build_metadata(item: dict, content_type: str, content_id: str) -> dict:
    meta = {"id": content_id, "type": content_type}

    if content_type == "movie":
        meta["name"] = item.get("title", "")
        raw_date = item.get("release_date", "")
    else:
        meta["name"] = item.get("name", "")
        raw_date = item.get("first_air_date", "")

    if raw_date:
        meta["releaseInfo"] = raw_date[:4]

    for field, key in [
        ("description", "overview"),
        ("imdbRating",  "vote_average"),
        ("runtime",     "runtime"),
    ]:
        val = item.get(key)
        if val:
            meta[field] = round(val, 1) if key == "vote_average" else str(val)

    if item.get("poster_path"):
        meta["poster"] = f"https://image.tmdb.org/t/p/w500{item['poster_path']}"
    if item.get("backdrop_path"):
        meta["background"] = f"https://image.tmdb.org/t/p/original{item['backdrop_path']}"
    if item.get("genres"):
        meta["genres"] = [g["name"] for g in item["genres"] if g.get("name")]

    credits = item.get("credits", {})
    if credits.get("cast"):
        meta["cast"] = [a["name"] for a in credits["cast"][:6] if a.get("name")]
    if content_type == "movie" and credits.get("crew"):
        dirs = [c["name"] for c in credits["crew"] if c.get("job") == "Director"]
        if dirs:
            meta["director"] = dirs

    return meta


# ═══════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════

@app.get("/manifest.json")
async def manifest():
    return {
        "id": "com.nuvio.trailers.v5",
        "version": "5.0.0",
        "name": "Nuvio AI Trailers v5",
        "description": (
            "Gemini AI + YouTube Data API for instant, accurate trailers. "
            "Place FIRST in addon list."
        ),
        "resources": ["meta"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": [],
    }


@app.get("/meta/{content_type}/{content_id}.json")
async def get_meta(
    content_type: str,
    content_id: str,
    lang1: str = Query(None),
    lang2: str = Query(None),
):
    pref1 = (lang1 or LANGUAGE_PREF_1).lower()
    pref2 = (lang2 or LANGUAGE_PREF_2).lower()
    cache_key = f"{content_type}_{content_id}_{pref1}_{pref2}"

    if cache_key in trailer_cache:
        logger.info(f"⚡ Cache: {content_id}")
        return trailer_cache[cache_key]

    logger.info(f"\n{'='*55}")
    logger.info(f"{content_id} | {content_type} | {pref1}→{pref2}")
    logger.info(f"{'='*55}")

    try:
        async with httpx.AsyncClient() as client:
            # 1. IMDb → TMDB
            find = await client.get(
                f"https://api.themoviedb.org/3/find/{content_id}"
                f"?api_key={TMDB_API_KEY}&external_source=imdb_id",
                timeout=8,
            )
            results = find.json().get(
                "movie_results" if content_type == "movie" else "tv_results", []
            )
            if not results:
                return {"meta": {"id": content_id, "type": content_type}}

            item    = results[0]
            tmdb_id = item.get("id")
            if not tmdb_id:
                return {"meta": {"id": content_id, "type": content_type}}

            # 2. Full TMDB data + trailer search — RUN IN PARALLEL ⚡
            tmdb_task    = get_extended_tmdb_data(content_type, tmdb_id, client)
            # We need movie_name/year first, so quick extract from basic item
            movie_name = item.get("title" if content_type == "movie" else "name", "")
            year       = item.get(
                "release_date" if content_type == "movie" else "first_air_date", ""
            )[:4]
            orig_lang  = item.get("original_language", "en")

            trailer_task = get_accurate_trailer(
                movie_name, year, pref1, pref2, orig_lang, content_type, client
            )

            # Run both concurrently
            extended, video_id = await asyncio.gather(tmdb_task, trailer_task)

            # Merge extended data into item
            if extended:
                for key in (
                    "credits", "genres", "vote_average", "runtime",
                    "overview", "backdrop_path", "poster_path",
                    "release_date", "first_air_date", "original_language",
                ):
                    if extended.get(key):
                        item[key] = extended[key]

    except Exception as e:
        logger.error(f"Handler: {e}")
        return {"meta": {"id": content_id, "type": content_type}}

    meta = build_metadata(item, content_type, content_id)

    if video_id:
        meta["trailer"]  = video_id
        meta["trailers"] = [{"source": video_id, "type": "Trailer"}]
        logger.info(f"✅ Done: {video_id}")
    else:
        logger.warning("⚠️  No trailer")

    response = {"meta": meta}
    trailer_cache[cache_key] = response
    logger.info(f"{'='*55}\n")
    return response


@app.get("/health")
async def health():
    return {
        "status":    "healthy",
        "version":   "5.0.0",
        "gemini":    "enabled" if GEMINI_API_KEY  else "disabled — set GEMINI_API_KEY",
        "youtube":   "API v3"  if YOUTUBE_API_KEY else "scraper fallback — set YOUTUBE_API_KEY",
        "languages": [LANGUAGE_PREF_1, LANGUAGE_PREF_2],
        "flow": [
            "1. Gemini generates 4 search queries in ONE call",
            "2. YouTube Data API v3 searches instantly (no scraping)",
            "3. Priority: lang1 → lang2 → original → generic",
            "4. TMDB + trailer search run in PARALLEL",
            "5. 7-day cache per content+language combo",
        ],
    }
