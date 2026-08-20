function SkeletonCard({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-xl border border-border bg-card ${className}`} />;
}

export default function MarketIntelligenceLoading() {
  return (
    <div className="flex flex-col gap-8" aria-label="Loading market intelligence">
      <SkeletonCard className="h-16" />
      <div className="space-y-3">
        <div className="h-3 w-36 animate-pulse rounded bg-border" />
        <div className="h-8 w-64 animate-pulse rounded bg-border" />
        <div className="h-4 w-full max-w-2xl animate-pulse rounded bg-border" />
      </div>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => <SkeletonCard key={index} className="h-24" />)}
      </div>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <SkeletonCard className="h-80 xl:col-span-2" />
        <SkeletonCard className="h-80" />
      </div>
      <SkeletonCard className="h-96" />
    </div>
  );
}
