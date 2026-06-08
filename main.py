import os
import asyncio
from fastapi import FastAPI
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

@app.get("/manifest.json")
async def get_manifest():
    return {
        "id": "com.yourname.youtubetrailers.v3",
        "version": "1.0.0",
        "name": "Nuvio Telugu Trailers (Premium Mix)",
        "description": "Full TMDB metadata with automated YouTube trailer matching.",
        "resources": ["meta"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"]
    }

@app.get("/meta/{content_type}/{content_id}.json")
async def get_custom_meta(content_type: str, content_id: str):
    cache_key = f"{content_type}_{content_id}"
    
    if cache_key in meta_cache:
        print(f"Loading {content_id} from instant cache!")
        return meta_cache[cache_key]

    # Default fallback structural response
    meta_response = {"meta": {"id": content_id, "type": content_type, "name": "Loading..."}}

    if not TMDB_API_KEY:
        print("ERROR: TMDB_API_KEY environment variable is missing in Render!")
        return meta_response

    async with httpx.AsyncClient() as client:
        try:
            # 1. Fetch deep metadata from TMDB
            tmdb_resp = await client.get(
                f"https://api.themoviedb.org/3/find/{content_id}?api_key={TMDB_API_KEY}&external_source=imdb_id"
            )
            tmdb_data = tmdb_resp.json()
            
            if isinstance(tmdb_data, dict) and "status_message" in tmdb_data:
                print(f"TMDB API ERROR: {tmdb_data.get('status_message')}")
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

            # If no name was found, drop out early
            if not name:
                return meta_response

            best_video_id = None
            print(f"Searching YouTube trailers for: {name} {year}")
            
            # 2. Parallel YouTube Searches
            search_telugu = VideosSearch(f"{name} {year} official telugu trailer", limit=1).next()
            search_english = VideosSearch(f"{name} {year} official english trailer", limit=1).next()
            search_original = VideosSearch(f"{name} {year} official trailer", limit=1).next()
            
            res_telugu, res_english, res_original = await asyncio.gather(
                search_telugu, search_english, search_original
            )
            
            if res_telugu and res_telugu.get('result'):
                title = res_telugu['result'][0]['title'].lower()
                if 'telugu' in title:
                    best_video_id = res_telugu['result'][0]['id']
            
            if not best_video_id and res_english and res_english.get('result'):
                title = res_english['result'][0]['title'].lower()
                if 'english' in title or 'official' in title:
                    best_video_id = res_english['result'][0]['id']
                    
            if not best_video_id and res_original and res_original.get('result'):
                best_video_id = res_original['result'][0]['id']

            # 3. Construct a fully compliant Stremio Meta Object
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
                print(f"SUCCESS: Found YouTube ID {best_video_id} for {name}")
                meta_data['trailers'] = [{"source": best_video_id, "type": "Trailer"}]
                meta_data['trailer'] = best_video_id
            else:
                print(f"INFO: No trailer found on YouTube for {name}")

            meta_response = {"meta": meta_data}

        except Exception as e:
            print(f"ERROR: {str(e)}")

        meta_cache[cache_key] = meta_response
        return meta_response
                  
