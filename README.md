# Nuvio Smart Trailer Proxy v2.0

**AI-powered YouTube trailer fetching with multi-language preferences for Nuvio app**

## ✨ Features

### 🌍 Smart Multi-Language Trailer Selection
- **First Preference Language**: Gets priority (e.g., Telugu)
- **Second Preference Language**: Fallback if first not available (e.g., English)  
- **Original Language**: Used if both preferences unavailable
- **Instant Performance**: All language searches happen in parallel for speed

### 🎯 Improved Accuracy
- **Eliminates wrong trailers** from aiometadata
- Language-specific keyword matching (Telugu, English, Hindi, Tamil, Kannada, Malayalam)
- Validates trailer titles to ensure correct matches
- Real-time verification before returning results

### ⚡ Lightning-Fast Performance
- **TTL Cache**: 24-hour caching for instant subsequent loads
- **Parallel async searches**: All language options searched simultaneously
- **Instant swap**: Switch trailers instantly when clicking movies in Nuvio app
- Sub-second response times

### 🔧 Configuration

#### Environment Variables
```bash
TMDB_API_KEY=your_tmdb_api_key_here
LANGUAGE_PREF_1=telugu        # First preference (default)
LANGUAGE_PREF_2=english       # Second preference (default)
```

#### Query Parameters (Override per request)
```
GET /meta/movie/tt0111161.json?lang1=telugu&lang2=english
GET /meta/movie/tt0111161.json?lang1=hindi&lang2=tamil
GET /meta/series/tt0944947.json?lang1=kannada&lang2=english
```

### 📋 Supported Languages
- `telugu` - Telugu (తెలుగు)
- `english` - English
- `hindi` - Hindi (हिंदी)
- `tamil` - Tamil (தமிழ்)
- `kannada` - Kannada (ಕನ್ನಡ)
- `malayan`/`malayalam` - Malayalam (മലയാളം)

## 🚀 How It Works

### Trailer Selection Logic
```
1. Search for trailer in LANGUAGE_PREF_1 (Telugu)
   ✅ Found? → Return Telugu trailer
   ❌ Not found? → Continue

2. Search for trailer in LANGUAGE_PREF_2 (English)
   ✅ Found? → Return English trailer
   ❌ Not found? → Continue

3. Search for original language trailer
   ✅ Found? → Return original trailer
   ❌ Not found? → Continue

4. Final fallback: Generic trailer search
```

### Request Flow
```
Nuvio App clicks movie
    ↓
FastAPI endpoint receives request
    ↓
Check cache (instant ⚡)
    ↓
Fetch TMDB metadata
    ↓
Search all preferred languages in PARALLEL
    ↓
Return best match based on preference order
    ↓
Cache result (TTL 24hrs)
    ↓
Show trailer instantly in Nuvio
```

## 📦 Installation

```bash
# Clone the repository
git clone https://github.com/dhanushtrade11-beep/nuvio-trailer-proxy.git
cd nuvio-trailer-proxy

# Install dependencies
pip install -r requirements.txt

# Copy and configure .env file
cp .env.example .env
# Edit .env and add your TMDB_API_KEY

# Run server
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## 🔌 API Endpoints

### 1. Get Manifest
```
GET /manifest.json
```
Returns addon metadata for Stremio integration.

### 2. Get Meta with Smart Trailers
```
GET /meta/{type}/{id}.json?lang1=telugu&lang2=english
```

**Parameters:**
- `type`: `movie` or `series`
- `id`: IMDb ID (e.g., `tt0111161`)
- `lang1`: First language preference (optional, defaults to LANGUAGE_PREF_1)
- `lang2`: Second language preference (optional, defaults to LANGUAGE_PREF_2)

**Response:**
```json
{
  "meta": {
    "id": "tt0111161",
    "type": "movie",
    "name": "The Shawshank Redemption",
    "description": "...",
    "releaseInfo": "1994",
    "poster": "https://...",
    "background": "https://...",
    "trailer": "dQw4w9WgXcQ",
    "trailers": [
      {
        "source": "dQw4w9WgXcQ",
        "type": "Trailer"
      }
    ]
  }
}
```

### 3. Health Check
```
GET /health
```
Returns system status and supported languages.

**Response:**
```json
{
  "status": "healthy",
  "version": "2.0.0",
  "default_languages": ["telugu", "english"],
  "supported_languages": ["telugu", "english", "hindi", "tamil", "kannada", "malayan"]
}
```

## 🎯 Example Usage in Nuvio

### Setup (First Time)
```bash
1. Deploy this addon to a server (Render, Heroku, VPS, etc.)
2. Add addon URL: http://your-server:8000/manifest.json
3. Configure language preferences in app settings
   - First choice: Telugu
   - Second choice: English
