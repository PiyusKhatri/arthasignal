export default function DashboardLoading() {
  return (
    <div className="mx-auto flex w-full max-w-[1600px] animate-pulse flex-col gap-6">
      <div className="h-10 rounded-xl border border-border bg-card" />
      <div className="rounded-2xl border border-border bg-card p-6">
        <div className="h-3 w-40 rounded bg-border" />
        <div className="mt-4 h-8 w-3/4 rounded bg-border" />
        <div className="mt-3 h-4 w-2/3 rounded bg-border" />
        <div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="h-24 rounded-xl bg-background" />
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="h-28 rounded-xl border border-border bg-card" />
        ))}
      </div>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(300px,0.55fr)]">
        <div className="h-[420px] rounded-2xl border border-border bg-card" />
        <div className="h-[420px] rounded-2xl border border-border bg-card" />
      </div>
    </div>
  );
}
