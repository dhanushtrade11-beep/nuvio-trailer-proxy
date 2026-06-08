import os
import asyncio
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

# User language preferences
LANGUAGE_PREF_1 = os.getenv("LANGUAGE_PREF_1", "telugu").lower()
LANGUAGE_PREF_2 = os.getenv("LANGUAGE_PREF_2", "english").lower()

print(f"\n🎬 Nuvio Trailer Proxy v3.0 - TRAILER ONLY MODE")
print(f"   Language Preference 1: {LANGUAGE_PREF_1}")
print(f"   Language Preference 2: {LANGUAGE_PREF_2}\n")

@app.get("/manifest.json")
async def get_manifest():
    """Manifest for Stremio integration"""
    return {
        "id": "com.nuvio.trailers.accurate",
        "version": "3.0.0",
        "name": "Nuvio Accurate Trailers",
        "description": "TRAILER-ONLY addon. Swaps trailers only with 100% accurate language preference matching. Works with aiometadata.",
        "resources": ["meta"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": []
    }

def get_language_keywords(language: str) -> list:
    """Get keywords for language detection"""
    keywords = {
        "telugu": ["telugu", "తెలుగు"],
        "english": ["english"],
        "hindi": ["hindi", "हिंदी"],
        "tamil": ["tamil", "தமிழ்"],
        "kannada": ["kannada", "ಕನ್ನಡ"],
        "malayalam": ["malayalam", "മലയാളം"],
    }
    return keywords.get(language, [language])

def string_similarity(a: str, b: str) -> float:
    """Calculate string similarity (0.0 to 1.0)"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def is_wrong_trailer(title: str, movie_name: str) -> bool:
    """
    Detect if trailer is DEFINITELY wrong by checking:
    1. Known wrong movies (hard blocklist)
    2. Zero similarity to actual movie name
    """
    title_lower = title.lower()
    movie_lower = movie_name.lower()
    
    # Hard blocklist - NEVER these trailers
    hard_blocklist = ["chihni", "arjun reddy", "ye raat", "kabali"]
    
    if any(blocked in title_lower for blocked in hard_blocklist):
        logger.info(f"   ⛔ BLOCKED (hard list): {title}")
        return True
    
    # Check if movie name appears in title
    movie_words = [w for w in movie_lower.split() if len(w) > 2]
    title_words = title_lower.split()
    
    # At least one significant word must match
    has_movie_match = any(word in title_lower for word in movie_words)
    
    if not has_movie_match:
        similarity = string_similarity(title, movie_name)
        # Reject if less than 40% similar AND doesn't contain movie words
        if similarity < 0.4:
            logger.info(f"   ⛔ LOW MATCH ({similarity:.0%}): {title}")
            return True
    
    return False

def is_valid_language_trailer(title: str, language: str) -> bool:
    """Check if trailer title contains language keyword"""
    keywords = get_language_keywords(language)
    title_lower = title.lower()
    
    # Must contain the language keyword
    has_language = any(kw in title_lower for kw in keywords)
    
    if not has_language:
        logger.info(f"   ❌ No '{language}' keyword: {title}")
        return False
    
    return True

async def search_trailer_exact_language(
    movie_name: str,
    year: str,
    language: str,
    content_type: str = "movie"
) -> dict:
    """
    Search for trailer in EXACT language preference.
    Only returns trailers with that specific language keyword.
    """
    logger.info(f"\n   🔍 Searching {language.upper()} trailer...")
    
    try:
        # Build search query
        search_query = f"{movie_name} {year} official {language} trailer"
        if content_type == "series":
            search_query += " series"
        
        logger.info(f"      Query: {search_query}")
        
        search = VideosSearch(search_query, limit=20)
        results = await search.next()
        
        if not results or not results.get('result'):
            logger.info(f"   ⚠️  No {language} trailers found")
            return None
        
        logger.info(f"      Found {len(results['result'])} results")
        
        for idx, video in enumerate(results['result'], 1):
            title = video.get('title', '')
            video_id = video.get('id', '')
            
            logger.info(f"      Result {idx}: {title}")
            
            # MUST contain "trailer"
            if "trailer" not in title.lower():
                logger.info(f"         ❌ Not a trailer")
                continue
            
            # Check if wrong trailer
            if is_wrong_trailer(title, movie_name):
                continue
            
            # Check if has language keyword
            if not is_valid_language_trailer(title, language):
                continue
            
            # Valid trailer found!
            similarity = string_similarity(title, movie_name)
            logger.info(f"         ✅ VALID {language.upper()}: similarity {similarity:.0%}")
            return {
                "id": video_id,
                "language": language,
                "title": title
            }
        
        logger.info(f"   ⚠️  No valid {language} trailer found in results")
        return None
    
    except Exception as e:
        logger.error(f"   ❌ Error: {str(e)}")
        return None

async def search_trailer_generic(
    movie_name: str,
    year: str,
    content_type: str = "movie"
) -> dict:
    """
    Final fallback: Search generic trailer (any language).
    Used when both preferences unavailable.
    """
    logger.info(f"\n   🎬 Fallback: Searching generic trailer...")
    
    try:
        search_query = f"{movie_name} {year} official trailer"
        if content_type == "series":
            search_query += " series"
        
        logger.info(f"      Query: {search_query}")
        
        search = VideosSearch(search_query, limit=20)
        results = await search.next()
        
        if not results or not results.get('result'):
            logger.info(f"   ⚠️  No generic trailers found")
            return None
        
        logger.info(f"      Found {len(results['result'])} results")
        
        for idx, video in enumerate(results['result'], 1):
            title = video.get('title', '')
            video_id = video.get('id', '')
            
            logger.info(f"      Result {idx}: {title}")
            
            # MUST contain "trailer"
            if "trailer" not in title.lower():
                logger.info(f"         ❌ Not a trailer")
                continue
            
            # Check if wrong trailer
            if is_wrong_trailer(title, movie_name):
                continue
            
            # Valid trailer found!
            similarity = string_similarity(title, movie_name)
            logger.info(f"         ✅ VALID: similarity {similarity:.0%}")
            return {
                "id": video_id,
                "language": "original",
                "title": title
            }
        
        logger.info(f"   ⚠️  No valid generic trailer found")
        return None
    
    except Exception as e:
        logger.error(f"   ❌ Error: {str(e)}")
        return None

async def find_best_trailer(
    movie_name: str,
    year: str,
    lang1: str,
    lang2: str,
    content_type: str = "movie"
) -> dict:
    """
    Find BEST trailer following language preference order:
    1. Try LANGUAGE PREF 1
    2. If not found → Try LANGUAGE PREF 2
    3. If not found → Try ORIGINAL
    
    INSTANTLY returns first available.
    """
    logger.info(f"\n🎯 Trailer Search for: {movie_name} ({year}) - {content_type}")
    logger.info(f"   Preferences: {lang1} → {lang2} → Original")
    
    # PASS 1: First preference language
    logger.info(f"\n[PASS 1] Language: {lang1}")
    result = await search_trailer_exact_language(movie_name, year, lang1, content_type)
    if result:
        logger.info(f"✅ FOUND {lang1} trailer: {result['title']}")
        return result
    
    # PASS 2: Second preference language
    logger.info(f"\n[PASS 2] Language: {lang2}")
    result = await search_trailer_exact_language(movie_name, year, lang2, content_type)
    if result:
        logger.info(f"✅ FOUND {lang2} trailer: {result['title']}")
        return result
    
    # PASS 3: Original language
    logger.info(f"\n[PASS 3] Language: ORIGINAL")
    result = await search_trailer_generic(movie_name, year, content_type)
    if result:
        logger.info(f"✅ FOUND original trailer: {result['title']}")
        return result
    
    logger.warning(f"❌ NO TRAILER FOUND for {movie_name}")
    return None

@app.get("/meta/{content_type}/{content_id}.json")
async def get_trailer_only_meta(
    content_type: str,
    content_id: str,
    lang1: str = Query(None),
    lang2: str = Query(None)
):
    """
    TRAILER-ONLY ADDON
    
    Returns ONLY trailer in 'trailers' and 'trailer' fields.
    Other fields (name, description, etc) remain EMPTY so aiometadata fills them.
    
    Language preference order:
    1. lang1 parameter (or LANGUAGE_PREF_1 env var)
    2. lang2 parameter (or LANGUAGE_PREF_2 env var)
    3. Original language (any available)
    
    Example:
    /meta/movie/tt0111161.json?lang1=telugu&lang2=english
    """
    
    # Use provided languages or defaults
    pref_lang1 = (lang1 or LANGUAGE_PREF_1).lower()
    pref_lang2 = (lang2 or LANGUAGE_PREF_2).lower()
    
    cache_key = f"{content_type}_{content_id}_{pref_lang1}_{pref_lang2}"
    
    # Check cache
    if cache_key in trailer_cache:
        cached = trailer_cache[cache_key]
        logger.info(f"⚡ Cache HIT for {content_id}")
        return cached
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Request: {content_type}/{content_id}")
    logger.info(f"Languages: {pref_lang1} > {pref_lang2}")
    logger.info(f"{'='*60}")
    
    # Get TMDB data to extract movie/series name and year
    try:
        async with httpx.AsyncClient() as client:
            tmdb_resp = await client.get(
                f"https://api.themoviedb.org/3/find/{content_id}?api_key={TMDB_API_KEY}&external_source=imdb_id"
            )
            tmdb_data = tmdb_resp.json()
    except Exception as e:
        logger.error(f"TMDB API error: {e}")
        return {"meta": {"id": content_id, "type": content_type}}
    
    movie_name = ""
    year = ""
    
    # Extract movie/series name and year
    if isinstance(tmdb_data, dict):
        if content_type == "movie":
            results = tmdb_data.get('movie_results', [])
            if results:
                movie_name = results[0].get('title', '')
                year = results[0].get('release_date', '')[:4]
        elif content_type == "series":
            results = tmdb_data.get('tv_results', [])
            if results:
                movie_name = results[0].get('name', '')
                year = results[0].get('first_air_date', '')[:4]
    
    if not movie_name:
        logger.warning(f"Could not find {content_id} in TMDB")
        return {"meta": {"id": content_id, "type": content_type}}
    
    # Find best trailer
    trailer_result = await find_best_trailer(
        movie_name,
        year,
        pref_lang1,
        pref_lang2,
        content_type
    )
    
    # Build response - ONLY trailer fields, let aiometadata fill rest
    meta_data = {
        "id": content_id,
        "type": content_type,
    }
    
    if trailer_result:
        video_id = trailer_result['id']
        used_lang = trailer_result['language']
        logger.info(f"\n✅ FINAL: {used_lang.upper()} trailer for {movie_name}")
        
        meta_data['trailer'] = video_id
        meta_data['trailers'] = [
            {
                "source": video_id,
                "type": "Trailer"
            }
        ]
    else:
        logger.warning(f"\n⚠️ No trailer available for {movie_name}")
        # Don't set trailer fields - let aiometadata use its own
    
    meta_response = {"meta": meta_data}
    
    # Cache result
    trailer_cache[cache_key] = meta_response
    
    logger.info(f"{'='*60}\n")
    
    return meta_response

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": "3.0.0",
        "mode": "TRAILER-ONLY",
        "pref_1": LANGUAGE_PREF_1,
        "pref_2": LANGUAGE_PREF_2,
        "description": "Returns ONLY trailer data. Metadata comes from aiometadata addon."
    }
