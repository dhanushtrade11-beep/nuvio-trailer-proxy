import os
import asyncio
from fastapi import FastAPI
import httpx
from cachetools import TTLCache

app = FastAPI()

# This pulls the secret key you set in Render's Environment Variables securely
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
AIOMETADATA_URL = "https://aiometadata.elfhosted.com"

# Cache up to 10,000 movies for 24 hours to keep Nuvio lightning fast
meta_cache = TTLCache(maxsize=10000, ttl=86400)

@app.get("/manifest.json")
async def get_manifest():
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{AIOMETADATA_URL}/manifest.json")
        manifest = resp.json()
        
        # Rename so you know you are installing your custom proxy
        manifest['id'] = "com.yourname.trailerproxy"
        manifest['name'] = "Nuvio Telugu Trailers (AIO Proxy)"
        manifest['description'] = "Forces accurate Telugu/English trailers over AIOMetadata."
        
        return manifest

@app.get("/catalog/{full_path:path}")
async def proxy_catalog(full_path: str):
    # Pass all catalog browsing directly to AIOMetadata untouched
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{AIOMETADATA_URL}/catalog/{full_path}")
        return resp.json()

@app.get("/meta/{content_type}/{content_id}.json")
async def get_custom_meta(content_type: str, content_id: str):
    # Check if we already have the perfect metadata saved in memory
    cache_key = f"{content_type}_{content_id}"
    if cache_key in meta_cache:
        return meta_cache[cache_key]

    async with httpx.AsyncClient() as client:
        # Start fetching AIOMetadata payload and TMDB ID at the exact same time
        meta_task = client.get(f"{AIOMETADATA_URL}/meta/{content_type}/{content_id}.json")
        tmdb_find_task = client.get(
            f"https://api.themoviedb.org/3/find/{content_id}?api_key={TMDB_API_KEY}&external_source=imdb_id"
        )
        
        meta_resp, find_resp = await asyncio.gather(meta_task, tmdb_find_task)
        meta_json = meta_resp.json()
        tmdb_data = find_resp.json()
        
        try:
            # Extract TMDB ID
            tmdb_id = tmdb_data['movie_results'][0]['id']
            
            # Fetch videos for that specific TMDB ID
            video_resp = await client.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}/videos?api_key={TMDB_API_KEY}"
            )
            videos = video_resp.json().get('results', [])
            
            # Filter strictly for "Trailer"
            trailers = [v for v in videos if v.get('type') == 'Trailer']
            
            best_trailer_key = None
            
            # Apply Language Preference
            telugu_trailers = [t for t in trailers if t.get('iso_639_1') == 'te']
            english_trailers = [t for t in trailers if t.get('iso_639_1') == 'en']
            
            if telugu_trailers:
                best_trailer_key = telugu_trailers[0]['key']
            elif english_trailers:
                best_trailer_key = english_trailers[0]['key']
            elif trailers:
                best_trailer_key = trailers[0]['key']
                
            # Override AIOMetadata's trailer
            if best_trailer_key:
                meta_json['meta']['trailers'] = [
                    {"source": best_trailer_key, "type": "Trailer"}
                ]
        except Exception:
            # If movie isn't on TMDB, it safely falls back to whatever AIOMetadata provided
            pass
            
        # Save to cache before returning
        meta_cache[cache_key] = meta_json
        return meta_json
      
