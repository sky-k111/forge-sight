import { useEffect, useMemo, useState } from "react";

import {
  createAnnouncement,
  listAnnouncements,
  publishAnnouncement,
  withdrawAnnouncement,
} from "../../api/messages";
import {
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  PageFrame,
  StatusPill,
} from "../../components/ui";
import Icon from "../../components/ui/Icon";

const STATUS = {
  draft: ["草稿", "gray"],
  published: ["已发布", "green"],
  withdrawn: ["已撤回", "red"],
  expired: ["已过期", "amber"],
};

export default function AnnouncementsPage({ embedded = false }) {
  const [items, setItems] = useState(null);
  const [filter, setFilter] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState({
    title: "",
    body: "",
    priority: "normal",
    scope: "all",
    target: "",
    isPinned: false,
    validFrom: "",
    expiresAt: "",
  });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");

  const load = async () => {
    setError(null);
    try {
      setItems(await listAnnouncements(filter));
    } catch (nextError) {
      setError(nextError);
    }
  };
  useEffect(() => {
    load();
  }, [filter]); // eslint-disable-line react-hooks/exhaustive-deps
  const estimate = useMemo(
    () =>
      form.scope === "all"
        ? "全部启用用户"
        : form.target.trim()
          ? `指定 ${form.target.split(",").filter(Boolean).length} 个对象`
          : "尚未指定接收范围",
    [form],
  );

  const submit = async (event) => {
    event.preventDefault();
    setNotice("");
    if (!form.title.trim() || !form.body.trim()) {
      setNotice("请填写公告标题和正文");
      return;
    }
    const audienceKey = {
      user: "userIds",
      organization: "organizationIds",
      role: "businessRoles",
    }[form.scope];
    const audience =
      form.scope === "all"
        ? { all: true }
        : {
            [audienceKey]: form.target
              .split(",")
              .map((item) => item.trim())
              .filter(Boolean),
          };
    setBusy("create");
    try {
      const created = await createAnnouncement({
        title: form.title.trim(),
        body: form.body.trim(),
        priority: form.priority,
        isPinned: form.isPinned,
        audience,
        validFrom: form.validFrom
          ? new Date(form.validFrom).toISOString()
          : null,
        expiresAt: form.expiresAt
          ? new Date(form.expiresAt).toISOString()
          : null,
      });
      setItems((current) => [created, ...(current ?? [])]);
      setFormOpen(false);
      setNotice("公告草稿已保存，可在确认影响范围后发布。");
    } catch (nextError) {
      setNotice(nextError.message);
    } finally {
      setBusy("");
    }
  };

  const act = async (key, operation) => {
    setBusy(key);
    setNotice("");
    try {
      await operation();
      await load();
      setNotice(
        key.startsWith("publish")
          ? "公告已发布并进入消息与邮件队列。"
          : "公告已撤回。",
      );
    } catch (nextError) {
      setNotice(nextError.message);
    } finally {
      setBusy("");
    }
  };
  if (error) {
    const errorView = <ErrorNotice title="公告加载失败" detail={error.message} onRetry={load} />;
    return embedded ? <div className="grid gap-4">{errorView}</div> : <PageFrame>{errorView}</PageFrame>;
  }

  const body = (
    <>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <p className="min-w-0 text-[13px] text-muted">以纯文本安全发布到站内消息，并同步排入邮件队列。</p>
        <Button
          variant="primary"
          onClick={() => setFormOpen((value) => !value)}
        >
          <Icon name={formOpen ? "close" : "plus"} size={15} />
          {formOpen ? "收起表单" : "创建公告"}
        </Button>
      </header>
      {notice && (
        <p
          className="rounded-control border border-line bg-surface px-3 py-2 text-sm text-ink-2"
          role="status"
        >
          {notice}
        </p>
      )}
      {formOpen && (
        <Card className="overflow-hidden">
          <form
            onSubmit={submit}
            className="grid gap-5 p-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(280px,0.8fr)]"
          >
            <div className="grid gap-4">
              <Field label="公告标题">
                <input
                  className="input-control h-10 text-sm"
                  value={form.title}
                  onChange={(event) =>
                    setForm({ ...form, title: event.target.value })
                  }
                  maxLength={200}
                />
              </Field>
              <Field label="公告正文">
                <textarea
                  className="input-control min-h-40 resize-y py-3 text-base leading-7"
                  value={form.body}
                  onChange={(event) =>
                    setForm({ ...form, body: event.target.value })
                  }
                  maxLength={10000}
                />
              </Field>
            </div>
            <aside className="grid content-start gap-4 rounded-section bg-surface-muted p-4">
              <div className="grid grid-cols-2 gap-3">
                <Field label="优先级">
                  <select
                    className="input-control h-10"
                    value={form.priority}
                    onChange={(event) =>
                      setForm({ ...form, priority: event.target.value })
                    }
                  >
                    <option value="normal">一般</option>
                    <option value="high">重要</option>
                    <option value="urgent">紧急</option>
                  </select>
                </Field>
                <Field label="接收范围">
                  <select
                    className="input-control h-10"
                    value={form.scope}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        scope: event.target.value,
                        target: "",
                      })
                    }
                  >
                    <option value="all">全部用户</option>
                    <option value="user">指定用户</option>
                    <option value="organization">指定组织</option>
                    <option value="role">指定角色</option>
                  </select>
                </Field>
              </div>
              {form.scope !== "all" && (
                <Field label="对象 ID（逗号分隔）">
                  <input
                    className="input-control h-10"
                    value={form.target}
                    onChange={(event) =>
                      setForm({ ...form, target: event.target.value })
                    }
                  />
                </Field>
              )}
              <div className="grid grid-cols-2 gap-3">
                <Field label="生效时间">
                  <input
                    type="datetime-local"
                    className="input-control h-10"
                    value={form.validFrom}
                    onChange={(event) =>
                      setForm({ ...form, validFrom: event.target.value })
                    }
                  />
                </Field>
                <Field label="失效时间">
                  <input
                    type="datetime-local"
                    className="input-control h-10"
                    value={form.expiresAt}
                    onChange={(event) =>
                      setForm({ ...form, expiresAt: event.target.value })
                    }
                  />
                </Field>
              </div>
              <label className="flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={form.isPinned}
                  onChange={(event) =>
                    setForm({ ...form, isPinned: event.target.checked })
                  }
                />
                在消息中心置顶
              </label>
              <div className="rounded-control border border-line bg-paper p-3">
                <span className="text-[12px] font-semibold text-muted">
                  发布影响摘要
                </span>
                <strong className="mt-1 block text-base text-ink">
                  {estimate}
                </strong>
                <p className="mt-1 text-[13px] leading-5 text-muted">
                  符合范围的启用用户会收到站内消息；已验证邮箱的用户还会收到邮件。
                </p>
              </div>
              <Button variant="primary" type="submit" disabled={Boolean(busy)}>
                {busy === "create" ? "正在保存…" : "保存公告草稿"}
              </Button>
            </aside>
          </form>
        </Card>
      )}
      <Card className="overflow-hidden">
        <div className="flex items-center gap-3 border-b border-line p-3">
          <select
            className="input-control h-9 w-auto"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="">全部状态</option>
            <option value="draft">草稿</option>
            <option value="published">已发布</option>
            <option value="withdrawn">已撤回</option>
          </select>
        </div>
        {items?.length ? (
          <div className="divide-y divide-line">
            {items.map((item) => (
              <AnnouncementRow
                key={item.id}
                item={item}
                busy={busy}
                onPublish={() =>
                  act(`publish-${item.id}`, () => publishAnnouncement(item.id))
                }
                onWithdraw={() => {
                  const reason = window.prompt("请输入撤回原因");
                  if (reason?.trim())
                    act(`withdraw-${item.id}`, () =>
                      withdrawAnnouncement(item.id, reason.trim()),
                    );
                }}
              />
            ))}
          </div>
        ) : (
          <EmptyState
            title="还没有公告"
            description="创建一条公告草稿，确认接收范围后再发布。"
          />
        )}
      </Card>
    </>
  );
  return embedded ? <div className="grid gap-4">{body}</div> : <PageFrame className="gap-4">{body}</PageFrame>;
}

