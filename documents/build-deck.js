// MQ & ACE Platform Story — 7-slide editable deck
// Built in the "NotebookLM" purple aesthetic of the reference
// Agentic_Platform_Intelligence_(4).pptx, with editable shapes/text and
// SVG-rendered visual upgrades (flowing curves, 3D blocks, gradient panels).

const pptxgen = require("pptxgenjs");
const path = require("path");
const fs = require("fs");
const JSZip = require("jszip");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");

// Icon imports
const Fa = require("react-icons/fa");
const Md = require("react-icons/md");
const Lu = require("react-icons/lu");

const OUT = path.join(__dirname, "MQ_ACE_Platform_Story.pptx");

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9"; // 10" x 5.625"
pres.author = "MQ/ACE Platform Support";
pres.title = "MQ & ACE Platform Story";
pres.subject = "Console chaos to conversational intelligence";

// ---------- Palette (NotebookLM purple) ----------
const P = {
  bg:           "FAFAFA",
  card:         "FFFFFF",
  border:       "E5E7EB",
  text:         "1F2937",
  textSoft:     "374151",
  muted:        "6B7280",
  mutedLight:   "9CA3AF",
  // Purple family
  purple:       "5B21B6",
  purpleDeep:   "4C1D95",
  purpleDark:   "3B0F8A",
  purpleMid:    "7C3AED",
  purpleLight:  "A78BFA",
  lavender:     "DDD6FE",
  lavenderPale: "EDE9FE",
  lavenderFog:  "F5F3FF",
  // Accents
  magenta:      "C026D3",
  pink:         "EC4899",
  // Stat gradient endpoints
  statTop:      "6D28D9",
  statBot:      "4C1D95",
  white:        "FFFFFF",
};

const FONT_H = "Aptos Display";
const FONT_B = "Aptos";
const FONT_M = "Consolas";

// ---------- Shadow helpers ----------
const shSoft = () => ({ type: "outer", color: "000000", opacity: 0.08, blur: 6, offset: 1, angle: 90 });
const shMed  = () => ({ type: "outer", color: "000000", opacity: 0.14, blur: 10, offset: 2, angle: 90 });
const shCard = () => ({ type: "outer", color: "5B21B6", opacity: 0.10, blur: 12, offset: 2, angle: 90 });

