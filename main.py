import asyncio
from fastapi import FastAPI
import httpx
from cachetools import TTLCache
from youtubesearchpython.__future__ import VideosSearch

app = FastAPI()

AIOMETADATA_URL = "https://aiometadata.elfhosted.com"

# Memory Cache to completely eliminate buffering for 24 hours once a movie is clicked
meta_cache = TTLCache(maxsize=10000, ttl=86400)

@app.get("/manifest.json")
async def get_manifest():
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{AIOMETADATA_URL}/manifest.json")
        manifest = resp.json()
        
        manifest['id'] = "com.yourname.youtubetrailers"
        manifest['name'] = "Nuvio YouTube Trailers (Instant Proxy)"
        manifest['description'] = "Instant Telugu > English > Original YouTube trailers."
        
        return manifest

@app.get("/catalog/{full_path:path}")
async def proxy_catalog(full_path: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{AIOMETADATA_URL}/catalog/{full_path}")
        return resp.json()

@app.get("/meta/{content_type}/{content_id}.json")
async def get_custom_meta(content_type: str, content_id: str):
    cache_key = f"{content_type}_{content_id}"
    
    # 1. INSTANT CACHE CHECK: If clicked before, return immediately (0.001 seconds)
    if cache_key in meta_cache:
        return meta_cache[cache_key]

    async with httpx.AsyncClient() as client:
        meta_resp = await client.get(f"{AIOMETADATA_URL}/meta/{content_type}/{content_id}.json")
        meta_json = meta_resp.json()
        
        try:
            name = meta_json.get('meta', {}).get('name', '')
            year = meta_json.get('meta', {}).get('year', '')
            
            if name:
                # 2. PARALLEL EXECUTION: Launch all 3 YouTube searches at the exact same time
                search_telugu = VideosSearch(f"{name} {year} official telugu trailer", limit=1).next()
                search_english = VideosSearch(f"{name} {year} official english trailer", limit=1).next()
                search_original = VideosSearch(f"{name} {year} official trailer", limit=1).next()
                
                # Wait for all 3 network tasks to finish together (saves seconds of delay)
                res_telugu, res_english, res_original = await asyncio.gather(
                    search_telugu, search_english, search_original
                )
                
                best_video_id = None
                
                # PREFERENCE 1: Check Telugu results
                if res_telugu and res_telugu.get('result'):
                    title = res_telugu['result'][0]['title'].lower()
                    if 'telugu' in title:
                        best_video_id = res_telugu['result'][0]['id']
                
                # PREFERENCE 2: Check English results if Telugu wasn't found
                if not best_video_id and res_english and res_english.get('result'):
                    title = res_english['result'][0]['title'].lower()
                    if 'english' in title or 'official' in title:
                        best_video_id = res_english['result'][0]['id']
                        
                # PREFERENCE 3: Absolute Fallback to Original Trailer
                if not best_video_id and res_original and res_original.get('result'):
                    best_video_id = res_original['result'][0]['id']
                
                # Inject the selected high-accuracy trailer
                if best_video_id:
                    meta_json['meta']['trailers'] = [
                        {"source": best_video_id, "type": "Trailer"}
                    ]
                    
        except Exception:
            pass
            
        # Store in memory cache so the next click is 100% instant
        meta_cache[cache_key] = meta_json
        return meta_json

