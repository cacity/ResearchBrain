let apiBase = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8765/v1";
let sessionToken = "";
let configuration: Promise<void> | null = null;

async function ensureConfigured(): Promise<void> {
  if (configuration) return configuration;
  configuration = (async () => {
    const tauriWindow = window as Window & { __TAURI_INTERNALS__?: unknown };
    if (!tauriWindow.__TAURI_INTERNALS__) return;
    const { invoke } = await import("@tauri-apps/api/core");
    const config = await invoke<{ port: number; token: string }>(
      "get_api_config",
    );
    apiBase = `http://127.0.0.1:${config.port}/v1`;
    sessionToken = config.token;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      try {
        const response = await fetch(`${apiBase}/health`, {
          headers: { Authorization: `Bearer ${sessionToken}` },
        });
        if (response.ok) return;
      } catch {
        // The sidecar may still be applying local schema migrations.
      }
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    throw new Error("ResearchBrain 本地服务启动超时");
  })();
  return configuration;
}

export type Library = {
  id: string;
  name: string;
  mode: "standalone" | "zotero_mirror";
  last_version: number | null;
};

export type Item = {
  id: string;
  type: string;
  title: string;
  year: number | null;
  container_title: string;
  status: string;
  pdf_status: string;
  pdf_count: number;
  parse_status: string;
  embedding_status: string;
  metadata_embedding_status: string;
  fulltext_embedding_status: string;
  knowledge_state:
    | "metadata_only"
    | "metadata_indexed"
    | "pdf_stored"
    | "parsed"
    | "fulltext_indexed";
  next_action: string;
  canonical_key: string;
  identifiers: Record<string, string>;
  doi: string;
};

export type Job = {
  id: string;
  batch_id: string | null;
  job_type: string;
  status: string;
  progress: number;
  attempt: number;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  error_code: string;
  error_message: string;
  created_at: string;
  finished_at: string | null;
};

export type ZoteroProbeStatus = {
  available: boolean;
  api_version?: string;
  library_version?: number;
  error?: string;
};

export type ZoteroSyncStatus = {
  library_id: string;
  library_name: string;
  last_version: number;
  counts: {
    items: number;
    pdf_ready: number;
    parsed: number;
    embedded: number;
  };
  job: {
    id: string;
    status: string;
    progress: number;
    result: Record<string, unknown>;
    error_code: string;
    error_message: string;
    created_at: string;
    finished_at: string | null;
  } | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Evidence[];
  limitations?: string[];
  model: string;
  search_queries?: string[];
  provider_statuses?: DiscoveryProviderStatus[];
};

export type ChatSessionSummary = {
  id: string;
  library_id: string;
  title: string;
  message_count: number;
  last_message_preview: string;
  created_at: string;
  updated_at: string;
};

export type ResearchApproval = {
  id: string;
  action: "import_dois";
  dois: string[];
  reason: string;
  status: "pending" | "approved" | "rejected";
  batch_id?: string;
};

export type ResearchRun = {
  id: string;
  session_id: string;
  user_message_id: string;
  assistant_message_id: string | null;
  mode: "local" | "hybrid" | "online";
  status:
    | "queued"
    | "running"
    | "paused"
    | "completed"
    | "failed"
    | "cancelled";
  phase: string;
  question: string;
  plan: Record<string, unknown>;
  coverage: Array<Record<string, unknown>>;
  budgets: Record<string, unknown>;
  approvals: ResearchApproval[];
  limitations: string[];
  metrics: Record<string, unknown>;
  error_code: string;
  error_message: string;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
};

export type ResearchEvent = {
  type: string;
  run_id: string;
  sequence: number;
  phase?: string;
  label?: string;
  delta?: string;
  count?: number;
  levels?: Record<string, number>;
  counts?: Record<string, number>;
  coverage?: Array<Record<string, unknown>>;
  message_id?: string;
  message?: string;
  code?: string;
  approval?: ResearchApproval;
  metrics?: Record<string, unknown>;
  limitations?: string[];
};

export type Evidence = {
  id: string;
  chunk_id: string;
  item_id: string;
  title: string;
  text: string;
  section: string;
  page_start: number | null;
  page_end: number | null;
  score: number;
  source_kind: "local" | "online";
  source_name: string;
  source_url: string;
  discovery_record: DiscoveryRecord | null;
};

export type AttachmentSummary = {
  id: string;
  logical_name: string;
  mime: string;
  status: string;
  bytes: number | null;
  sha256: string | null;
  source_url: string;
};

