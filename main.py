import os
import asyncio
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx
from cachetools import TTLCache
from youtubesearchpython.__future__ import VideosSearch

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

# Default language preferences (can be overridden per request)
DEFAULT_LANGUAGE_PREFS = [
    os.getenv("LANGUAGE_PREF_1", "telugu").lower(),
    os.getenv("LANGUAGE_PREF_2", "english").lower(),
]

# Language-specific search keywords
LANGUAGE_KEYWORDS = {
    "telugu": ["telugu", "తెలుగు"],
    "english": ["english", "official"],
    "hindi": ["hindi", "हिंदी"],
    "tamil": ["tamil", "தமிழ்"],
    "kannada": ["kannada", "ಕನ್ನಡ"],
    "malayan": ["malayan", "malayalam", "മലയാളം"],
}

# Blocklist - common wrong trailers to exclude
BLOCKLIST_KEYWORDS = [
    "chihni",
    "arjun reddy",
    "ye raat",
    "kabali",
    "baahubali",
    "magnum opus",
    "making",
    "behind the scenes",
    "bts",
    "clips",
    "scene",
    "songs",
    "movie review",
    "reaction",
    "short film",
]

def is_accurate_match(title: str, movie_name: str, keywords: list) -> bool:
    """
    Advanced matching to avoid wrong trailers.
    Checks:
    1. Contains language keywords
    2. Contains movie name or major words from it
    3. Is actually a trailer
    4. Not a blocklisted content
    """
    title_lower = title.lower()
    movie_lower = movie_name.lower()
    
    # Check if it's in blocklist
    if any(blocked in title_lower for blocked in BLOCKLIST_KEYWORDS):
        print(f"   ⛔ Blocked (blocklist): {title}")
        return False
    
    # Must contain language keyword
    if not any(keyword in title_lower for keyword in keywords):
        print(f"   ❌ No language keyword match: {title}")
        return False
    
    # Must contain "trailer"
    if "trailer" not in title_lower:
        print(f"   ❌ Not a trailer: {title}")
        return False
    
    # Must contain movie name or key words from it
    # Extract key words (ignore small words)
    movie_words = [w for w in movie_lower.split() if len(w) > 2]
    
    # Check if at least one significant movie word is in the title
    movie_match = any(word in title_lower for word in movie_words)
    
    if not movie_match:
        print(f"   ❌ Movie name mismatch: {title} vs {movie_name}")
        return False
    
    print(f"   ✅ Valid match: {title}")
    return True

async def search_trailer_by_language(movie_name: str, year: str, language: str) -> dict:
    """
    Search for trailer in a specific language with ADVANCED accuracy.
    Returns: {"id": video_id, "title": title, "language": language} or None
    """
    try:
        keywords = LANGUAGE_KEYWORDS.get(language, [language])
        search_query = f"{movie_name} {year} official {language} trailer"
        
        print(f"\n🔍 Searching {language.upper()} trailer: {search_query}")
        
        search = VideosSearch(search_query, limit=5)
        results = await search.next()
        
        if results and results.get('result'):
            # Find the most accurate match from top results
            for idx, video in enumerate(results['result'], 1):
                video_title = video.get('title', '')
                video_id = video.get('id', '')
                
                print(f"   Result {idx}: {video_title}")
                
                # Advanced matching
                if is_accurate_match(video_title, movie_name, keywords):
                    print(f"   ✨ FOUND {language.upper()} trailer!")
                    return {
                        "id": video_id,
                        "title": video_title,
                        "language": language
                    }
            
            print(f"   ⚠️ No accurate {language} match in top results")
    
    except Exception as e:
        print(f"   ❌ Error searching {language} trailer: {str(e)}")
    
    return None

