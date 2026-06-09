import os
import asyncio
import json
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
from cachetools import TTLCache
from youtubesearchpython.__future__ import VideosSearch
from difflib import SequenceMatcher
import logging

# Setup logging
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

trailer_cache = TTLCache(maxsize=10000, ttl=86400)  # 24-hour cache

TMDB_API_KEY = os.getenv("TMDB_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# User language preferences
LANGUAGE_PREF_1 = os.getenv("LANGUAGE_PREF_1", "telugu").lower()
LANGUAGE_PREF_2 = os.getenv("LANGUAGE_PREF_2", "english").lower()

# Gemini model
GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

print(f"\n🎬 Nuvio Trailer Proxy v4.0 - GEMINI AI POWERED")
print(f"   Language Preference 1: {LANGUAGE_PREF_1}")
print(f"   Language Preference 2: {LANGUAGE_PREF_2}")
print(f"   Gemini AI: {'✅ Enabled' if GEMINI_API_KEY else '⚠️  Disabled (no GEMINI_API_KEY)'}\n")


# ─────────────────────────────────────────────
#  GEMINI AI HELPERS
# ─────────────────────────────────────────────

async def gemini_generate(prompt: str, client: httpx.AsyncClient) -> str | None:
    """Call Gemini API and return the text response."""
    if not GEMINI_API_KEY:
        return None
    try:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 512}
        }
        resp = await client.post(
            f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
            json=payload,
            timeout=10
        )
        if resp.status_code != 200:
            logger.warning(f"Gemini API error: {resp.status_code}")
            return None
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.error(f"Gemini call failed: {e}")
        return None


async def gemini_build_search_query(
    movie_name: str,
    year: str,
    language: str,
    original_language: str,
    content_type: str,
    client: httpx.AsyncClient
) -> str:
    """
    Ask Gemini to craft the best YouTube search query for a trailer
    in the requested language, using knowledge of the movie/show.
    Falls back to a hand-crafted query if Gemini is unavailable.
    """
    prompt = f"""You are a YouTube search expert for movie trailers.

Movie/Show details:
- Title: {movie_name}
- Year: {year}
- Type: {content_type}
- Original language: {original_language}
- Requested trailer language: {language}

Generate the SINGLE best YouTube search query to find the official {language} trailer for this title.
Rules:
1. Include the movie name, year, and the word "trailer".
2. Add the language name if it differs from English (e.g. "Telugu trailer", "Hindi trailer").
3. If the movie has a known dubbed/localized title in that language, use it.
4. Keep it concise (under 10 words).
5. Return ONLY the search query string — no explanation, no quotes, no extra text.

Search query:"""

    result = await gemini_generate(prompt, client)
    if result and len(result) > 3:
        logger.info(f"   🤖 Gemini query ({language}): {result}")
        return result.strip('"').strip("'")

    # Fallback
    query = f"{movie_name} {year} official {language} trailer"
    if content_type == "series":
        query += " series"
    return query


async def gemini_pick_best_trailer(
    candidates: list[dict],
    movie_name: str,
    year: str,
    language: str,
    client: httpx.AsyncClient
) -> str | None:
    """
    Given a list of YouTube video candidates (title + id), ask Gemini
    to pick the best matching official trailer. Returns the video_id or None.
    """
    if not candidates or not GEMINI_API_KEY:
        return None

    numbered = "\n".join(
        [f"{i+1}. [{v['id']}] {v['title']}" for i, v in enumerate(candidates)]
    )
    prompt = f"""You are a movie trailer expert.

I searched YouTube for the official {language} trailer of:
  Title: {movie_name} ({year})

Here are the top results:
{numbered}

Pick the ONE result that is the official {language} trailer (dubbed or original) for exactly this movie/show.
Rules:
- It must be a TRAILER (not a review, reaction, song, clip, or behind-the-scenes).
- It must be for THIS specific movie (not a different film with a similar name).
- Prefer the language "{language}" if available.
- If none are correct, reply: NONE

Reply with ONLY the video ID (e.g. dQw4w9WgXcQ) or NONE."""

    result = await gemini_generate(prompt, client)
    if result:
        result = result.strip().strip('"').strip("'")
        if result == "NONE" or len(result) < 5:
            return None
        # Validate it's one of our candidates
        valid_ids = {v["id"] for v in candidates}
        if result in valid_ids:
            logger.info(f"   🤖 Gemini picked: {result}")
            return result
        # Gemini might have returned just the ID inside a sentence
        for vid_id in valid_ids:
            if vid_id in result:
                logger.info(f"   🤖 Gemini picked (extracted): {vid_id}")
                return vid_id
    return None


