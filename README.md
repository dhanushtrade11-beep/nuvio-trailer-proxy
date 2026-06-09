# aiometadata × nuvio-trailer-proxy Integration

## What this does

Replaces the generic TMDB trailer in aiometadata with an **AI + language-aware** trailer
selected by the nuvio-trailer-proxy.

```
User opens movie in Nuvio / Stremio
           ↓
   aiometadata receives meta request
           ↓
   Fetches TMDB metadata (existing)  ←── unchanged
           ↓
   Calls nuvio-trailer-proxy         ←── NEW
     ├── Gemini AI generates 4 YouTube search queries
     │     (lang1, lang2, original language, generic)
     ├── YouTube Data API searches in priority order
     │     lang1 → lang2 → original → generic
     └── Returns best YouTube video ID
           ↓
   Swaps TMDB trailer with proxy result
           ↓
   Returns meta to Nuvio / Stremio
```

## Files in this package

```
nuvio-trailer-integration/
│
├── nuvio-trailer-proxy/          ← The Python proxy server (deploy this)
│   ├── main.py                   ← Original proxy source (unchanged)
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
│
├── aiometadata-patch/            ← Changes to make in your aiometadata fork
│   ├── addon/src/utils/
│   │   └── trailerProxy.ts       ← DROP THIS FILE into your repo
│   └── PATCHES.md                ← Exact before/after diffs for 3 files
│
├── docker-compose.yml            ← Complete stack (aiometadata + proxy + redis)
├── .env.example                  ← All env vars documented
└── README.md                     ← This file
```

---

## Step-by-step setup

### 1. Fork & clone aiometadata

```bash
git clone https://github.com/cedya77/aiometadata
cd aiometadata
git checkout dev
```

### 2. Copy the utility file

```bash
cp path/to/aiometadata-patch/addon/src/utils/trailerProxy.ts \
   addon/src/utils/trailerProxy.ts
```

### 3. Apply the code patches

Open `PATCHES.md` and apply the before/after changes to:
- `addon/src/metadata/tmdb.ts`   ← main one
- `addon/src/metadata/anilist.ts` (if you use AniList)
- `addon/src/metadata/tvdb.ts`   (if TVDB returns trailers)

Each patch is tiny: one import line + ~5 lines of code after where the trailer is set.

### 4. Add the proxy folder next to docker-compose.yml

```
your-deployment/
├── docker-compose.yml            ← from this package
├── .env                          ← from .env.example
└── nuvio-trailer-proxy/
    ├── main.py
    ├── requirements.txt
    └── Dockerfile
```

### 5. Configure .env

```dotenv
TMDB_API=your_existing_tmdb_key
GEMINI_API_KEY=your_gemini_key      # free at aistudio.google.com
YOUTUBE_API_KEY=your_yt_key         # optional but recommended
TRAILER_LANG_1=telugu               # your first language
TRAILER_LANG_2=english              # fallback language
TRAILER_PROXY_URL=http://nuvio_trailer_proxy:8000
```

### 6. Build & run

```bash
docker compose up -d --build
```

---

## Language options

Set `TRAILER_LANG_1` and `TRAILER_LANG_2` to any of:

| Value | Language |
|-------|----------|
| `telugu` | Telugu (తెలుగు) |
| `english` | English |
| `hindi` | Hindi (हिंदी) |
| `tamil` | Tamil (தமிழ்) |
| `kannada` | Kannada (ಕನ್ನಡ) |
| `malayalam` | Malayalam (മലയാളം) |

You can also pass `?lang1=hindi&lang2=tamil` per-request via Stremio URL parameters
if you want per-user overrides.

---

## How the proxy selects a trailer

```
Step 1: Gemini AI generates 4 YouTube search queries in one API call:
        "Movie Name 2023 telugu official trailer"
        "Movie Name 2023 english official trailer"
        "Movie Name 2023 official trailer"        ← original language
        "Movie Name 2023 trailer"                  ← generic fallback

Step 2: YouTube Data API (or scraper if no key) searches each query
        and returns the first video where the title contains "trailer"

Step 3: Stop at the first language tier that finds a result:
        telugu found → return telugu trailer  ✅
        telugu not found → try english
        english found → return english trailer ✅
        ...and so on

Step 4: Cache the result for 7 days (per content + language combo)
```

---

## Disable AI (language-sort only)

Leave `GEMINI_API_KEY` blank in your `.env`. The proxy will use simple
hand-crafted queries (`"{title} {year} {lang} official trailer"`) instead.
Language priority still works — only the query crafting is less smart.

---

## Troubleshooting

**Trailer didn't change?**
- Check `TRAILER_PROXY_URL` is set and the proxy container is running
- Check proxy logs: `docker logs nuvio_trailer_proxy`
- The proxy logs every step: `[LANG1] query → ✅ Found: YT_ID`

**Proxy container not starting?**
- Check `TMDB_API_KEY` inside the proxy container matches your TMDB key env var name
- The docker-compose maps `TMDB_API` (aiometadata name) → `TMDB_API_KEY` (proxy name)

**Wrong trailer still showing?**
- Try setting `YOUTUBE_API_KEY` — the scraper fallback is less accurate
- Check if the movie has trailers in your language on YouTube manually
- Increase `TRAILER_LANG_1` specificity: `hindi dubbed` instead of `hindi`

**aiometadata TMDB trailer as fallback?**
- If the proxy returns `null` or times out, `trailerProxy.ts` silently keeps
  the TMDB trailer. So you always get _some_ trailer even if the proxy fails.
