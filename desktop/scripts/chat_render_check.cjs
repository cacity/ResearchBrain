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
            pathname === "/v1/chat/sessions/test-session/messages" &&
            request.method() === "POST"
        ) {
            return json(route, {
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
                    },
                ],
                model: "test-model",
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
