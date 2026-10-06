'use client'
import React, { useEffect, useRef, useState } from 'react'

export type LngLat = [number, number]
export type BBox = [number, number, number, number]

export const API_OFFLINE_MESSAGE =
  'Flood API offline - run "pnpm dev:all" so the FastAPI backend is listening on port 8000.'

// `next.config.ts` rewrites /backend/* to 127.0.0.1:8000, so a stopped backend
// surfaces as a 500 on an otherwise healthy frontend. Nothing in the response
// distinguishes that from a real API failure, so every non-2xx is reported as
// an offline backend rather than as an opaque status code.
export class BackendUnavailableError extends Error {
  constructor(public readonly status: number) {
    super(API_OFFLINE_MESSAGE)
    this.name = 'BackendUnavailableError'
  }
}

export type AreaOfInterest = {
  name: string
  bbox: BBox
  center: LngLat
}

type SearchResult = {
  place_id?: string | number
  text?: string
  properties?: Record<string, unknown>
  place_name?: string
  center?: LngLat
  geometry?: { coordinates?: unknown }
}

const MAPTILER_GEOCODE_URL = 'https://api.maptiler.com/geocoding/{query}.json'
const GEOCODE_LIMIT = 8
export const SEARCH_UNAVAILABLE_MESSAGE =
  'Place search unavailable - check your network connection or MapTiler key.'

// MapTiler answers browser requests with Access-Control-Allow-Origin: *, so the
// key (NEXT_PUBLIC_*, already used by app/map.tsx) can be sent straight from
// the client. That keeps search working while the FastAPI backend is down; the
// backend proxy stays as a fallback for a rejected direct call.
async function geocodePlaces(query: string, signal: AbortSignal): Promise<SearchResult[]> {
  const key = process.env.NEXT_PUBLIC_MAPTILER_KEY
  let directError: string | null = null

  if (key) {
    try {
      const url = `${MAPTILER_GEOCODE_URL.replace('{query}', encodeURIComponent(query))}?key=${encodeURIComponent(key)}&limit=${GEOCODE_LIMIT}`
      const res = await fetch(url, { signal })
      if (!res.ok) throw new Error(`Place search failed (MapTiler HTTP ${res.status})`)
      const data = await res.json()
      if (Array.isArray(data.features)) return data.features as SearchResult[]
      throw new Error(SEARCH_UNAVAILABLE_MESSAGE)
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') throw e
      directError = e instanceof Error ? e.message : SEARCH_UNAVAILABLE_MESSAGE
    }
  }

  try {
    const res = await fetch(`/backend/geocode?q=${encodeURIComponent(query)}`, { signal })
    if (!res.ok) throw new BackendUnavailableError(res.status)
    const data = await res.json()
    if (Array.isArray(data.features)) return data.features as SearchResult[]
    throw new Error(SEARCH_UNAVAILABLE_MESSAGE)
  } catch (e) {
    if (e instanceof Error && e.name === 'AbortError') throw e
    // Both paths failed: prefer the direct call's reason (bad key, rate limit)
    // over the backend proxy's generic "API offline" message.
    throw new Error(directError ?? SEARCH_UNAVAILABLE_MESSAGE)
  }
}

// MapTiler's /geocoding/ endpoint is deprecated and answers with a Polygon
// feature rather than a Point, so `center` is frequently absent. Fall back to
// the mean of the outer ring.
function resultCenter(r: SearchResult): LngLat | null {
  if (r.center && r.center.length >= 2) {
    return [Number(r.center[0]), Number(r.center[1])]
  }

  const coords = r.geometry?.coordinates
  if (!Array.isArray(coords) || coords.length === 0) return null

  const nested = coords as unknown[]
  if (typeof nested[0] === 'number') return null // bare point, no centre needed

  // Polygon -> outer ring; MultiPolygon -> first ring.
  const first = nested[0] as unknown[]
  let ring = nested
  if (Array.isArray(first) && Array.isArray(first[0])) {
    const inner = first[0] as unknown[]
    ring = Array.isArray(inner[0]) ? inner : first
  }

  const pts = ring.filter(
    (p): p is LngLat =>
      Array.isArray(p) && p.length >= 2 && typeof p[0] === 'number' && typeof p[1] === 'number'
  )
  if (pts.length === 0) return null

  const sum = pts.reduce<LngLat>((acc, p) => [acc[0] + p[0], acc[1] + p[1]], [0, 0])
  return [sum[0] / pts.length, sum[1] / pts.length]
}

// AOI half-extent in degrees. 0.125 deg is roughly 13.8 km of latitude, giving
// a ~27 km box that stays inside one Sentinel-1 acquisition.
const AOI_HALF_DEG = 0.125