// ---------- Image helpers ----------
async function svgToPng(svg) {
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

async function iconPng(IconComponent, color = "#5B21B6", size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(
    React.createElement(IconComponent, { color, size: String(size) })
  );
  return svgToPng(svg);
}

// Flowing organic curves for the title slide — hand-tuned bezier paths
async function curvesPng() {
  const w = 600, h = 540;
  // Focal point on the left edge, mid-height
  const fx = 35, fy = 290;
  // Each curve = end-point + two control points + colour + stroke
  // Designed to spread outward like a calligraphic burst
  const curves = [
    { ex: 540, ey: 100, c1x: 200, c1y: 30,  c2x: 360, c2y: 80,  col: "#5B21B6", sw: 3.2 },
    { ex: 555, ey: 140, c1x: 180, c1y: 70,  c2x: 370, c2y: 120, col: "#6D28D9", sw: 2.8 },
    { ex: 565, ey: 180, c1x: 160, c1y: 120, c2x: 380, c2y: 160, col: "#7C3AED", sw: 3.0 },
    { ex: 570, ey: 220, c1x: 150, c1y: 170, c2x: 390, c2y: 200, col: "#5B21B6", sw: 2.5 },
    { ex: 575, ey: 260, c1x: 140, c1y: 220, c2x: 400, c2y: 240, col: "#8B5CF6", sw: 3.4 },
    { ex: 575, ey: 290, c1x: 140, c1y: 250, c2x: 410, c2y: 280, col: "#4C1D95", sw: 2.8 },
    { ex: 575, ey: 320, c1x: 140, c1y: 330, c2x: 410, c2y: 310, col: "#A78BFA", sw: 2.6 },
    { ex: 570, ey: 360, c1x: 150, c1y: 370, c2x: 400, c2y: 350, col: "#7C3AED", sw: 3.2 },
    { ex: 565, ey: 400, c1x: 160, c1y: 410, c2x: 390, c2y: 390, col: "#6D28D9", sw: 2.4 },
    { ex: 555, ey: 440, c1x: 180, c1y: 460, c2x: 370, c2y: 430, col: "#5B21B6", sw: 3.0 },
    { ex: 540, ey: 470, c1x: 200, c1y: 500, c2x: 360, c2y: 460, col: "#8B5CF6", sw: 2.8 },
    // Secondary inner loops
    { ex: 380, ey: 230, c1x: 130, c1y: 200, c2x: 250, c2y: 200, col: "#4C1D95", sw: 2.0 },
    { ex: 380, ey: 360, c1x: 130, c1y: 380, c2x: 250, c2y: 380, col: "#4C1D95", sw: 2.0 },
    { ex: 290, ey: 200, c1x: 120, c1y: 180, c2x: 200, c2y: 170, col: "#7C3AED", sw: 1.8 },
    { ex: 290, ey: 380, c1x: 120, c1y: 400, c2x: 200, c2y: 410, col: "#7C3AED", sw: 1.8 },
  ];
  // Focal point dot
  const dot = `<circle cx="${fx}" cy="${fy}" r="4" fill="#4C1D95"/>`;
  const paths = curves.map(c =>
    `<path d="M${fx},${fy} C${c.c1x},${c.c1y} ${c.c2x},${c.c2y} ${c.ex},${c.ey}" ` +
    `stroke="${c.col}" stroke-width="${c.sw}" fill="none" opacity="0.78" stroke-linecap="round"/>`
  ).join("");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${paths}${dot}</svg>`;
  return svgToPng(svg);
}

// Smooth vertical gradient panel (used for big stat tiles)
async function gradPanelPng(topHex, botHex, w = 200, h = 240) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${topHex}"/>
      <stop offset="100%" stop-color="${botHex}"/>
    </linearGradient></defs>
    <rect width="${w}" height="${h}" fill="url(#g)" rx="14" ry="14"/>
  </svg>`;
  return svgToPng(svg);
}

// 3D ISO stair-step or pyramid-tier block. Single PNG with three faces.
async function block3DPng({ frontW = 240, frontH = 160, depth = 36, base, top, side, rx = 4 }) {
  // Slight diagonal — depth shifts up-right
  const dx = depth, dy = depth * 0.55;
  const W = frontW + dx, H = frontH + dy;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
    <!-- Right side -->
    <polygon points="${frontW},${dy} ${W},0 ${W},${frontH} ${frontW},${H}" fill="${side}"/>
    <!-- Top -->
    <polygon points="0,${dy} ${dx},0 ${W},0 ${frontW},${dy}" fill="${top}"/>
    <!-- Front -->
    <rect x="0" y="${dy}" width="${frontW}" height="${frontH}" fill="${base}" rx="${rx}" ry="${rx}"/>
  </svg>`;
  return svgToPng(svg);
}

// 3D pyramid tier (trapezoid front + top face only — pyramid layers sit on top of each other)
async function tier3DPng({ topW = 160, botW = 260, h = 70, depth = 30, base, top }) {
  const dx = depth, dy = depth * 0.55;
  const W = botW + dx, H = h + dy;
  const offsetTop = (botW - topW) / 2;
  // Front trapezoid: top edge from (offsetTop, dy) to (offsetTop+topW, dy); bottom from (0, H) to (botW, H)
  const frontPts = `${offsetTop},${dy} ${offsetTop + topW},${dy} ${botW},${H} 0,${H}`;
  // Top face: tilted parallelogram from front-top-edge back-up to (+dx, -dy) offset
  const topPts = `${offsetTop},${dy} ${offsetTop + topW},${dy} ${offsetTop + topW + dx},${dy - dy} ${offsetTop + dx},${dy - dy}`;
  // Right side: from front-top-right back-up + down front-right
  const sidePts = `${offsetTop + topW},${dy} ${offsetTop + topW + dx},0 ${botW + dx},${h} ${botW},${H}`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
    <polygon points="${sidePts}" fill="${top}"/>
    <polygon points="${topPts}" fill="${top}"/>
    <polygon points="${frontPts}" fill="${base}"/>
  </svg>`;
  return svgToPng(svg);
}

// ---------- Pre-render assets ----------
async function buildAssets() {
  const purple = "#" + P.purple;
  const purpleDeep = "#" + P.purpleDeep;
  const purpleMid = "#" + P.purpleMid;
  const purpleDark = "#" + P.purpleDark;
  const magenta = "#" + P.magenta;
  const white = "#" + P.white;

  return {
    // Decorative
    curves:   await curvesPng(),
    // Stat gradients
    statGrad: await gradPanelPng("#" + P.statTop, "#" + P.statBot, 200, 240),
    // 3D stair steps (slide 3) — three blocks of increasing height
    step1: await block3DPng({ frontW: 240, frontH: 130, depth: 40, base: "#6D28D9", top: "#A78BFA", side: "#4C1D95" }),
    step2: await block3DPng({ frontW: 240, frontH: 180, depth: 40, base: "#6D28D9", top: "#A78BFA", side: "#4C1D95" }),
    step3: await block3DPng({ frontW: 240, frontH: 240, depth: 40, base: "#7C3AED", top: "#C4B5FD", side: "#5B21B6" }),
    // 3D pyramid tiers (slide 4)
    tier1: await tier3DPng({ topW: 360, botW: 400, h: 75, depth: 28, base: "#3B0F8A", top: "#5B21B6" }),  // base (widest)
    tier2: await tier3DPng({ topW: 290, botW: 340, h: 75, depth: 28, base: "#5B21B6", top: "#7C3AED" }),
    tier3: await tier3DPng({ topW: 220, botW: 280, h: 75, depth: 28, base: "#7C3AED", top: "#A78BFA" }),
    tier4: await tier3DPng({ topW: 150, botW: 210, h: 75, depth: 28, base: "#C026D3", top: "#E879F9" }),   // top (highlight)
    // Icons — slide 1 domains
    claims:       await iconPng(Lu.LuClipboardList, purple),
    eligibility:  await iconPng(Lu.LuCircleCheck, purple),
    enrollment:   await iconPng(Lu.LuUserCheck, purple),
    portals:      await iconPng(Lu.LuMonitor, purple),
    providerData: await iconPng(Lu.LuUsers, purple),
    pharmacy:     await iconPng(Lu.LuPill, purple),
    correspond:   await iconPng(Lu.LuMail, purple),
    reinsurance:  await iconPng(Lu.LuHandshake, purple),
    // Slide 1 stat tile icons (small, low-opacity white)
    qmgr:         await iconPng(Lu.LuServer, white),
    aceNode:      await iconPng(Lu.LuNetwork, white),
    server:       await iconPng(Lu.LuHardDrive, white),
    app:          await iconPng(Lu.LuLayoutGrid, white),
    queue:        await iconPng(Lu.LuInbox, white),
    flow:         await iconPng(Lu.LuWorkflow, white),
    // Slide 2
    user:         await iconPng(Lu.LuUser, purple),
    console:      await iconPng(Lu.LuMonitor, purple),
    // Slide 4 comparison
    train:        await iconPng(Lu.LuTrainFront, purple),
    map:          await iconPng(Lu.LuMap, purple),
    // Slide 5
    chat:         await iconPng(Lu.LuMessageCircle, purple),
    infinity:     await iconPng(Lu.LuInfinity, magenta),
    usb:          await iconPng(Lu.LuUsb, purple),
    shield:       await iconPng(Lu.LuShield, purple),
    database:     await iconPng(Lu.LuDatabase, purple),
    log:          await iconPng(Lu.LuFileText, purple),
    // Slide 6
    bigShield:    await iconPng(Lu.LuShieldCheck, white),
    bigGear:      await iconPng(Lu.LuSettings, white),
    bigChip:      await iconPng(Lu.LuCpu, white),
    tools:        await iconPng(Lu.LuServer, white),
  };
}

// ---------- Shared chrome ----------
function pageNum(slide, num, total = 7) {
  slide.addText(`${num} / ${total}`, {
    x: 9.20, y: 5.30, w: 0.65, h: 0.20,
    fontFace: FONT_B, fontSize: 8, color: P.mutedLight, align: "right", margin: 0, charSpacing: 1,
  });
}

function brandTag(slide) {
  slide.addShape(pres.shapes.OVAL, {
    x: 0.50, y: 0.32, w: 0.10, h: 0.10,
    fill: { color: P.purple }, line: { type: "none" },
  });
  slide.addText("MQ & ACE PLATFORM STORY", {
    x: 0.66, y: 0.27, w: 5, h: 0.20,
    fontFace: FONT_B, fontSize: 8, color: P.muted, bold: true, charSpacing: 5, valign: "middle", margin: 0,
  });
}

// Stat tile (uses the pre-rendered gradient PNG as background)
function purpleStat(slide, x, y, w, h, num, label, iconData, gradPng) {
  slide.addImage({ data: gradPng, x, y, w, h });
  if (iconData) {
    slide.addImage({ data: iconData, x: x + w - 0.42, y: y + 0.08, w: 0.30, h: 0.30, transparency: 35 });
  }
  slide.addText(num, {
    x: x + 0.14, y: y + 0.10, w: w - 0.28, h: h * 0.55,
    fontFace: FONT_H, fontSize: 30, color: P.white, bold: true, align: "left", valign: "middle", margin: 0,
  });
  slide.addText(label, {
    x: x + 0.14, y: y + h * 0.62, w: w - 0.28, h: h * 0.32,
    fontFace: FONT_B, fontSize: 9.5, color: P.lavender, charSpacing: 2, align: "left", valign: "top", margin: 0,
  });
}

// ============================================================
// SLIDE 0 — Title
// ============================================================
function buildSlide0(I) {
  const s = pres.addSlide();
  s.background = { color: P.bg };

  // Flowing curve decoration (SVG-rendered) — left half
  s.addImage({ data: I.curves, x: -0.20, y: 0.30, w: 5.20, h: 4.70 });

  // Title (right half, generous space)
  s.addText("From Console Chaos\nto Conversational\nIntelligence", {
    x: 5.30, y: 1.40, w: 4.50, h: 2.55,
    fontFace: FONT_H, fontSize: 38, color: P.purpleDeep, bold: true, margin: 0, valign: "top",
  });
  s.addText("An MQ/ACE Platform Maturity Story", {
    x: 5.30, y: 4.00, w: 4.50, h: 0.40,
    fontFace: FONT_B, fontSize: 15, color: P.muted, italic: true, margin: 0,
  });

  // Presenter byline (bottom-right)
  s.addShape(pres.shapes.RECTANGLE, {
    x: 6.70, y: 4.70, w: 2.90, h: 0.02,
    fill: { color: P.purple }, line: { type: "none" },
  });
  s.addText("PRESENTED BY", {
    x: 6.70, y: 4.76, w: 2.90, h: 0.20,
    fontFace: FONT_B, fontSize: 8, color: P.muted, bold: true, charSpacing: 5, align: "right", margin: 0,
  });
  s.addText("Ajit K.", {
    x: 6.70, y: 4.96, w: 2.90, h: 0.30,
    fontFace: FONT_H, fontSize: 16, color: P.purpleDeep, bold: true, align: "right", margin: 0,
  });
  s.addText("MQ/ACE Platform Support", {
    x: 6.70, y: 5.26, w: 2.90, h: 0.20,
    fontFace: FONT_B, fontSize: 9, color: P.muted, italic: true, align: "right", margin: 0,
  });

  s.addNotes("Set the room. Over the next few minutes I'll walk you through the platform we run, the daily reality of running it, what we've already done about that, and where we're taking it next — with agentic AI.");
}

// ============================================================
// SLIDE 1 — Overview: 7-layer pyramid + 6 stats + 8 domains
// ============================================================
function buildSlide1(I) {
  const s = pres.addSlide();
  s.background = { color: P.bg };

  brandTag(s);

  s.addText("IBM MQ guarantees the message, while IBM ACE transforms it", {
    x: 0.50, y: 0.70, w: 9.00, h: 0.75,
    fontFace: FONT_H, fontSize: 24, color: P.text, bold: true, margin: 0, valign: "top",
  });

  // 7-layer pyramid stack (uniform width, varied colour, ACE/MQ highlighted)
  const stackX = 0.50, stackY = 1.65, stackW = 3.60, layerH = 0.40, layerGap = 0.06;
  const layers = [
    { lbl: "1. Channel",           fill: P.lavenderPale, fg: P.text },
    { lbl: "2. Gateway",           fill: P.lavender,     fg: P.text },
    { lbl: "3. Orchestration",     fill: P.purpleLight,  fg: P.white },
    { lbl: "4. Integration  (ACE)", fill: P.purple,       fg: P.white, hi: true },
    { lbl: "5. Messaging  (MQ)",   fill: P.purpleDeep,   fg: P.white, hi: true },
    { lbl: "6. Systems of Record", fill: P.purpleLight,  fg: P.white },
    { lbl: "7. Data",              fill: P.lavender,     fg: P.text },
  ];
  layers.forEach((L, i) => {
    const y = stackY + i * (layerH + layerGap);
    s.addShape(pres.shapes.RECTANGLE, {
      x: stackX, y, w: stackW, h: layerH,
      fill: { color: L.fill }, line: { color: P.purpleDeep, width: L.hi ? 0.75 : 0.25 },
      shadow: L.hi ? shMed() : shSoft(),
    });
    s.addText(L.lbl, {
      x: stackX + 0.20, y, w: stackW - 0.40, h: layerH,
      fontFace: FONT_B, fontSize: 11, color: L.fg, bold: L.hi, align: "left", valign: "middle", margin: 0,
    });
  });

  // 6 stat tiles, 3x2
  const stats = [
    ["150+",   "Queue Managers",       I.qmgr],
    ["60",     "ACE Integration Nodes", I.aceNode],
    ["200",    "Integration Servers",  I.server],
    ["600",    "Deployed Applications", I.app],
    ["5,000+", "Active Queues",        I.queue],
    ["8,000+", "Message Flows",        I.flow],
  ];
  const gX = 4.55, gY = 1.65, gCols = 3, gGap = 0.12;
  const gW = (10 - gX - 0.50 - (gCols - 1) * gGap) / gCols;
  const gH = 1.20;
  stats.forEach(([n, lab, ic], i) => {
    const col = i % gCols, row = Math.floor(i / gCols);
    purpleStat(s, gX + col * (gW + gGap), gY + row * (gH + 0.14), gW, gH, n, lab, ic, I.statGrad);
  });

  // 8 insurance domain chips
  const domains = [
    ["Claims",         I.claims],
    ["Eligibility",    I.eligibility],
    ["Enrollment",     I.enrollment],
    ["Portals",        I.portals],
    ["Provider Data",  I.providerData],
    ["Pharmacy",       I.pharmacy],
    ["Correspondence", I.correspond],
    ["Reinsurance",    I.reinsurance],
  ];
  const dY = 4.85, dH = 0.55, dGap = 0.06;
  const dW = (9.0 - (domains.length - 1) * dGap) / domains.length;
  let dx = 0.50;
  domains.forEach(([name, ic]) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: dx, y: dY, w: dW, h: dH,
      fill: { color: P.lavenderFog }, line: { color: P.lavender, width: 0.5 },
      rectRadius: 0.06,
    });
    s.addImage({ data: ic, x: dx + 0.10, y: dY + 0.12, w: 0.32, h: 0.32 });
    s.addText(name, {
      x: dx + 0.46, y: dY, w: dW - 0.52, h: dH,
      fontFace: FONT_B, fontSize: 9, color: P.purpleDeep, bold: true, valign: "middle", margin: 0,
    });
    dx += dW + dGap;
  });

  pageNum(s, 2);
  s.addNotes("MQ owns the transport guarantee; ACE owns the translation. The carrier can swap a portal, upgrade a claims engine, or onboard a new partner without touching the rest — because everything meets in the middle on a queue or a flow. That's the architecture on paper. The next slide is what running it actually looks like, day to day.");
}

