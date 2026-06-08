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

AIOMETADATA_URL = "https://aiometadata.elfhosted.com"
meta_cache = TTLCache(maxsize=10000, ttl=86400)

# This header disguises your Render server as a normal Windows/Chrome user
# to bypass Elfhosted/Cloudflare bot protection
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Accept": "application/json"
}

@app.get("/manifest.json")
async def get_manifest():
    return {
        "id": "com.yourname.youtubetrailers.v2",
        "version": "1.0.0",
        "name": "Nuvio YouTube Trailers (Instant Proxy)",
        "description": "Forces accurate Telugu > English > Original trailers over AIOMetadata.",
        "resources": ["catalog", "meta"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": [
            {
                "type": "movie",
                "id": "aiometadata_movies",
                "name": "AIO Movies"
            },
            {
                "type": "series",
                "id": "aiometadata_series",
                "name": "AIO Series"
            }
        ]
    }

@app.get("/catalog/{content_type}/{catalog_id}.json")
async def proxy_catalog(content_type: str, catalog_id: str):
    # Added HEADERS and follow_redirects to safely navigate bot-checks
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        resp = await client.get(f"{AIOMETADATA_URL}/catalog/{content_type}/{catalog_id}.json")
        try:
            return resp.json()
        except Exception:
            # Prevents a full server crash if AIOMetadata sends an HTML block page
            return {"metas": []}

@app.get("/meta/{content_type}/{content_id}.json")
async def get_custom_meta(content_type: str, content_id: str):
    cache_key = f"{content_type}_{content_id}"
    
    if cache_key in meta_cache:
        print(f"Loading {content_id} from instant cache!")
        return meta_cache[cache_key]

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        print(f"Intercepted metadata request for ID: {content_id}")
        resp = await client.get(f"{AIOMETADATA_URL}/meta/{content_type}/{content_id}.json")
        
        try:
            meta_json = resp.json()
        except Exception:
            print("CRITICAL: AIOMetadata returned a non-JSON response (Likely a bot block).")
            # Return basic empty metadata so Nuvio doesn't freeze
            return {"meta": {"id": content_id, "type": content_type}}
        
        try:
            name = meta_json.get('meta', {}).get('name', '')
            year = meta_json.get('meta', {}).get('year', '')
            
            if name:
                print(f"Searching YouTube trailers for: {name} {year}")
                
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
                    meta_json['meta']['trailers'] = [{"source": best_video_id, "type": "Trailer"}]
                    meta_json['meta']['trailer'] = best_video_id
                else:
                    print(f"FAILED: YouTube search returned zero results for {name}")
                    
        except Exception as e:
            print(f"CRITICAL ERROR during YouTube Search: {str(e)}")
            pass
            
        meta_cache[cache_key] = meta_json
        return meta_json

