const os = require("os");
const path = require("path");
const { chromium } = require("playwright");

(async () => {
    const browser = await chromium.launch({
        headless: true,
        ...(process.env.RB_BROWSER_PATH
            ? { executablePath: process.env.RB_BROWSER_PATH }
            : {}),
    });
    const errors = [];
    const outputDir = process.env.RB_VISUAL_DIR || os.tmpdir();
    const appUrl = process.env.RB_APP_URL || "http://127.0.0.1:1420/";
    const page = await browser.newPage({
        viewport: { width: 1360, height: 860 },
        deviceScaleFactor: 1,
    });
    page.on("console", (message) => {
        if (message.type() === "error") errors.push(message.text());
    });
    page.on("pageerror", (error) => errors.push(error.message));
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
    const mirrorRows = page.locator(".zotero-mirror-row");
    if ((await mirrorRows.count()) > 0) {
        await mirrorRows
            .first()
            .getByRole("button", { name: /同步/ })
            .waitFor();
    }
    const settingsScroll = await page
        .locator(".content-area")
        .evaluate((element) => ({
            clientHeight: element.clientHeight,
            scrollHeight: element.scrollHeight,
        }));
    if (settingsScroll.scrollHeight <= settingsScroll.clientHeight) {
        throw new Error(
            "Settings page does not expose a scrollable content area",
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
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
