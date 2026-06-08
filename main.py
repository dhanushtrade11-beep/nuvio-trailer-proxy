import os
import asyncio
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
from cachetools import TTLCache
from youtubesearchpython.__future__ import VideosSearch
from difflib import SequenceMatcher

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

meta_cache = TTLCache(maxsize=10000, ttl=86400)

TMDB_API_KEY = os.getenv("TMDB_API_KEY")

# Default language preferences
DEFAULT_LANGUAGE_PREFS = [
    os.getenv("LANGUAGE_PREF_1", "telugu").lower(),
    os.getenv("LANGUAGE_PREF_2", "english").lower(),
]

# Language-specific search keywords
LANGUAGE_KEYWORDS = {
    "telugu": ["telugu", "తెలుగు"],
    "english": ["english", "official"],
    "hindi": ["hindi", "हिंदी"],
    "tamil": ["tamil", "தமిழ்"],
    "kannada": ["kannada", "ಕನ್ನಡ"],
    "malayan": ["malayan", "malayalam", "മലയാളം"],
}

# STRICT blocklist - only obvious wrong trailers
HARD_BLOCKLIST = [
    "chihni",
    "arjun reddy",
    "ye raat",
]

# SOFT blocklist - try to avoid but not absolute
SOFT_BLOCKLIST = [
    "making",
    "behind the scenes",
    "bts",
    "clips",
    "scene",
    "songs",
    "review",
    "reaction",
    "short film",
    "fan made",
]

def extract_significant_words(text: str) -> list:
    """Extract significant words (length > 2) from text"""
    return [w for w in text.lower().split() if len(w) > 2]

