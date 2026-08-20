function SkeletonCard({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-xl border border-border bg-card ${className}`} />;
}

export default function StockIntelligenceLoading() {
  return (
    <div className="flex flex-col gap-8" aria-label="Loading stock intelligence">
      <SkeletonCard className="h-28" />
      <div className="space-y-3">
        <div className="h-3 w-32 animate-pulse rounded bg-border" />
        <div className="h-7 w-56 animate-pulse rounded bg-border" />
      </div>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <SkeletonCard className="h-64" />
        <SkeletonCard className="h-64" />
      </div>
      <SkeletonCard className="h-56" />
      <SkeletonCard className="h-96" />
    </div>
  );
}
