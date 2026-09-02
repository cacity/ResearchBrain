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

const assistantMessage = {
    id: "assistant-message",
    role: "assistant",
    content:
        "**主要结论**\n\n- 第一项结果 [E1]\n- 第二项结果 [E2]\n\n| 数据 | 结论 |\n| --- | --- |\n| 摘要 | 可检索 |",
    citations: [
        {
            id: "E1",
            chunk_id: "metadata:item-1",
            item_id: "item-1",
            title: "元数据证据",
            text: "Title: Example\nAbstract: Metadata evidence.",
            section: "题录与摘要",
            page_start: null,
            page_end: null,
            score: 0.9,
            source_kind: "local",
            source_name: "local-library",
            source_url: "",
            discovery_record: null,
        },
        {
            id: "E2",
            chunk_id: "chunk-2",
            item_id: "item-2",
            title: "PDF 证据",
            text: "PDF evidence text.",
            section: "Results",
            page_start: 3,
            page_end: 4,
            score: 0.8,
            source_kind: "local",
            source_name: "local-library",
            source_url: "",
            discovery_record: null,
        },
    ],
    model: "test-model",
};

(async () => {
    const browser = await chromium.launch({
        headless: true,
        ...(process.env.RB_BROWSER_PATH
            ? { executablePath: process.env.RB_BROWSER_PATH }
            : {}),
    });
    const page = await browser.newPage({
        viewport: { width: 1360, height: 860 },
    });
    const browserErrors = [];
    page.on("console", (message) => {
        if (message.type() === "error") browserErrors.push(message.text());
    });
    page.on("pageerror", (error) => browserErrors.push(error.message));

    await page.route(`${apiBase}/**`, async (route) => {
        const request = route.request();
        const pathname = new URL(request.url()).pathname;
        if (pathname === "/v1/health") {
            return json(route, { status: "ok", components: {} });
        }
        if (pathname === "/v1/libraries") {
            return json(route, [
                {
                    id: "test-library",
                    name: "Markdown 验证库",
                    mode: "standalone",
                    last_version: null,
                },
            ]);
        }
        if (pathname === "/v1/jobs") return json(route, []);
        if (pathname === "/v1/libraries/test-library/items") {
            return json(route, []);
        }
        if (pathname === "/v1/chat/sessions" && request.method() === "POST") {
            return json(route, { id: "test-session" });
        }
        if (
            pathname === "/v1/chat/sessions/test-session/runs" &&
            request.method() === "POST"
        ) {
            return json(route, {
                id: "test-run",
                session_id: "test-session",
                status: "queued",
                phase: "queued",
            });
        }
        if (
            pathname === "/v1/research/runs/test-run/events" &&
            request.method() === "GET"
        ) {
            const events = [
                {
                    type: "phase_started",
                    run_id: "test-run",
                    sequence: 1,
                    phase: "local_search",
                    label: "正在检索本地文库",
                },
                {
                    type: "evidence_updated",
                    run_id: "test-run",
                    sequence: 2,
                    count: 6,
                    levels: { fulltext_page: 4, structured_abstract: 2 },
                },
                {
                    type: "coverage_updated",
                    run_id: "test-run",
                    sequence: 3,
                    counts: {
                        covered: 2,
                        partial: 1,
                        insufficient_evidence: 0,
                    },
                },
                {
                    type: "approval_available",
                    run_id: "test-run",
                    sequence: 4,
                    approval: {
                        id: "approval-1",
                        action: "import_dois",
                        dois: ["10.1000/example"],
                        reason: "Open full text may be available",
                        status: "pending",
                    },
                },
                {
                    type: "answer_delta",
                    run_id: "test-run",
                    sequence: 5,
                    delta: assistantMessage.content,
                },
                {
                    type: "run_completed",
                    run_id: "test-run",
                    sequence: 6,
                    message_id: "assistant-message",
                },
            ];
            return route.fulfill({
                status: 200,
                contentType: "text/event-stream",
                body: events
                    .map(
                        (event) =>
                            `id: ${event.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
                    )
                    .join(""),
            });
        }
        if (pathname === "/v1/research/runs/test-run") {
            return json(route, {
                id: "test-run",
                session_id: "test-session",
                status: "completed",
                phase: "completed",
            });
        }
        if (
            pathname === "/v1/chat/sessions/test-session/messages" &&
            request.method() === "GET"
        ) {
            return json(route, [
                {
                    id: "user-message",
                    role: "user",
                    content: "测试 Markdown",
                    citations: [],
                    model: "",
                },
                assistantMessage,
            ]);
        }
        if (
            pathname === "/v1/research/runs/test-run/approvals/approval-1" &&
            request.method() === "POST"
        ) {
            return json(route, {
                run_id: "test-run",
                batch_id: "batch-12345678",
                approval: { status: "approved" },
            });
        }
        return json(route, []);
    });

    await page.goto(process.env.RB_APP_URL || "http://127.0.0.1:1420/", {
        waitUntil: "networkidle",
    });
    await page.getByRole("button", { name: "证据对话" }).click();
    await page.locator(".composer textarea").fill("测试 Markdown");
    await page.getByTitle("发送").click();
    await page.locator(".message.assistant").waitFor();
    await page.getByText("发现 1 篇可导入文献").waitFor();

    const answer = page.locator(".message.assistant");
    if ((await answer.locator("strong").first().textContent()) !== "主要结论") {
        throw new Error("Markdown bold text was not rendered");
    }
    if ((await answer.locator("li").count()) !== 2) {
        throw new Error("Markdown list was not rendered");
    }
    if ((await answer.locator("table").count()) !== 1) {
        throw new Error("GFM table was not rendered");
    }
    if ((await answer.locator(".message-content").innerText()).includes("**")) {
        throw new Error("Raw Markdown markers remain visible");
    }

    const metadataCitation = page.getByRole("button", {
        name: "查看证据 E1，题录/摘要",
    });
    const pdfCitation = page.getByRole("button", {
        name: "查看证据 E2，第 3-4 页",
    });
    await metadataCitation.click();
    if (
        !(await page.locator(".evidence-detail").innerText()).includes(
            "题录/摘要",
        )
    ) {
        throw new Error("Metadata evidence location is unclear");
    }
    await pdfCitation.click();
    const pdfDetail = await page.locator(".evidence-detail").innerText();
    if (
        !pdfDetail.includes("Results · 第 3-4 页") ||
        pdfDetail.includes("p.?")
    ) {
        throw new Error("PDF evidence location was not rendered correctly");
    }
    await page.getByRole("button", { name: "导入并查找开放全文" }).click();
    await page.getByText(/已排队，批次 batch-12/).waitFor();

    const output = path.join(
        process.env.RB_VISUAL_DIR || os.tmpdir(),
        "researchbrain-chat-markdown.png",
    );
    await page.screenshot({ path: output, fullPage: true });
    if (browserErrors.length) {
        throw new Error(`Browser errors: ${browserErrors.join(" | ")}`);
    }
    console.log(JSON.stringify({ output, browserErrors }));
    await browser.close();
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