// ============================================================
// SLIDE 2 — Pain: detective trail
// ============================================================
function buildSlide2(I) {
  const s = pres.addSlide();
  s.background = { color: P.bg };

  brandTag(s);

  s.addText("Every routine diagnostic requires a detective trail across disconnected tools", {
    x: 0.50, y: 0.70, w: 9.00, h: 0.85,
    fontFace: FONT_H, fontSize: 22, color: P.text, bold: true, margin: 0, valign: "top",
  });

  // Person medallion
  s.addShape(pres.shapes.OVAL, {
    x: 0.60, y: 2.00, w: 1.20, h: 1.20,
    fill: { color: P.lavenderPale }, line: { color: P.purpleLight, width: 1 },
  });
  s.addImage({ data: I.user, x: 0.78, y: 2.18, w: 0.84, h: 0.84 });
  s.addText("Platform admin", {
    x: 0.40, y: 3.25, w: 1.60, h: 0.25,
    fontFace: FONT_B, fontSize: 9, color: P.muted, bold: true, align: "center", margin: 0,
  });

  // 6 console nodes connected by dashed lines
  const nodes = [
    { x: 2.50, y: 1.85, lbl: "Console A" },
    { x: 4.30, y: 1.75, lbl: "Monitor B" },
    { x: 6.20, y: 1.95, lbl: "Log C" },
    { x: 3.10, y: 2.95, lbl: "Config D" },
    { x: 5.30, y: 3.10, lbl: "Events E" },
    { x: 7.40, y: 2.85, lbl: "Audit F" },
  ];
  const ox = 1.85, oy = 2.60;
  nodes.forEach((n) => {
    const tx = n.x + 0.45, ty = n.y + 0.20;
    s.addShape(pres.shapes.LINE, {
      x: ox, y: oy, w: tx - ox, h: ty - oy,
      line: { color: P.purpleLight, width: 1, dashType: "dash" },
    });
  });
  nodes.forEach((n) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: n.x, y: n.y, w: 1.00, h: 0.40,
      fill: { color: P.lavenderFog }, line: { color: P.purple, width: 0.75 },
      rectRadius: 0.05,
    });
    s.addImage({ data: I.console, x: n.x + 0.05, y: n.y + 0.08, w: 0.24, h: 0.24 });
    s.addText(n.lbl, {
      x: n.x + 0.30, y: n.y, w: 0.70, h: 0.40,
      fontFace: FONT_B, fontSize: 9, color: P.text, bold: true, valign: "middle", margin: 0,
    });
  });

  s.addText("No single pane of glass.\nDozens of consoles per question.", {
    x: 8.30, y: 1.85, w: 1.50, h: 0.90,
    fontFace: FONT_B, fontSize: 9.5, color: P.muted, italic: true, margin: 0, valign: "top",
  });

  // Bottom stat callouts (4 deep-purple)
  const blStats = [
    { n: "20–45 min", d: "To trace a stuck message or answer a routine query" },
    { n: "16×5",      d: "Our coverage vs a 24×7 platform requirement" },
    { n: "2–3",       d: "Subject Matter Experts holding deep tribal knowledge" },
    { n: "Months",    d: "Time required to onboard a new platform admin" },
  ];
  const bY = 3.80, bH = 1.35, bGap = 0.12;
  const bW = (9.0 - (blStats.length - 1) * bGap) / blStats.length;
  blStats.forEach((b, i) => {
    const bx = 0.50 + i * (bW + bGap);
    s.addShape(pres.shapes.RECTANGLE, {
      x: bx, y: bY, w: bW, h: bH,
      fill: { color: P.statBot }, line: { type: "none" },
      shadow: shCard(),
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x: bx, y: bY, w: bW, h: 0.04,
      fill: { color: P.magenta }, line: { type: "none" },
    });
    s.addText(b.n, {
      x: bx + 0.15, y: bY + 0.10, w: bW - 0.30, h: 0.55,
      fontFace: FONT_H, fontSize: 26, color: P.white, bold: true, align: "left", valign: "middle", margin: 0,
    });
    s.addText(b.d, {
      x: bx + 0.15, y: bY + 0.70, w: bW - 0.30, h: 0.55,
      fontFace: FONT_B, fontSize: 9.5, color: P.lavender, align: "left", valign: "top", margin: 0,
    });
  });

  pageNum(s, 3);
  s.addNotes("Every pain on this slide has the same root cause: the diagnostic experience is manual, fragmented, and expert-gated. It's not a staffing problem — adding people doesn't fix it, because the tooling forces the same workflow on every person. We haven't sat still: over the last two years the team has built thirteen in-house automations that take the most painful, repetitive work off our plate — the next slide walks through that journey, and the five tracks they now cover.");
}

