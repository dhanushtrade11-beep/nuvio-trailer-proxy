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

# Securely pull the TMDB API key from Render's Environment Variables
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

@app.get("/manifest.json")
async def get_manifest():
    return {
        "id": "com.yourname.youtubetrailers.v3",
        "version": "1.0.0",
        "name": "Nuvio Telugu Trailers (Client Mix)",
        "description": "Smart YouTube trailers mixed into your existing addons.",
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

    # Set up the baseline response
    meta_response = {"meta": {"id": content_id, "type": content_type}}

    if not TMDB_API_KEY:
        print("ERROR: TMDB_API_KEY environment variable is missing in Render!")
        return meta_response

    async with httpx.AsyncClient() as client:
        try:
            # 1. Ask TMDB for the movie name using your secure API key
            tmdb_resp = await client.get(
                f"https://api.themoviedb.org/3/find/{content_id}?api_key={TMDB_API_KEY}&external_source=imdb_id"
            )
            tmdb_data = tmdb_resp.json()
            
            name, year = "", ""
            if content_type == "movie" and tmdb_data.get('movie_results'):
                name = tmdb_data['movie_results'][0].get('title', '')
                year = tmdb_data['movie_results'][0].get('release_date', '')[:4]
            elif content_type == "series" and tmdb_data.get('tv_results'):
                name = tmdb_data['tv_results'][0].get('name', '')
                year = tmdb_data['tv_results'][0].get('first_air_date', '')[:4]

            best_video_id = None
            
            # 2. If TMDB gave us a name, search YouTube
            if name:
                print(f"Searching YouTube trailers for: {name} {year}")
                
                search_telugu = VideosSearch(f"{name} {year} official telugu trailer", limit=1).next()
                search_english = VideosSearch(f"{name} {year} official english trailer", limit=1).next()
                search_original = VideosSearch(f"{name} {year} official trailer", limit=1).next()
                
                res_telugu, res_english, res_original = await asyncio.gather(
                    search_telugu, search_english, search_original
                )
                
                # Check Telugu -> English -> Original
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

            # 3. Add the winning trailer to the metadata response
            if best_video_id:
                print(f"SUCCESS: Found YouTube ID {best_video_id} for {name}")
                meta_response['meta']['trailers'] = [{"source": best_video_id, "type": "Trailer"}]
                meta_response['meta']['trailer'] = best_video_id

        except Exception as e:
            print(f"ERROR: {str(e)}")

        # Cache and return only the trailer metadata for Nuvio to mix in
        meta_cache[cache_key] = meta_response
        return meta_response