def string_similarity(a: str, b: str) -> float:
    """Calculate string similarity ratio using SequenceMatcher"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def is_valid_trailer_title(title: str) -> bool:
    """Check if title looks like a real trailer"""
    title_lower = title.lower()
    
    # Hard blocklist - absolutely reject these
    if any(blocked in title_lower for blocked in HARD_BLOCKLIST):
        return False
    
    # Must contain "trailer"
    if "trailer" not in title_lower:
        return False
    
    return True

def is_soft_blocked(title: str) -> bool:
    """Check if content is in soft blocklist"""
    title_lower = title.lower()
    return any(blocked in title_lower for blocked in SOFT_BLOCKLIST)

async def search_trailer_by_language(
    movie_name: str, 
    year: str, 
    language: str,
    content_type: str = "movie"
) -> dict:
    """
    Multi-pass trailer search with flexible matching.
    
    Pass 1: Language-specific + strict name match
    Pass 2: Language-specific + medium name match  
    Pass 3: Generic + lenient name match
    Pass 4: Final fallback - any official trailer
    """
    try:
        keywords = LANGUAGE_KEYWORDS.get(language, [language])
        
        if content_type == "series":
            search_query = f"{movie_name} {year} {language} trailer series"
        else:
            search_query = f"{movie_name} {year} {language} trailer"
        
        print(f"\n🔍 Searching {language.upper()}: {search_query}")
        
        search = VideosSearch(search_query, limit=15)
        results = await search.next()
        
        if not results or not results.get('result'):
            print(f"   ⚠️ No results found")
            return None
        
        video_list = results['result']
        print(f"   Found {len(video_list)} results, validating...")
        
        # PASS 1: Strict matching - language keyword + strong name match
        print(f"   [PASS 1] Strict: Language + name match (≥60%)")
        for idx, video in enumerate(video_list, 1):
            title = video.get('title', '')
            video_id = video.get('id', '')
            
            if not is_valid_trailer_title(title):
                continue
            
            # Must have language keyword
            has_language = any(kw in title.lower() for kw in keywords)
            if not has_language:
                continue
            
            # Similarity check
            similarity = string_similarity(title, movie_name)
            if similarity >= 0.6:
                if not is_soft_blocked(title):
                    print(f"   ✅ PASS 1 Result {idx}: {title} (sim: {similarity:.0%})")
                    return {"id": video_id, "title": title, "language": language}
        
        # PASS 2: Medium matching - language keyword + medium name match
        print(f"   [PASS 2] Medium: Language + looser match (≥40%)")
        for idx, video in enumerate(video_list, 1):
            title = video.get('title', '')
            video_id = video.get('id', '')
            
            if not is_valid_trailer_title(title):
                continue
            
            # Must have language keyword
            has_language = any(kw in title.lower() for kw in keywords)
            if not has_language:
                continue
            
            # Similarity check
            similarity = string_similarity(title, movie_name)
            if similarity >= 0.4:
                print(f"   ✅ PASS 2 Result {idx}: {title} (sim: {similarity:.0%})")
                return {"id": video_id, "title": title, "language": language}
        
        # PASS 3: Lenient matching - no language keyword requirement, just name match
        print(f"   [PASS 3] Lenient: Name match only (≥50%)")
        for idx, video in enumerate(video_list, 1):
            title = video.get('title', '')
            video_id = video.get('id', '')
            
            if not is_valid_trailer_title(title):
                continue
            
            # Skip soft blocklist
            if is_soft_blocked(title):
                continue
            
            # Similarity check (no language requirement)
            similarity = string_similarity(title, movie_name)
            if similarity >= 0.5:
                print(f"   ✅ PASS 3 Result {idx}: {title} (sim: {similarity:.0%})")
                return {"id": video_id, "title": title, "language": language}
        
        # PASS 4: Final fallback - any official trailer
        print(f"   [PASS 4] Fallback: Any official trailer")
        for idx, video in enumerate(video_list, 1):
            title = video.get('title', '')
            video_id = video.get('id', '')
            
            if not is_valid_trailer_title(title):
                continue
            
            if "official" in title.lower():
                print(f"   ✅ PASS 4 Result {idx}: {title}")
                return {"id": video_id, "title": title, "language": language}
        
        print(f"   ❌ No match found in any pass")
        return None
    
    except Exception as e:
        print(f"   ❌ Error: {str(e)}")
        return None

async def get_best_trailer(
    movie_name: str, 
    year: str, 
    language_prefs: list,
    content_type: str = "movie"
) -> dict:
    """
    Get best trailer with intelligent fallback.
    
    1. Try first language preference
    2. Try second language preference
    3. Try generic search (no language requirement)
    """
    
    print(f"\n🎬 Smart Trailer Search: {movie_name} ({year}) - {content_type}")
    print(f"🎯 Language preferences: {language_prefs}")
    
    # Search all preferences in parallel
    search_tasks = [
        search_trailer_by_language(movie_name, year, lang, content_type) 
        for lang in language_prefs
    ]
    
    results = await asyncio.gather(*search_tasks, return_exceptions=True)
    
    # Return first successful match
    for idx, result in enumerate(results):
        if result and isinstance(result, dict):
            print(f"\n✅ Found {language_prefs[idx]} trailer!")
            return result
    
    # Fallback: Generic search without language requirement
    print(f"\n⚠️ Language preferences exhausted. Trying generic search...")
    
    try:
        generic_query = f"{movie_name} {year} official trailer"
        if content_type == "series":
            generic_query += " series"
        
        print(f"   Searching: {generic_query}")
        
        search = VideosSearch(generic_query, limit=15)
        results = await search.next()
        
        if results and results.get('result'):
            for idx, video in enumerate(results['result'], 1):
                title = video.get('title', '')
                video_id = video.get('id', '')
                
                if not is_valid_trailer_title(title):
                    continue
                
                # Generic matching - just check similarity
                similarity = string_similarity(title, movie_name)
                if similarity >= 0.4 and not is_hard_blocked(title):
                    print(f"   ✅ Found generic result {idx}: {title}")
                    return {"id": video_id, "language": "generic"}
    
    except Exception as e:
        print(f"   ❌ Generic search error: {str(e)}")
    
    print(f"\n❌ No trailer found for {movie_name}")
    return None

def is_hard_blocked(title: str) -> bool:
    """Check hard blocklist"""
    return any(blocked in title.lower() for blocked in HARD_BLOCKLIST)

@app.get("/manifest.json")
async def get_manifest():
    return {
        "id": "com.yourname.youtubetrailers.v3",
        "version": "2.3.0",
        "name": "Nuvio Smart Trailers (Ultra-Accurate)",
        "description": "Multi-pass AI trailer matching with intelligent fallback. Gets trailers that actually exist on YouTube.",
        "resources": ["meta"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"]
    }

@app.get("/meta/{content_type}/{content_id}.json")
async def get_custom_meta(
    content_type: str, 
    content_id: str,
    lang1: str = Query(None),
    lang2: str = Query(None)
):
    """
    Fetch metadata with ULTRA-ACCURATE trailer selection using multi-pass algorithm.
    
    Search Strategy:
    - PASS 1: Language-specific + strict name match
    - PASS 2: Language-specific + medium name match
    - PASS 3: Lenient name match (no language requirement)
    - PASS 4: Fallback to generic trailer search
    
    Query Parameters:
    - lang1: First language preference (default: telugu)
    - lang2: Second language preference (default: english)
    """
    
    cache_key = f"{content_type}_{content_id}_{lang1}_{lang2}"
    
    if cache_key in meta_cache:
        print(f"⚡ Loading from cache!")
        return meta_cache[cache_key]
    
    language_prefs = [
        (lang1 or DEFAULT_LANGUAGE_PREFS[0]).lower(),
        (lang2 or DEFAULT_LANGUAGE_PREFS[1]).lower(),
    ]
    
    meta_response = {"meta": {"id": content_id, "type": content_type, "name": "Loading..."}}
    
    if not TMDB_API_KEY:
        print("❌ TMDB_API_KEY missing!")
        return meta_response
    
    async with httpx.AsyncClient() as client:
        try:
            print(f"\n📡 Fetching TMDB: {content_id}")
            tmdb_resp = await client.get(
                f"https://api.themoviedb.org/3/find/{content_id}?api_key={TMDB_API_KEY}&external_source=imdb_id"
            )
            tmdb_data = tmdb_resp.json()
            
            if isinstance(tmdb_data, dict) and "status_message" in tmdb_data:
                return meta_response
            
            name, year, overview, poster_path, backdrop_path = "", "", "", "", ""
            
            if isinstance(tmdb_data, dict):
                if content_type == "movie":
                    results = tmdb_data.get('movie_results', [])
                    if results:
                        item = results[0]
                        name = item.get('title', '')
                        year = item.get('release_date', '')[:4]
                        overview = item.get('overview', '')
                        poster_path = item.get('poster_path', '')
                        backdrop_path = item.get('backdrop_path', '')
                elif content_type == "series":
                    results = tmdb_data.get('tv_results', [])
                    if results:
                        item = results[0]
                        name = item.get('name', '')
                        year = item.get('first_air_date', '')[:4]
                        overview = item.get('overview', '')
                        poster_path = item.get('poster_path', '')
                        backdrop_path = item.get('backdrop_path', '')
            
            if not name:
                return meta_response
            
            print(f"✨ Found: {name} ({year})")
            
            # Get trailer with multi-pass search
            trailer_result = await get_best_trailer(name, year, language_prefs, content_type)
            best_video_id = trailer_result['id'] if trailer_result else None
            
            meta_data = {
                "id": content_id,
                "type": content_type,
                "name": name,
                "description": overview,
                "releaseInfo": year
            }
            
            if poster_path:
                meta_data["poster"] = f"https://image.tmdb.org/t/p/w500{poster_path}"
            if backdrop_path:
                meta_data["background"] = f"https://image.tmdb.org/t/p/original{backdrop_path}"
            
            if best_video_id:
                print(f"✅ FINAL: Found trailer!")
                meta_data['trailers'] = [{"source": best_video_id, "type": "Trailer"}]
                meta_data['trailer'] = best_video_id
            
            meta_response = {"meta": meta_data}
        
        except Exception as e:
            print(f"❌ ERROR: {str(e)}")
        
        meta_cache[cache_key] = meta_response
        return meta_response

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "2.3.0",
        "features": {
            "multi_pass_search": "4-pass algorithm for maximum accuracy",
            "flexible_matching": "Relaxed similarity thresholds",
            "intelligent_fallback": "Finds trailers that exist",
            "language_preferences": "User-configurable"
        }
    }