function Field({ label, children }) {
  return (
    <label className="grid gap-1.5 text-[13px] font-semibold text-ink">
      {label}
      {children}
    </label>
  );
}
function AnnouncementRow({ item, busy, onPublish, onWithdraw }) {
  const [label, tone] = STATUS[item.status] ?? [item.status, "gray"];
  return (
    <article className="grid gap-4 px-4 py-4 md:grid-cols-[minmax(0,1fr)_auto]">
      <div>
        <span className="flex flex-wrap items-center gap-2">
          <StatusPill tone={tone}>{label}</StatusPill>
          <StatusPill
            tone={
              item.priority === "urgent"
                ? "red"
                : item.priority === "high"
                  ? "amber"
                  : "gray"
            }
          >
            {item.priority === "urgent"
              ? "紧急"
              : item.priority === "high"
                ? "重要"
                : "一般"}
          </StatusPill>
          {item.isPinned && (
            <span className="text-[12px] font-semibold text-primary">置顶</span>
          )}
        </span>
        <h3 className="mt-2 text-base font-semibold text-ink">{item.title}</h3>
        <p className="mt-1 line-clamp-2 text-sm leading-6 text-muted">
          {item.body}
        </p>
        <p className="mt-2 text-[12px] text-muted">
          预计接收 {item.estimatedRecipientCount ?? 0} 人 · 修订 {item.revision}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {item.status === "draft" && (
          <Button
            variant="primary"
            disabled={Boolean(busy)}
            onClick={onPublish}
          >
            发布公告
          </Button>
        )}
        {item.status === "published" && (
          <Button
            variant="danger"
            disabled={Boolean(busy)}
            onClick={onWithdraw}
          >
            撤回公告
          </Button>
        )}
      </div>
    </article>
  );
}
