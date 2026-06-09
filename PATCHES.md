# aiometadata — Trailer Proxy Integration Patches

Apply these changes in your aiometadata fork after copying `trailerProxy.ts`
into `addon/src/utils/`.

The proxy (`nuvio-trailer-proxy/main.py`) is a **Python FastAPI server** that:
- Takes an IMDb ID + two language preferences
- Uses **Gemini AI** to craft the best YouTube search queries per language
- Searches YouTube in order: lang1 → lang2 → original → generic
- Returns `{ meta: { trailer: "YT_ID", trailers: [...] } }`

The changes below replace whatever TMDB returned with the proxy's AI-selected,
language-matched trailer. Everything else in aiometadata is untouched.

---

## How to find the right spot in each file

Run this grep in your repo root:

```bash
grep -rn "trailers\b" addon/src/ --include="*.ts" -l
```

Then inside each file found:

```bash
grep -n "trailers\|youtube\.com\|source.*key\|key.*Trailer" addon/src/metadata/tmdb.ts
```

---

## PATCH 1 — addon/src/metadata/tmdb.ts  (TMDB movies & series)

### Add import at the top (after existing imports):

```typescript
import { fetchProxyTrailer } from "../utils/trailerProxy";
```

### Find this block (the exact shape varies slightly by version — search for `trailers`):

```typescript
// ── BEFORE ──────────────────────────────────────────────────────────────────

if (includeVideos) {
  const videosData = await tmdbFetch(`/movie/${tmdbId}/videos`, lang);
  // --- or for series: ---
  const videosData = await tmdbFetch(`/tv/${tmdbId}/videos`, lang);

  const trailer = videosData?.results?.find(
    (v: any) => v.site === "YouTube" && v.type === "Trailer" && v.official
  ) ?? videosData?.results?.find(
    (v: any) => v.site === "YouTube" && v.type === "Trailer"
  );

  if (trailer) {
    meta.trailers = [{ source: trailer.key, type: "Trailer" }];
  }
}
```

### Replace with:

```typescript
// ── AFTER ───────────────────────────────────────────────────────────────────

if (includeVideos) {
  const videosData = await tmdbFetch(`/movie/${tmdbId}/videos`, lang);
  // --- or for series: ---
  const videosData = await tmdbFetch(`/tv/${tmdbId}/videos`, lang);

  const trailer = videosData?.results?.find(
    (v: any) => v.site === "YouTube" && v.type === "Trailer" && v.official
  ) ?? videosData?.results?.find(
    (v: any) => v.site === "YouTube" && v.type === "Trailer"
  );

  if (trailer) {
    // Set TMDB trailer as default first
    meta.trailers = [{ source: trailer.key, type: "Trailer" }];

    // Then try to replace it with the AI + language-aware proxy result
    // imdbId should be in scope here (the tt-ID used to query TMDB)
    const proxyTrailers = await fetchProxyTrailer(
      imdbId,                                    // the tt-prefixed IMDb ID in scope
      meta.type === "series" ? "series" : "movie"
      // lang1 and lang2 will use TRAILER_LANG_1 / TRAILER_LANG_2 env vars by default
      // or pass config.language if you want to forward the user's aiometadata language
    );
    if (proxyTrailers) {
      meta.trailers = proxyTrailers;   // ← swap TMDB trailer with proxy's pick
    }
  }
}
```

> **Note on `imdbId` in scope**: In `tmdb.ts` the IMDb ID arrives as the `id`
> parameter to the meta handler (e.g. `tt1234567`). If TMDB is queried via its
> own numeric ID, the IMDb ID may be in `externalIds.imdb_id`. Use whichever
> is available in that scope.

---

## PATCH 2 — addon/src/metadata/anilist.ts  (AniList / anime trailers)

AniList trailers come as `media.trailer.id` (YouTube ID) when `media.trailer.site === "youtube"`.

### Add import:

```typescript
import { fetchProxyTrailer } from "../utils/trailerProxy";
```

### BEFORE:

```typescript
if (media.trailer?.site === "youtube" && media.trailer?.id) {
  meta.trailers = [{ source: media.trailer.id, type: "Trailer" }];
}
```

### AFTER:

```typescript
if (media.trailer?.site === "youtube" && media.trailer?.id) {
  // Default: use AniList's trailer
  meta.trailers = [{ source: media.trailer.id, type: "Trailer" }];

  // Try proxy if we have an IMDb ID for this anime
  if (meta.imdb_id || imdbId) {
    const proxyTrailers = await fetchProxyTrailer(
      meta.imdb_id ?? imdbId,
      "series"
    );
    if (proxyTrailers) {
      meta.trailers = proxyTrailers;
    }
  }
}
```

---

## PATCH 3 — addon/src/metadata/tvdb.ts  (TVDB trailers, if any)

TVDB sometimes provides trailer URLs directly.

### Add import:

```typescript
import { fetchProxyTrailer } from "../utils/trailerProxy";
```

### BEFORE (search for where `trailers` is set in tvdb.ts):

```typescript
if (trailerUrl) {
  meta.trailers = [{ source: trailerUrl, type: "Trailer" }];
}
```

### AFTER:

```typescript
if (trailerUrl) {
  meta.trailers = [{ source: trailerUrl, type: "Trailer" }];

  // Extract YouTube ID from TVDB trailer URL and run through proxy
  const ytMatch = trailerUrl.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([a-zA-Z0-9_-]{11})/);
  if (ytMatch && (meta.imdb_id || imdbId)) {
    const proxyTrailers = await fetchProxyTrailer(
      meta.imdb_id ?? imdbId,
      contentType === "series" ? "series" : "movie"
    );
    if (proxyTrailers) {
      meta.trailers = proxyTrailers;
    }
  }
}
```

---

## Summary of all changes

| What | File | Lines changed |
|------|------|--------------|
| New file | `addon/src/utils/trailerProxy.ts` | New — copy from this package |
| Import + trailer swap | `addon/src/metadata/tmdb.ts` | +1 import, +5 lines after trailer found |
| Import + trailer swap | `addon/src/metadata/anilist.ts` | +1 import, +4 lines after trailer found |
| Import + trailer swap | `addon/src/metadata/tvdb.ts` | +1 import, +5 lines after trailer found |
| New env vars | `.env` / `.env.example` | +3 vars |

No other files are touched. Catalogs, posters, artwork, caching, config UI — all unchanged.
