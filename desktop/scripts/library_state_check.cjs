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
        const pathname = new URL(route.request().url()).pathname;
        if (pathname === "/v1/health") return json(route, { status: "ok" });
        if (pathname === "/v1/jobs") return json(route, []);
        if (pathname === "/v1/libraries") {
            return json(route, [
                {
                    id: "identity-library",
                    name: "身份状态测试库",
                    mode: "standalone",
                    last_version: null,
                },
            ]);
        }
        if (pathname === "/v1/libraries/identity-library/items") {
            return json(route, [
                {
                    id: "item-1",
                    type: "article-journal",
                    title: "A deliberately long research article title used to verify stable library layout",
                    year: 2026,
                    container_title:
                        "Journal of Reproducible Literature Systems",
                    status: "active",
                    pdf_status: "ready",
                    pdf_count: 1,
                    parse_status: "ready",
                    embedding_status: "ready",
                    metadata_embedding_status: "ready",
                    fulltext_embedding_status: "ready",
                    knowledge_state: "fulltext_indexed",
                    next_action: "none",
                    canonical_key: "doi:10.1000/researchbrain.test",
                    identifiers: { doi: "10.1000/researchbrain.test" },
                    doi: "10.1000/researchbrain.test",
                },
            ]);
        }
        return json(route, []);
    });

    await page.goto(process.env.RB_APP_URL || "http://127.0.0.1:1420/", {
        waitUntil: "networkidle",
    });
    const indicators = page.locator(".pipeline-statuses .pipeline-indicator");
    await indicators.first().waitFor();
    if ((await indicators.count()) !== 4) {
        throw new Error(
            `Expected four pipeline indicators, found ${await indicators.count()}`,
        );
    }
    for (let index = 0; index < 4; index += 1) {
        if (!(await indicators.nth(index).isEnabled())) {
            throw new Error(`Pipeline indicator ${index + 1} is not clickable`);
        }
    }
    const layout = await page.evaluate(() => ({
        viewportWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
        labelsFit: Array.from(
            document.querySelectorAll(".pipeline-copy"),
        ).every((element) => element.scrollWidth <= element.clientWidth),
    }));
    if (layout.scrollWidth > layout.viewportWidth || !layout.labelsFit) {
        throw new Error(
            `Library status layout overflow: ${JSON.stringify(layout)}`,
        );
    }
    const output = path.join(
        process.env.RB_VISUAL_DIR || os.tmpdir(),
        "researchbrain-library-identity-states.png",
    );
    await page.screenshot({ path: output, fullPage: true });
    if (browserErrors.length) throw new Error(browserErrors.join(" | "));
    console.log(JSON.stringify({ output, layout }));
    await browser.close();
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
