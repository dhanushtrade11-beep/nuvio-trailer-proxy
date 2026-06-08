import asyncio
from fastapi import FastAPI
import httpx
from cachetools import TTLCache
from youtubesearchpython.__future__ import VideosSearch

app = FastAPI()

AIOMETADATA_URL = "https://aiometadata.elfhosted.com"
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
    
    if cache_key in meta_cache:
        print(f"Loading {content_id} from instant cache!")
        return meta_cache[cache_key]

    async with httpx.AsyncClient() as client:
        meta_resp = await client.get(f"{AIOMETADATA_URL}/meta/{content_type}/{content_id}.json")
        meta_json = meta_resp.json()
        
        try:
            name = meta_json.get('meta', {}).get('name', '')
            year = meta_json.get('meta', {}).get('year', '')
            
            if name:
                print(f"Searching trailers for: {name} {year}")
                
                search_telugu = VideosSearch(f"{name} {year} official telugu trailer", limit=1).next()
                search_english = VideosSearch(f"{name} {year} official english trailer", limit=1).next()
                search_original = VideosSearch(f"{name} {year} official trailer", limit=1).next()
                
                res_telugu, res_english, res_original = await asyncio.gather(
                    search_telugu, search_english, search_original
                )
                
                best_video_id = None
                
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
                
                if best_video_id:
                    print(f"SUCCESS: Injected YouTube ID {best_video_id} for {name}")
                    
                    # 1. Modern Format (Array)
                    meta_json['meta']['trailers'] = [
                        {"source": best_video_id, "type": "Trailer"}
                    ]
                    # 2. Legacy Format (String) to force older apps to see it
                    meta_json['meta']['trailer'] = best_video_id
                else:
                    print(f"FAILED: YouTube search returned zero results for {name}")
                    
        except Exception as e:
            print(f"CRITICAL ERROR during YouTube Search: {str(e)}")
            pass
            
        meta_cache[cache_key] = meta_json
        return meta_json

