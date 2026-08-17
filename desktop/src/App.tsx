import {
  BookOpen,
  Bot,
  Check,
  ChevronDown,
  CircleAlert,
  Database,
  Download,
  ExternalLink,
  FileSearch,
  FileText,
  FolderSync,
  Import,
  KeyRound,
  LibraryBig,
  LoaderCircle,
  Menu,
  MessageSquareText,
  Paperclip,
  Play,
  Plus,
  RefreshCw,
  Search,
  Save,
  Settings,
  SlidersHorizontal,
  SquareTerminal,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  api,
  ChatMessage,
  ChatSessionSummary,
  ConfigStatus,
  DiscoveryRecord,
  Item,
  Job,
  Library,
  ZoteroProbeStatus,
  ZoteroSyncStatus,
} from "./api";

type View = "library" | "chat" | "import" | "discover" | "jobs" | "settings";

const NAVIGATION: { id: View; label: string; icon: typeof LibraryBig }[] = [
  { id: "library", label: "文献库", icon: LibraryBig },
  { id: "chat", label: "证据对话", icon: MessageSquareText },
  { id: "import", label: "批量导入", icon: Import },
  { id: "discover", label: "在线发现", icon: FileSearch },
  { id: "jobs", label: "任务中心", icon: SlidersHorizontal },
  { id: "settings", label: "设置", icon: Settings },
];

function App() {
  const [view, setView] = useState<View>("library");
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [activeLibraryId, setActiveLibraryId] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [health, setHealth] = useState<"checking" | "online" | "offline">(
    "checking",
  );
  const [error, setError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const activeLibrary =
    libraries.find((library) => library.id === activeLibraryId) ?? null;

  const refresh = useCallback(async () => {
    try {
      const [healthResult, libraryResult, jobResult] = await Promise.all([
        api.health(),
        api.libraries(),
        api.jobs(),
      ]);
      setHealth(healthResult.status === "ok" ? "online" : "offline");
      setLibraries(libraryResult);
      setJobs(jobResult);
      setActiveLibraryId((current) => current || libraryResult[0]?.id || "");
      setError("");
    } catch (reason) {
      setHealth("offline");
      setError(reason instanceof Error ? reason.message : "服务不可用");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!activeLibraryId) {
      setItems([]);
      return;
    }
    api
      .items(activeLibraryId)
      .then(setItems)
      .catch((reason) => setError(String(reason)));
  }, [activeLibraryId, jobs]);

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? "sidebar-open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark">
            <BookOpen size={19} />
          </div>
          <div>
            <strong>ResearchBrain</strong>
            <span>Local research workspace</span>
          </div>
          <button
            className="icon-button sidebar-close"
            onClick={() => setSidebarOpen(false)}
            title="关闭菜单"
          >
            <X size={18} />
          </button>
        </div>

        <div className="library-picker">
          <label htmlFor="library">当前文库</label>
          <div className="select-wrap">
            <Database size={16} />
            <select
              id="library"
              value={activeLibraryId}
              onChange={(event) => setActiveLibraryId(event.target.value)}
            >
              {!libraries.length && <option value="">尚未创建文库</option>}
              {libraries.map((library) => (
                <option key={library.id} value={library.id}>
                  {library.name}
                </option>
              ))}
            </select>
            <ChevronDown size={15} />
          </div>
        </div>

        <nav className="navigation" aria-label="主导航">
          {NAVIGATION.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              className={view === id ? "nav-active" : ""}
              onClick={() => {
                setView(id);
                setSidebarOpen(false);
              }}
            >
              <Icon size={18} />
              <span>{label}</span>
              {id === "jobs" && jobs.some((job) => job.status === "failed") && (
                <i className="nav-alert" />
              )}
            </button>
          ))}
        </nav>

        <div className="sidebar-status">
          <span className={`status-dot ${health}`} />
          <span>
            {health === "online"
              ? "本地服务已连接"
              : health === "checking"
                ? "正在检查服务"
                : "本地服务离线"}
          </span>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <button
            className="icon-button menu-button"
            onClick={() => setSidebarOpen(true)}
            title="打开菜单"
          >
            <Menu size={19} />
          </button>
          <div>
            <h1>{NAVIGATION.find((entry) => entry.id === view)?.label}</h1>
            <p>{activeLibrary?.name ?? "选择或创建一个文库"}</p>
          </div>
          <button
            className="icon-button"
            onClick={() => void refresh()}
            title="刷新"
          >
            <RefreshCw size={18} />
          </button>
        </header>

        {error && (
          <div className="error-banner">
            <CircleAlert size={17} />
            <span>{error}</span>
            <button onClick={() => setError("")}>
              <X size={16} />
            </button>
          </div>
        )}

        <div
          className={`content-area ${view === "chat" ? "chat-content-area" : ""}`}
        >
          {!activeLibrary && view !== "settings" ? (
            <FirstLibrary
              onCreated={(library) => {
                setLibraries([library]);
                setActiveLibraryId(library.id);
              }}
            />
          ) : (
            <>
              {view === "library" && (
                <LibraryView
                  items={items}
                  onNavigate={setView}
                  onError={setError}
                  onRefresh={() =>
                    activeLibraryId && api.items(activeLibraryId).then(setItems)
                  }
                />
              )}
              {view === "chat" && activeLibrary && (
                <ChatView library={activeLibrary} onImported={refresh} />
              )}
              {view === "import" && activeLibrary && (
                <ImportView library={activeLibrary} onQueued={refresh} />
              )}
              {view === "discover" && activeLibrary && (
                <DiscoveryView library={activeLibrary} onQueued={refresh} />
              )}
              {view === "jobs" && <JobsView jobs={jobs} onRefresh={refresh} />}
              {view === "settings" && (
                <SettingsView
                  libraries={libraries}
                  onRefresh={refresh}
                  onCreated={(library) =>
                    setLibraries((values) => [...values, library])
                  }
                />
              )}
            </>
          )}
        </div>
      </main>
      {sidebarOpen && (
        <button
          className="scrim"
          onClick={() => setSidebarOpen(false)}
          aria-label="关闭菜单"
        />
      )}
    </div>
  );
}

