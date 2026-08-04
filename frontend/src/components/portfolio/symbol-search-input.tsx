"use client";

import { useEffect, useRef, useState } from "react";

type SymbolResult = { symbol: string; company_name: string };

export function SymbolSearchInput({
  value,
  onSelect,
  error,
}: {
  value: string;
  onSelect: (symbol: string) => void;
  error?: string | null;
}) {
  const [query, setQuery] = useState(value);
  const [results, setResults] = useState<SymbolResult[]>([]);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  function handleChange(next: string) {
    const upper = next.toUpperCase();
    setQuery(upper);
    onSelect(upper);
    if (upper.trim().length === 0) {
      setResults([]);
      setOpen(false);
    }
  }

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length === 0) {
      return;
    }

    let cancelled = false;
    const timer = setTimeout(() => {
      fetch(`/api/stocks/search?q=${encodeURIComponent(trimmed)}`)
        .then((res) => (res.ok ? res.json() : []))
        .then((data: SymbolResult[]) => {
          if (!cancelled) {
            setResults(data);
            setOpen(true);
          }
        })
        .catch(() => {
          if (!cancelled) {
            setResults([]);
          }
        });
    }, 200);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="mb-4" ref={containerRef}>
      <label htmlFor="symbol-search" className="mb-1 block text-sm text-text-secondary">
        Symbol
      </label>
      <div className="relative">
        <input
          id="symbol-search"
          type="text"
          value={query}
          onChange={(event) => handleChange(event.target.value)}
          onFocus={() => {
            if (results.length > 0) setOpen(true);
          }}
          placeholder="e.g. NABIL"
          autoComplete="off"
          aria-invalid={error ? true : undefined}
          className={`w-full rounded-md border bg-background px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-accent-primary ${
            error ? "border-danger" : "border-border"
          }`}
        />
        {open && results.length > 0 ? (
          <ul className="absolute z-10 mt-1 max-h-56 w-full overflow-y-auto rounded-md border border-border bg-card shadow-lg">
            {results.map((result) => (
              <li key={result.symbol}>
                <button
                  type="button"
                  onClick={() => {
                    setQuery(result.symbol);
                    onSelect(result.symbol);
                    setOpen(false);
                  }}
                  className="flex w-full flex-col items-start px-3 py-2 text-left text-sm hover:bg-background"
                >
                  <span className="font-medium text-text-primary">{result.symbol}</span>
                  <span className="text-xs text-text-secondary">{result.company_name}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      {error ? <p className="mt-1 text-xs text-danger-text">{error}</p> : null}
    </div>
  );
}
