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

print(f"\n🎬 Nuvio Trailer Proxy v3.3 - GUARANTEED TRAILER OVERRIDE")
print(f"   Language Preference 1: {LANGUAGE_PREF_1}")
print(f"   Language Preference 2: {LANGUAGE_PREF_2}\n")

@app.get("/manifest.json")
async def get_manifest():
    """Manifest for Stremio integration - HIGHEST PRIORITY"""
    return {
        "id": "com.nuvio.trailers.accurate",
        "version": "3.3.0",
        "name": "Nuvio Accurate Trailers",
        "description": "Overwrites incorrect trailers with accurate language preferences. MUST be placed FIRST in addon list.",
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
        "malayalam": ["malayalam", "മలയാളం"],
    }
    return keywords.get(language, [language])

def string_similarity(a: str, b: str) -> float:
    """Calculate string similarity (0.0 to 1.0)"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def is_wrong_trailer(title: str, movie_name: str) -> bool:
    """Check if trailer is definitely wrong"""
    title_lower = title.lower()
    movie_lower = movie_name.lower()
    
    # Hard blocklist
    hard_blocklist = ["chihni", "arjun reddy", "ye raat", "kabali"]
    
    if any(blocked in title_lower for blocked in hard_blocklist):
        return True
    
    # Check if movie name appears in title
    movie_words = [w for w in movie_lower.split() if len(w) > 2]
    has_movie_match = any(word in title_lower for word in movie_words)
    
    if not has_movie_match:
        similarity = string_similarity(title, movie_name)
        if similarity < 0.4:
            return True
    
    return False

def is_valid_language_trailer(title: str, language: str) -> bool:
    """Check if trailer has language keyword"""
    keywords = get_language_keywords(language)
    title_lower = title.lower()
    has_language = any(kw in title_lower for kw in keywords)
    return has_language

async def search_trailer_exact_language(
    movie_name: str,
    year: str,
    language: str,
    content_type: str = "movie"
) -> str:
    """Search for trailer in exact language. Returns video ID or None."""
    
    try:
        search_query = f"{movie_name} {year} official {language} trailer"
        if content_type == "series":
            search_query += " series"
        
        search = VideosSearch(search_query, limit=20)
        results = await search.next()
        
        if not results or not results.get('result'):
            return None
        
        for video in results['result']:
            title = video.get('title', '')
            video_id = video.get('id', '')
            
            # MUST contain "trailer"
            if "trailer" not in title.lower():
                continue
            
            # Check if wrong trailer
            if is_wrong_trailer(title, movie_name):
                continue
            
            # Check if has language keyword
            if not is_valid_language_trailer(title, language):
                continue
            
            # Valid trailer found!
            return video_id
        
        return None
    
    except Exception as e:
        logger.error(f"Error searching {language}: {str(e)}")
        return None

async def search_trailer_generic(
    movie_name: str,
    year: str,
    content_type: str = "movie"
) -> str:
    """Generic trailer search. Returns video ID or None."""
    
    try:
        search_query = f"{movie_name} {year} official trailer"
        if content_type == "series":
            search_query += " series"
        
        search = VideosSearch(search_query, limit=20)
        results = await search.next()
        
        if not results or not results.get('result'):
            return None
        
        for video in results['result']:
            title = video.get('title', '')
            video_id = video.get('id', '')
            
            # MUST contain "trailer"
            if "trailer" not in title.lower():
                continue
            
            # Check if wrong trailer
            if is_wrong_trailer(title, movie_name):
                continue
            
            # Valid trailer found!
            return video_id
        
        return None
    
    except Exception as e:
        logger.error(f"Error generic search: {str(e)}")
        return None

async def get_accurate_trailer(
    movie_name: str,
    year: str,
    lang1: str,
    lang2: str,
    content_type: str = "movie"
) -> str:
    """
    Get accurate trailer ID following language preferences.
    Returns video ID or None.
    
    Logic:
    1. Try language 1
    2. Try language 2
    3. Try generic
    """
    
    logger.info(f"🎯 Finding trailer: {movie_name} ({year})")
    
    # PASS 1: First preference
    logger.info(f"   [PASS 1] Searching {lang1}...")
    video_id = await search_trailer_exact_language(movie_name, year, lang1, content_type)
    if video_id:
        logger.info(f"   ✅ Found {lang1} trailer: {video_id}")
        return video_id
    
    # PASS 2: Second preference
    logger.info(f"   [PASS 2] Searching {lang2}...")
    video_id = await search_trailer_exact_language(movie_name, year, lang2, content_type)
    if video_id:
        logger.info(f"   ✅ Found {lang2} trailer: {video_id}")
        return video_id
    
    # PASS 3: Generic
    logger.info(f"   [PASS 3] Searching original language...")
    video_id = await search_trailer_generic(movie_name, year, content_type)
    if video_id:
        logger.info(f"   ✅ Found original trailer: {video_id}")
        return video_id
    
    logger.info(f"   ⚠️  No trailer found")
    return None

@app.get("/meta/{content_type}/{content_id}.json")
async def get_trailer_meta(
    content_type: str,
    content_id: str,
    lang1: str = Query(None),
    lang2: str = Query(None)
):
    """
    GET ACCURATE TRAILER
    
    This addon MUST BE FIRST in your addon list to override aiometadata.
    
    Strategy:
    1. Get movie name from TMDB
    2. Search for accurate trailer based on language preferences
    3. Return COMPLETE metadata with our trailer
    4. When placed FIRST, Stremio uses our trailer (not aiometadata's)
    
    If you place this addon AFTER aiometadata, it won't work because
    Stremio stops requesting after first addon returns full response.
    
    ADDON ORDER MUST BE:
    1. Nuvio Trailer Proxy (this)
    2. aiometadata (or any other addon)
    """
    
    pref_lang1 = (lang1 or LANGUAGE_PREF_1).lower()
    pref_lang2 = (lang2 or LANGUAGE_PREF_2).lower()
    
    cache_key = f"{content_type}_{content_id}_{pref_lang1}_{pref_lang2}"
    
    # Check cache
    if cache_key in trailer_cache:
        logger.info(f"⚡ Cache HIT: {content_id}")
        return trailer_cache[cache_key]
    
    logger.info(f"\n{'='*50}")
    logger.info(f"ID: {content_id} | Type: {content_type}")
    logger.info(f"Languages: {pref_lang1} > {pref_lang2}")
    logger.info(f"{'='*50}")
    
    # Get movie name and year from TMDB
    movie_name = ""
    year = ""
    overview = ""
    poster_path = ""
    backdrop_path = ""
    
    try:
        async with httpx.AsyncClient() as client:
            tmdb_resp = await client.get(
                f"https://api.themoviedb.org/3/find/{content_id}?api_key={TMDB_API_KEY}&external_source=imdb_id",
                timeout=10
            )
            tmdb_data = tmdb_resp.json()
    except Exception as e:
        logger.error(f"TMDB error: {e}")
        return {"meta": {"id": content_id, "type": content_type}}
    
    # Extract metadata
    if isinstance(tmdb_data, dict):
        if content_type == "movie":
            results = tmdb_data.get('movie_results', [])
            if results:
                item = results[0]
                movie_name = item.get('title', '')
                year = item.get('release_date', '')[:4]
                overview = item.get('overview', '')
                poster_path = item.get('poster_path', '')
                backdrop_path = item.get('backdrop_path', '')
        
        elif content_type == "series":
            results = tmdb_data.get('tv_results', [])
            if results:
                item = results[0]
                movie_name = item.get('name', '')
                year = item.get('first_air_date', '')[:4]
                overview = item.get('overview', '')
                poster_path = item.get('poster_path', '')
                backdrop_path = item.get('backdrop_path', '')
    
    if not movie_name:
        logger.warning(f"Not found in TMDB")
        response = {"meta": {"id": content_id, "type": content_type}}
        trailer_cache[cache_key] = response
        return response
    
    # Get accurate trailer
    video_id = await get_accurate_trailer(
        movie_name,
        year,
        pref_lang1,
        pref_lang2,
        content_type
    )
    
    # Return COMPLETE metadata with our accurate trailer
    # This way, when placed FIRST, our trailer is used
    meta_response = {
        "meta": {
            "id": content_id,
            "type": content_type,
            "name": movie_name,
            "releaseInfo": year,
        }
    }
    
    # Add optional fields
    if overview:
        meta_response["meta"]["description"] = overview
    if poster_path:
        meta_response["meta"]["poster"] = f"https://image.tmdb.org/t/p/w500{poster_path}"
    if backdrop_path:
        meta_response["meta"]["background"] = f"https://image.tmdb.org/t/p/original{backdrop_path}"
    
    # Add trailer (this is the key - our accurate trailer)
    if video_id:
        logger.info(f"✅ Adding accurate trailer: {video_id}")
        meta_response["meta"]["trailer"] = video_id
        meta_response["meta"]["trailers"] = [{"source": video_id, "type": "Trailer"}]
    else:
        logger.info(f"⚠️ No trailer found")
    
    # Cache
    trailer_cache[cache_key] = meta_response
    
    logger.info(f"{'='*50}\n")
    
    return meta_response

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "3.3.0",
        "mode": "FULL OVERRIDE - MUST BE FIRST",
        "pref_1": LANGUAGE_PREF_1,
        "pref_2": LANGUAGE_PREF_2,
        "IMPORTANT": "Place this addon FIRST in your Stremio addon list, BEFORE aiometadata",
        "note": "Returns full metadata. Because it's FIRST, Stremio uses our trailer and stops here."
    }