```

### Usage (In App)
```bash
1. Click on any movie in Nuvio
2. Trailer automatically fetches and displays
3. Uses cached result on subsequent views (instant ⚡)
4. Respects language preference order
5. Falls back gracefully if preferred language unavailable
```

## 🔄 Key Improvements Over Old Version

| Feature | Old | New |
|---------|-----|-----|
| Language Support | Hardcoded (Telugu→English→Original) | **Dynamic & Configurable** |
| Accuracy | Basic string matching | **Advanced keyword validation** |
| Performance | Sequential searches | **Parallel async searches** |
| Cache | Per content only | **Per content + language combo** |
| Fallback | Limited | **4-level intelligent fallback** |
| API | Fixed | **Query parameters for flexibility** |
| Wrong Trailers | Common issue | **Significantly reduced** |
| Customization | None | **Full environment + query control** |

## 🐛 Troubleshooting

### Trailers still showing wrong videos?
- Check that language keywords in code match actual YouTube trailer titles
- Verify TMDB_API_KEY is valid
- Check logs for language preference order
- Try querying with explicit lang1/lang2 parameters

### Slow first load?
- YouTube search takes 2-3 seconds
- Subsequent loads use cache (instant ⚡)
- Consider increasing client timeout if needed
- First request per content/language combo will always be slower

### Missing trailers for some movies?
- Verify movie exists on TMDB and YouTube
- Try different language preferences
- Check YouTube for availability in your region
- Check server logs for error messages

### Cache not working?
- Verify cachetools is installed
- Check that TTL_CACHE is set to 86400 seconds (24 hours)
- Different language prefs create different cache keys

## 📊 Logs

The system provides detailed logging:
```
🔍 Searching telugu trailer: The Shawshank Redemption 1994 official telugu trailer
✅ Found telugu trailer: The Shawshank Redemption Official Telugu Trailer
🎯 Language preferences: ['telugu', 'english']
📡 Fetching TMDB data for tt0111161...
✨ Found: The Shawshank Redemption (1994)
🎬 SUCCESS: Found telugu trailer (ID: xyz123) for The Shawshank Redemption
⚡ Loading tt0111161 from instant cache!
```

## 🔐 Security Notes
- All CORS origins allowed (modify in main.py if needed for production)
- TMDB API key stored in environment variables (never commit .env)
- YouTube search is public API (no keys required)
- Sensitive data should use environment variables

## 📝 Version History

**v2.0.0** - Language Preferences Update ✨ *CURRENT*
- ✅ Multi-language preference system
- ✅ Parallel async trailer searches
- ✅ Improved accuracy matching
- ✅ Query parameter customization
- ✅ Health check endpoint
- ✅ Better error handling and logging
- ✅ Configuration via environment variables

**v1.0.0** - Initial Release
- Basic trailer fetching from TMDB + YouTube
- Hardcoded Telugu → English → Original preference

## 🚀 Deployment

### On Render.com (Recommended for free tier)
```bash
1. Push repo to GitHub
2. Create new Web Service on Render
3. Connect GitHub repo
4. Set environment variables in dashboard:
   - TMDB_API_KEY: your_key
   - LANGUAGE_PREF_1: telugu
   - LANGUAGE_PREF_2: english
5. Deploy!
```

### On Heroku
```bash
1. Create Procfile:
   web: uvicorn main:app --host 0.0.0.0 --port $PORT
2. Deploy using Heroku CLI
3. Set config vars with your API keys
```

### On VPS (Docker)
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 🤝 Contributing
Found a bug or want to improve? Feel free to submit issues and pull requests!

## 📄 License
MIT License - Feel free to use for personal and commercial projects

---

**Made for Nuvio App with ❤️**

*Get accurate trailers in your preferred language, instantly!*
