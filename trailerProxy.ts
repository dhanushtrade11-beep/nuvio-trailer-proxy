/**
 * addon/src/utils/trailerProxy.ts
 *
 * Integrates the nuvio-trailer-proxy into aiometadata.
 *
 * The proxy (main.py) works as follows:
 *   1. Receives an IMDb ID + language preferences
 *   2. Uses Gemini AI to generate the best YouTube search queries per language
 *   3. Searches YouTube (Data API v3 or scraper fallback) in order:
 *      lang1 → lang2 → original language → generic
 *   4. Returns: { meta: { trailer: "YT_ID", trailers: [{ source: "YT_ID", type: "Trailer" }] } }
 *
 * This module calls the proxy and returns the trailer fields ready to
 * drop into any aiometadata meta response — replacing the TMDB trailer.
 *
 * Environment variables (add to your .env):
 *   TRAILER_PROXY_URL   – base URL of deployed nuvio-trailer-proxy
 *                         e.g. "http://nuvio_trailer_proxy:8000"
 *                         Leave blank to disable (TMDB trailer used as-is).
 *   TRAILER_LANG_1      – first language preference  (default: "telugu")
 *   TRAILER_LANG_2      – second language preference (default: "english")
 */

const TRAILER_PROXY_URL = (process.env.TRAILER_PROXY_URL ?? "").replace(/\/$/, "");
const TRAILER_LANG_1    = process.env.TRAILER_LANG_1 ?? "telugu";
const TRAILER_LANG_2    = process.env.TRAILER_LANG_2 ?? "english";

/** What the proxy's /meta endpoint returns */
interface ProxyMetaResponse {
  meta: {
    id: string;
    type: string;
    trailer?: string;          // YouTube video ID
    trailers?: Array<{ source: string; type: string }>;
    [key: string]: unknown;
  };
}

/** Stremio trailer shape used by aiometadata */
export interface StremioTrailer {
  source: string;   // YouTube video ID
  type: "Trailer";
}

/**
 * Fetches the best trailer for a title from the nuvio-trailer-proxy.
 *
 * @param imdbId      IMDb ID of the title (e.g. "tt1234567")
 * @param contentType "movie" | "series"
 * @param lang1       First language preference (overrides env default)
 * @param lang2       Second language preference (overrides env default)
 * @returns           Array with one StremioTrailer, or null if proxy is
 *                    disabled / unavailable / returned no trailer.
 *                    On null, caller should keep the TMDB trailer as-is.
 */
export async function fetchProxyTrailer(
  imdbId: string,
  contentType: "movie" | "series",
  lang1?: string,
  lang2?: string
): Promise<StremioTrailer[] | null> {
  // Proxy not configured — skip silently
  if (!TRAILER_PROXY_URL) return null;

  const l1 = (lang1 ?? TRAILER_LANG_1).toLowerCase();
  const l2 = (lang2 ?? TRAILER_LANG_2).toLowerCase();

  const url =
    `${TRAILER_PROXY_URL}/meta/${contentType}/${imdbId}.json` +
    `?lang1=${encodeURIComponent(l1)}&lang2=${encodeURIComponent(l2)}`;

  try {
    const response = await fetch(url, {
      signal: AbortSignal.timeout(10_000), // 10s — proxy does async YouTube search
    });

    if (!response.ok) {
      console.warn(`[trailerProxy] ${url} → HTTP ${response.status}`);
      return null;
    }

    const data = (await response.json()) as ProxyMetaResponse;

    // The proxy returns trailers as an array of { source: "YT_ID", type: "Trailer" }
    if (data?.meta?.trailers && data.meta.trailers.length > 0) {
      return data.meta.trailers as StremioTrailer[];
    }

    // Fallback: proxy returned a bare trailer string
    if (data?.meta?.trailer) {
      return [{ source: data.meta.trailer, type: "Trailer" }];
    }

    console.warn(`[trailerProxy] No trailer in proxy response for ${imdbId}`);
    return null;
  } catch (err) {
    // Timeout, network error, parse error — fail silently, keep TMDB trailer
    console.warn(`[trailerProxy] Failed for ${imdbId}: ${(err as Error).message}`);
    return null;
  }
}
