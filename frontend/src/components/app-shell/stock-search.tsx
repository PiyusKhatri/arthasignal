"use client";

import { ArrowRight, Clock3, LoaderCircle, Search, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

type SearchResult = {
  symbol: string;
  company_name: string;
};

type SearchStatus = "idle" | "loading" | "ready" | "error";

const RECENT_KEY = "artha-recent-stock-searches:v1";

function readRecent(): SearchResult[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(RECENT_KEY) ?? "[]") as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is SearchResult => {
        if (!item || typeof item !== "object") return false;
        const candidate = item as Partial<SearchResult>;
        return typeof candidate.symbol === "string" && typeof candidate.company_name === "string";
      })
      .slice(0, 5);
  } catch {
    return [];
  }
}

export function StockSearch({
  variant = "compact",
  shortcutEnabled = true,
}: {
  variant?: "compact" | "hero";
  shortcutEnabled?: boolean;
}) {
  const router = useRouter();
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [recent, setRecent] = useState<SearchResult[]>([]);
  const [status, setStatus] = useState<SearchStatus>("idle");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    setRecent(readRecent());
  }, []);

  useEffect(() => {
    if (!shortcutEnabled) return;
    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [shortcutEnabled]);

  useEffect(() => {
    const handlePointer = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handlePointer);
    return () => document.removeEventListener("mousedown", handlePointer);
  }, []);

  useEffect(() => {
    const normalized = query.trim();
    setActiveIndex(0);
    if (!normalized) {
      setResults([]);
      setStatus("idle");
      return;
    }

    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      setStatus("loading");
      fetch(`/api/stocks/search?q=${encodeURIComponent(normalized)}`, {
        cache: "no-store",
        signal: controller.signal,
      })
        .then(async (response) => {
          if (!response.ok) throw new Error(`Search failed: ${response.status}`);
          const payload = (await response.json()) as unknown;
          if (!Array.isArray(payload)) return [];
          return payload.filter((item): item is SearchResult => {
            if (!item || typeof item !== "object") return false;
            const candidate = item as Partial<SearchResult>;
            return typeof candidate.symbol === "string" && typeof candidate.company_name === "string";
          });
        })
        .then((items) => {
          setResults(items.slice(0, 10));
          setStatus("ready");
          setOpen(true);
          items.slice(0, 5).forEach((item) => router.prefetch(`/stock/${item.symbol}`));
        })
        .catch((error: unknown) => {
          if (error instanceof DOMException && error.name === "AbortError") return;
          setResults([]);
          setStatus("error");
          setOpen(true);
        });
    }, 160);

    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [query, router]);

  const visibleItems = useMemo(() => (query.trim() ? results : recent), [query, recent, results]);

  const choose = (item: SearchResult) => {
    const nextRecent = [item, ...recent.filter((entry) => entry.symbol !== item.symbol)].slice(0, 5);
    setRecent(nextRecent);
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(nextRecent));
    setQuery("");
    setOpen(false);
    router.push(`/stock/${encodeURIComponent(item.symbol)}`);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => Math.min(current + 1, Math.max(0, visibleItems.length - 1)));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((current) => Math.max(0, current - 1));
      return;
    }
    if (event.key === "Enter" && visibleItems[activeIndex]) {
      event.preventDefault();
      choose(visibleItems[activeIndex]);
      return;
    }
    if (event.key === "Escape") {
      setOpen(false);
      inputRef.current?.blur();
    }
  };

  const isHero = variant === "hero";

  return (
    <div ref={rootRef} className="relative w-full" data-testid="global-stock-search">
      <div
        className={`relative flex items-center border bg-background transition-colors focus-within:border-accent-primary-light ${
          isHero ? "rounded-xl px-4 py-1 shadow-sm" : "rounded-lg"
        }`}
      >
        <Search
          className={`pointer-events-none absolute text-text-secondary ${isHero ? "left-4 size-5" : "left-3 size-4"}`}
          aria-hidden="true"
        />
        <input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder={isHero ? "Search NABIL, HDL, Nepal Bank..." : "Search stocks..."}
          aria-label="Search stocks"
          aria-expanded={open}
          aria-controls="stock-search-results"
          aria-autocomplete="list"
          className={`w-full bg-transparent text-text-primary outline-none placeholder:text-text-secondary ${
            isHero ? "py-3 pl-8 pr-16 text-base" : "py-2 pl-9 pr-14 text-sm"
          }`}
        />
        <div className="absolute right-2 flex items-center gap-1">
          {status === "loading" ? <LoaderCircle className="size-4 animate-spin text-text-secondary" aria-hidden="true" /> : null}
          {query ? (
            <button
              type="button"
              onClick={() => {
                setQuery("");
                inputRef.current?.focus();
              }}
              className="flex size-7 items-center justify-center rounded-md text-text-secondary hover:bg-card hover:text-text-primary"
              aria-label="Clear stock search"
            >
              <X className="size-4" aria-hidden="true" />
            </button>
          ) : shortcutEnabled ? (
            <span className="hidden rounded border border-border px-1.5 py-0.5 text-[10px] font-medium text-text-secondary sm:inline">⌘K</span>
          ) : null}
        </div>
      </div>

      {open && (query.trim() || recent.length > 0) ? (
        <div
          id="stock-search-results"
          role="listbox"
          className="absolute left-0 right-0 top-full z-50 mt-2 overflow-hidden rounded-xl border border-border bg-card shadow-2xl"
        >
          {!query.trim() && recent.length > 0 ? (
            <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-[11px] font-medium uppercase tracking-[0.12em] text-text-secondary">
              <Clock3 className="size-3.5" aria-hidden="true" /> Recent stocks
            </div>
          ) : null}

          {status === "error" && query.trim() ? (
            <div className="px-4 py-5 text-sm text-text-secondary">Search is temporarily unavailable. Try again in a moment.</div>
          ) : null}

          {status === "ready" && query.trim() && results.length === 0 ? (
            <div className="px-4 py-5 text-sm text-text-secondary">No active NEPSE stock matched “{query.trim()}”.</div>
          ) : null}

          {visibleItems.length > 0 ? (
            <div className="max-h-80 overflow-y-auto p-1.5">
              {visibleItems.map((item, index) => (
                <button
                  key={item.symbol}
                  type="button"
                  role="option"
                  aria-selected={index === activeIndex}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => choose(item)}
                  className={`grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors ${
                    index === activeIndex ? "bg-background" : "hover:bg-background"
                  }`}
                >
                  <span className="flex size-9 items-center justify-center rounded-lg border border-border bg-card text-xs font-bold text-accent-text">
                    {item.symbol.slice(0, 3)}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-semibold text-text-primary">{item.symbol}</span>
                    <span className="block truncate text-xs text-text-secondary">{item.company_name}</span>
                  </span>
                  <ArrowRight className="size-4 text-text-secondary" aria-hidden="true" />
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
