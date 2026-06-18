/**
 * HyperFrames renderer — frame-by-frame GSAP capture via Puppeteer.
 *
 * Usage:
 *   node hf_render.mjs <htmlPath> <outputPath> <duration> <width> <height> <fps> [--static]
 *
 * When the HTML contains a GSAP timeline registered on window.__timelines,
 * the renderer seeks frame-by-frame and captures each frame, producing a
 * properly animated MP4.  Falls back to static single-screenshot capture
 * when no timeline is found or --static is passed.
 */
import puppeteerCore from 'puppeteer-core';
import { readFileSync, unlinkSync, mkdirSync, readdirSync, existsSync } from 'fs';
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { platform } from 'os';

const args = process.argv.slice(2);
const staticFlag = args.includes('--static');
const positional = args.filter(a => !a.startsWith('--'));

const [htmlPath, outputPath, durationStr, widthStr, heightStr, fpsStr] = positional;
const duration = parseFloat(durationStr) || 2.0;
const width  = parseInt(widthStr)  || 1080;
const height = parseInt(heightStr) || 1920;
const fps    = parseInt(fpsStr)    || 30;

// ── Resolve Chrome and FFmpeg — mirrors transcribe.py convention ─────────
function findFirst(candidates) {
    for (const p of candidates) { if (p && existsSync(p)) return p; }
    return null;
}

const isWin = platform() === 'win32';
const chromePaths = [
    process.env.CHROMIUM_PATH,
    ...(isWin ? [
        'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    ] : [
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
    ]),
];
const executablePath = findFirst(chromePaths);
if (!executablePath) {
    console.error('[hf_render] No Chrome/Chromium found');
    process.exit(1);
}

// Match the Python convention: on Windows use the known absolute path,
// on Linux rely on PATH (apt-get install ffmpeg in Dockerfile).
const ffmpegCandidates = [
    process.env.FFMPEG_PATH,
    ...(isWin ? [
        'C:\\Users\\KANWAGI\\Downloads\\ffmpeg-master-latest-win64-gpl-shared\\ffmpeg-master-latest-win64-gpl-shared\\bin\\ffmpeg.exe',
        'C:\\tmp\\ffmpeg_extract\\ffmpeg-8.1.1-essentials_build\\bin\\ffmpeg.exe',
    ] : []),
    'ffmpeg',
];
const ffmpeg = findFirst(ffmpegCandidates) || 'ffmpeg';

const browser = await puppeteerCore.launch({
    headless: 'new',
    executablePath,
    args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--window-size=' + width + ',' + height,
    ]
});

const page = await browser.newPage();
await page.setViewport({ width, height });

const html = readFileSync(htmlPath, 'utf8');
await page.setContent(html, { waitUntil: 'networkidle0' });

// Detect if GSAP timeline is registered
const hasTimeline = await page.evaluate(() => {
    return !!(window.__timelines && Object.keys(window.__timelines).length > 0);
});

if (!staticFlag && hasTimeline) {
    // ── Frame-by-frame GSAP capture ──────────────────────────────────────
    const totalFrames = Math.ceil(duration * fps);
    const framesDir = join(dirname(outputPath), '_hf_frames_' + Date.now());
    mkdirSync(framesDir, { recursive: true });

    console.log(`[hf_render] Animated capture: ${totalFrames} frames @ ${fps}fps, ${duration}s`);

    for (let i = 0; i < totalFrames; i++) {
        const t = i / fps;
        // Seek all registered GSAP timelines to time t, then call __afterSeek
        await page.evaluate((seekTime) => {
            for (const [id, tl] of Object.entries(window.__timelines)) {
                tl.seek(seekTime);
            }
            if (typeof window.__afterSeek === 'function') window.__afterSeek();
        }, t);
        // Small delay for browser repaint
        await new Promise(r => setTimeout(r, 8));
        const framePath = join(framesDir, `frame_${String(i).padStart(6, '0')}.png`);
        await page.screenshot({ path: framePath, fullPage: false });
    }

    await browser.close();

    // Stitch frames into video with FFmpeg
    const framePattern = join(framesDir, 'frame_%06d.png');
    execSync(
        `"${ffmpeg}" -y -loglevel error -framerate ${fps} -i "${framePattern}" ` +
        `-vf "scale=${width}:${height}" -c:v libx264 -preset ultrafast -crf 18 ` +
        `-pix_fmt yuv420p -an "${outputPath}"`,
        { timeout: 120000 }
    );

    // Cleanup frames
    try {
        for (const f of readdirSync(framesDir)) unlinkSync(join(framesDir, f));
        unlinkSync(framesDir);
    } catch(e) {
        try { execSync(`rm -rf "${framesDir}"`, { timeout: 10000 }); } catch(e2) {}
    }

    console.log('rendered:' + outputPath);
} else {
    // ── Static capture (no GSAP timeline or --static flag) ───────────────
    // Wait for CSS animations / initial render to settle
    await new Promise(r => setTimeout(r, Math.min(2000, duration * 700)));

    const screenshotPath = outputPath.replace('.mp4', '_frame.png');
    await page.screenshot({ path: screenshotPath, fullPage: false });

    await browser.close();

    execSync(
        `"${ffmpeg}" -y -loglevel error -loop 1 -i "${screenshotPath}" -t ${duration} ` +
        `-vf "scale=${width}:${height}" -c:v libx264 -preset ultrafast -crf 18 ` +
        `-pix_fmt yuv420p -an "${outputPath}"`,
        { timeout: 60000 }
    );

    try { unlinkSync(screenshotPath); } catch(e) {}
    console.log('rendered:' + outputPath);
}