// ============================================================
// SLIDE 3 — Automation: 3D stair steps
// ============================================================
function buildSlide3(I) {
  const s = pres.addSlide();
  s.background = { color: P.bg };

  brandTag(s);

  s.addText("Two years of in-house automation eliminated predictable manual toil", {
    x: 0.50, y: 0.70, w: 9.00, h: 0.85,
    fontFace: FONT_H, fontSize: 22, color: P.text, bold: true, margin: 0, valign: "top",
  });

  // Top right: dashboard preview cards
  const dashes = ["Configuration\nDashboard", "Monitor\nDashboard", "Auto-\nCertification"];
  const tY = 1.65, tH = 0.90, tW = 1.30, tGap = 0.12;
  let tx = 5.30;
  dashes.forEach((title) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: tx, y: tY, w: tW, h: tH,
      fill: { color: P.white }, line: { color: P.purple, width: 0.75 },
      rectRadius: 0.05, shadow: shSoft(),
    });
    // mini chart area
    s.addShape(pres.shapes.RECTANGLE, {
      x: tx + 0.08, y: tY + 0.08, w: tW - 0.16, h: 0.40,
      fill: { color: P.lavenderPale }, line: { color: P.purpleLight, width: 0.5 },
    });
    for (let j = 0; j < 4; j++) {
      const bh = 0.08 + j * 0.05;
      s.addShape(pres.shapes.RECTANGLE, {
        x: tx + 0.14 + j * 0.24, y: tY + 0.48 - bh, w: 0.18, h: bh,
        fill: { color: P.purple }, line: { type: "none" },
      });
    }
    s.addText(title, {
      x: tx, y: tY + 0.52, w: tW, h: 0.38,
      fontFace: FONT_B, fontSize: 9, color: P.text, bold: true, align: "center", valign: "middle", margin: 0,
    });
    tx += tW + tGap;
  });

  // 3D stair steps — each step uses pre-rendered 3D block PNG; text overlaid
  // Step dimensions:
  //   step1 frontH=130 (1.30"), step2 180 (1.80"), step3 240 (2.40")
  //   We size each to a consistent width visually and place baselines aligned.
  const baseY = 5.05; // baseline (bottom of all steps in inches)
  const sW = 1.95;    // visual width per step
  const sGap = 0.28;
  const sStartX = 0.55;
  const stepsMeta = [
    { era: "Pre-2024", num: "0",  bg: I.step1, frontH_in: 1.30 },
    { era: "2024",     num: "9",  bg: I.step2, frontH_in: 1.80 },
    { era: "2025",     num: "13", bg: I.step3, frontH_in: 2.40 },
  ];
  // PNG aspect: frontW=240 + depth=40 = 280 wide; frontH varies + depth*0.55=22 px
  // We'll size the image so its frontW fits sW (image total width slightly larger than sW for the depth)
  const imgWFactor = (240 + 40) / 240; // ratio
  const imgHForFront = (frontHpx) => (frontHpx + 22) / 240 * (sW); // proportional
  // Simpler: hardcode dimensions
  // step1 image: width = sW * (280/240) = 2.275"; height = (130+22)/240 * 2.275 = 1.44"
  // Each step image displays with front bottom at baseY.
  stepsMeta.forEach((sp, i) => {
    const imgW = sW * (280 / 240);
    const frontHpx = (sp.bg === I.step1) ? 130 : (sp.bg === I.step2) ? 180 : 240;
    const totalHpx = frontHpx + 22;
    const imgH = (totalHpx / 240) * sW; // scale height proportionally
    const x = sStartX + i * (sW + sGap);
    const y = baseY - sp.frontH_in - 0.22; // 0.22" for depth top
    s.addImage({ data: sp.bg, x, y, w: imgW, h: imgH });

    // Text overlays on the front face of the step
    // Front face starts at y + (depth offset = 0.22" * (dy/totalY) hmm). Just put text in approx area.
    const frontTop = baseY - sp.frontH_in;
    // Era label
    s.addText(sp.era, {
      x: x + 0.16, y: frontTop + 0.10, w: sW - 0.32, h: 0.25,
      fontFace: FONT_B, fontSize: 10, color: P.lavender, bold: true, charSpacing: 3, margin: 0,
    });
    // Big number
    s.addText(sp.num, {
      x: x, y: frontTop + 0.35, w: sW, h: sp.frontH_in - 0.55,
      fontFace: FONT_H, fontSize: 60, color: P.white, bold: true, align: "center", valign: "middle", margin: 0,
    });
    // descriptor (under the step)
    s.addText("automations", {
      x: x, y: baseY + 0.05, w: sW, h: 0.25,
      fontFace: FONT_B, fontSize: 11, color: P.purpleDeep, italic: true, align: "center", margin: 0,
    });
  });

  // Ground line under steps
  s.addShape(pres.shapes.LINE, {
    x: 0.40, y: baseY, w: 6.50, h: 0,
    line: { color: P.purpleLight, width: 0.75 },
  });

  pageNum(s, 4);
  s.addNotes("Thirteen automations is real progress — but it isn't the finish line. Each automation still answers a predefined question; anything outside the script still escalates to a human. The next step is closing that gap.");
}