export function bboxForCenter(center: LngLat, halfDeg = AOI_HALF_DEG): BBox {
  const [lon, lat] = center
  return [
    Math.max(-180, lon - halfDeg),
    Math.max(-90, lat - halfDeg),
    Math.min(180, lon + halfDeg),
    Math.min(90, lat + halfDeg),
  ]
}

export function toAreaOfInterest(r: SearchResult): AreaOfInterest | null {
  const center = resultCenter(r)
  if (!center) return null
  const name = r.place_name || r.text || (r.properties?.name as string) || 'Selected area'
  return { name, bbox: bboxForCenter(center), center }
}

export type SearchProps = {
  onResultSelect?: (aoi: AreaOfInterest) => void
}

export default function Search({ onResultSelect }: SearchProps) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const abortRef = useRef<AbortController | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)

  const queryLongEnough = query.trim().length >= 2

  useEffect(() => {
    if (!queryLongEnough) return

    const controller = new AbortController()
    abortRef.current?.abort()
    abortRef.current = controller
    const t = setTimeout(async () => {
      setLoading(true)
      setError(null)
      try {
        const feats = await geocodePlaces(query, controller.signal)
        setResults(feats)
        setActiveIndex(0)
        setOpen(true)
      } catch (e) {
        if (e instanceof Error && e.name === 'AbortError') return
        setResults([])
        setError(e instanceof Error ? e.message : 'Search error')
        // The panel is only rendered when `open` is set, so an error raised
        // without this stayed invisible: the box looked inert on failure.
        setOpen(true)
      } finally {
        setLoading(false)
      }
    }, 300)

    return () => {
      clearTimeout(t)
      controller.abort()
    }
  }, [query, queryLongEnough])

  // Derive visibility rather than resetting state from an effect, so shrinking
  // the query below the threshold hides stale results without a render cascade.
  const visibleResults = queryLongEnough ? results : []
  const showPanel = open && queryLongEnough

  const handleSelect = (r: SearchResult) => {
    const aoi = toAreaOfInterest(r)
    if (aoi) onResultSelect?.(aoi)
    setOpen(false)
    setResults([])
    setQuery('')
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      setOpen(false)
      inputRef.current?.blur()
      return
    }
    if (e.key === 'ArrowDown' && visibleResults.length > 0) {
      e.preventDefault()
      setOpen(true)
      setActiveIndex((i) => (i + 1) % visibleResults.length)
      return
    }
    if (e.key === 'ArrowUp' && visibleResults.length > 0) {
      e.preventDefault()
      setActiveIndex((i) => (i - 1 + visibleResults.length) % visibleResults.length)
      return
    }
    if (e.key === 'Enter' && showPanel && visibleResults.length > 0) {
      e.preventDefault()
      const target = visibleResults[Math.min(activeIndex, visibleResults.length - 1)]
      if (target) handleSelect(target)
    }
  }

  const labelFor = (r: SearchResult) =>
    (r.place_name || r.text || (r.properties?.name as string) || 'Result') as string

  return (
    <div className="fs-search">
      <div className="fs-search__box">
        <svg className="fs-search__icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="11" cy="11" r="7" />
          <path d="M20 20l-3.5-3.5" strokeLinecap="round" />
        </svg>
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => visibleResults.length > 0 && setOpen(true)}
          placeholder="Search area of interest..."
          aria-label="Search area of interest"
          role="combobox"
          aria-expanded={showPanel}
          aria-controls="fs-search-listbox"
          aria-autocomplete="list"
          aria-busy={loading}
        />
        {loading && <span className="fs-search__spinner" />}
      </div>
      {showPanel && visibleResults.length > 0 && (
        <ul id="fs-search-listbox" className="fs-search__results" role="listbox">
          {visibleResults.map((r, i) => (
            <li key={r.place_id ?? i}>
              <button
                type="button"
                role="option"
                aria-selected={i === activeIndex}
                className={i === activeIndex ? 'fs-search__result--active' : undefined}
                onMouseEnter={() => setActiveIndex(i)}
                onClick={() => handleSelect(r)}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 21s7-7.1 7-12a7 7 0 10-14 0c0 4.9 7 12 7 12z" />
                  <circle cx="12" cy="9" r="2.4" />
                </svg>
                <span>{labelFor(r)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {showPanel && error && (
        <div className="fs-search__results fs-search__results--error" role="alert">
          {error}
        </div>
      )}
      {showPanel && !loading && !error && visibleResults.length === 0 && (
        <div className="fs-search__results">No results</div>
      )}
    </div>
  )
}