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

print(f"\n🎬 Nuvio Trailer Proxy v3.4 - COMPLETE METADATA + ACCURATE TRAILER")
print(f"   Language Preference 1: {LANGUAGE_PREF_1}")
print(f"   Language Preference 2: {LANGUAGE_PREF_2}\n")

@app.get("/manifest.json")
async def get_manifest():
    """Manifest for Stremio integration"""
    return {
        "id": "com.nuvio.trailers.complete",
        "version": "3.4.0",
        "name": "Nuvio Accurate Trailers (Complete)",
        "description": "Returns FULL metadata (cast, director, rating, genres, etc) + accurate trailer. Override aiometadata.",
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
        "hindi": ["hindi", "हిंदी"],
        "tamil": ["tamil", "தమిழ்"],
        "kannada": ["kannada", "ಕನ್ನಡ"],
        "malayalam": ["malayalam", "മലയാളം"],
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
    """Get accurate trailer ID following language preferences."""
    
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

async def get_extended_tmdb_data(content_type: str, tmdb_id: int, client: httpx.AsyncClient) -> dict:
    """Get extended TMDB data including cast, crew, rating, etc."""
    
    try:
        if content_type == "movie":
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=credits,ratings"
        else:
            url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=credits,ratings"
        
        resp = await client.get(url, timeout=10)
        return resp.json() if resp.status_code == 200 else {}
    except Exception as e:
        logger.error(f"Error getting extended TMDB: {e}")
        return {}

def build_complete_metadata(item: dict, content_type: str, content_id: str) -> dict:
    """Build complete metadata response from TMDB data"""
    
    meta = {
        "id": content_id,
        "type": content_type,
    }
    
    # Basic fields
    if content_type == "movie":
        if item.get('title'):
            meta["name"] = item['title']
        if item.get('release_date'):
            meta["releaseInfo"] = item['release_date'][:4]
    else:
        if item.get('name'):
            meta["name"] = item['name']
        if item.get('first_air_date'):
            meta["releaseInfo"] = item['first_air_date'][:4]
    
    # Description
    if item.get('overview'):
        meta["description"] = item['overview']
    
    # Images
    if item.get('poster_path'):
        meta["poster"] = f"https://image.tmdb.org/t/p/w500{item['poster_path']}"
    if item.get('backdrop_path'):
        meta["background"] = f"https://image.tmdb.org/t/p/original{item['backdrop_path']}"
    
    # Rating/Vote
    if item.get('vote_average'):
        meta["imdbRating"] = item['vote_average']
    
    # Runtime
    if item.get('runtime'):
        meta["runtime"] = str(item['runtime'])
    
    # Genres
    if item.get('genres'):
        genre_names = [g.get('name', '') for g in item['genres'] if g.get('name')]
        if genre_names:
            meta["genres"] = genre_names
    
    # Cast
    if item.get('credits', {}).get('cast'):
        cast_list = []
        for actor in item['credits']['cast'][:5]:  # Top 5 cast
            if actor.get('name'):
                cast_list.append(actor['name'])
        if cast_list:
            meta["cast"] = cast_list
    
    # Director/Creator
    if content_type == "movie" and item.get('credits', {}).get('crew'):
        directors = [c['name'] for c in item['credits']['crew'] if c.get('job') == 'Director']
        if directors:
            meta["director"] = directors
    
    # IMDb ID (if available)
    if item.get('external_ids', {}).get('imdb_id'):
        meta["imdbId"] = item['external_ids']['imdb_id']
    
    return meta

@app.get("/meta/{content_type}/{content_id}.json")
async def get_complete_trailer_meta(
    content_type: str,
    content_id: str,
    lang1: str = Query(None),
    lang2: str = Query(None)
):
    """
    COMPLETE METADATA + ACCURATE TRAILER
    
    Returns full metadata including:
    - Cast, Director, Writer
    - Rating, IMDb score
    - Genres, Runtime
    - Poster, Background
    - OUR accurate trailer (overrides aiometadata)
    
    ADDON MUST BE FIRST in Nuvio addon list.
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
    
    # Get TMDB data
    try:
        async with httpx.AsyncClient() as client:
            # First, find TMDB ID using external ID
            find_resp = await client.get(
                f"https://api.themoviedb.org/3/find/{content_id}?api_key={TMDB_API_KEY}&external_source=imdb_id",
                timeout=10
            )
            find_data = find_resp.json()
            
            if not find_data:
                return {"meta": {"id": content_id, "type": content_type}}
            
            # Extract TMDB ID and basic data
            tmdb_id = None
            item = None
            
            if content_type == "movie":
                results = find_data.get('movie_results', [])
                if results:
                    item = results[0]
                    tmdb_id = item.get('id')
            else:
                results = find_data.get('tv_results', [])
                if results:
                    item = results[0]
                    tmdb_id = item.get('id')
            
            if not item or not tmdb_id:
                return {"meta": {"id": content_id, "type": content_type}}
            
            # Get extended data (cast, crew, etc)
            extended_data = await get_extended_tmdb_data(content_type, tmdb_id, client)
            
            # Merge data
            if extended_data:
                item['credits'] = extended_data.get('credits', {})
                if extended_data.get('external_ids'):
                    item['external_ids'] = extended_data['external_ids']
                if extended_data.get('genres'):
                    item['genres'] = extended_data['genres']
                if extended_data.get('vote_average'):
                    item['vote_average'] = extended_data['vote_average']
                if extended_data.get('runtime'):
                    item['runtime'] = extended_data['runtime']
    
    except Exception as e:
        logger.error(f"TMDB error: {e}")
        return {"meta": {"id": content_id, "type": content_type}}
    
    # Get movie name for trailer search
    movie_name = ""
    if content_type == "movie":
        movie_name = item.get('title', '')
        year = item.get('release_date', '')[:4]
    else:
        movie_name = item.get('name', '')
        year = item.get('first_air_date', '')[:4]
    
    if not movie_name:
        return {"meta": {"id": content_id, "type": content_type}}
    
    # Get accurate trailer
    video_id = await get_accurate_trailer(movie_name, year, pref_lang1, pref_lang2, content_type)
    
    # Build complete metadata
    meta_data = build_complete_metadata(item, content_type, content_id)
    
    # Add our accurate trailer
    if video_id:
        logger.info(f"✅ Adding accurate trailer: {video_id}")
        meta_data['trailer'] = video_id
        meta_data['trailers'] = [{"source": video_id, "type": "Trailer"}]
    
    meta_response = {"meta": meta_data}
    
    # Cache
    trailer_cache[cache_key] = meta_response
    
    logger.info(f"{'='*50}\n")
    
    return meta_response

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "3.4.0",
        "mode": "COMPLETE METADATA + ACCURATE TRAILER",
        "pref_1": LANGUAGE_PREF_1,
        "pref_2": LANGUAGE_PREF_2,
        "features": [
            "Cast, Director, Writer",
            "IMDb rating",
            "Genres, Runtime",
            "Poster, Background",
            "OUR accurate trailer"
        ],
        "IMPORTANT": "Place this addon FIRST in your Stremio addon list"
    }
