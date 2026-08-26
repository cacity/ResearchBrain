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
    let fulltextQueued = false;
    page.on("console", (message) => {
        if (message.type() === "error") browserErrors.push(message.text());
    });
    page.on("pageerror", (error) => browserErrors.push(error.message));

    await page.route(`${apiBase}/**`, async (route) => {
        const pathname = new URL(route.request().url()).pathname;
        if (pathname === "/v1/health") return json(route, { status: "ok" });
        if (pathname === "/v1/jobs") return json(route, []);
        if (pathname === "/v1/harness/status") {
            return json(route, {
                available: true,
                configured: true,
                running: false,
                owned_process: false,
                port: 3080,
                url: "http://127.0.0.1:3080",
                dsh_package: "@deepseek-ai/dsh@0.1.1-rc.2",
                node: {
                    command: "C:\\ResearchBrain\\runtime\\node.exe",
                    version: "24.19.0",
                    source: "portable",
                    supported: true,
                },
                profile_path: "C:\\ResearchBrain\\harness\\profiles\\web",
                workspace_path: "C:\\ResearchBrain\\harness\\workspace",
                log_path: "C:\\ResearchBrain\\harness\\harness.log",
                error: "",
                skills: {
                    installed: 2,
                    enabled: 1,
                    issues: 1,
                    deployed: ["researchbrain-literature"],
                    restart_required: false,
                },
            });
        }
        if (pathname === "/v1/skills") {
            return json(route, [
                {
                    name: "researchbrain-literature",
                    description: "Built-in evidence research workflow.",
                    default_prompt: "Use $researchbrain-literature.",
                    compatibility: "compatible",
                    dependencies: [
                        {
                            type: "mcp",
                            value: "researchbrain",
                            description: "Local literature tools",
                        },
                    ],
                    permissions: ["调用 ResearchBrain MCP 工具"],
                    file_count: 1,
                    source_kind: "builtin",
                    source: "ResearchBrain",
                    source_ref: "",
                    source_subpath: "",
                    sha256: "",
                    enabled: true,
                    builtin: true,
                    managed_path: "",
                    installed_at: "",
                    updated_at: "",
                },
                {
                    name: "third-party-review",
                    description:
                        "A third-party review Skill with local scripts that require inspection before use.",
                    default_prompt: "Use $third-party-review.",
                    compatibility: "review_required",
                    dependencies: [],
                    permissions: ["执行 Skill 附带的本地脚本"],
                    file_count: 4,
                    source_kind: "github",
                    source: "https://github.com/example/review-skill",
                    source_ref: "main",
                    source_subpath: "",
                    sha256: "abc",
                    enabled: false,
                    builtin: false,
                    managed_path:
                        "C:\\ResearchBrain\\skills\\third-party-review",
                    installed_at: "2026-08-26T00:00:00Z",
                    updated_at: "2026-08-26T00:00:00Z",
                },
            ]);
        }
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
                {
                    id: "item-2",
                    type: "article-journal",
                    title: "An open paper waiting for its DOI PDF",
                    year: 2025,
                    container_title: "Open Research Journal",
                    status: "active",
                    pdf_status: fulltextQueued ? "queued" : "none",
                    pdf_count: 0,
                    parse_status: "none",
                    embedding_status: "ready",
                    metadata_embedding_status: "ready",
                    fulltext_embedding_status: "none",
                    knowledge_state: "metadata_indexed",
                    next_action: "resolve_pdf",
                    canonical_key: "doi:10.1000/open-paper",
                    identifiers: { doi: "10.1000/open-paper" },
                    doi: "10.1000/open-paper",
                },
            ]);
        }
        if (pathname === "/v1/items/item-2/fulltext") {
            fulltextQueued = true;
            return route.fulfill({
                status: 202,
                contentType: "application/json",
                body: JSON.stringify({
                    id: "fulltext-job",
                    status: "queued",
                    job_type: "resolve_fulltext",
                    doi: "10.1000/open-paper",
                    requeued: false,
                }),
            });
        }
        return json(route, []);
    });

    await page.goto(process.env.RB_APP_URL || "http://127.0.0.1:1420/", {
        waitUntil: "networkidle",
    });
    const firstRow = page.locator("tbody tr").first();
    const indicators = firstRow.locator(
        ".pipeline-statuses .pipeline-indicator",
    );
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
    const missingRow = page
        .locator("tbody tr")
        .filter({ hasText: "An open paper waiting for its DOI PDF" });
    await missingRow.locator(".pipeline-indicator").first().click();
    await page.waitForFunction(() =>
        Array.from(document.querySelectorAll("tbody tr")).some(
            (row) =>
                row.textContent?.includes(
                    "An open paper waiting for its DOI PDF",
                ) && row.textContent?.includes("排队"),
        ),
    );
    if (!fulltextQueued) {
        throw new Error(
            "Clicking a missing DOI PDF did not queue full-text retrieval",
        );
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
    await page.getByRole("button", { name: "深度调研" }).click();
    await page.getByText("ResearchBrain MCP").waitFor();
    const harnessLayout = await page.evaluate(() => ({
        viewportWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
        pageScrollWidth: document.querySelector(".harness-page")?.scrollWidth,
        pageClientWidth: document.querySelector(".harness-page")?.clientWidth,
    }));
    if (
        harnessLayout.scrollWidth > harnessLayout.viewportWidth ||
        Number(harnessLayout.pageScrollWidth) >
            Number(harnessLayout.pageClientWidth)
    ) {
        throw new Error(
            `Harness layout overflow: ${JSON.stringify(harnessLayout)}`,
        );
    }
    const harnessOutput = path.join(
        process.env.RB_VISUAL_DIR || os.tmpdir(),
        "researchbrain-harness.png",
    );
    await page.screenshot({ path: harnessOutput, fullPage: true });
    await page.getByRole("button", { name: "Skills", exact: true }).click();
    await page.getByText("third-party-review").waitFor();
    const skillsLayout = await page.evaluate(() => ({
        viewportWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
        pageScrollWidth: document.querySelector(".skills-page")?.scrollWidth,
        pageClientWidth: document.querySelector(".skills-page")?.clientWidth,
    }));
    if (
        skillsLayout.scrollWidth > skillsLayout.viewportWidth ||
        Number(skillsLayout.pageScrollWidth) >
            Number(skillsLayout.pageClientWidth)
    ) {
        throw new Error(
            `Skills layout overflow: ${JSON.stringify(skillsLayout)}`,
        );
    }
    const skillsOutput = path.join(
        process.env.RB_VISUAL_DIR || os.tmpdir(),
        "researchbrain-skills.png",
    );
    await page.screenshot({ path: skillsOutput, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByTitle("打开菜单").click();
    await page.getByRole("button", { name: "Skills", exact: true }).click();
    await page.waitForTimeout(300);
    const mobileSkillsLayout = await page.evaluate(() => ({
        viewportWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
        pageScrollWidth: document.querySelector(".skills-page")?.scrollWidth,
        pageClientWidth: document.querySelector(".skills-page")?.clientWidth,
    }));
    if (
        mobileSkillsLayout.scrollWidth > mobileSkillsLayout.viewportWidth ||
        Number(mobileSkillsLayout.pageScrollWidth) >
            Number(mobileSkillsLayout.pageClientWidth)
    ) {
        throw new Error(
            `Mobile Skills layout overflow: ${JSON.stringify(mobileSkillsLayout)}`,
        );
    }
    const mobileSkillsOutput = path.join(
        process.env.RB_VISUAL_DIR || os.tmpdir(),
        "researchbrain-skills-mobile.png",
    );
    await page.screenshot({ path: mobileSkillsOutput, fullPage: true });
    if (browserErrors.length) throw new Error(browserErrors.join(" | "));
    console.log(
        JSON.stringify({
            output,
            harnessOutput,
            skillsOutput,
            mobileSkillsOutput,
            layout,
            harnessLayout,
            skillsLayout,
            mobileSkillsLayout,
        }),
    );
    await browser.close();
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