async def gemini_detect_language(title: str, client: httpx.AsyncClient) -> str | None:
    """Ask Gemini which language a YouTube video title is in."""
    if not GEMINI_API_KEY:
        return None
    prompt = f"""Identify the language of this YouTube trailer title:
"{title}"

Reply with ONE word in lowercase (e.g. telugu, hindi, tamil, kannada, malayalam, english, korean, spanish, french...).
If unsure, reply: unknown"""
    result = await gemini_generate(prompt, client)
    if result:
        return result.strip().lower()
    return None


# ─────────────────────────────────────────────
#  LANGUAGE KEYWORDS (fallback without Gemini)
# ─────────────────────────────────────────────

def get_language_keywords(language: str) -> list:
    keywords = {
        "telugu":    ["telugu", "తెలుగు"],
        "english":   ["english"],
        "hindi":     ["hindi", "हिंदी"],
        "tamil":     ["tamil", "தமிழ்"],
        "kannada":   ["kannada", "ಕನ್ನಡ"],
        "malayalam": ["malayalam", "മലയാളം"],
        "korean":    ["korean", "한국어"],
        "japanese":  ["japanese", "日本語"],
        "french":    ["french", "français"],
        "spanish":   ["spanish", "español"],
    }
    return keywords.get(language, [language])


def string_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def is_wrong_trailer(title: str, movie_name: str) -> bool:
    """Hard-check: is this clearly a wrong movie?"""
    title_lower = title.lower()
    movie_lower = movie_name.lower()

    hard_blocklist = ["chihni", "arjun reddy", "ye raat", "kabali"]
    if any(blocked in title_lower for blocked in hard_blocklist):
        return True

    movie_words = [w for w in movie_lower.split() if len(w) > 2]
    has_movie_match = any(word in title_lower for word in movie_words)
    if not has_movie_match:
        if string_similarity(title, movie_name) < 0.35:
            return True
    return False


def is_valid_language_trailer_keyword(title: str, language: str) -> bool:
    keywords = get_language_keywords(language)
    return any(kw in title.lower() for kw in keywords)


# ─────────────────────────────────────────────
#  TRAILER SEARCH
# ─────────────────────────────────────────────

async def search_trailer_for_language(
    movie_name: str,
    year: str,
    language: str,
    original_language: str,
    content_type: str,
    client: httpx.AsyncClient
) -> str | None:
    """
    Search YouTube for a trailer in the given language.
    Uses Gemini to build the query AND pick the best result.
    Falls back to keyword matching if Gemini is unavailable.
    """
    try:
        # Step 1: Gemini crafts the best search query
        search_query = await gemini_build_search_query(
            movie_name, year, language, original_language, content_type, client
        )
        logger.info(f"   🔍 Query: {search_query}")

        search = VideosSearch(search_query, limit=15)
        results = await search.next()

        if not results or not results.get("result"):
            return None

        # Filter to only trailer videos
        candidates = []
        for video in results["result"]:
            title = video.get("title", "")
            vid_id = video.get("id", "")
            if not vid_id:
                continue
            if "trailer" not in title.lower():
                continue
            if is_wrong_trailer(title, movie_name):
                continue
            candidates.append({"id": vid_id, "title": title})

        if not candidates:
            return None

        # Step 2: Gemini picks the best match from candidates
        if GEMINI_API_KEY:
            picked = await gemini_pick_best_trailer(candidates, movie_name, year, language, client)
            if picked:
                return picked

        # Step 3: Fallback — keyword-based language match
        for c in candidates:
            if is_valid_language_trailer_keyword(c["title"], language):
                return c["id"]

        # Step 4: Return first valid candidate
        return candidates[0]["id"] if candidates else None

    except Exception as e:
        logger.error(f"Search error ({language}): {e}")
        return None


