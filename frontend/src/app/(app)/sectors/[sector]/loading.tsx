export default function SectorDetailLoading() {
  return (
    <div className="flex animate-pulse flex-col gap-7" aria-label="Loading sector details">
      <div className="h-5 w-28 rounded bg-border" />

      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="grid gap-6 px-5 py-6 sm:px-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.65fr)]">
          <div>
            <div className="h-6 w-40 rounded-full bg-border" />
            <div className="mt-4 h-9 w-64 rounded bg-border" />
            <div className="mt-3 h-4 w-full max-w-xl rounded bg-border" />
            <div className="mt-2 h-4 w-4/5 max-w-lg rounded bg-border" />
          </div>
          <div className="h-32 rounded-xl bg-background" />
        </div>
        <div className="grid border-t border-border sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="h-28 border-b border-border p-5 sm:border-r xl:border-b-0">
              <div className="h-3 w-24 rounded bg-border" />
              <div className="mt-4 h-7 w-20 rounded bg-border" />
            </div>
          ))}
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.55fr)]">
        <div className="h-72 rounded-2xl border border-border bg-card" />
        <div className="h-72 rounded-2xl border border-border bg-card" />
      </section>

      <section className="rounded-2xl border border-border bg-card p-5">
        <div className="h-5 w-48 rounded bg-border" />
        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, index) => (
            <div key={index} className="h-24 rounded-xl border border-border bg-background" />
          ))}
        </div>
      </section>
    </div>
  );
}
