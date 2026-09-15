import { useState } from "react";
import Icon from "../../components/ui/Icon";
import cn from "../../lib/cn";
import { PageFrame } from "../../components/ui";
import AnnouncementsPage from "./AnnouncementsPage";
import DeliveriesPage from "./DeliveriesPage";

const tabs = [
  { id: "announcements", label: "公告管理", icon: "message" },
  { id: "deliveries", label: "投递记录", icon: "send" },
];

export default function MessagingPage() {
  const [tab, setTab] = useState("announcements");

  return (
    <PageFrame className="max-w-[1360px] gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-primary-soft text-primary"><Icon name="send" size={16} /></span>
          <div className="min-w-0">
            <h2 className="text-sm font-semibold tracking-tight text-ink">消息与投递</h2>
            <p className="mt-0.5 text-[11px] text-muted">站内公告的创建发布，与周报、告警的邮件投递状态集中管理</p>
          </div>
        </div>
        <div className="flex shrink-0 rounded-control bg-surface-2 p-0.5" role="tablist" aria-label="消息与投递视图">
          {tabs.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              onClick={() => setTab(item.id)}
              className={cn(
                "flex h-7 cursor-pointer items-center gap-1.5 rounded-tag px-2.5 text-xs font-medium transition-colors",
                tab === item.id ? "bg-surface text-ink shadow-card" : "text-muted hover:text-ink",
              )}
            >
              <Icon name={item.icon} size={13} />
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {tab === "announcements" ? <AnnouncementsPage embedded /> : <DeliveriesPage embedded />}
    </PageFrame>
  );
}
