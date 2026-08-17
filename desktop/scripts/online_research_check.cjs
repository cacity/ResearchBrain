const os = require("os");
const path = require("path");
const { chromium } = require("playwright");

const apiBase = "http://127.0.0.1:8765/v1";

function json(route, body) {
    return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
    });
}

(async () => {
    const browser = await chromium.launch({
        headless: true,
        ...(process.env.RB_BROWSER_PATH
            ? { executablePath: process.env.RB_BROWSER_PATH }
            : {}),
    });
    const page = await browser.newPage({
        viewport: { width: 1440, height: 900 },
    });
    const errors = [];
    page.on("console", (message) => {
        if (message.type() === "error") errors.push(message.text());
    });
    page.on("pageerror", (error) => errors.push(error.message));

    await page.route(`${apiBase}/**`, async (route) => {
        const request = route.request();
        const pathname = new URL(request.url()).pathname;
        if (pathname === "/v1/health")
            return json(route, { status: "ok", components: {} });
        if (pathname === "/v1/libraries") {
            return json(route, [
                {
                    id: "library",
                    name: "联网调研测试库",
                    mode: "standalone",
                    last_version: null,
                },
            ]);
        }
        if (pathname === "/v1/jobs") return json(route, []);
        if (pathname === "/v1/libraries/library/items") return json(route, []);
        if (pathname === "/v1/chat/sessions" && request.method() === "GET") {
            return json(route, [
                {
                    id: "history-session",
                    library_id: "library",
                    title: "May 2024 storm review",
                    message_count: 2,
                    last_message_preview: "Restored evidence-grounded answer",
                    created_at: "2026-08-16T08:00:00Z",
                    updated_at: "2026-08-16T08:01:00Z",
                },
            ]);
        }
        if (
            pathname === "/v1/chat/sessions/history-session/messages" &&
            request.method() === "GET"
        ) {
            return json(route, [
                {
                    id: "history-user",
                    role: "user",
                    content: "Restored question",
                    citations: [],
                    model: "",
                },
                {
                    id: "history-assistant",
                    role: "assistant",
                    content: "Restored evidence-grounded answer",
                    citations: [],
                    model: "fixture",
                },
            ]);
        }
        if (pathname === "/v1/chat/sessions" && request.method() === "POST") {
            return json(route, { id: "session" });
        }
        if (
            pathname === "/v1/chat/sessions/session/messages" &&
            request.method() === "POST"
        ) {
            const discoveryRecord = {
                source: "crossref",
                source_id: "10.1000/online",
                title: "Online evidence with a DOI",
                authors: ["Ada Lovelace"],
                year: 2025,
                venue: "Space Weather",
                abstract: "Online evidence returned from an academic API.",
                doi: "10.1000/online",
                url: "https://doi.org/10.1000/online",
                sources: ["crossref", "openalex"],
                identifiers: { doi: "10.1000/online", openalex: "W2" },
                is_oa: false,
                fulltext_url: "",
                publication_type: "article-journal",
            };
            return json(route, {
                id: "assistant",
                role: "assistant",
                content: "在线摘要支持该结论 [W1]。",
                citations: [
                    {
                        id: "W1",
                        chunk_id: "web:crossref:10.1000/online",
                        item_id: "",
                        title: discoveryRecord.title,
                        text: `Title: ${discoveryRecord.title}\nDOI: ${discoveryRecord.doi}`,
                        section: "online title/abstract",
                        page_start: null,
                        page_end: null,
                        score: 1,
                        source_kind: "online",
                        source_name: "crossref, openalex",
                        source_url: discoveryRecord.url,
                        discovery_record: discoveryRecord,
                    },
                ],
                model: "test-model",
            });
        }
        if (pathname === "/v1/discovery/search") {
            return json(route, {
                records: [
                    {
                        source: "pubmed",
                        source_id: "123",
                        title: "Open record without a DOI",
                        authors: ["Ada Lovelace"],
                        year: 2025,
                        venue: "Space Weather",
                        abstract: "An abstract returned through PubMed EFetch.",
                        doi: "",
                        url: "https://pubmed.ncbi.nlm.nih.gov/123/",
                        sources: ["pubmed", "openalex"],
                        identifiers: { pmid: "123", openalex: "W1" },
                        is_oa: true,
                        fulltext_url:
                            "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/",
                        publication_type: "article-journal",
                    },
                ],
                providers: [
                    {
                        source: "pubmed",
                        status: "complete",
                        count: 1,
                        elapsed_ms: 80,
                        error: "",
                    },
                    {
                        source: "openalex",
                        status: "complete",
                        count: 1,
                        elapsed_ms: 60,
                        error: "",
                    },
                    {
                        source: "crossref",
                        status: "failed",
                        count: 0,
                        elapsed_ms: 100,
                        error: "HTTP 429",
                    },
                ],
            });
        }
        if (pathname === "/v1/discovery/import") {
            return json(route, {
                created: 1,
                duplicates: 0,
                item_ids: ["item"],
                fulltext_queued: 1,
                embedding_job_id: "job",
            });
        }
        return json(route, []);
    });

    await page.goto(process.env.RB_APP_URL || "http://127.0.0.1:4173/", {
        waitUntil: "networkidle",
    });
    await page.getByRole("button", { name: "在线发现" }).click();
    await page.getByPlaceholder(/Crossref/).fill("geomagnetic storm");
    await page.getByRole("button", { name: "搜索", exact: true }).click();
    await page.getByText("Open record without a DOI").waitFor();
    await page.getByText("crossref · HTTP 429").waitFor();
    const checkbox = page.locator(".result-row input[type=checkbox]");
    if (await checkbox.isDisabled())
        throw new Error("Non-DOI discovery record cannot be selected");
    await checkbox.check();
    await page.getByRole("button", { name: /导入并处理/ }).click();
    await page.getByText(/已导入 1 篇/).waitFor();
    const discoveryScreenshot = path.join(
        os.tmpdir(),
        "researchbrain-online-discovery.png",
    );
    await page.screenshot({ path: discoveryScreenshot, fullPage: true });

    await page.getByRole("button", { name: "证据对话" }).click();
    await page
        .locator(".chat-history")
        .getByText("May 2024 storm review")
        .waitFor();
    await page
        .locator(".message.assistant")
        .getByText("Restored evidence-grounded answer")
        .waitFor();
    await page.locator(".chat-history-header button").click();
    await page.locator(".chat-empty").waitFor();
    for (const label of ["仅本地", "本地优先 + 联网", "全网调研"]) {
        await page.getByRole("button", { name: label, exact: true }).waitFor();
    }
    await page.getByRole("button", { name: "全网调研", exact: true }).click();
    await page.locator(".composer textarea").fill("测试在线证据导入");
    await page.getByTitle("发送").click();
    await page.getByRole("button", { name: /查看证据 W1/ }).click();
    await page.getByRole("button", { name: "导入文库" }).click();
    await page.getByText(/已加入文库/).waitFor();
    const scrolling = await page.evaluate(() => {
        const content = document.querySelector(".content-area");
        const history = document.querySelector(".chat-session-list");
        const messages = document.querySelector(".message-list");
        const evidence = document.querySelector(".evidence-panel");
        if (!content || !history || !messages || !evidence) return null;
        return {
            contentOverflow: getComputedStyle(content).overflowY,
            historyOverflow: getComputedStyle(history).overflowY,
            messageOverflow: getComputedStyle(messages).overflowY,
            evidenceOverflow: getComputedStyle(evidence).overflowY,
            evidenceHeight: evidence.getBoundingClientRect().height,
            viewportHeight: window.innerHeight,
        };
    });
    if (
        !scrolling ||
        scrolling.contentOverflow !== "hidden" ||
        scrolling.historyOverflow !== "auto" ||
        scrolling.messageOverflow !== "auto" ||
        scrolling.evidenceOverflow !== "auto" ||
        scrolling.evidenceHeight >= scrolling.viewportHeight
    ) {
        throw new Error(
            `Chat columns do not scroll independently: ${JSON.stringify(scrolling)}`,
        );
    }
    const screenshot = path.join(
        os.tmpdir(),
        "researchbrain-online-research.png",
    );
    await page.screenshot({ path: screenshot, fullPage: true });
    if (errors.length) throw new Error(`Browser errors: ${errors.join(" | ")}`);
    console.log(JSON.stringify({ discoveryScreenshot, screenshot, errors }));
    await browser.close();
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