// ============================================================
// SLIDE 4 — Maturity pyramid + comparison cards
// ============================================================
function buildSlide4(I) {
  const s = pres.addSlide();
  s.background = { color: P.bg };

  brandTag(s);

  s.addText("Observability answers what we scripted, but Agentic AI answers what we didn't", {
    x: 0.50, y: 0.70, w: 9.00, h: 0.85,
    fontFace: FONT_H, fontSize: 22, color: P.text, bold: true, margin: 0, valign: "top",
  });

  // 3D pyramid built from SVG tier PNGs, stacked centered
  // Each tier image has slightly different botW; we center horizontally
  // Place from bottom up
  const pyCx = 2.80, pyBottom = 4.95;
  const tiers = [
    { img: I.tier1, lbl: "1. The Estate",         botW_in: 4.00, h_in: 0.85 },
    { img: I.tier2, lbl: "2. Observability",      botW_in: 3.40, h_in: 0.85 },
    { img: I.tier3, lbl: "3. Self-Healing",       botW_in: 2.80, h_in: 0.85 },
    { img: I.tier4, lbl: "4. Agentic AI Self-Service", botW_in: 2.10, h_in: 0.85 },
  ];
  // Image aspect: total width = botW + depth (28px = 0.28"); total height = h + 15
  let yCur = pyBottom;
  tiers.forEach((t) => {
    const imgW = t.botW_in + 0.28; // depth in inches
    const imgH = t.h_in + 0.16;
    const x = pyCx - t.botW_in / 2;
    const y = yCur - t.h_in;
    s.addImage({ data: t.img, x, y: y - 0.16, w: imgW, h: imgH });
    // Label on the front face (centered horizontally over the tier)
    s.addText(t.lbl, {
      x: pyCx - t.botW_in / 2, y, w: t.botW_in, h: t.h_in,
      fontFace: FONT_B, fontSize: 11, color: P.white, bold: true, align: "center", valign: "middle", margin: 0,
    });
    yCur -= t.h_in + 0.05;
  });

  // Comparison cards on the right
  const cardX = 6.10, cardW = 3.40, cardH = 1.55, cardGap = 0.22;
  const cards = [
    { tag: "Scripted Automation", icon: I.train,
      body: "Answers predefined questions.\nRuns on schedules or thresholds." },
    { tag: "Agentic AI", icon: I.map,
      body: "Picks the right tool dynamically.\nReasons across the estate.\nPlain English in, structured answer out." },
  ];
  let cY = 1.75;
  cards.forEach((c) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: cardX, y: cY, w: cardW, h: cardH,
      fill: { color: P.white }, line: { color: P.lavender, width: 1 },
      rectRadius: 0.08,
      shadow: shCard(),
    });
    // icon medallion
    s.addShape(pres.shapes.OVAL, {
      x: cardX + (cardW - 0.55) / 2, y: cY + 0.15, w: 0.55, h: 0.55,
      fill: { color: P.lavenderPale }, line: { color: P.purpleLight, width: 0.75 },
    });
    s.addImage({ data: c.icon, x: cardX + (cardW - 0.36) / 2, y: cY + 0.24, w: 0.36, h: 0.36 });
    s.addText(c.tag, {
      x: cardX, y: cY + 0.75, w: cardW, h: 0.28,
      fontFace: FONT_B, fontSize: 12, color: P.purpleDeep, bold: true, align: "center", margin: 0,
    });
    s.addText(c.body, {
      x: cardX + 0.20, y: cY + 1.02, w: cardW - 0.40, h: cardH - 1.05,
      fontFace: FONT_B, fontSize: 10, color: P.muted, align: "center", valign: "top", margin: 0,
    });
    cY += cardH + cardGap;
  });

  pageNum(s, 5);
  s.addNotes("Slide 3 was about taking known toil off the team's plate. Slide 4 is about taking the rest off — the free-form, unpredictable, \"I just need to know X\" questions that no script can anticipate. This isn't a chatbot bolted onto a dashboard; it's an autonomous reasoner that decides which diagnostics to run, in what order, and how to present the answer — within guardrails we set. The foundation we built in 2024-25 is exactly what makes this safe and credible to roll out.");
}