export type LiteratureLookup = {
  found: boolean;
  canonical_key: string;
  doi: string;
  pdf_sha256: string | null;
  exact_pdf_known: boolean | null;
  recommended_action: string;
  matches: Item[];
};

export type ArtifactSummary = {
  id: string;
  parser_name: string;
  page_count: number;
  created_at: string;
};

export type DiscoveryRecord = {
  source: string;
  source_id: string;
  title: string;
  authors: string[];
  year: number | null;
  venue: string;
  abstract: string;
  doi: string;
  url: string;
  sources: string[];
  identifiers: Record<string, string>;
  is_oa: boolean;
  fulltext_url: string;
  publication_type: string;
};

export type DiscoveryProviderStatus = {
  source: string;
  status: "complete" | "failed";
  count: number;
  elapsed_ms: number;
  error: string;
};

export type DiscoverySearchOptions = {
  sources: string[];
  yearFrom?: number;
  yearTo?: number;
  oaOnly: boolean;
};

export type ConfigStatus = {
  contact_email: string;
  minimax_group_id: string;
  zotero_data_dir: string;
  mineru_executable: string;
  harness_port: number;
  secrets: {
    minimax_api_key: boolean;
    deepseek_api_key: boolean;
    ncbi_api_key: boolean;
    openalex_api_key: boolean;
  };
};

export type HarnessStatus = {
  available: boolean;
  configured: boolean;
  running: boolean;
  owned_process: boolean;
  port: number;
  url: string;
  dsh_package: string;
  node: {
    command: string;
    version: string;
    source: string;
    supported: boolean;
  };
  profile_path: string;
  workspace_path: string;
  log_path: string;
  error: string;
  skills: {
    installed: number;
    enabled: number;
    issues: number;
    deployed: string[];
    restart_required: boolean;
  };
};

export type SkillDependency = {
  type: string;
  value: string;
  description: string;
};