async def search_trailer_generic(
    movie_name: str,
    year: str,
    original_language: str,
    content_type: str,
    client: httpx.AsyncClient
) -> str | None:
    """Final fallback: generic trailer search with Gemini assistance."""
    try:
        search_query = await gemini_build_search_query(
            movie_name, year, "original", original_language, content_type, client
        )
        search = VideosSearch(search_query, limit=10)
        results = await search.next()

        if not results or not results.get("result"):
            return None

        candidates = []
        for video in results["result"]:
            title = video.get("title", "")
            vid_id = video.get("id", "")
            if not vid_id or "trailer" not in title.lower():
                continue
            if is_wrong_trailer(title, movie_name):
                continue
            candidates.append({"id": vid_id, "title": title})

        if not candidates:
            return None

        if GEMINI_API_KEY:
            picked = await gemini_pick_best_trailer(
                candidates, movie_name, year, original_language, client
            )
            if picked:
                return picked

        return candidates[0]["id"]
    except Exception as e:
        logger.error(f"Generic search error: {e}")
        return None


async def get_accurate_trailer(
    movie_name: str,
    year: str,
    lang1: str,
    lang2: str,
    original_language: str,
    content_type: str,
    client: httpx.AsyncClient
) -> str | None:
    """
    4-pass trailer search:
      1. Preferred language 1  (Gemini-powered)
      2. Preferred language 2  (Gemini-powered)
      3. Original language     (Gemini-powered)
      4. Generic fallback
    """
    logger.info(f"🎯 Finding trailer: {movie_name} ({year})")

    for pass_num, lang in enumerate([lang1, lang2, original_language], start=1):
        label = ["lang1", "lang2", "original"][pass_num - 1]
        logger.info(f"   [PASS {pass_num}] Searching {lang} ({label})...")
        video_id = await search_trailer_for_language(
            movie_name, year, lang, original_language, content_type, client
        )
        if video_id:
            logger.info(f"   ✅ Found {lang} trailer: {video_id}")
            return video_id

    logger.info("   [PASS 4] Generic fallback search...")
    video_id = await search_trailer_generic(
        movie_name, year, original_language, content_type, client
    )
    if video_id:
        logger.info(f"   ✅ Found generic trailer: {video_id}")
        return video_id

    logger.warning("   ⚠️  No trailer found")
    return None


# ─────────────────────────────────────────────
#  TMDB HELPERS
# ─────────────────────────────────────────────

async def get_extended_tmdb_data(
    content_type: str, tmdb_id: int, client: httpx.AsyncClient
) -> dict:
    try:
        if content_type == "movie":
            url = (
                f"https://api.themoviedb.org/3/movie/{tmdb_id}"
                f"?api_key={TMDB_API_KEY}&append_to_response=credits,external_ids"
            )
        else:
            url = (
                f"https://api.themoviedb.org/3/tv/{tmdb_id}"
                f"?api_key={TMDB_API_KEY}&append_to_response=credits,external_ids"
            )
        resp = await client.get(url, timeout=10)
        return resp.json() if resp.status_code == 200 else {}
    except Exception as e:
        logger.error(f"TMDB extended error: {e}")
        return {}


def build_complete_metadata(item: dict, content_type: str, content_id: str) -> dict:
    meta = {"id": content_id, "type": content_type}

    if content_type == "movie":
        if item.get("title"):
            meta["name"] = item["title"]
        if item.get("release_date"):
            meta["releaseInfo"] = item["release_date"][:4]
    else:
        if item.get("name"):
            meta["name"] = item["name"]
        if item.get("first_air_date"):
            meta["releaseInfo"] = item["first_air_date"][:4]

    if item.get("overview"):
        meta["description"] = item["overview"]
    if item.get("poster_path"):
        meta["poster"] = f"https://image.tmdb.org/t/p/w500{item['poster_path']}"
    if item.get("backdrop_path"):
        meta["background"] = f"https://image.tmdb.org/t/p/original{item['backdrop_path']}"
    if item.get("vote_average"):
        meta["imdbRating"] = round(item["vote_average"], 1)
    if item.get("runtime"):
        meta["runtime"] = str(item["runtime"])
    if item.get("genres"):
        meta["genres"] = [g["name"] for g in item["genres"] if g.get("name")]

    credits = item.get("credits", {})
    if credits.get("cast"):
        meta["cast"] = [a["name"] for a in credits["cast"][:6] if a.get("name")]
    if content_type == "movie" and credits.get("crew"):
        directors = [c["name"] for c in credits["crew"] if c.get("job") == "Director"]
        if directors:
            meta["director"] = directors

    return meta