// ============================================================
// SLIDE 5 — Architecture: 5-layer flow + audit branch
// ============================================================
function buildSlide5(I) {
  const s = pres.addSlide();
  s.background = { color: P.bg };

  brandTag(s);

  s.addText("Autonomous reasoning streams through an open protocol and strict safety layers", {
    x: 0.50, y: 0.70, w: 9.00, h: 0.85,
    fontFace: FONT_H, fontSize: 22, color: P.text, bold: true, margin: 0, valign: "top",
  });

  const layers = [
    { tag: "Web Chat UI",      sub: "(Streamlit)",                              icon: I.chat },
    { tag: "Agentic Backend",  sub: "(FastAPI / LangGraph /\nGPT-5.5)",          icon: I.infinity, special: true },
    { tag: "MCP Server",       sub: "(FastMCP)",                                icon: I.usb },
    { tag: "Safety\nLayer",    sub: "",                                         icon: I.shield },
    { tag: "Target Systems",   sub: "Live\nIBM MQ, IBM ACE,\nOffline Inventory CSVs", icon: I.database },
  ];
  const lW = 1.55, lH = 1.65, arrowW = 0.20;
  const totalW = layers.length * lW + (layers.length - 1) * arrowW;
  const startX = (10 - totalW) / 2;
  const layerY = 1.90;
  let lx = startX;
  layers.forEach((L, i) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: lx, y: layerY, w: lW, h: lH,
      fill: { color: P.white }, line: { color: P.purple, width: 1 },
      rectRadius: 0.08,
      shadow: shCard(),
    });
    // Icon in medallion at top
    s.addShape(pres.shapes.OVAL, {
      x: lx + (lW - 0.65) / 2, y: layerY + 0.15, w: 0.65, h: 0.65,
      fill: { color: L.special ? P.magenta : P.lavenderPale },
      line: { color: P.purple, width: 0.75 },
    });
    s.addImage({
      data: L.icon,
      x: lx + (lW - 0.42) / 2, y: layerY + 0.27, w: 0.42, h: 0.42,
    });
    // Tag
    s.addText(L.tag, {
      x: lx + 0.08, y: layerY + 0.88, w: lW - 0.16, h: 0.35,
      fontFace: FONT_B, fontSize: 11, color: P.purpleDeep, bold: true, align: "center", valign: "middle", margin: 0,
    });
    // Sub
    s.addText(L.sub, {
      x: lx + 0.08, y: layerY + 1.23, w: lW - 0.16, h: 0.40,
      fontFace: FONT_B, fontSize: 8, color: P.muted, italic: true, align: "center", valign: "top", margin: 0,
    });
    lx += lW;
    if (i < layers.length - 1) {
      s.addShape(pres.shapes.RIGHT_ARROW, {
        x: lx + 0.02, y: layerY + (lH - 0.20) / 2, w: arrowW - 0.04, h: 0.20,
        fill: { color: P.purple }, line: { type: "none" },
      });
      lx += arrowW;
    }
  });

  // Audit branch
  const mcpCenterX = startX + 2 * (lW + arrowW) + lW / 2;
  s.addShape(pres.shapes.LINE, {
    x: mcpCenterX, y: layerY + lH, w: 0, h: 0.38,
    line: { color: P.purple, width: 1, dashType: "dash", endArrowType: "triangle" },
  });
  const auY = layerY + lH + 0.42, auW = 2.40, auH = 0.78;
  const auX = mcpCenterX - auW / 2;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: auX, y: auY, w: auW, h: auH,
    fill: { color: P.lavenderPale }, line: { color: P.purple, width: 0.75 },
    rectRadius: 0.06,
    shadow: shSoft(),
  });
  s.addImage({ data: I.log, x: auX + 0.18, y: auY + 0.16, w: 0.46, h: 0.46 });
  s.addText("Audit Log", {
    x: auX + 0.76, y: auY + 0.08, w: auW - 0.86, h: 0.28,
    fontFace: FONT_B, fontSize: 12, color: P.purpleDeep, bold: true, valign: "middle", margin: 0,
  });
  s.addText("(JSONL · Power BI)", {
    x: auX + 0.76, y: auY + 0.38, w: auW - 0.86, h: 0.30,
    fontFace: FONT_B, fontSize: 9, color: P.muted, italic: true, valign: "top", margin: 0,
  });

  pageNum(s, 6);
  s.addNotes("This is not a chatbot with an API stitched to the back. It is an autonomous reasoning agent with a bounded, audited surface area — every layer was chosen so the security, observability, and read-only guarantees are properties of the architecture itself, not policies that have to be re-enforced by every developer. The next slide opens the hood further — the engineering controls, coding standards, security shield, and governance that make this safe to roll out against production diagnostics.");
}