export type SkillRecord = {
  name: string;
  description: string;
  default_prompt: string;
  compatibility:
    | "compatible"
    | "review_required"
    | "needs_configuration"
    | "incompatible";
  dependencies: SkillDependency[];
  permissions: string[];
  file_count: number;
  source_kind: "builtin" | "local" | "archive" | "github";
  source: string;
  source_ref: string;
  source_subpath: string;
  sha256: string;
  enabled: boolean;
  builtin: boolean;
  managed_path: string;
  installed_at: string;
  updated_at: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  await ensureConfigured();
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(sessionToken ? { Authorization: `Bearer ${sessionToken}` } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const payload = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    const detail =
      typeof payload.detail === "string"
        ? payload.detail
        : typeof payload.detail?.message === "string"
          ? payload.detail.message
          : JSON.stringify(payload.detail);
    throw new Error(detail || `HTTP ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function requestBlob(path: string): Promise<Blob> {
  await ensureConfigured();
  const response = await fetch(`${apiBase}${path}`, {
    headers: sessionToken ? { Authorization: `Bearer ${sessionToken}` } : {},
  });
  if (!response.ok) {
    const payload = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    const detail =
      typeof payload.detail === "string"
        ? payload.detail
        : typeof payload.detail?.message === "string"
          ? payload.detail.message
          : JSON.stringify(payload.detail);
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return response.blob();
}

export const api = {
  health: () =>
    request<{ status: string; components: Record<string, string> }>("/health"),
  configStatus: () => request<ConfigStatus>("/config/status"),
  updateConfig: (
    contactEmail: string,
    minimaxGroupId: string,
    zoteroDataDir: string,
    mineruExecutable: string,
  ) =>
    request<{
      contact_email: string;
      minimax_group_id: string;
      zotero_data_dir: string;
      mineru_executable: string;
      retried_fulltext: number;
    }>("/config", {
      method: "PUT",
      body: JSON.stringify({
        contact_email: contactEmail,
        minimax_group_id: minimaxGroupId,
        zotero_data_dir: zoteroDataDir,
        mineru_executable: mineruExecutable,
      }),
    }),
  updateCredential: (
    name:
      | "minimax_api_key"
      | "deepseek_api_key"
      | "ncbi_api_key"
      | "openalex_api_key",
    value: string,
  ) =>
    request<{ name: string; configured: boolean; retried: number }>(
      "/config/credential",
      {
        method: "PUT",
        body: JSON.stringify({ name, value }),
      },
    ),
  libraries: () => request<Library[]>("/libraries"),
  createLibrary: (name: string, mode: Library["mode"]) =>
    request<Library>("/libraries", {
      method: "POST",
      body: JSON.stringify({ name, mode }),
    }),
  items: (libraryId: string) =>
    request<Item[]>(`/libraries/${libraryId}/items?limit=500`),
  lookupLiterature: (libraryId: string, doi: string, pdfSha256?: string) =>
    request<LiteratureLookup>(`/libraries/${libraryId}/items/lookup`, {
      method: "POST",
      body: JSON.stringify({ doi, pdf_sha256: pdfSha256 || null }),
    }),
  attachments: (itemId: string) =>
    request<AttachmentSummary[]>(`/items/${itemId}/attachments`),
  attachmentContent: (attachmentId: string) =>
    requestBlob(`/attachments/${attachmentId}/content`),
  artifacts: (itemId: string) =>
    request<ArtifactSummary[]>(`/items/${itemId}/artifacts`),
  artifactMarkdown: (artifactId: string) =>
    requestBlob(`/artifacts/${artifactId}/markdown`),
  resolveItemFulltext: (itemId: string) =>
    request<{
      id: string;
      status: string;
      job_type: string;
      doi: string;
      requeued: boolean;
    }>(`/items/${itemId}/fulltext`, { method: "POST" }),
  jobs: () => request<Job[]>("/jobs?limit=500"),
  runNextJob: () =>
    request<Record<string, unknown>>("/jobs/run-next", { method: "POST" }),
  retryJob: (jobId: string) =>
    request(`/jobs/${jobId}/retry`, { method: "POST" }),
  retryFailedJobs: (libraryId?: string) =>
    request<{ retried: number }>("/jobs/retry-failed", {
      method: "POST",
      body: JSON.stringify({ library_id: libraryId || null, job_types: [] }),
    }),
  importDois: (libraryId: string, dois: string[], includeSi: boolean) =>
    request<{ id: string; total: number; input_errors: unknown[] }>(
      "/imports/doi",
      {
        method: "POST",
        body: JSON.stringify({
          library_id: libraryId,
          dois,
          include_si: includeSi,
        }),
      },
    ),
  zoteroStatus: () => request<ZoteroProbeStatus>("/zotero/status"),
  syncZotero: (libraryId: string) =>
    request(`/libraries/${libraryId}/zotero/sync`, { method: "POST" }),
  zoteroSyncStatus: (libraryId: string) =>
    request<ZoteroSyncStatus>(`/libraries/${libraryId}/zotero/sync-status`),
  createSession: (libraryId: string) =>
    request<{ id: string }>("/chat/sessions", {
      method: "POST",
      body: JSON.stringify({ library_id: libraryId, title: "New research" }),
    }),
  chatSessions: (libraryId: string) =>
    request<ChatSessionSummary[]>(
      `/chat/sessions?library_id=${encodeURIComponent(libraryId)}`,
    ),
  messages: (sessionId: string) =>
    request<ChatMessage[]>(`/chat/sessions/${sessionId}/messages`),
  sendMessage: (
    sessionId: string,
    content: string,
    mode: "local" | "hybrid" | "online" = "local",
  ) =>
    request<ChatMessage>(`/chat/sessions/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, evidence_limit: 15, mode }),
    }),
  createResearchRun: (
    sessionId: string,
    content: string,
    mode: "local" | "hybrid" | "online" = "local",
  ) =>
    request<ResearchRun>(`/chat/sessions/${sessionId}/runs`, {
      method: "POST",
      body: JSON.stringify({ content, evidence_limit: 20, mode }),
    }),
  researchRun: (runId: string) =>
    request<ResearchRun>(`/research/runs/${runId}`),
  researchRuns: (sessionId: string) =>
    request<ResearchRun[]>(`/chat/sessions/${sessionId}/runs`),
  cancelResearchRun: (runId: string) =>
    request<ResearchRun>(`/research/runs/${runId}/cancel`, {
      method: "POST",
    }),
  retryResearchRun: (runId: string) =>
    request<ResearchRun>(`/research/runs/${runId}/retry`, {
      method: "POST",
    }),
  steerResearchRun: (
    runId: string,
    content: string,
    kind: "constraint" | "follow_up" = "constraint",
  ) =>
    request<{ run_id: string; queued: boolean }>(
      `/research/runs/${runId}/steer`,
      {
        method: "POST",
        body: JSON.stringify({ content, kind }),
      },
    ),
  approveResearchAction: (runId: string, approvalId: string) =>
    request<{ run_id: string; batch_id: string; approval: ResearchApproval }>(
      `/research/runs/${runId}/approvals/${approvalId}`,
      { method: "POST" },
    ),
  rejectResearchAction: (runId: string, approvalId: string) =>
    request<{ run_id: string; approval: ResearchApproval }>(
      `/research/runs/${runId}/approvals/${approvalId}/reject`,
      { method: "POST" },
    ),
  streamResearchEvents: async (
    runId: string,
    onEvent: (event: ResearchEvent) => void,
    signal?: AbortSignal,
  ) => {
    await ensureConfigured();
    const response = await fetch(`${apiBase}/research/runs/${runId}/events`, {
      headers: {
        Accept: "text/event-stream",
        ...(sessionToken ? { Authorization: `Bearer ${sessionToken}` } : {}),
      },
      signal,
    });
    if (!response.ok || !response.body) {
      const payload = await response
        .json()
        .catch(() => ({ detail: response.statusText }));
      throw new Error(String(payload.detail || `HTTP ${response.status}`));
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = block
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (data) onEvent(JSON.parse(data) as ResearchEvent);
        boundary = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
  },
  discover: (query: string, options: DiscoverySearchOptions) =>
    request<{
      records: DiscoveryRecord[];
      providers: DiscoveryProviderStatus[];
    }>("/discovery/search", {
      method: "POST",
      body: JSON.stringify({
        query,
        limit_per_source: 10,
        sources: options.sources,
        year_from: options.yearFrom,
        year_to: options.yearTo,
        oa_only: options.oaOnly,
      }),
    }),
  importDiscovered: (libraryId: string, records: DiscoveryRecord[]) =>
    request<{
      created: number;
      duplicates: number;
      item_ids: string[];
      fulltext_queued: number;
      embedding_job_id: string;
    }>("/discovery/import", {
      method: "POST",
      body: JSON.stringify({
        library_id: libraryId,
        records,
        include_si: false,
      }),
    }),
  exportReferences: (itemIds: string[], format: string) =>
    request<{ filename: string; mime: string; content: string }>("/exports", {
      method: "POST",
      body: JSON.stringify({ item_ids: itemIds, format }),
    }),
  uploadAttachment: async (itemId: string, file: File) => {
    await ensureConfigured();
    const body = new FormData();
    body.append("file", file);
    const response = await fetch(`${apiBase}/items/${itemId}/attachments`, {
      method: "POST",
      headers: sessionToken ? { Authorization: `Bearer ${sessionToken}` } : {},
      body,
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  },
  registerCodexMcp: async () => {
    const tauriWindow = window as Window & { __TAURI_INTERNALS__?: unknown };
    if (!tauriWindow.__TAURI_INTERNALS__)
      throw new Error("请在 ResearchBrain 桌面版中注册 Codex MCP");
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<string>("register_codex_mcp");
  },
  harnessStatus: () => request<HarnessStatus>("/harness/status"),
  installHarness: (libraryId: string, port: number) =>
    request<HarnessStatus>("/harness/install", {
      method: "POST",
      body: JSON.stringify({ library_id: libraryId, port }),
    }),
  startHarness: (libraryId: string, port: number) =>
    request<HarnessStatus>("/harness/start", {
      method: "POST",
      body: JSON.stringify({ library_id: libraryId, port }),
    }),
  stopHarness: () =>
    request<HarnessStatus>("/harness/stop", { method: "POST" }),
  skills: () => request<SkillRecord[]>("/skills"),
  installSkill: (input: {
    source_kind: "local" | "archive" | "github";
    source: string;
    ref: string;
    subpath: string;
    enabled: boolean;
  }) =>
    request<SkillRecord>("/skills", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updateSkill: (name: string) =>
    request<SkillRecord>(`/skills/${encodeURIComponent(name)}/update`, {
      method: "POST",
    }),
  enableSkill: (name: string, enabled: boolean) =>
    request<SkillRecord>(`/skills/${encodeURIComponent(name)}/enabled`, {
      method: "PUT",
      body: JSON.stringify({ enabled }),
    }),
  uninstallSkill: (name: string) =>
    request<void>(`/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),
  revealSkill: (name: string) =>
    request<{ path: string }>(`/skills/${encodeURIComponent(name)}/reveal`, {
      method: "POST",
    }),
  launchSkill: (name: string, libraryId: string, port: number) =>
    request<{ skill: string; prompt: string; harness: HarnessStatus }>(
      `/skills/${encodeURIComponent(name)}/launch`,
      {
        method: "POST",
        body: JSON.stringify({ library_id: libraryId, port }),
      },
    ),
};
