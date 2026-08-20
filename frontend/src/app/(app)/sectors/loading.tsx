export default function SectorsLoading() {
  return (
    <div className="flex flex-col gap-7" aria-label="Loading sector intelligence">
      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="px-5 py-6 sm:px-6">
          <div className="h-4 w-36 animate-pulse rounded bg-border" />
          <div className="mt-4 h-8 w-64 animate-pulse rounded bg-border" />
          <div className="mt-3 h-4 max-w-2xl animate-pulse rounded bg-border" />
        </div>
        <div className="grid border-t border-border sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="p-5">
              <div className="h-3 w-28 animate-pulse rounded bg-border" />
              <div className="mt-3 h-7 w-32 animate-pulse rounded bg-border" />
              <div className="mt-2 h-3 w-40 animate-pulse rounded bg-border" />
            </div>
          ))}
        </div>
      </section>

      <section>
        <div className="h-6 w-52 animate-pulse rounded bg-border" />
        <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <div key={index} className="h-56 animate-pulse rounded-2xl border border-border bg-card" />
          ))}
        </div>
      </section>
    </div>
  );
}