function FirstLibrary({
  onCreated,
}: {
  onCreated: (library: Library) => void;
}) {
  const [name, setName] = useState("我的文献库");
  const [busy, setBusy] = useState(false);
  const create = async () => {
    setBusy(true);
    try {
      onCreated(await api.createLibrary(name, "standalone"));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="empty-state">
      <Database size={28} />
      <h2>创建本地文献库</h2>
      <input value={name} onChange={(e) => setName(e.target.value)} />
      <button
        className="primary-button"
        onClick={() => void create()}
        disabled={busy}
      >
        {busy ? (
          <LoaderCircle className="spin" size={17} />
        ) : (
          <Plus size={17} />
        )}
        创建文库
      </button>
    </section>
  );
}

function LibraryView({
  items,
  onRefresh,
  onNavigate,
  onError,
}: {
  items: Item[];
  onRefresh: () => void;
  onNavigate: (view: View) => void;
  onError: (message: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [viewer, setViewer] = useState<{ url: string; title: string } | null>(
    null,
  );
  const visible = useMemo(
    () =>
      items.filter((item) =>
        `${item.title} ${item.container_title} ${item.year ?? ""} ${item.doi}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      ),
    [items, query],
  );
  const exportSelected = async (format: string) => {
    const result = await api.exportReferences(selected, format);
    const blob = new Blob([result.content], { type: result.mime });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = result.filename;
    link.click();
    URL.revokeObjectURL(link.href);
  };
  const showBlob = (blob: Blob, title: string) => {
    setViewer((current) => {
      if (current) URL.revokeObjectURL(current.url);
      return { url: URL.createObjectURL(blob), title };
    });
  };
  const openPdf = async (item: Item) => {
    if (item.pdf_status !== "ready") {
      document.getElementById(`pdf-input-${item.id}`)?.click();
      return;
    }
    try {
      const attachment = (await api.attachments(item.id)).find(
        (value) => value.status === "stored",
      );
      if (!attachment) throw new Error("没有找到已保存的 PDF 文件");
      showBlob(
        await api.attachmentContent(attachment.id),
        attachment.logical_name,
      );
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason));
    }
  };
  const openArtifact = async (item: Item) => {
    if (item.parse_status !== "ready") {
      onNavigate("jobs");
      return;
    }
    try {
      const artifact = (await api.artifacts(item.id))[0];
      if (!artifact) throw new Error("没有找到解析后的 Markdown");
      showBlob(
        await api.artifactMarkdown(artifact.id),
        `${item.title} · Markdown`,
      );
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : String(reason));
    }
  };
  return (
    <section className="data-view">
      <div className="toolbar">
        <div className="search-field">
          <Search size={17} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="按标题、期刊或年份筛选"
          />
        </div>
        <button className="secondary-button" onClick={onRefresh}>
          <RefreshCw size={16} />
          刷新
        </button>
        <div className="export-menu">
          <Download size={16} />
          <select
            disabled={!selected.length}
            defaultValue=""
            onChange={(e) => {
              if (e.target.value) void exportSelected(e.target.value);
              e.target.value = "";
            }}
          >
            <option value="">导出</option>
            <option value="csl-json">CSL-JSON</option>
            <option value="bibtex">BibTeX</option>
            <option value="ris">RIS</option>
            <option value="doi">DOI 列表</option>
            <option value="markdown">Markdown</option>
          </select>
        </div>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th className="check-column">
                <input
                  type="checkbox"
                  checked={
                    visible.length > 0 && selected.length === visible.length
                  }
                  onChange={(e) =>
                    setSelected(
                      e.target.checked ? visible.map((item) => item.id) : [],
                    )
                  }
                />
              </th>
              <th>题名</th>
              <th>年份</th>
              <th>来源</th>
              <th>PDF / 解析 / 摘要 / 全文</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((item) => (
              <tr key={item.id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selected.includes(item.id)}
                    onChange={(e) =>
                      setSelected((values) =>
                        e.target.checked
                          ? [...values, item.id]
                          : values.filter((id) => id !== item.id),
                      )
                    }
                  />
                </td>
                <td>
                  <strong>{item.title}</strong>
                  <span
                    className="item-subtitle"
                    title={`${item.canonical_key} · ${item.knowledge_state}`}
                  >
                    {item.type}
                    {item.doi ? ` · DOI ${item.doi}` : ""}
                  </span>
                </td>
                <td>{item.year ?? "-"}</td>
                <td>{item.container_title || "-"}</td>
                <td>
                  <div className="pipeline-statuses">
                    <PipelineIndicator
                      icon={Paperclip}
                      label="PDF"
                      value={item.pdf_status}
                      onClick={() => void openPdf(item)}
                    />
                    <PipelineIndicator
                      icon={FileText}
                      label="解析"
                      value={item.parse_status}
                      onClick={() => void openArtifact(item)}
                    />
                    <PipelineIndicator
                      icon={Database}
                      label="摘要"
                      value={item.metadata_embedding_status}
                      onClick={() => onNavigate("chat")}
                    />
                    <PipelineIndicator
                      icon={Database}
                      label="全文"
                      value={item.fulltext_embedding_status}
                      onClick={() => onNavigate("chat")}
                    />
                  </div>
                  <input
                    id={`pdf-input-${item.id}`}
                    className="hidden-file-input"
                    type="file"
                    accept="application/pdf,.pdf"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file)
                        void api
                          .uploadAttachment(item.id, file)
                          .then(onRefresh)
                          .catch((reason) =>
                            onError(
                              reason instanceof Error
                                ? reason.message
                                : String(reason),
                            ),
                          );
                      event.target.value = "";
                    }}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!visible.length && (
          <div className="table-empty">当前筛选下没有文献</div>
        )}
      </div>
      {viewer && (
        <div className="document-viewer" role="dialog" aria-modal="true">
          <div className="document-viewer-bar">
            <strong>{viewer.title}</strong>
            <button
              className="icon-button"
              onClick={() => {
                URL.revokeObjectURL(viewer.url);
                setViewer(null);
              }}
              title="关闭阅读器"
            >
              <X size={18} />
            </button>
          </div>
          <iframe src={viewer.url} title={viewer.title} />
        </div>
      )}
    </section>
  );
}

function PipelineIndicator({
  icon: Icon,
  label,
  value,
  onClick,
}: {
  icon: typeof Paperclip;
  label: string;
  value: string;
  onClick: () => void;
}) {
  const display =
    value === "ready"
      ? "完成"
      : value === "none"
        ? label === "摘要" || label === "全文"
          ? "待生成"
          : "无"
        : value === "queued"
          ? "排队"
          : value === "running"
            ? "处理中"
            : value === "retry_wait"
              ? "待重试"
              : value === "review_required"
                ? "待处理"
                : value === "missing"
                  ? "缺失"
                  : value === "failed"
                    ? "失败"
                    : "待处理";
  return (
    <button
      type="button"
      className={`pipeline-indicator ${value}`}
      title={`${label}：${display}，点击操作`}
      onClick={onClick}
    >
      <Icon size={13} />
      <span className="pipeline-copy">
        <strong>{label}</strong>
        <small>{display}</small>
      </span>
    </button>
  );
}

type ChatEvidence = ChatMessage["citations"][number];

function evidenceLocation(evidence: ChatEvidence) {
  if (evidence.source_kind === "online")
    return `在线摘要 · ${evidence.source_name}`;
  const section = evidence.section?.trim();
  if (evidence.chunk_id.startsWith("metadata:") || section === "题录与摘要") {
    return "题录/摘要";
  }
  if (evidence.page_start !== null) {
    if (
      evidence.page_end !== null &&
      evidence.page_end !== evidence.page_start
    ) {
      return `第 ${evidence.page_start}-${evidence.page_end} 页`;
    }
    return `第 ${evidence.page_start} 页`;
  }
  return section || "正文片段";
}

function evidenceDetailLocation(evidence: ChatEvidence) {
  const location = evidenceLocation(evidence);
  const section = evidence.section?.trim();
  if (evidence.page_start !== null && section && section !== "题录与摘要") {
    return `${section} · ${location}`;
  }
  return location;
}

function AssistantMarkdown({ content }: { content: string }) {
  return (
    <Markdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: (props) => <a {...props} target="_blank" rel="noreferrer" />,
      }}
    >
      {content}
    </Markdown>
  );
}

function ChatView({
  library,
  onImported,
}: {
  library: Library;
  onImported: () => void;
}) {
  const [sessionId, setSessionId] = useState("");
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"local" | "hybrid" | "online">("local");
  const [importingEvidence, setImportingEvidence] = useState(false);
  const [evidenceImportStatus, setEvidenceImportStatus] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<ChatEvidence | null>(
    null,
  );
  useEffect(() => {
    let cancelled = false;
    setLoadingHistory(true);
    setSessionId("");
    setSessions([]);
    setMessages([]);
    setSelectedEvidence(null);
    setEvidenceImportStatus("");
    api
      .chatSessions(library.id)
      .then(async (values) => {
        if (cancelled) return;
        setSessions(values);
        if (values.length) {
          const latestId = values[0].id;
          const restored = await api.messages(latestId);
          if (!cancelled) {
            setSessionId(latestId);
            setMessages(restored);
          }
        }
      })
      .catch(() => {
        if (!cancelled) setSessions([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });
    return () => {
      cancelled = true;
    };
  }, [library.id]);
  const openSession = async (id: string) => {
    if (busy || id === sessionId) return;
    setLoadingHistory(true);
    setSelectedEvidence(null);
    setEvidenceImportStatus("");
    try {
      setMessages(await api.messages(id));
      setSessionId(id);
    } finally {
      setLoadingHistory(false);
    }
  };
  const newConversation = () => {
    if (busy) return;
    setSessionId("");
    setMessages([]);
    setSelectedEvidence(null);
    setEvidenceImportStatus("");
  };
  const selectEvidence = (evidence: ChatEvidence) => {
    setSelectedEvidence(evidence);
    setEvidenceImportStatus("");
  };
  const importOnlineEvidence = async () => {
    const record = selectedEvidence?.discovery_record;
    if (!record || importingEvidence) return;
    setImportingEvidence(true);
    setEvidenceImportStatus("");
    try {
      const result = await api.importDiscovered(library.id, [record]);
      setEvidenceImportStatus(
        result.created > 0
          ? `已加入文库，排队处理 ${result.fulltext_queued} 个开放全文`
          : "文库中已存在，已合并可用元数据",
      );
      onImported();
    } catch (reason) {
      setEvidenceImportStatus(`导入失败：${String(reason)}`);
    } finally {
      setImportingEvidence(false);
    }
  };
  const send = async (event: FormEvent) => {
    event.preventDefault();
    const question = input.trim();
    if (!question || busy) return;
    setInput("");
    setBusy(true);
    setMessages((values) => [
      ...values,
      {
        id: crypto.randomUUID(),
        role: "user",
        content: question,
        citations: [],
        model: "",
      },
    ]);
    try {
      const id = sessionId || (await api.createSession(library.id)).id;
      setSessionId(id);
      if (!sessionId) {
        api
          .chatSessions(library.id)
          .then(setSessions)
          .catch(() => undefined);
      }
      const answer = await api.sendMessage(id, question, mode);
      setMessages((values) => [...values, answer]);
      api
        .chatSessions(library.id)
        .then(setSessions)
        .catch(() => undefined);
    } catch (reason) {
      const raw = reason instanceof Error ? reason.message : String(reason);
      const detail = chatErrorText(raw);
      setMessages((values) => [
        ...values,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: `无法完成本次回答：${detail}\n\n请检查设置中的模型状态，以及任务中心里的解析和向量任务。`,
          citations: [],
          limitations: [detail],
          model: "local-error",
        },
      ]);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="chat-layout">
      <aside className="chat-history">
        <div className="chat-history-header">
          <h2>历史对话</h2>
          <button
            type="button"
            onClick={newConversation}
            disabled={busy}
            title="新对话"
          >
            <Plus size={15} />
            新对话
          </button>
        </div>
        <div className="chat-session-list">
          {loadingHistory && !sessions.length && (
            <div className="chat-history-loading">
              <LoaderCircle className="spin" size={15} />
              正在读取
            </div>
          )}
          {sessions.map((session) => (
            <button
              type="button"
              key={session.id}
              className={session.id === sessionId ? "active" : ""}
              onClick={() => void openSession(session.id)}
              title={session.title}
            >
              <strong>
                {session.title === "New research" ? "新对话" : session.title}
              </strong>
              <span>{session.last_message_preview || "尚无消息"}</span>
              <small>
                {new Date(session.updated_at).toLocaleString("zh-CN", {
                  month: "numeric",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
                {` · ${session.message_count} 条`}
              </small>
            </button>
          ))}
          {!loadingHistory && !sessions.length && (
            <p className="muted">还没有历史对话</p>
          )}
        </div>
      </aside>
      <div className="conversation">
        <div className="message-list">
          {!messages.length && (
            <div className="chat-empty">
              <Bot size={27} />
              <h2>
                {mode === "local" ? "基于本地文献提问" : "本地与在线联合调研"}
              </h2>
              <p>
                {mode === "local"
                  ? "回答将附带可定位到页码的证据。"
                  : "在线摘要与本地全文会使用不同证据标记。"}
              </p>
            </div>
          )}
          {messages.map((message) => (
            <article key={message.id} className={`message ${message.role}`}>
              <div className="message-role">
                {message.role === "user" ? "你" : "ResearchBrain"}
              </div>
              <div className="message-content">
                {message.role === "assistant" ? (
                  <AssistantMarkdown content={message.content} />
                ) : (
                  message.content
                )}
              </div>
              {message.citations?.length > 0 && (
                <div className="citation-row">
                  {message.citations.map((citation) => (
                    <button
                      key={citation.id}
                      className={
                        citation.source_kind === "online" ? "online" : ""
                      }
                      onClick={() => selectEvidence(citation)}
                      title={`${citation.title} · ${evidenceLocation(citation)}`}
                      aria-label={`查看证据 ${citation.id}，${evidenceLocation(citation)}`}
                    >
                      {citation.id} · {evidenceLocation(citation)}
                    </button>
                  ))}
                </div>
              )}
            </article>
          ))}
          {busy && (
            <div className="thinking">
              <LoaderCircle className="spin" size={18} />
              {mode === "local"
                ? "正在准备题录/摘要索引并检索本地证据"
                : "正在生成检索式并查询学术数据源"}
            </div>
          )}
        </div>
        <form className="composer" onSubmit={send}>
          <div className="compact-chat-history">
            <label htmlFor="chat-history-select">历史对话</label>
            <select
              id="chat-history-select"
              value={sessionId}
              onChange={(event) => {
                if (event.target.value) void openSession(event.target.value);
                else newConversation();
              }}
              disabled={busy}
            >
              <option value="">新对话</option>
              {sessions.map((session) => (
                <option key={session.id} value={session.id}>
                  {session.title === "New research" ? "新对话" : session.title}
                </option>
              ))}
            </select>
          </div>
          <div className="research-mode" aria-label="调研范围">
            {(
              [
                ["local", "仅本地"],
                ["hybrid", "本地优先 + 联网"],
                ["online", "全网调研"],
              ] as const
            ).map(([value, label]) => (
              <button
                type="button"
                key={value}
                className={mode === value ? "active" : ""}
                onClick={() => setMode(value)}
                disabled={busy}
              >
                {label}
              </button>
            ))}
          </div>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="询问方法、数据、结果或研究差异"
            rows={3}
          />
          <button
            className="send-button"
            disabled={!input.trim() || busy}
            title="发送"
          >
            <Play size={18} fill="currentColor" />
          </button>
        </form>
      </div>
      <aside className="evidence-panel">
        <h2>证据</h2>
        {selectedEvidence ? (
          <div className="evidence-detail">
            <strong>{selectedEvidence.title}</strong>
            <span>{evidenceDetailLocation(selectedEvidence)}</span>
            <div className="evidence-actions">
              {selectedEvidence.source_url && (
                <a
                  href={selectedEvidence.source_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink size={13} />
                  打开来源
                </a>
              )}
              {selectedEvidence.source_kind === "online" &&
                selectedEvidence.discovery_record && (
                  <button
                    type="button"
                    onClick={() => void importOnlineEvidence()}
                    disabled={importingEvidence}
                    title="将该在线文献导入当前文库并排队处理"
                  >
                    {importingEvidence ? (
                      <LoaderCircle className="spin" size={13} />
                    ) : (
                      <Import size={13} />
                    )}
                    导入文库
                  </button>
                )}
            </div>
            {evidenceImportStatus && (
              <p className="evidence-import-status" aria-live="polite">
                {evidenceImportStatus}
              </p>
            )}
            <p>{selectedEvidence.text}</p>
          </div>
        ) : (
          <p className="muted">点击回答下方的引用查看原文片段。</p>
        )}
      </aside>
    </section>
  );
}

function chatErrorText(message: string) {
  if (message.includes("MiniMax API key is not configured"))
    return "MiniMax API Key 未成功保存，请在设置中重新填写并点击“保存设置”";
  if (message.includes("DeepSeek API key is not configured"))
    return "DeepSeek API Key 未成功保存，请在设置中重新填写并点击“保存设置”";
  if (
    message.includes("MiniMax HTTP 401") ||
    message.includes("MiniMax HTTP 403")
  )
    return "MiniMax 密钥鉴权失败，请检查 API Key 和 Group ID";
  if (
    message.includes("DeepSeek HTTP 401") ||
    message.includes("DeepSeek HTTP 403")
  )
    return "DeepSeek 密钥鉴权失败，请检查 API Key";
  if (message.includes("timed out")) return "模型请求超时，请检查网络后重试";
  return message;
}

function ImportView({
  library,
  onQueued,
}: {
  library: Library;
  onQueued: () => void;
}) {
  const [value, setValue] = useState("");
  const [includeSi, setIncludeSi] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const submit = async () => {
    const dois = value
      .split(/[\n,;]+/)
      .map((v) => v.trim())
      .filter(Boolean);
    if (!dois.length) return;
    setBusy(true);
    try {
      const batch = await api.importDois(library.id, dois, includeSi);
      setResult(
        `已排队 ${batch.total} 条，输入错误 ${batch.input_errors.length} 条`,
      );
      setValue("");
      onQueued();
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="form-page">
      <div className="section-heading">
        <h2>DOI 批量导入</h2>
        <p>元数据、开放全文、解析和向量化将由后台任务依次执行。</p>
      </div>
      <label className="field-label">DOI 列表</label>
      <textarea
        className="doi-input"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={"10.1000/example\nhttps://doi.org/10.1000/another"}
        rows={12}
      />
      <label className="toggle-row">
        <input
          type="checkbox"
          checked={includeSi}
          onChange={(e) => setIncludeSi(e.target.checked)}
        />
        <span className="toggle" />
        <div>
          <strong>同时查找补充材料</strong>
          <span>仅下载有明确开放许可的候选文件</span>
        </div>
      </label>
      <div className="form-actions">
        <button
          className="primary-button"
          onClick={() => void submit()}
          disabled={busy || !value.trim()}
        >
          {busy ? (
            <LoaderCircle className="spin" size={17} />
          ) : (
            <Import size={17} />
          )}
          开始导入
        </button>
        {result && (
          <span className="success-text">
            <Check size={16} />
            {result}
          </span>
        )}
      </div>
    </section>
  );
}

function DiscoveryView({
  library,
  onQueued,
}: {
  library: Library;
  onQueued: () => void;
}) {
  const [query, setQuery] = useState("");
  const [records, setRecords] = useState<DiscoveryRecord[]>([]);
  const [providers, setProviders] = useState<
    Awaited<ReturnType<typeof api.discover>>["providers"]
  >([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [sources, setSources] = useState([
    "crossref",
    "openalex",
    "arxiv",
    "pubmed",
  ]);
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [oaOnly, setOaOnly] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const recordKey = (record: DiscoveryRecord) =>
    record.identifiers.doi ||
    record.identifiers.pmid ||
    record.identifiers.arxiv ||
    `${record.source}:${record.source_id}:${record.title}`;
  const search = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim() || !sources.length) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await api.discover(query, {
        sources,
        yearFrom: yearFrom ? Number(yearFrom) : undefined,
        yearTo: yearTo ? Number(yearTo) : undefined,
        oaOnly,
      });
      setRecords(result.records);
      setProviders(result.providers);
      setSelected([]);
    } catch (reason) {
      setRecords([]);
      setProviders([]);
      setMessage(`搜索失败：${String(reason)}`);
    } finally {
      setBusy(false);
    }
  };
  const importSelected = async () => {
    const chosen = records.filter((record) =>
      selected.includes(recordKey(record)),
    );
    if (!chosen.length) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await api.importDiscovered(library.id, chosen);
      setMessage(
        `已导入 ${result.created} 篇，合并重复 ${result.duplicates} 篇，开放全文任务 ${result.fulltext_queued} 个`,
      );
      setSelected([]);
      onQueued();
    } catch (reason) {
      setMessage(`导入失败：${String(reason)}`);
    } finally {
      setBusy(false);
    }
  };
  const scholarUrl = `https://scholar.google.com/scholar?q=${encodeURIComponent(query.trim())}`;
  return (
    <section className="data-view">
      <form className="discovery-bar" onSubmit={search}>
        <div className="search-field">
          <Search size={17} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="跨 Crossref、OpenAlex、arXiv 和 PubMed 搜索"
          />
        </div>
        <button className="primary-button" disabled={busy}>
          {busy ? (
            <LoaderCircle className="spin" size={17} />
          ) : (
            <Search size={17} />
          )}
          搜索
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={!selected.length}
          onClick={() => void importSelected()}
        >
          <Import size={16} />
          导入并处理 ({selected.length})
        </button>
        <a
          className={`secondary-button scholar-link ${!query.trim() ? "disabled" : ""}`}
          href={query.trim() ? scholarUrl : undefined}
          target="_blank"
          rel="noreferrer"
          aria-disabled={!query.trim()}
        >
          <ExternalLink size={16} />
          Google Scholar
        </a>
      </form>
      <div className="discovery-filters">
        <div className="source-filters">
          {[
            ["crossref", "Crossref"],
            ["openalex", "OpenAlex"],
            ["arxiv", "arXiv"],
            ["pubmed", "PubMed"],
          ].map(([value, label]) => (
            <label key={value}>
              <input
                type="checkbox"
                checked={sources.includes(value)}
                onChange={(event) =>
                  setSources((current) =>
                    event.target.checked
                      ? [...current, value]
                      : current.filter((source) => source !== value),
                  )
                }
              />
              {label}
            </label>
          ))}
        </div>
        <label className="year-filter">
          年份
          <input
            type="number"
            min="1800"
            max="2200"
            value={yearFrom}
            onChange={(event) => setYearFrom(event.target.value)}
            placeholder="起"
          />
          <span>至</span>
          <input
            type="number"
            min="1800"
            max="2200"
            value={yearTo}
            onChange={(event) => setYearTo(event.target.value)}
            placeholder="止"
          />
        </label>
        <label className="oa-filter">
          <input
            type="checkbox"
            checked={oaOnly}
            onChange={(event) => setOaOnly(event.target.checked)}
          />
          仅开放获取
        </label>
      </div>
      {(providers.length > 0 || message) && (
        <div className="provider-statuses" aria-live="polite">
          {providers.map((provider) => (
            <span
              key={provider.source}
              className={provider.status === "complete" ? "complete" : "failed"}
              title={provider.error || `${provider.elapsed_ms} ms`}
            >
              {provider.source} ·{" "}
              {provider.status === "complete"
                ? `${provider.count} 篇`
                : provider.error}
            </span>
          ))}
          {message && <strong>{message}</strong>}
        </div>
      )}
      <div className="result-list">
        {records.map((record) => {
          const key = recordKey(record);
          return (
            <article className="result-row" key={key}>
              <input
                type="checkbox"
                checked={selected.includes(key)}
                onChange={(e) =>
                  setSelected((values) =>
                    e.target.checked
                      ? [...values, key]
                      : values.filter((id) => id !== key),
                  )
                }
              />
              <div>
                <div className="result-meta">
                  {(record.sources || [record.source]).map((source) => (
                    <span key={source}>{source}</span>
                  ))}
                  {record.year && <span>{record.year}</span>}
                  {record.doi && <span>{record.doi}</span>}
                  {record.is_oa && <span className="oa-badge">开放全文</span>}
                </div>
                <h3>
                  <a href={record.url} target="_blank" rel="noreferrer">
                    {record.title}
                  </a>
                </h3>
                <p>
                  {record.authors.slice(0, 5).join(", ")}
                  {record.venue ? ` · ${record.venue}` : ""}
                </p>
                {record.abstract && (
                  <p className="result-abstract">{record.abstract}</p>
                )}
              </div>
            </article>
          );
        })}
        {!records.length && (
          <div className="table-empty">
            {busy
              ? "正在并行查询学术数据源"
              : "输入主题、作者或关键词开始在线发现"}
          </div>
        )}
      </div>
    </section>
  );
}

function JobsView({ jobs, onRefresh }: { jobs: Job[]; onRefresh: () => void }) {
  const retryable = jobs.filter((job) =>
    ["failed", "review_required"].includes(job.status),
  ).length;
  const run = async () => {
    await api.runNextJob();
    onRefresh();
  };
  const retryFailed = async () => {
    await api.retryFailedJobs();
    onRefresh();
  };
  return (
    <section className="data-view">
      <div className="toolbar">
        <button className="primary-button" onClick={() => void run()}>
          <Play size={16} />
          执行下一个任务
        </button>
        <button
          className="secondary-button"
          disabled={!retryable}
          onClick={() => void retryFailed()}
        >
          <RefreshCw size={16} />
          重试失败任务{retryable ? `（${retryable}）` : ""}
        </button>
        <span className="muted">桌面后台服务启用后会自动持续执行</span>
      </div>
      <div className="job-list">
        {jobs.map((job) => (
          <article key={job.id} className="job-row">
            <div className="job-icon">
              {job.status === "complete" ? (
                <Check size={17} />
              ) : job.status === "failed" ? (
                <CircleAlert size={17} />
              ) : (
                <LoaderCircle
                  size={17}
                  className={job.status === "running" ? "spin" : ""}
                />
              )}
            </div>
            <div>
              <strong>{job.job_type.replaceAll("_", " ")}</strong>
              <span>
                {job.error_code
                  ? jobErrorText(job)
                  : String(job.payload.doi ?? job.payload.item_id ?? job.id)}
              </span>
            </div>
            <Status value={job.status} />
            <span className="job-attempt">尝试 {job.attempt}</span>
            {["failed", "review_required", "canceled"].includes(job.status) && (
              <button
                className="icon-button"
                onClick={() => api.retryJob(job.id).then(onRefresh)}
                title="重试"
              >
                <RefreshCw size={16} />
              </button>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function jobErrorText(job: Job) {
  if (job.error_code === "contact_email_missing")
    return "缺少联系邮箱：请在设置中填写邮箱，保存后重试";
  if (job.error_code === "api_key_missing")
    return "缺少模型密钥：请在设置中重新保存对应 API Key";
  if (job.error_code === "no_oa_fulltext")
    return "没有发现可合法自动下载的开放 PDF，可手工添加 PDF";
  return `${job.error_code} · ${job.error_message}`;
}

function SettingsView({
  libraries,
  onCreated,
  onRefresh,
}: {
  libraries: Library[];
  onCreated: (library: Library) => void;
  onRefresh: () => void;
}) {
  const [name, setName] = useState("");
  const [mode, setMode] = useState<Library["mode"]>("standalone");
  const [zotero, setZotero] = useState<ZoteroProbeStatus | null>(null);
  const [zoteroSync, setZoteroSync] = useState<
    Record<string, ZoteroSyncStatus>
  >({});
  const [syncingLibrary, setSyncingLibrary] = useState("");
  const [config, setConfig] = useState<ConfigStatus | null>(null);
  const [contactEmail, setContactEmail] = useState("");
  const [minimaxGroupId, setMinimaxGroupId] = useState("");
  const [zoteroDataDir, setZoteroDataDir] = useState("");
  const [mineruExecutable, setMineruExecutable] = useState("mineru");
  const [minimaxKey, setMinimaxKey] = useState("");
  const [deepseekKey, setDeepseekKey] = useState("");
  const [ncbiKey, setNcbiKey] = useState("");
  const [openalexKey, setOpenalexKey] = useState("");
  const [saved, setSaved] = useState("");
  const [configError, setConfigError] = useState("");
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [savingConfig, setSavingConfig] = useState(false);
  const [dirty, setDirty] = useState(false);
  const mirrorLibraries = useMemo(
    () => libraries.filter((library) => library.mode === "zotero_mirror"),
    [libraries],
  );
  useEffect(() => {
    let cancelled = false;
    api
      .configStatus()
      .then((value) => {
        if (cancelled) return;
        setConfig(value);
        setContactEmail(value.contact_email);
        setMinimaxGroupId(value.minimax_group_id);
        setZoteroDataDir(value.zotero_data_dir);
        setMineruExecutable(value.mineru_executable);
        setConfigError("");
        setDirty(false);
      })
      .catch((reason) => {
        if (!cancelled) setConfigError(`无法读取设置：${String(reason)}`);
      })
      .finally(() => {
        if (!cancelled) setLoadingConfig(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    let cancelled = false;
    Promise.all(
      mirrorLibraries.map((library) => api.zoteroSyncStatus(library.id)),
    )
      .then((values) => {
        if (cancelled) return;
        setZoteroSync(
          Object.fromEntries(values.map((value) => [value.library_id, value])),
        );
      })
      .catch((reason) => {
        if (!cancelled)
          setConfigError(`无法读取 Zotero 同步状态：${String(reason)}`);
      });
    return () => {
      cancelled = true;
    };
  }, [mirrorLibraries]);
  const create = async () => {
    if (!name.trim()) return;
    onCreated(await api.createLibrary(name, mode));
    setName("");
  };
  const probe = async () => {
    const status = await api.zoteroStatus();
    setZotero(status);
  };
  const syncLibrary = async (libraryId: string) => {
    if (dirty) {
      setConfigError("请先保存设置，再同步 Zotero");
      return;
    }
    setSyncingLibrary(libraryId);
    setConfigError("");
    try {
      await api.syncZotero(libraryId);
      for (let attempt = 0; attempt < 300; attempt += 1) {
        const value = await api.zoteroSyncStatus(libraryId);
        setZoteroSync((current) => ({ ...current, [libraryId]: value }));
        if (
          value.job &&
          ["complete", "failed", "review_required", "canceled"].includes(
            value.job.status,
          )
        )
          break;
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
      }
      onRefresh();
    } catch (reason) {
      setConfigError(`Zotero 同步失败：${String(reason)}`);
    } finally {
      setSyncingLibrary("");
    }
  };
  const registerCodex = async () => {
    const message = await api.registerCodexMcp();
    setSaved(message || "Codex MCP 已注册，新任务中生效");
  };
  const markDirty = () => {
    setDirty(true);
    setSaved("");
    setConfigError("");
  };
  const saveSettings = async () => {
    setSavingConfig(true);
    setSaved("");
    setConfigError("");
    try {
      const publicResult = await api.updateConfig(
        contactEmail,
        minimaxGroupId,
        zoteroDataDir,
        mineruExecutable,
      );
      let retried = publicResult.retried_fulltext;
      if (minimaxKey.trim()) {
        const result = await api.updateCredential(
          "minimax_api_key",
          minimaxKey,
        );
        retried += result.retried;
      }
      if (deepseekKey.trim())
        await api.updateCredential("deepseek_api_key", deepseekKey);
      if (ncbiKey.trim()) await api.updateCredential("ncbi_api_key", ncbiKey);
      if (openalexKey.trim())
        await api.updateCredential("openalex_api_key", openalexKey);
      const value = await api.configStatus();
      setConfig(value);
      setContactEmail(value.contact_email);
      setMinimaxGroupId(value.minimax_group_id);
      setZoteroDataDir(value.zotero_data_dir);
      setMineruExecutable(value.mineru_executable);
      setMinimaxKey("");
      setDeepseekKey("");
      setNcbiKey("");
      setOpenalexKey("");
      setDirty(false);
      setSaved(
        retried
          ? `所有设置已保存，已重新排队 ${retried} 个任务`
          : "所有设置已保存",
      );
    } catch (reason) {
      setConfigError(`保存失败：${String(reason)}`);
    } finally {
      setSavingConfig(false);
    }
  };
  if (loadingConfig)
    return (
      <section className="settings-page settings-loading">
        <LoaderCircle className="spin" size={22} />
        <span>正在读取本机设置</span>
      </section>
    );
  return (
    <section className="settings-page">
      <div className="settings-savebar" aria-live="polite">
        <div>
          {configError ? (
            <span className="settings-error">
              <CircleAlert size={16} />
              {configError}
            </span>
          ) : saved ? (
            <span className="success-text">
              <Check size={16} />
              {saved}
            </span>
          ) : (
            <span className="muted">
              {dirty ? "有尚未保存的修改" : "所有修改已保存"}
            </span>
          )}
        </div>
        <button
          className="primary-button"
          disabled={!dirty || savingConfig}
          onClick={() => void saveSettings()}
        >
          {savingConfig ? (
            <LoaderCircle className="spin" size={16} />
          ) : (
            <Save size={16} />
          )}
          保存设置
        </button>
      </div>
      <div className="settings-section">
        <h2>文库</h2>
        <div className="settings-grid">
          <label>
            名称
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="新文库名称"
            />
          </label>
          <label>
            模式
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as Library["mode"])}
            >
              <option value="standalone">独立文库</option>
              <option value="zotero_mirror">Zotero 只读镜像</option>
            </select>
          </label>
          <button className="secondary-button" onClick={() => void create()}>
            <Plus size={16} />
            创建
          </button>
        </div>
        <p className="muted">现有文库：{libraries.length}</p>
      </div>
      <div className="settings-section">
        <h2>Zotero</h2>
        <div className="zotero-settings">
          <label>
            数据目录
            <input
              value={zoteroDataDir}
              onChange={(e) => {
                setZoteroDataDir(e.target.value);
                markDirty();
              }}
              placeholder="%USERPROFILE%\Zotero"
            />
          </label>
          <div className="inline-actions">
            <button className="secondary-button" onClick={() => void probe()}>
              <FolderSync size={16} />
              检测本地 API
            </button>
            <span>
              {!zotero
                ? "未检测"
                : zotero.available
                  ? `API 可用 · Zotero 版本 ${zotero.library_version ?? "-"}`
                  : zotero.error || "不可用"}
            </span>
          </div>
        </div>
        <div className="zotero-mirror-list">
          {mirrorLibraries.map((library) => {
            const sync = zoteroSync[library.id];
            const job = sync?.job;
            const result = job?.result ?? {};
            const running =
              syncingLibrary === library.id ||
              Boolean(
                job && ["queued", "running", "retry_wait"].includes(job.status),
              );
            return (
              <article className="zotero-mirror-row" key={library.id}>
                <div className="zotero-mirror-title">
                  <strong>{library.name}</strong>
                  <span>
                    同步版本 {sync?.last_version ?? library.last_version ?? 0}
                  </span>
                </div>
                <div className="zotero-counts">
                  <span>文献 {sync?.counts.items ?? 0}</span>
                  <span>PDF {sync?.counts.pdf_ready ?? 0}</span>
                  <span>解析 {sync?.counts.parsed ?? 0}</span>
                  <span>向量 {sync?.counts.embedded ?? 0}</span>
                </div>
                <div className="zotero-sync-result">
                  {job?.status === "complete" ? (
                    <span>
                      新增 {Number(result.items_created ?? 0)} · 更新{" "}
                      {Number(result.items_updated ?? 0)} · PDF 导入{" "}
                      {Number(result.attachments_imported ?? 0)} · 缺失{" "}
                      {Number(result.attachments_missing ?? 0)}
                    </span>
                  ) : job?.error_code ? (
                    <span className="settings-error">
                      {job.error_code} · {job.error_message}
                    </span>
                  ) : (
                    <span>尚未同步</span>
                  )}
                </div>
                <button
                  className="secondary-button"
                  disabled={running || dirty}
                  onClick={() => void syncLibrary(library.id)}
                >
                  {running ? (
                    <LoaderCircle className="spin" size={16} />
                  ) : (
                    <FolderSync size={16} />
                  )}
                  {(sync?.last_version ?? library.last_version ?? 0) > 0
                    ? "增量同步"
                    : "首次同步"}
                </button>
              </article>
            );
          })}
          {!mirrorLibraries.length && (
            <p className="muted">尚未创建 Zotero 只读镜像文库</p>
          )}
        </div>
      </div>
      <div className="settings-section">
        <h2>网络元数据</h2>
        <div className="settings-grid settings-grid-two">
          <label>
            联系邮箱
            <input
              type="email"
              value={contactEmail}
              onChange={(e) => {
                setContactEmail(e.target.value);
                markDirty();
              }}
              placeholder="用于 Crossref、Unpaywall 和 PubMed"
            />
          </label>
          <label>
            MiniMax Group ID
            <input
              value={minimaxGroupId}
              onChange={(e) => {
                setMinimaxGroupId(e.target.value);
                markDirty();
              }}
              placeholder="旧版 embedding 接口需要"
            />
          </label>
        </div>
        {!contactEmail.trim() && (
          <p className="settings-error">
            未填写联系邮箱，Unpaywall 无法查找开放
            PDF；保存邮箱后可在任务中心重试失败任务。
          </p>
        )}
        <div className="credential-grid network-credentials">
          <label>
            NCBI API Key（可选）
            <div className="credential-input">
              <KeyRound size={16} />
              <input
                type="password"
                value={ncbiKey}
                onChange={(event) => {
                  setNcbiKey(event.target.value);
                  markDirty();
                }}
                placeholder={
                  config?.secrets.ncbi_api_key
                    ? "已安全保存，可提高 PubMed 请求额度"
                    : "不填写也可使用 PubMed"
                }
              />
              <Status
                value={config?.secrets.ncbi_api_key ? "configured" : "optional"}
              />
            </div>
          </label>
          <label>
            OpenAlex API Key（建议）
            <div className="credential-input">
              <KeyRound size={16} />
              <input
                type="password"
                value={openalexKey}
                onChange={(event) => {
                  setOpenalexKey(event.target.value);
                  markDirty();
                }}
                placeholder={
                  config?.secrets.openalex_api_key
                    ? "已安全保存，不回显明文"
                    : "填写免费 Key 可获得稳定额度"
                }
              />
              <Status
                value={
                  config?.secrets.openalex_api_key
                    ? "configured"
                    : "recommended"
                }
              />
            </div>
          </label>
        </div>
      </div>
      <div className="settings-section">
        <h2>模型密钥</h2>
        <div className="credential-grid">
          <label>
            MiniMax API Key
            <div className="credential-input">
              <KeyRound size={16} />
              <input
                type="password"
                value={minimaxKey}
                onChange={(e) => {
                  setMinimaxKey(e.target.value);
                  markDirty();
                }}
                placeholder={
                  config?.secrets.minimax_api_key
                    ? "已安全保存，不回显明文"
                    : "尚未配置"
                }
              />
              <Status
                value={
                  config?.secrets.minimax_api_key ? "configured" : "missing"
                }
              />
            </div>
          </label>
          <label>
            DeepSeek API Key
            <div className="credential-input">
              <KeyRound size={16} />
              <input
                type="password"
                value={deepseekKey}
                onChange={(e) => {
                  setDeepseekKey(e.target.value);
                  markDirty();
                }}
                placeholder={
                  config?.secrets.deepseek_api_key
                    ? "已安全保存，不回显明文"
                    : "尚未配置"
                }
              />
              <Status
                value={
                  config?.secrets.deepseek_api_key ? "configured" : "missing"
                }
              />
            </div>
          </label>
        </div>
      </div>
      <div className="settings-section">
        <h2>文档解析</h2>
        <div className="runtime-path">
          <label>
            MinerU 可执行文件
            <input
              value={mineruExecutable}
              onChange={(e) => {
                setMineruExecutable(e.target.value);
                markDirty();
              }}
              placeholder="mineru 或 <path>\mineru.exe"
            />
          </label>
          <Status
            value={
              mineruExecutable === "mineru" ? "PATH lookup" : "configured path"
            }
          />
        </div>
      </div>
      <div className="settings-section">
        <h2>Codex</h2>
        <div className="inline-actions">
          <button
            className="secondary-button"
            onClick={() => void registerCodex()}
          >
            <SquareTerminal size={16} />
            注册 MCP
          </button>
          <span className="muted">注册后在新的 Codex 任务中调用本地文献库</span>
        </div>
      </div>
      <div className="settings-section">
        <h2>模型与运行时</h2>
        <div className="runtime-list">
          <div>
            <span>MiniMax embedding</span>
            <Status
              value={config?.secrets.minimax_api_key ? "configured" : "missing"}
            />
          </div>
          <div>
            <span>DeepSeek generation</span>
            <Status
              value={
                config?.secrets.deepseek_api_key ? "configured" : "missing"
              }
            />
          </div>
          <div>
            <span>MinerU fallback</span>
            <Status value="PyMuPDF available" />
          </div>
        </div>
      </div>
    </section>
  );
}

function Status({ value }: { value: string }) {
  const tone =
    value === "complete" || value === "active" || value.includes("configured")
      ? "success"
      : value === "failed" || value === "tombstone"
        ? "danger"
        : value === "review_required"
          ? "warning"
          : "neutral";
  return (
    <span className={`status-badge ${tone}`}>{value.replaceAll("_", " ")}</span>
  );
}

export default App;