async def get_best_trailer(movie_name: str, year: str, language_prefs: list) -> dict:
    """
    Get the best trailer based on language preferences.
    Falls back through preferences in order, then to original trailer.
    
    Returns: {"id": video_id, "language": language_used} or None
    """
    
    print(f"\n🎬 Fetching trailer for: {movie_name} ({year})")
    print(f"🎯 Language preference order: {language_prefs}")
    
    # Search all languages in parallel for speed
    search_tasks = [
        search_trailer_by_language(movie_name, year, lang) 
        for lang in language_prefs
    ]
    
    results = await asyncio.gather(*search_tasks, return_exceptions=True)
    
    # Return the first successful match (respects preference order)
    for idx, result in enumerate(results):
        if result and isinstance(result, dict):
            print(f"\n✅ SUCCESS: Using {language_prefs[idx]} trailer")
            return result
    
    # Fallback to original language with strict matching
    print(f"\n⚠️ Preferences exhausted, trying original language...")
    original_query = f"{movie_name} {year} official trailer"
    print(f"   Searching: {original_query}")
    
    try:
        search = VideosSearch(original_query, limit=5)
        results = await search.next()
        
        if results and results.get('result'):
            for idx, video in enumerate(results['result'], 1):
                video_title = video.get('title', '')
                video_id = video.get('id', '')
                
                print(f"   Result {idx}: {video_title}")
                
                # Simple validation: must be trailer, contain movie name, not in blocklist
                title_lower = video_title.lower()
                movie_words = [w for w in movie_name.lower().split() if len(w) > 2]
                
                if ("trailer" in title_lower and 
                    any(word in title_lower for word in movie_words) and
                    not any(blocked in title_lower for blocked in BLOCKLIST_KEYWORDS)):
                    
                    print(f"   ✨ Found original trailer!")
                    return {
                        "id": video_id,
                        "language": "original"
                    }
    
    except Exception as e:
        print(f"   ❌ Original search error: {str(e)}")
    
    print(f"\n❌ No valid trailer found for {movie_name}")
    return None

@app.get("/manifest.json")
async def get_manifest():
    return {
        "id": "com.yourname.youtubetrailers.v3",
        "version": "2.1.0",
        "name": "Nuvio Smart Trailers (Multi-Language)",
        "description": "TMDB metadata with AI-powered YouTube trailer matching + Language Preferences + Accuracy Enhanced.",
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
    Fetch metadata with ACCURATE trailer selection.
    
    Query Parameters:
    - lang1: First language preference (default: LANGUAGE_PREF_1 env var or 'telugu')
    - lang2: Second language preference (default: LANGUAGE_PREF_2 env var or 'english')
    
    Example: /meta/movie/tt0111161.json?lang1=telugu&lang2=english
    """
    
    cache_key = f"{content_type}_{content_id}_{lang1}_{lang2}"
    
    # Check cache for instant response
    if cache_key in meta_cache:
        print(f"⚡ Loading {content_id} from instant cache!")
        return meta_cache[cache_key]
    
    # Determine language preferences for this request
    language_prefs = [
        (lang1 or DEFAULT_LANGUAGE_PREFS[0]).lower(),
        (lang2 or DEFAULT_LANGUAGE_PREFS[1]).lower(),
    ]
    
    # Default fallback structural response
    meta_response = {"meta": {"id": content_id, "type": content_type, "name": "Loading..."}}
    
    if not TMDB_API_KEY:
        print("❌ ERROR: TMDB_API_KEY environment variable is missing!")
        return meta_response
    
    async with httpx.AsyncClient() as client:
        try:
            # 1. Fetch metadata from TMDB
            print(f"\n📡 Fetching TMDB data for {content_id}...")
            tmdb_resp = await client.get(
                f"https://api.themoviedb.org/3/find/{content_id}?api_key={TMDB_API_KEY}&external_source=imdb_id"
            )
            tmdb_data = tmdb_resp.json()
            
            if isinstance(tmdb_data, dict) and "status_message" in tmdb_data:
                print(f"❌ TMDB API ERROR: {tmdb_data.get('status_message')}")
                return meta_response
            
            name, year, overview, poster_path, backdrop_path = "", "", "", "", ""
            
            # Extract TMDB data
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
            
            # If no name found, return early
            if not name:
                print(f"❌ No content found for {content_id}")
                return meta_response
            
            print(f"✨ Found: {name} ({year})")
            
            # 2. Get best trailer with ACCURATE language preferences
            trailer_result = await get_best_trailer(name, year, language_prefs)
            best_video_id = trailer_result['id'] if trailer_result else None
            used_language = trailer_result['language'] if trailer_result else "none"
            
            # 3. Construct Stremio Meta Object
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
                print(f"\n✅ FINAL: Found {used_language} trailer for {name}")
                meta_data['trailers'] = [{"source": best_video_id, "type": "Trailer"}]
                meta_data['trailer'] = best_video_id
            else:
                print(f"\n⚠️ No trailer found for {name}")
            
            meta_response = {"meta": meta_data}
        
        except Exception as e:
            print(f"❌ ERROR: {str(e)}")
        
        # Cache the response for instant future access
        meta_cache[cache_key] = meta_response
        return meta_response

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": "2.1.0",
        "default_languages": DEFAULT_LANGUAGE_PREFS,
        "supported_languages": list(LANGUAGE_KEYWORDS.keys()),
        "accuracy_enhanced": True,
        "blocklist_enabled": True
    }