# ─────────────────────────────────────────────
#  FASTAPI ROUTES
# ─────────────────────────────────────────────

@app.get("/manifest.json")
async def get_manifest():
    return {
        "id": "com.nuvio.trailers.gemini",
        "version": "4.0.0",
        "name": "Nuvio AI Trailers (Gemini Powered)",
        "description": (
            "Full metadata + AI-accurate trailers using Google Gemini. "
            "Gemini crafts smarter search queries and picks the correct trailer from results."
        ),
        "resources": ["meta"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": []
    }


@app.get("/meta/{content_type}/{content_id}.json")
async def get_complete_trailer_meta(
    content_type: str,
    content_id: str,
    lang1: str = Query(None),
    lang2: str = Query(None),
):
    pref_lang1 = (lang1 or LANGUAGE_PREF_1).lower()
    pref_lang2 = (lang2 or LANGUAGE_PREF_2).lower()
    cache_key = f"{content_type}_{content_id}_{pref_lang1}_{pref_lang2}"

    if cache_key in trailer_cache:
        logger.info(f"⚡ Cache HIT: {content_id}")
        return trailer_cache[cache_key]

    logger.info(f"\n{'='*55}")
    logger.info(f"ID: {content_id} | Type: {content_type}")
    logger.info(f"Languages: {pref_lang1} > {pref_lang2}")
    logger.info(f"{'='*55}")

    try:
        async with httpx.AsyncClient() as client:
            # 1. Resolve IMDb ID → TMDB
            find_resp = await client.get(
                f"https://api.themoviedb.org/3/find/{content_id}"
                f"?api_key={TMDB_API_KEY}&external_source=imdb_id",
                timeout=10
            )
            find_data = find_resp.json()

            item = None
            tmdb_id = None
            if content_type == "movie":
                results = find_data.get("movie_results", [])
            else:
                results = find_data.get("tv_results", [])

            if results:
                item = results[0]
                tmdb_id = item.get("id")

            if not item or not tmdb_id:
                return {"meta": {"id": content_id, "type": content_type}}

            # 2. Get extended data
            extended = await get_extended_tmdb_data(content_type, tmdb_id, client)
            if extended:
                for key in ("credits", "external_ids", "genres", "vote_average",
                            "runtime", "overview", "backdrop_path", "poster_path"):
                    if extended.get(key):
                        item[key] = extended[key]

            # 3. Extract movie name / year / original language
            if content_type == "movie":
                movie_name = item.get("title", "")
                year = item.get("release_date", "")[:4]
            else:
                movie_name = item.get("name", "")
                year = item.get("first_air_date", "")[:4]

            original_language = item.get("original_language", "en")

            if not movie_name:
                return {"meta": {"id": content_id, "type": content_type}}

            # 4. Get accurate trailer (Gemini AI powered)
            video_id = await get_accurate_trailer(
                movie_name, year, pref_lang1, pref_lang2,
                original_language, content_type, client
            )

    except Exception as e:
        logger.error(f"Main handler error: {e}")
        return {"meta": {"id": content_id, "type": content_type}}

    # 5. Build response
    meta_data = build_complete_metadata(item, content_type, content_id)

    if video_id:
        logger.info(f"✅ Trailer set: {video_id}")
        meta_data["trailer"] = video_id
        meta_data["trailers"] = [{"source": video_id, "type": "Trailer"}]

    response = {"meta": meta_data}
    trailer_cache[cache_key] = response

    logger.info(f"{'='*55}\n")
    return response


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "4.0.0",
        "gemini_ai": "enabled" if GEMINI_API_KEY else "disabled (set GEMINI_API_KEY)",
        "gemini_model": GEMINI_MODEL,
        "default_languages": [LANGUAGE_PREF_1, LANGUAGE_PREF_2],
        "supported_languages": [
            "telugu", "english", "hindi", "tamil",
            "kannada", "malayalam", "korean", "japanese", "french", "spanish"
        ],
        "features": [
            "Gemini AI search query generation",
            "Gemini AI trailer picker from candidates",
            "4-pass language fallback",
            "Complete TMDB metadata (cast, director, rating, genres)",
            "24-hour TTL cache per content+language",
        ],
        "note": "Place this addon FIRST in your Stremio/Nuvio addon list"
  }