// ============================================================
// SLIDE 6 — Engineering rigour: pillars + shield + tools
// ============================================================
function buildSlide6(I) {
  const s = pres.addSlide();
  s.background = { color: P.bg };

  brandTag(s);

  s.addText("Production-ready AI demands capabilities you can trust and controls you can prove", {
    x: 0.50, y: 0.70, w: 9.00, h: 0.85,
    fontFace: FONT_H, fontSize: 21, color: P.text, bold: true, margin: 0, valign: "top",
  });

  // Shield badge top-right with concentric rings
  const shCx = 8.40, shCy = 2.65, shR = 1.00;
  // outer halo (very pale)
  s.addShape(pres.shapes.OVAL, {
    x: shCx - shR - 0.20, y: shCy - shR - 0.20, w: 2 * (shR + 0.20), h: 2 * (shR + 0.20),
    fill: { color: P.lavenderPale }, line: { type: "none" }, transparency: 50,
  });
  // mid ring
  s.addShape(pres.shapes.OVAL, {
    x: shCx - shR, y: shCy - shR, w: 2 * shR, h: 2 * shR,
    fill: { color: P.purple }, line: { color: P.purpleDeep, width: 1.5 },
    shadow: shCard(),
  });
  // inner ring
  s.addShape(pres.shapes.OVAL, {
    x: shCx - shR + 0.18, y: shCy - shR + 0.18, w: 2 * shR - 0.36, h: 2 * shR - 0.36,
    fill: { color: P.purpleDeep }, line: { color: P.purpleLight, width: 1 },
  });
  s.addImage({ data: I.bigShield, x: shCx - 0.42, y: shCy - 0.50, w: 0.84, h: 0.84 });
  s.addText("Defense-\nin-Depth", {
    x: shCx - shR, y: shCy + 0.36, w: 2 * shR, h: 0.45,
    fontFace: FONT_B, fontSize: 10, color: P.white, bold: true, align: "center", valign: "top", margin: 0,
  });

  // 3 pillar cards
  const pillars = [
    { tag: "Capabilities", icon: I.bigChip, sub: "Autonomous reasoning · 14 read-only tools · open MCP protocol" },
    { tag: "Controls",     icon: I.bigShield, sub: "Read-only by construction · 6-layer defence-in-depth" },
    { tag: "Craft",        icon: I.bigGear, sub: "Single-route enforcement · per-call audit · ContextVars" },
  ];
  const pW = 2.20, pH = 2.05, pGap = 0.16;
  let pX = 0.50;
  pillars.forEach((pl) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: pX, y: 1.65, w: pW, h: pH,
      fill: { color: P.white }, line: { color: P.lavender, width: 1 },
      rectRadius: 0.08,
      shadow: shCard(),
    });
    s.addShape(pres.shapes.RECTANGLE, {
      x: pX, y: 1.65, w: pW, h: 0.06,
      fill: { color: P.purple }, line: { type: "none" },
    });
    s.addShape(pres.shapes.OVAL, {
      x: pX + (pW - 0.65) / 2, y: 1.85, w: 0.65, h: 0.65,
      fill: { color: P.purple }, line: { type: "none" },
    });
    s.addImage({ data: pl.icon, x: pX + (pW - 0.42) / 2, y: 1.95, w: 0.42, h: 0.42 });
    s.addText(pl.tag, {
      x: pX, y: 2.55, w: pW, h: 0.32,
      fontFace: FONT_H, fontSize: 14, color: P.purpleDeep, bold: true, align: "center", margin: 0,
    });
    s.addText(pl.sub, {
      x: pX + 0.15, y: 2.90, w: pW - 0.30, h: 0.75,
      fontFace: FONT_B, fontSize: 9.5, color: P.muted, align: "center", valign: "top", margin: 0,
    });
    pX += pW + pGap;
  });

  // GET-ONLY banner
  const banY = 3.90, banH = 0.38;
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.50, y: banY, w: 9.00, h: banH,
    fill: { color: P.purpleDeep }, line: { type: "none" },
  });
  s.addText("GET-ONLY  ·  NO MODIFY VERBS  ·  HOSTNAME ALLOW-LIST  ·  10/12 AGENTIC COMPONENTS", {
    x: 0.50, y: banY, w: 9.00, h: banH,
    fontFace: FONT_B, fontSize: 10, color: P.white, bold: true, charSpacing: 4, align: "center", valign: "middle", margin: 0,
  });

  // 2 tool boxes
  const boxY = 4.45, boxH = 0.90, boxGap = 0.20;
  const boxW = (9.0 - boxGap) / 2;

  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.50, y: boxY, w: boxW, h: boxH,
    fill: { color: P.statBot }, line: { type: "none" },
    rectRadius: 0.08, shadow: shMed(),
  });
  s.addImage({ data: I.tools, x: 0.65, y: boxY + 0.18, w: 0.50, h: 0.50 });
  s.addText("7 IBM MQ Tools", {
    x: 1.30, y: boxY + 0.10, w: boxW - 1.40, h: 0.32,
    fontFace: FONT_H, fontSize: 16, color: P.white, bold: true, valign: "middle", margin: 0,
  });
  s.addText("find_mq_object · dspmq · dspmqver · runmqsc · run_mqsc_for_object · get_queue_depth · get_channel_status", {
    x: 1.30, y: boxY + 0.42, w: boxW - 1.40, h: 0.42,
    fontFace: FONT_M, fontSize: 8, color: P.lavender, valign: "top", margin: 0,
  });

  const aceX = 0.50 + boxW + boxGap;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: aceX, y: boxY, w: boxW, h: boxH,
    fill: { color: P.statBot }, line: { type: "none" },
    rectRadius: 0.08, shadow: shMed(),
  });
  s.addImage({ data: I.tools, x: aceX + 0.15, y: boxY + 0.18, w: 0.50, h: 0.50 });
  s.addText("6 IBM ACE Tools", {
    x: aceX + 0.80, y: boxY + 0.10, w: boxW - 0.90, h: 0.32,
    fontFace: FONT_H, fontSize: 16, color: P.white, bold: true, valign: "middle", margin: 0,
  });
  s.addText("list_ace_nodes · get_ace_node_status · list_ace_servers · list_ace_applications · list_ace_message_flows · search_ace_local_dump", {
    x: aceX + 0.80, y: boxY + 0.42, w: boxW - 0.90, h: 0.42,
    fontFace: FONT_M, fontSize: 8, color: P.lavender, valign: "top", margin: 0,
  });

  pageNum(s, 7);
  s.addNotes("The agentic AI is not a demo glued to our platform — it's a governed system whose security, observability, and read-only guarantees are architectural properties, not policies that have to be re-enforced by every developer who touches the codebase. That distinction is what makes it credible to run against production diagnostics, and what lets us onboard new tools (or new domains entirely) without lowering the bar.");
}

