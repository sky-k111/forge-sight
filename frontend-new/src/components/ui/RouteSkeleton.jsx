export function RouteSkeleton() {
  return (
    <div className="mx-auto grid w-full max-w-[1360px] gap-5 px-8 pb-12 pt-7" role="status" aria-label="页面加载中" aria-live="polite">
      <div className="flex min-h-28 items-end justify-between gap-6">
        <div className="grid flex-1 animate-pulse content-end gap-2.5">
          <i className="h-2 w-28 rounded-full bg-surface-3" />
          <i className="h-7 w-[440px] max-w-full rounded-control bg-surface-3" />
          <i className="h-2 w-[620px] max-w-full rounded-full bg-surface-3" />
        </div>
      </div>
      <div className="grid animate-pulse gap-3.5 lg:grid-cols-3">
        {[1, 2, 3].map((item) => (
          <div key={item} className="grid min-h-44 content-start gap-2.5 rounded-card border border-line bg-surface p-4.5 shadow-card">
            <i className="h-2 w-24 rounded-full bg-surface-3" />
            <i className="my-1.5 h-6 w-32 rounded-control bg-surface-3" />
            <i className="h-2 w-full rounded-full bg-surface-3" />
            <i className="h-2 w-3/4 rounded-full bg-surface-3" />
          </div>
        ))}
      </div>
      <span className="sr-only">正在加载页面内容</span>
    </div>
  );
}
