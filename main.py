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

def is_accurate_match(title: str, keywords: list) -> bool:
    """
    Check if title contains any of the keywords.
    More accurate matching to avoid wrong trailers.
    """
    title_lower = title.lower()
    return any(keyword in title_lower for keyword in keywords)

async def search_trailer_by_language(movie_name: str, year: str, language: str) -> dict:
    """
    Search for trailer in a specific language with improved accuracy.
    Returns: {"id": video_id, "title": title, "language": language} or None
    """
    try:
        keywords = LANGUAGE_KEYWORDS.get(language, [language])
        search_query = f"{movie_name} {year} official {language} trailer"
        
        print(f"🔍 Searching {language} trailer: {search_query}")
        
        search = VideosSearch(search_query, limit=3)
        results = await search.next()
        
        if results and results.get('result'):
            # Find the most accurate match
            for video in results['result']:
                video_title = video.get('title', '').lower()
                video_id = video.get('id', '')
                
                # Check if it's a legit trailer in the requested language
                if is_accurate_match(video_title, keywords) and 'trailer' in video_title:
                    print(f"✅ Found {language} trailer: {video_title}")
                    return {
                        "id": video_id,
                        "title": video['title'],
                        "language": language
                    }
            
            # Fallback: if no exact match, return first result if it's a trailer
            if results['result'][0].get('title', '').lower().endswith('trailer'):
                print(f"⚠️ Using fallback {language} result: {results['result'][0]['title']}")
                return {
                    "id": results['result'][0]['id'],
                    "title": results['result'][0]['title'],
                    "language": language
                }
    except Exception as e:
        print(f"❌ Error searching {language} trailer: {str(e)}")
    
    return None

async def get_best_trailer(movie_name: str, year: str, language_prefs: list) -> dict:
    """
    Get the best trailer based on language preferences.
    Falls back through preferences in order, then to original trailer.
    
    Returns: {"id": video_id, "language": language_used} or None
    """
    
    # Search all languages in parallel for speed
    search_tasks = [
        search_trailer_by_language(movie_name, year, lang) 
        for lang in language_prefs
    ]
    
    results = await asyncio.gather(*search_tasks, return_exceptions=True)
    
    # Return the first successful match (respects preference order)
    for result in results:
        if result and isinstance(result, dict):
            return result
    
    # Fallback to original language
    print(f"🎬 No preference match, searching original trailer...")
    original = await search_trailer_by_language(movie_name, year, "original")
    if original:
        return original
    
    # Last resort - generic search
    try:
        search = VideosSearch(f"{movie_name} {year} trailer", limit=1)
        results = await search.next()
        if results and results.get('result'):
            video = results['result'][0]
            return {
                "id": video.get('id'),
                "language": "unknown"
            }
    except Exception as e:
        print(f"❌ Generic search failed: {str(e)}")
    
    return None

@app.get("/manifest.json")
async def get_manifest():
    return {
        "id": "com.yourname.youtubetrailers.v3",
        "version": "2.0.0",
        "name": "Nuvio Smart Trailers (Multi-Language)",
        "description": "TMDB metadata with AI-powered YouTube trailer matching + Language Preferences.",
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
    Fetch metadata with smart trailer selection.
    
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
    
    print(f"🎯 Language preferences: {language_prefs}")
    
    # Default fallback structural response
    meta_response = {"meta": {"id": content_id, "type": content_type, "name": "Loading..."}}
    
    if not TMDB_API_KEY:
        print("❌ ERROR: TMDB_API_KEY environment variable is missing!")
        return meta_response
    
    async with httpx.AsyncClient() as client:
        try:
            # 1. Fetch metadata from TMDB
            print(f"📡 Fetching TMDB data for {content_id}...")
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
            
            # 2. Get best trailer with language preferences (INSTANT - parallel searches)
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
                print(f"🎬 SUCCESS: Found {used_language} trailer (ID: {best_video_id}) for {name}")
                meta_data['trailers'] = [{"source": best_video_id, "type": "Trailer"}]
                meta_data['trailer'] = best_video_id
            else:
                print(f"⚠️ No trailer found for {name}")
            
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
        "version": "2.0.0",
        "default_languages": DEFAULT_LANGUAGE_PREFS,
        "supported_languages": list(LANGUAGE_KEYWORDS.keys())
    }