// ---------- Build all ----------
(async () => {
  console.log("Rendering assets (curves, gradients, 3D blocks, icons)...");
  const I = await buildAssets();
  console.log("Assets ready. Building slides...");

  buildSlide0(I);
  buildSlide1(I);
  buildSlide2(I);
  buildSlide3(I);
  buildSlide4(I);
  buildSlide5(I);
  buildSlide6(I);

  await pres.writeFile({ fileName: OUT });
  console.log("WROTE:", OUT);
  await repairContentTypes(OUT);
  console.log("REPAIRED Content_Types overrides");
})().catch((e) => {
  console.error("BUILD FAILED:", e);
  process.exit(1);
});

// ----- Post-process: strip phantom slideMaster overrides from Content_Types.xml -----
// pptxgenjs 4.0.1 writes <Override> entries for slideMaster2..N that don't exist on
// disk, which causes PowerPoint to prompt for "repair" on open. This walks the file,
// finds Override declarations pointing at non-existent parts, and removes them.
async function repairContentTypes(file) {
  const data = fs.readFileSync(file);
  const zip = await JSZip.loadAsync(data);
  const ctName = "[Content_Types].xml";
  const ctFile = zip.file(ctName);
  if (!ctFile) return;
  let xml = await ctFile.async("string");
  // Find every Override PartName and check if that file exists in the archive
  const re = /<Override\s+PartName="([^"]+)"\s+ContentType="[^"]+"\s*\/>/g;
  let removed = 0;
  xml = xml.replace(re, (match, partPath) => {
    // partPath looks like "/ppt/slideMasters/slideMaster2.xml" — strip leading slash for zip lookup
    const lookup = partPath.replace(/^\//, "");
    if (!zip.file(lookup)) {
      removed++;
      return ""; // drop this Override
    }
    return match;
  });
  if (removed === 0) return;
  zip.file(ctName, xml);
  // Write the modified zip back to disk
  const buf = await zip.generateAsync({
    type: "nodebuffer",
    compression: "DEFLATE",
    compressionOptions: { level: 6 },
  });
  fs.writeFileSync(file, buf);
  console.log(`  stripped ${removed} phantom Override(s)`);
}
