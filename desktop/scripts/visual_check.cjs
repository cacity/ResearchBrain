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

let browser;

(async () => {
    browser = await chromium.launch({
        headless: true,
        ...(process.env.RB_BROWSER_PATH
            ? { executablePath: process.env.RB_BROWSER_PATH }
            : {}),
    });
    const errors = [];
    const outputDir = process.env.RB_VISUAL_DIR || os.tmpdir();
    const appUrl = process.env.RB_APP_URL || "http://127.0.0.1:4173/";
    const page = await browser.newPage({
        viewport: { width: 1360, height: 860 },
        deviceScaleFactor: 1,
    });
    page.on("console", (message) => {
        if (message.type() === "error") errors.push(message.text());
    });
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route(`${apiBase}/**`, async (route) => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname === "/v1/health") return json(route, { status: "ok" });
        if (pathname === "/v1/libraries") {
            return json(route, [
                {
                    id: "visual-library",
                    name: "视觉测试库",
                    mode: "standalone",
                    last_version: null,
                },
                ...Array.from({ length: 5 }, (_, index) => ({
                    id: `zotero-${index + 1}`,
                    name: `Zotero 镜像 ${index + 1}`,
                    mode: "zotero_mirror",
                    last_version: 100 + index,
                })),
            ]);
        }
        if (pathname === "/v1/jobs") return json(route, []);
        if (pathname === "/v1/libraries/visual-library/items")
            return json(route, []);
        if (pathname === "/v1/config/status")
            return json(route, {
                contact_email: "",
                minimax_group_id: "",
                zotero_data_dir: "C:\\Users\\tester\\Zotero",
                mineru_executable: "mineru",
                harness_port: 3080,
                secrets: {
                    minimax_api_key: false,
                    deepseek_api_key: false,
                    ncbi_api_key: false,
                    openalex_api_key: false,
                },
            });
        if (pathname === "/v1/harness/status")
            return json(route, {
                available: false,
                configured: false,
                running: false,
                skills: { installed: 0, enabled: 0, issues: 0, deployed: [] },
            });
        if (pathname === "/v1/skills") return json(route, []);
        if (pathname === "/v1/zotero/mirrors") return json(route, []);
        if (pathname === "/v1/zotero/status")
            return json(route, { available: true, library_version: 105 });
        if (pathname.endsWith("/zotero/sync-status"))
            return json(route, {
                library_id: pathname.split("/")[3],
                library_name: "Zotero 测试镜像",
                last_version: 105,
                counts: { items: 100, pdf_ready: 80, parsed: 70, embedded: 65 },
                job: null,
            });
        return json(route, []);
    });
    await page.goto(appUrl, { waitUntil: "networkidle" });
    await page.screenshot({
        path: path.join(outputDir, "researchbrain-library.png"),
        fullPage: true,
    });
    await page.getByRole("button", { name: "证据对话" }).click();
    await page.screenshot({
        path: path.join(outputDir, "researchbrain-chat.png"),
        fullPage: true,
    });
    await page.setViewportSize({ width: 1360, height: 640 });
    await page.getByRole("button", { name: "设置" }).click();
    await page.locator(".content-area").waitFor({ state: "visible" });
    const mirrorRows = page.locator(".zotero-mirror-row");
    if ((await mirrorRows.count()) > 0) {
        await mirrorRows
            .first()
            .getByRole("button", { name: /同步/ })
            .waitFor();
    }
    const settingsScroll = await page.evaluate(() => {
        const element = document.querySelector(".content-area");
        if (!element) return null;
        return {
            clientHeight: element.clientHeight,
            scrollHeight: element.scrollHeight,
            display: getComputedStyle(element).display,
            gridRow: getComputedStyle(element).gridRow,
            rect: element.getBoundingClientRect().toJSON(),
            workspaceRect: element.parentElement
                ?.getBoundingClientRect()
                .toJSON(),
        };
    });
    await page.screenshot({
        path: path.join(outputDir, "researchbrain-settings-debug.png"),
        fullPage: true,
    });
    if (
        !settingsScroll ||
        settingsScroll.scrollHeight <= settingsScroll.clientHeight
    ) {
        throw new Error(
            `Settings page does not expose a scrollable content area: ${JSON.stringify({ settingsScroll, mirrorRows: await mirrorRows.count(), errors })}`,
        );
    }
    await page.locator(".settings-section").last().scrollIntoViewIfNeeded();
    const finalSectionVisible = await page
        .locator(".settings-section")
        .last()
        .evaluate((element) => {
            const rect = element.getBoundingClientRect();
            return rect.top < window.innerHeight && rect.bottom > 0;
        });
    if (!finalSectionVisible) {
        throw new Error(
            "The final settings section cannot be reached by scrolling",
        );
    }
    await page.screenshot({
        path: path.join(outputDir, "researchbrain-settings.png"),
        fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByTitle("打开菜单").click();
    await page.screenshot({
        path: path.join(outputDir, "researchbrain-mobile-menu.png"),
        fullPage: true,
    });
    await page.getByRole("button", { name: "批量导入" }).click();
    await page.screenshot({
        path: path.join(outputDir, "researchbrain-mobile-import.png"),
        fullPage: true,
    });
    const metrics = await page.evaluate(() => ({
        viewportWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
        viewportHeight: document.documentElement.clientHeight,
        scrollHeight: document.documentElement.scrollHeight,
    }));
    if (errors.length) {
        throw new Error(`Browser errors: ${errors.join(" | ")}`);
    }
    console.log(JSON.stringify({ errors, metrics, outputDir }));
    await browser.close();
    browser = null;
})().catch(async (error) => {
    if (browser) await browser.close();
    console.error(error);
    process.exitCode = 1;
});
