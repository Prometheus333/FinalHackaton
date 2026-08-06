import React, { useEffect, useRef, useState, useCallback } from "react";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  BrainCircuit,
  Check,
  Gauge,
  LineChart,
  Radar,
  ShieldHalf,
  Sparkles,
  Terminal,
  Zap,
} from "lucide-react";

/* ------------------------------------------------------------------ *
 *  MERIDIAN — AI trading assistant landing page
 *  Palette   void #04060B · cyan #2DE2E6 · emerald #3DDC97
 *            violet #8B5CF6 · rose #FF5C7A · text #E8EEF7
 *  Type      Space Grotesk (display) · Inter (body) · IBM Plex Mono (data)
 *  Signature Cursor-reactive neural orb with travelling signal pulses
 * ------------------------------------------------------------------ */

const CSS = `
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

.mrd { 
  --void:#04060B; --ink:#080C15; --line:rgba(255,255,255,.08);
  --cyan:#2DE2E6; --emerald:#3DDC97; --violet:#8B5CF6; --rose:#FF5C7A;
  --text:#E8EEF7; --muted:#7C8AA0;
  background:var(--void); color:var(--text); position:relative; overflow-x:hidden;
  font-family:'Inter',ui-sans-serif,system-ui,-apple-system,sans-serif;
  -webkit-font-smoothing:antialiased;
}
.mrd *,.mrd *::before,.mrd *::after{box-sizing:border-box;}
.mrd h1,.mrd h2,.mrd h3,.mrd .disp{font-family:'Space Grotesk',ui-sans-serif,system-ui,sans-serif;}
.mrd .mono{font-family:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace;}
.mrd p{margin:0;}
.mrd button{font:inherit;color:inherit;cursor:pointer;}
.mrd :focus-visible{outline:2px solid var(--cyan);outline-offset:3px;border-radius:4px;}

/* ---------- atmosphere ---------- */
.mrd-bg{position:fixed;inset:0;pointer-events:none;z-index:0;}
.mrd-grid{position:absolute;inset:0;
  background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),
                   linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);
  background-size:64px 64px;
  mask-image:radial-gradient(ellipse 80% 60% at 50% 0%,#000 30%,transparent 78%);
  -webkit-mask-image:radial-gradient(ellipse 80% 60% at 50% 0%,#000 30%,transparent 78%);}
.mrd-blob{position:absolute;border-radius:999px;filter:blur(90px);opacity:.5;}
.mrd-noise{position:absolute;inset:0;opacity:.22;mix-blend-mode:overlay;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3'/%3E%3C/filter%3E%3Crect width='140' height='140' filter='url(%23n)' opacity='.5'/%3E%3C/svg%3E");}

/* ---------- shell ---------- */
.mrd-wrap{position:relative;z-index:1;max-width:1200px;margin:0 auto;padding:0 24px;}
.mrd-sec{padding:112px 0;position:relative;}
@media(max-width:760px){.mrd-sec{padding:76px 0;} .mrd-wrap{padding:0 18px;}}

/* ---------- glass ---------- */
.glass{background:linear-gradient(160deg,rgba(255,255,255,.058),rgba(255,255,255,.018));
  border:1px solid var(--line);border-radius:18px;
  backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
  box-shadow:0 24px 60px -30px rgba(0,0,0,.9), inset 0 1px 0 rgba(255,255,255,.07);}

/* ---------- nav ---------- */
.nav{position:fixed;top:0;left:0;right:0;z-index:40;transition:.35s ease;
  border-bottom:1px solid transparent;}
.nav.on{background:rgba(4,6,11,.72);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  border-bottom-color:var(--line);}
.nav-in{max-width:1200px;margin:0 auto;padding:16px 24px;display:flex;align-items:center;gap:28px;}
.nav a.lnk{color:var(--muted);text-decoration:none;font-size:14px;transition:color .2s;}
.nav a.lnk:hover{color:var(--text);}
@media(max-width:860px){.nav-links{display:none;}}

/* ---------- buttons ---------- */
.btn{display:inline-flex;align-items:center;gap:9px;padding:13px 22px;border-radius:12px;
  font-weight:600;font-size:14.5px;border:1px solid transparent;transition:.25s ease;
  letter-spacing:.1px;text-decoration:none;}
.btn-p{background:linear-gradient(120deg,#2DE2E6,#5EEAD4 45%,#8B5CF6);color:#04060B;
  box-shadow:0 0 0 1px rgba(45,226,230,.35),0 12px 34px -12px rgba(45,226,230,.7);}
.btn-p:hover{transform:translateY(-2px);box-shadow:0 0 0 1px rgba(45,226,230,.6),0 18px 44px -12px rgba(45,226,230,.85);}
.btn-g{background:rgba(255,255,255,.045);border-color:var(--line);color:var(--text);}
.btn-g:hover{background:rgba(255,255,255,.09);border-color:rgba(45,226,230,.45);transform:translateY(-2px);}
.btn-s{padding:9px 16px;font-size:13.5px;border-radius:10px;}

/* ---------- eyebrow / labels ---------- */
.eyebrow{display:inline-flex;align-items:center;gap:8px;font-size:11.5px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--muted);}
.tag{display:inline-flex;align-items:center;gap:7px;padding:6px 12px;border-radius:999px;
  border:1px solid var(--line);background:rgba(255,255,255,.04);font-size:12px;color:#B7C4D6;}
.dot{width:6px;height:6px;border-radius:999px;background:var(--emerald);
  box-shadow:0 0 10px var(--emerald);animation:beat 2s infinite;}
@keyframes beat{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.4;transform:scale(.7)}}

/* ---------- type ---------- */
.h1{font-size:clamp(38px,6.2vw,74px);line-height:1.02;letter-spacing:-.035em;font-weight:600;margin:0;}
.h2{font-size:clamp(28px,3.9vw,46px);line-height:1.08;letter-spacing:-.03em;font-weight:600;margin:0;}
.grad{background:linear-gradient(100deg,#2DE2E6 0%,#7DD3FC 38%,#A78BFA 78%);
  -webkit-background-clip:text;background-clip:text;color:transparent;}
.lead{color:var(--muted);font-size:17px;line-height:1.62;max-width:56ch;}
@media(max-width:760px){.lead{font-size:15.5px;}}

/* ---------- reveal ---------- */
.rv{opacity:0;transform:translateY(26px);transition:opacity .8s cubic-bezier(.2,.7,.3,1),transform .8s cubic-bezier(.2,.7,.3,1);}
.rv.in{opacity:1;transform:none;}

/* ---------- ticker ---------- */
.tick{border-top:1px solid var(--line);border-bottom:1px solid var(--line);
  background:rgba(255,255,255,.02);overflow:hidden;position:relative;}
.tick::before,.tick::after{content:'';position:absolute;top:0;bottom:0;width:110px;z-index:2;pointer-events:none;}
.tick::before{left:0;background:linear-gradient(90deg,var(--void),transparent);}
.tick::after{right:0;background:linear-gradient(-90deg,var(--void),transparent);}
.tick-row{display:flex;width:max-content;animation:slideL 38s linear infinite;}
.tick:hover .tick-row{animation-play-state:paused;}
@keyframes slideL{to{transform:translateX(-50%)}}
.tick-i{display:flex;align-items:baseline;gap:10px;padding:14px 26px;border-right:1px solid rgba(255,255,255,.05);
  font-size:13px;white-space:nowrap;}

/* ---------- feature cards ---------- */
.card{position:relative;padding:26px;border-radius:18px;overflow:hidden;
  transition:transform .4s cubic-bezier(.2,.7,.3,1),border-color .4s,box-shadow .4s;}
.card::after{content:'';position:absolute;inset:0;opacity:0;transition:opacity .45s;pointer-events:none;
  background:radial-gradient(420px circle at var(--mx,50%) var(--my,0%),rgba(45,226,230,.15),transparent 62%);}
.card:hover{transform:translateY(-7px);border-color:rgba(45,226,230,.34);
  box-shadow:0 34px 70px -34px rgba(45,226,230,.45),inset 0 1px 0 rgba(255,255,255,.1);}
.card:hover::after{opacity:1;}
.ico{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;
  border:1px solid var(--line);background:rgba(255,255,255,.04);transition:.4s;}
.card:hover .ico{transform:rotate(-8deg) scale(1.06);}

/* ---------- terminal ---------- */
.caret{display:inline-block;width:8px;height:15px;background:var(--cyan);margin-left:2px;
  vertical-align:-2px;animation:blink 1s steps(2) infinite;}
@keyframes blink{50%{opacity:0}}
.line-in{animation:lineIn .4s ease both;}
@keyframes lineIn{from{opacity:0;transform:translateY(6px)}}

/* ---------- chart ---------- */
.chart-slide{animation:chartSlide 1.1s linear;}
@keyframes chartSlide{from{transform:translateX(var(--step,14px))}to{transform:translateX(0)}}
.halo{transform-box:fill-box;transform-origin:center;animation:halo 2.2s ease-out infinite;}
@keyframes halo{0%{transform:scale(1);opacity:.85}100%{transform:scale(5);opacity:0}}

/* ---------- misc motion ---------- */
.float{animation:float 9s ease-in-out infinite;}
@keyframes float{50%{transform:translateY(-14px)}}
.sheen{position:relative;overflow:hidden;}
.sheen::before{content:'';position:absolute;top:0;bottom:0;width:40%;left:-60%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.09),transparent);animation:sheen 5.5s ease-in-out infinite;}
@keyframes sheen{60%,100%{left:120%}}

/* ---------- form ---------- */
.field{width:100%;padding:14px 16px;border-radius:12px;background:rgba(255,255,255,.04);
  border:1px solid var(--line);color:var(--text);font-size:15px;transition:.25s;}
.field::placeholder{color:#5D6B80;}
.field:focus{outline:none;border-color:rgba(45,226,230,.6);background:rgba(45,226,230,.06);
  box-shadow:0 0 0 4px rgba(45,226,230,.1);}
.pop{animation:pop .5s cubic-bezier(.2,1.5,.4,1) both;}
@keyframes pop{from{opacity:0;transform:scale(.86)}}

@media(prefers-reduced-motion:reduce){
  .mrd *,.mrd *::before,.mrd *::after{animation-duration:.001ms!important;animation-iteration-count:1!important;
    transition-duration:.001ms!important;}
  .rv{opacity:1;transform:none;}
}
`;

/* ---------------------------- reveal hook --------------------------- */
function Reveal({ children, delay = 0, style, className = "" }) {
  const ref = useRef(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setSeen(true);
          io.disconnect();
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -60px 0px" }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return (
    <div
      ref={ref}
      className={"rv " + (seen ? "in " : "") + className}
      style={{ transitionDelay: delay + "ms", ...style }}
    >
      {children}
    </div>
  );
}

/* --------------------------- neural orb ----------------------------- */
function NeuralOrb() {
  const hostRef = useRef(null);
  const canvasRef = useRef(null);
  const mouse = useRef({ x: 0, y: 0, tx: 0, ty: 0, active: false, px: -999, py: -999 });

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;
    const ctx = canvas.getContext("2d");
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let w = 0, h = 0, raf = 0;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const N = 230;
    const COLORS = [
      [45, 226, 230],   // cyan   – price nodes
      [139, 92, 246],   // violet – inference nodes
      [61, 220, 151],   // emerald – confirmed signals
    ];
    const nodes = [];
    for (let i = 0; i < N; i++) {
      const y = 1 - (i / (N - 1)) * 2;
      const r = Math.sqrt(Math.max(0, 1 - y * y));
      const th = Math.PI * (3 - Math.sqrt(5)) * i;
      nodes.push({
        x: Math.cos(th) * r,
        y,
        z: Math.sin(th) * r,
        c: i % 11 === 0 ? 2 : i % 4 === 0 ? 1 : 0,
        lit: 0,
      });
    }

    const edges = [];
    for (let i = 0; i < N; i++) {
      const near = [];
      for (let j = 0; j < N; j++) {
        if (i === j) continue;
        const dx = nodes[i].x - nodes[j].x;
        const dy = nodes[i].y - nodes[j].y;
        const dz = nodes[i].z - nodes[j].z;
        near.push([dx * dx + dy * dy + dz * dz, j]);
      }
      near.sort((a, b) => a[0] - b[0]);
      for (let k = 0; k < 2; k++) if (near[k][1] > i) edges.push([i, near[k][1]]);
    }

    let pulses = [];
    const spawn = () => {
      if (pulses.length > 14) return;
      const e = edges[(Math.random() * edges.length) | 0];
      pulses.push({ a: e[0], b: e[1], t: 0, sp: 0.012 + Math.random() * 0.016 });
    };

    const resize = () => {
      const rect = host.getBoundingClientRect();
      w = rect.width;
      h = rect.height;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = w + "px";
      canvas.style.height = h + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(host);

    const onMove = (ev) => {
      const rect = host.getBoundingClientRect();
      const px = ev.clientX - rect.left;
      const py = ev.clientY - rect.top;
      mouse.current.px = px;
      mouse.current.py = py;
      mouse.current.tx = (px / rect.width - 0.5) * 2;
      mouse.current.ty = (py / rect.height - 0.5) * 2;
      mouse.current.active = true;
    };
    const onLeave = () => {
      mouse.current.active = false;
      mouse.current.tx = 0;
      mouse.current.ty = 0;
      mouse.current.px = -999;
      mouse.current.py = -999;
    };
    host.addEventListener("pointermove", onMove);
    host.addEventListener("pointerleave", onLeave);

    let t = 0;
    const proj = [];

    const frame = () => {
      t += reduce ? 0.0015 : 0.0042;
      mouse.current.x += (mouse.current.tx - mouse.current.x) * 0.055;
      mouse.current.y += (mouse.current.ty - mouse.current.y) * 0.055;

      const cx = w / 2;
      const cy = h / 2;
      const R = Math.min(w, h) * 0.335;
      const ry = t + mouse.current.x * 0.55;
      const rx = -0.22 + mouse.current.y * 0.4;
      const cy_ = Math.cos(ry), sy_ = Math.sin(ry);
      const cx_ = Math.cos(rx), sx_ = Math.sin(rx);

      ctx.clearRect(0, 0, w, h);

      // core glow
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.9);
      g.addColorStop(0, "rgba(45,226,230,.16)");
      g.addColorStop(0.42, "rgba(139,92,246,.09)");
      g.addColorStop(1, "rgba(4,6,11,0)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);

      for (let i = 0; i < N; i++) {
        const n = nodes[i];
        const x1 = n.x * cy_ - n.z * sy_;
        const z1 = n.x * sy_ + n.z * cy_;
        const y2 = n.y * cx_ - z1 * sx_;
        const z2 = n.y * sx_ + z1 * cx_;
        const s = 1.9 / (1.9 - z2 * 0.6);
        proj[i] = { x: cx + x1 * R * s, y: cy + y2 * R * s, z: z2, s };
        const dx = proj[i].x - mouse.current.px;
        const dy = proj[i].y - mouse.current.py;
        const near = Math.max(0, 1 - Math.sqrt(dx * dx + dy * dy) / 130);
        n.lit += (near * near - n.lit) * 0.18;
      }

      // orbit rings
      ctx.lineWidth = 1;
      for (let k = 0; k < 2; k++) {
        const tilt = k === 0 ? 0.42 : -0.6;
        ctx.beginPath();
        for (let a = 0; a <= 64; a++) {
          const ang = (a / 64) * Math.PI * 2;
          const ox = Math.cos(ang) * 1.28;
          const oz = Math.sin(ang) * 1.28;
          const oy = Math.sin(ang) * tilt * 0.5;
          const x1 = ox * cy_ - oz * sy_;
          const z1 = ox * sy_ + oz * cy_;
          const y2 = oy * cx_ - z1 * sx_;
          const z2 = oy * sx_ + z1 * cx_;
          const s = 1.9 / (1.9 - z2 * 0.6);
          const px = cx + x1 * R * s;
          const py = cy + y2 * R * s;
          if (a === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.strokeStyle = k === 0 ? "rgba(45,226,230,.16)" : "rgba(139,92,246,.14)";
        ctx.stroke();
      }

      // edges
      for (let e = 0; e < edges.length; e++) {
        const a = proj[edges[e][0]];
        const b = proj[edges[e][1]];
        const depth = (a.z + b.z) * 0.5;
        const alpha = 0.05 + (depth + 1) * 0.075;
        const lit = Math.max(nodes[edges[e][0]].lit, nodes[edges[e][1]].lit);
        ctx.strokeStyle = "rgba(125,211,252," + (alpha + lit * 0.45).toFixed(3) + ")";
        ctx.lineWidth = 0.7 + lit * 1.1;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }

      // nodes
      for (let i = 0; i < N; i++) {
        const p = proj[i];
        const n = nodes[i];
        const col = COLORS[n.c];
        const base = 0.3 + (p.z + 1) * 0.32;
        const a = Math.min(1, base + n.lit * 0.9);
        const r = (0.9 + p.s * 1.15) * (1 + n.lit * 1.5);
        if (n.lit > 0.06 || n.c === 2) {
          ctx.beginPath();
          ctx.arc(p.x, p.y, r * 3.4, 0, 6.284);
          ctx.fillStyle = "rgba(" + col[0] + "," + col[1] + "," + col[2] + "," + (0.12 * (n.lit + (n.c === 2 ? 0.35 : 0))).toFixed(3) + ")";
          ctx.fill();
        }
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, 6.284);
        ctx.fillStyle = "rgba(" + col[0] + "," + col[1] + "," + col[2] + "," + a.toFixed(3) + ")";
        ctx.fill();
      }

      // travelling signal pulses
      if (!reduce && Math.random() < 0.13) spawn();
      pulses = pulses.filter((p) => p.t < 1);
      for (const p of pulses) {
        p.t += p.sp;
        const a = proj[p.a];
        const b = proj[p.b];
        const x = a.x + (b.x - a.x) * p.t;
        const y = a.y + (b.y - a.y) * p.t;
        const fade = Math.sin(p.t * Math.PI);
        ctx.beginPath();
        ctx.arc(x, y, 6.5 * fade, 0, 6.284);
        ctx.fillStyle = "rgba(61,220,151," + (0.16 * fade).toFixed(3) + ")";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x, y, 1.9 * fade, 0, 6.284);
        ctx.fillStyle = "rgba(190,255,230," + fade.toFixed(3) + ")";
        ctx.fill();
      }

      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      host.removeEventListener("pointermove", onMove);
      host.removeEventListener("pointerleave", onLeave);
    };
  }, []);

  return (
    <div
      ref={hostRef}
      style={{ position: "relative", width: "100%", aspectRatio: "1 / 1", touchAction: "none" }}
    >
      <canvas ref={canvasRef} style={{ display: "block", width: "100%", height: "100%" }} />
      <div
        className="mono"
        style={{
          position: "absolute", left: 0, bottom: 6, fontSize: 11, color: "#5D6B80",
          letterSpacing: ".12em", textTransform: "uppercase",
        }}
      >
        inference core · 230 nodes
      </div>
      <div
        className="mono"
        style={{
          position: "absolute", right: 0, bottom: 6, fontSize: 11, color: "#5D6B80",
          letterSpacing: ".12em",
        }}
      >
        move your cursor
      </div>
    </div>
  );
}

/* ------------------------------ ticker ------------------------------ */
const TICKS = [
  ["BTC/USD", "68,412.20", 1.84],
  ["ETH/USD", "3,571.06", 0.92],
  ["NASDAQ 100", "19,884.5", -0.41],
  ["EUR/USD", "1.0874", 0.12],
  ["XAU/USD", "2,398.60", 0.68],
  ["USD/MXN", "18.32", -0.27],
  ["SPX", "5,472.9", 0.35],
  ["WTI", "78.44", -1.12],
  ["SOL/USD", "172.85", 3.21],
  ["TSLA", "244.18", -0.76],
];

function Ticker() {
  const row = TICKS.concat(TICKS);
  return (
    <div className="tick">
      <div className="tick-row">
        {row.map((t, i) => (
          <div className="tick-i mono" key={i}>
            <span style={{ color: "#93A2B8" }}>{t[0]}</span>
            <span style={{ color: "#E8EEF7" }}>{t[1]}</span>
            <span style={{ color: t[2] >= 0 ? "var(--emerald)" : "var(--rose)" }}>
              {t[2] >= 0 ? "+" : ""}
              {t[2].toFixed(2)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------------------------- live chart ---------------------------- */
const W = 560, H = 220, STEP = 14, PTS = 40;

function seedSeries() {
  const out = [];
  let v = 100;
  for (let i = 0; i < PTS; i++) {
    v += (Math.random() - 0.46) * 3.4;
    out.push(v);
  }
  return out;
}

function LiveChart() {
  const [data, setData] = useState(seedSeries);
  const [tick, setTick] = useState(0);
  const [live, setLive] = useState(false);
  const hostRef = useRef(null);

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => setLive(e.isIntersecting), { threshold: 0.2 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => {
      setData((d) => {
        const last = d[d.length - 1];
        const next = last + (Math.random() - 0.44) * 3.6;
        return d.slice(1).concat(next);
      });
      setTick((n) => n + 1);
    }, 1100);
    return () => clearInterval(id);
  }, [live]);

  const min = Math.min(...data), max = Math.max(...data);
  const pad = (max - min) * 0.25 + 0.6;
  const yOf = (v) => H - 24 - ((v - min + pad) / (max - min + pad * 2)) * (H - 44);
  const xOf = (i) => i * STEP;

  let d = "";
  data.forEach((v, i) => { d += (i ? "L" : "M") + xOf(i).toFixed(1) + " " + yOf(v).toFixed(1) + " "; });
  const area = d + "L" + xOf(data.length - 1) + " " + H + " L0 " + H + " Z";

  const lastV = data[data.length - 1];
  const lastX = xOf(data.length - 1);
  const lastY = yOf(lastV);
  const up = lastV >= data[data.length - 6];

  // predicted cone
  const drift = (lastV - data[data.length - 8]) / 8;
  const p1 = lastV + drift * 7;
  const spread = 5.5;
  const cone =
    "M" + lastX + " " + lastY +
    " L" + (lastX + 96) + " " + yOf(p1 + spread) +
    " L" + (lastX + 96) + " " + yOf(p1 - spread) + " Z";

  return (
    <div ref={hostRef} style={{ position: "relative" }}>
      <svg viewBox={"0 0 " + (W + 110) + " " + H} style={{ width: "100%", display: "block" }}>
        <defs>
          <linearGradient id="mrdFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#2DE2E6" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#2DE2E6" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="mrdLine" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#2DE2E6" stopOpacity="0.25" />
            <stop offset="70%" stopColor="#2DE2E6" />
            <stop offset="100%" stopColor="#7DD3FC" />
          </linearGradient>
          <clipPath id="mrdClip"><rect x="0" y="0" width={W} height={H} /></clipPath>
        </defs>

        {[0, 1, 2, 3].map((i) => (
          <line key={i} x1="0" x2={W + 110} y1={26 + i * 52} y2={26 + i * 52}
            stroke="rgba(255,255,255,.055)" strokeDasharray="2 6" />
        ))}

        <g clipPath="url(#mrdClip)">
          <g className="chart-slide" key={tick} style={{ "--step": STEP + "px" }}>
            <path d={area} fill="url(#mrdFill)" />
            <path d={d} fill="none" stroke="url(#mrdLine)" strokeWidth="2"
              strokeLinejoin="round" strokeLinecap="round" />
          </g>
        </g>

        <path d={cone} fill="rgba(139,92,246,.16)" stroke="rgba(139,92,246,.45)" strokeDasharray="3 4" />
        <text x={lastX + 22} y={yOf(p1) - 10} className="mono" fill="#A78BFA" fontSize="10"
          letterSpacing="1">FORECAST 15m</text>

        <circle className="halo" cx={lastX} cy={lastY} r="4" fill="none"
          stroke={up ? "#3DDC97" : "#FF5C7A"} strokeWidth="1.2" />
        <circle cx={lastX} cy={lastY} r="4" fill={up ? "#3DDC97" : "#FF5C7A"} />
        <line x1={lastX} x2={W + 110} y1={lastY} y2={lastY}
          stroke={up ? "rgba(61,220,151,.4)" : "rgba(255,92,122,.4)"} strokeDasharray="4 4" />
      </svg>
    </div>
  );
}

/* --------------------------- terminal feed -------------------------- */
const FEED = [
  ["SCAN", "cyan", "6,412 order-book snapshots ingested"],
  ["SIGNAL", "emerald", "BTC/USD long · entry 68,240 · conf 0.91"],
  ["RISK", "violet", "position capped at 1.2% of equity"],
  ["FILL", "emerald", "0.42 BTC filled in 38 ms · slip 0.04%"],
  ["SCAN", "cyan", "volatility regime shift on NDX detected"],
  ["SIGNAL", "rose", "TSLA short blocked · earnings in 6 h"],
];

function TerminalFeed() {
  const [rows, setRows] = useState([]);
  const [typed, setTyped] = useState("");
  const idx = useRef(0);
  const ch = useRef(0);

  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { setRows(FEED); return; }
    let stop = false;
    const run = () => {
      if (stop) return;
      const cur = FEED[idx.current % FEED.length];
      if (ch.current <= cur[2].length) {
        setTyped(cur[2].slice(0, ch.current));
        ch.current++;
        setTimeout(run, 16);
      } else {
        setRows((r) => [...r.slice(-4), cur]);
        setTyped("");
        ch.current = 0;
        idx.current++;
        setTimeout(run, 900);
      }
    };
    const id = setTimeout(run, 600);
    return () => { stop = true; clearTimeout(id); };
  }, []);

  const cur = FEED[idx.current % FEED.length];
  const col = (c) => ({ cyan: "#2DE2E6", emerald: "#3DDC97", violet: "#A78BFA", rose: "#FF5C7A" }[c]);

  return (
    <div className="mono" style={{ fontSize: 12.5, lineHeight: 1.9, minHeight: 150 }}>
      {rows.map((r, i) => (
        <div key={i + r[2]} className="line-in" style={{ opacity: 0.42 + i * 0.12 }}>
          <span style={{ color: col(r[1]) }}>[{r[0]}]</span>{" "}
          <span style={{ color: "#B7C4D6" }}>{r[2]}</span>
        </div>
      ))}
      {typed !== "" && (
        <div>
          <span style={{ color: col(cur[1]) }}>[{cur[0]}]</span>{" "}
          <span style={{ color: "#E8EEF7" }}>{typed}</span>
          <span className="caret" />
        </div>
      )}
    </div>
  );
}

/* ------------------------------- chat ------------------------------- */
const SCRIPT = [
  { who: "you", text: "How does BTC look into the New York open?" },
  { who: "ai", text: "Momentum is building. 4h RSI at 61 and rising, funding still neutral, and the 68,100 shelf has absorbed three sell walls this session. I'd take longs above 68,240." },
  { who: "you", text: "Size it against my rules." },
  { who: "ai", text: "0.42 BTC — 1.2% of equity, stop at 67,410 under the shelf. Max loss 0.9R. Want me to arm the order?" },
];

function ChatDemo() {
  const [shown, setShown] = useState([]);
  const [typing, setTyping] = useState("");
  const [dots, setDots] = useState(false);
  const boxRef = useRef(null);
  const [live, setLive] = useState(false);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setLive(true); io.disconnect(); } },
      { threshold: 0.3 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!live) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { setShown(SCRIPT); return; }
    let i = 0, c = 0, stop = false;
    const step = () => {
      if (stop) return;
      const m = SCRIPT[i];
      if (!m) return;
      if (c <= m.text.length) {
        setTyping(m.text.slice(0, c));
        c += m.who === "ai" ? 2 : 1;
        setTimeout(step, m.who === "ai" ? 14 : 34);
      } else {
        setShown((s) => [...s, m]);
        setTyping("");
        c = 0;
        i++;
        if (i < SCRIPT.length) setTimeout(begin, 620);
      }
    };
    const begin = () => {
      if (stop) return;
      const m = SCRIPT[i];
      if (!m) return;
      if (m.who === "ai") {
        setDots(true);
        setTimeout(() => {
          if (stop) return;
          setDots(false);
          step();
        }, 900);
      } else {
        step();
      }
    };
    const id = setTimeout(begin, 500);
    return () => { stop = true; clearTimeout(id); };
  }, [live]);

  const cursor = SCRIPT[shown.length];

  const bubble = (m, text, key, ghost) => {
    const isAI = m.who === "ai";
    return (
      <div key={key} className="line-in" style={{ display: "flex", justifyContent: isAI ? "flex-start" : "flex-end" }}>
        <div
          style={{
            maxWidth: "88%", padding: "11px 14px", borderRadius: 14, fontSize: 13.8, lineHeight: 1.6,
            border: "1px solid " + (isAI ? "rgba(139,92,246,.32)" : "rgba(255,255,255,.09)"),
            background: isAI
              ? "linear-gradient(150deg,rgba(139,92,246,.16),rgba(45,226,230,.06))"
              : "rgba(255,255,255,.05)",
            color: isAI ? "#EDE9FE" : "#D5DEEA",
            borderBottomLeftRadius: isAI ? 5 : 14,
            borderBottomRightRadius: isAI ? 14 : 5,
          }}
        >
          {isAI && (
            <div className="mono" style={{ fontSize: 10, letterSpacing: ".16em", color: "#A78BFA", marginBottom: 6 }}>
              MERIDIAN
            </div>
          )}
          {text}
          {ghost && <span className="caret" style={{ height: 13 }} />}
        </div>
      </div>
    );
  };

  return (
    <div ref={boxRef} style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 320 }}>
      {shown.map((m, i) => bubble(m, m.text, i))}
      {typing !== "" && cursor && bubble(cursor, typing, "t", true)}
      {dots && (
        <div style={{ display: "flex", gap: 5, padding: "8px 4px" }}>
          {[0, 1, 2].map((i) => (
            <span key={i} className="dot" style={{ background: "#A78BFA", boxShadow: "0 0 8px #8B5CF6", animationDelay: i * 0.18 + "s" }} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ----------------------------- counter ------------------------------ */
function Counter({ to, decimals = 0, suffix = "", prefix = "" }) {
  const [v, setV] = useState(0);
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return;
      io.disconnect();
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (reduce) { setV(to); return; }
      const t0 = performance.now();
      const dur = 1600;
      const run = (t) => {
        const p = Math.min(1, (t - t0) / dur);
        const e2 = 1 - Math.pow(1 - p, 3);
        setV(to * e2);
        if (p < 1) requestAnimationFrame(run);
      };
      requestAnimationFrame(run);
    }, { threshold: 0.4 });
    io.observe(el);
    return () => io.disconnect();
  }, [to]);
  return (
    <span ref={ref} className="mono">
      {prefix}
      {v.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}
      {suffix}
    </span>
  );
}

/* ---------------------------- feature card -------------------------- */
function FeatureCard({ icon: Icon, tint, kicker, title, body, wide }) {
  const onMove = useCallback((e) => {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--mx", e.clientX - r.left + "px");
    e.currentTarget.style.setProperty("--my", e.clientY - r.top + "px");
  }, []);
  return (
    <div className="card glass" onMouseMove={onMove} style={{ gridColumn: wide ? "span 2" : "auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <div className="ico" style={{ color: tint }}>
          <Icon size={19} strokeWidth={1.7} />
        </div>
        <span className="mono" style={{ fontSize: 10.5, letterSpacing: ".18em", color: "#5D6B80" }}>
          {kicker}
        </span>
      </div>
      <h3 style={{ fontSize: 19.5, margin: "0 0 10px", letterSpacing: "-.02em", fontWeight: 600 }}>{title}</h3>
      <p style={{ color: "var(--muted)", fontSize: 14.6, lineHeight: 1.65 }}>{body}</p>
    </div>
  );
}

/* ------------------------------- page ------------------------------- */
export default function MeridianLanding() {
  const [scrolled, setScrolled] = useState(false);
  const [y, setY] = useState(0);
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    const onScroll = () => {
      setScrolled(window.scrollY > 12);
      setY(window.scrollY);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const submit = () => {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
      setErr("Enter a valid email so we can send your access key.");
      return;
    }
    setErr("");
    setSent(true);
  };

  const go = (id) => (e) => {
    e.preventDefault();
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="mrd">
      <style>{CSS}</style>

      {/* atmosphere */}
      <div className="mrd-bg">
        <div className="mrd-grid" />
        <div className="mrd-blob" style={{
          width: 620, height: 620, top: -180, right: -120, background: "#0E7490",
          transform: "translateY(" + y * 0.09 + "px)",
        }} />
        <div className="mrd-blob" style={{
          width: 520, height: 520, top: 420, left: -200, background: "#5B21B6", opacity: 0.42,
          transform: "translateY(" + y * -0.05 + "px)",
        }} />
        <div className="mrd-noise" />
      </div>

      {/* nav */}
      <nav className={"nav" + (scrolled ? " on" : "")}>
        <div className="nav-in">
          <a href="#top" onClick={go("top")} style={{ display: "flex", alignItems: "center", gap: 10, textDecoration: "none", color: "inherit" }}>
            <div style={{
              width: 30, height: 30, borderRadius: 9, display: "grid", placeItems: "center",
              background: "linear-gradient(140deg,#2DE2E6,#8B5CF6)", color: "#04060B",
              boxShadow: "0 0 18px -4px rgba(45,226,230,.8)",
            }}>
              <BrainCircuit size={17} strokeWidth={2} />
            </div>
            <span className="disp" style={{ fontWeight: 600, letterSpacing: "-.01em", fontSize: 17 }}>Meridian</span>
          </a>
          <div className="nav-links" style={{ display: "flex", gap: 26, marginLeft: 14 }}>
            <a className="lnk" href="#capabilities" onClick={go("capabilities")}>Capabilities</a>
            <a className="lnk" href="#demo" onClick={go("demo")}>Live demo</a>
            <a className="lnk" href="#numbers" onClick={go("numbers")}>Performance</a>
          </div>
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
            <a className="lnk" href="#access" onClick={go("access")} style={{ marginRight: 4 }}>Sign in</a>
            <a className="btn btn-p btn-s" href="#demo" onClick={go("demo")}>
              Try the demo <ArrowRight size={15} />
            </a>
          </div>
        </div>
      </nav>

      {/* hero */}
      <header id="top" style={{ paddingTop: 132 }}>
        <div className="mrd-wrap">
          <div style={{
            display: "grid", gridTemplateColumns: "minmax(0,1.08fr) minmax(0,.92fr)",
            gap: 46, alignItems: "center",
          }} className="hero-grid">
            <div>
              <Reveal>
                <div className="tag mono" style={{ marginBottom: 22 }}>
                  <span className="dot" /> Model v4.2 · live on 180 instruments
                </div>
              </Reveal>
              <Reveal delay={90}>
                <h1 className="h1">
                  The future of trading,<br />
                  powered by <span className="grad">artificial intelligence</span>.
                </h1>
              </Reveal>
              <Reveal delay={180}>
                <p className="lead" style={{ marginTop: 22 }}>
                  Meridian reads the order book, the tape and the news in the same breath,
                  scores every setup against your own risk rules, and hands you the trade
                  before the candle closes.
                </p>
              </Reveal>
              <Reveal delay={260}>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 32 }}>
                  <a className="btn btn-p sheen" href="#demo" onClick={go("demo")}>
                    Try the demo <ArrowRight size={16} />
                  </a>
                  <a className="btn btn-g" href="#capabilities" onClick={go("capabilities")}>
                    See how it works
                  </a>
                </div>
              </Reveal>
              <Reveal delay={340}>
                <div className="mono" style={{ display: "flex", gap: 22, marginTop: 30, fontSize: 12, color: "#5D6B80", flexWrap: "wrap" }}>
                  <span>38 ms median fill</span>
                  <span>SOC 2 Type II</span>
                  <span>No card required</span>
                </div>
              </Reveal>
            </div>

            <Reveal delay={140}>
              <div className="float"><NeuralOrb /></div>
            </Reveal>
          </div>
        </div>

        <div style={{ marginTop: 64 }}>
          <Ticker />
        </div>
      </header>

      {/* capabilities */}
      <section id="capabilities" className="mrd-sec">
        <div className="mrd-wrap">
          <Reveal>
            <span className="eyebrow"><Sparkles size={13} /> Capabilities</span>
            <h2 className="h2" style={{ marginTop: 16, maxWidth: "18ch" }}>
              One assistant across the whole trade.
            </h2>
            <p className="lead" style={{ marginTop: 16 }}>
              Research, sizing, execution and review run on the same model, so nothing gets
              lost between the idea and the fill.
            </p>
          </Reveal>

          <div className="feat-grid" style={{
            display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 18, marginTop: 46,
          }}>
            {[
              { icon: Radar, tint: "#2DE2E6", kicker: "ALWAYS ON", title: "Predictive analysis, 24/7",
                body: "180 instruments watched across four sessions. Meridian flags regime shifts the moment volatility, flow and correlation start disagreeing with price." },
              { icon: Zap, tint: "#3DDC97", kicker: "EXECUTION", title: "Automated order routing",
                body: "Signals become orders in 38 ms median, split across venues to keep slippage under 0.05% on size you actually trade." },
              { icon: ShieldHalf, tint: "#A78BFA", kicker: "GUARDRAILS", title: "Risk management you set",
                body: "Give it a drawdown ceiling and it sizes every position around it — blocking trades into earnings, thin books or correlated exposure you already hold." },
            ].map((f, i) => (
              <Reveal key={f.title} delay={i * 110}>
                <FeatureCard {...f} />
              </Reveal>
            ))}
          </div>

          <Reveal delay={120}>
            <div className="card glass sheen" style={{ marginTop: 18, padding: 30 }}>
              <div className="cap-wide" style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 34, alignItems: "center" }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
                    <div className="ico" style={{ color: "#2DE2E6" }}><Terminal size={19} strokeWidth={1.7} /></div>
                    <span className="mono" style={{ fontSize: 10.5, letterSpacing: ".18em", color: "#5D6B80" }}>
                      EXPLAINABILITY
                    </span>
                  </div>
                  <h3 style={{ fontSize: 22, margin: "0 0 10px", letterSpacing: "-.02em", fontWeight: 600 }}>
                    Every call arrives with its reasoning
                  </h3>
                  <p style={{ color: "var(--muted)", fontSize: 15, lineHeight: 1.65 }}>
                    No black box. Open any signal to see the features that moved it, the
                    confidence attached, and the exact rule that let it through — or stopped it.
                  </p>
                </div>
                <div style={{
                  borderRadius: 14, border: "1px solid var(--line)", background: "rgba(0,0,0,.35)",
                  padding: "16px 18px",
                }}>
                  <div className="mono" style={{ fontSize: 10.5, color: "#5D6B80", letterSpacing: ".16em", marginBottom: 10 }}>
                    ENGINE LOG
                  </div>
                  <TerminalFeed />
                </div>
              </div>
            </div>
          </Reveal>
        </div>
      </section>

      {/* demo */}
      <section id="demo" className="mrd-sec">
        <div className="mrd-wrap">
          <Reveal>
            <span className="eyebrow"><Activity size={13} /> Live demo</span>
            <h2 className="h2" style={{ marginTop: 16, maxWidth: "20ch" }}>
              Ask in plain language. Get a trade you can defend.
            </h2>
          </Reveal>

          <Reveal delay={110}>
            <div className="glass" style={{ marginTop: 40, padding: 0, overflow: "hidden" }}>
              <div style={{
                display: "flex", alignItems: "center", gap: 10, padding: "14px 18px",
                borderBottom: "1px solid var(--line)", background: "rgba(255,255,255,.02)",
              }}>
                <span style={{ width: 9, height: 9, borderRadius: 99, background: "#FF5C7A", opacity: .75 }} />
                <span style={{ width: 9, height: 9, borderRadius: 99, background: "#F0B429", opacity: .75 }} />
                <span style={{ width: 9, height: 9, borderRadius: 99, background: "#3DDC97", opacity: .75 }} />
                <span className="mono" style={{ fontSize: 11.5, color: "#5D6B80", marginLeft: 10 }}>
                  meridian — workspace / BTCUSD
                </span>
                <span className="tag mono" style={{ marginLeft: "auto", fontSize: 10.5, padding: "4px 10px" }}>
                  <span className="dot" /> STREAMING
                </span>
              </div>

              <div className="demo-grid" style={{ display: "grid", gridTemplateColumns: "minmax(0,1.05fr) minmax(0,.95fr)" }}>
                <div style={{ padding: "24px 22px", borderRight: "1px solid var(--line)" }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 4 }}>
                    <span className="disp" style={{ fontSize: 26, fontWeight: 600, letterSpacing: "-.02em" }}>BTC/USD</span>
                    <span className="mono" style={{ color: "var(--emerald)", fontSize: 13.5 }}>+1.84%</span>
                    <span className="mono" style={{ color: "#5D6B80", fontSize: 11.5, marginLeft: "auto" }}>15m · Binance perp</span>
                  </div>
                  <LiveChart />
                  <div style={{ display: "flex", gap: 10, marginTop: 14, flexWrap: "wrap" }}>
                    {[["Trend", "Bullish", "#3DDC97"], ["Liquidity", "Deep", "#2DE2E6"], ["Confidence", "0.91", "#A78BFA"]].map((c) => (
                      <div key={c[0]} className="mono" style={{
                        padding: "7px 12px", borderRadius: 10, border: "1px solid var(--line)",
                        background: "rgba(255,255,255,.03)", fontSize: 11.5,
                      }}>
                        <span style={{ color: "#5D6B80" }}>{c[0]} </span>
                        <span style={{ color: c[2] }}>{c[1]}</span>
                      </div>
                    ))}
                  </div>
                </div>

                <div style={{ padding: "24px 22px", display: "flex", flexDirection: "column" }}>
                  <ChatDemo />
                  <div style={{
                    marginTop: "auto", display: "flex", alignItems: "center", gap: 10,
                    padding: "11px 14px", borderRadius: 12, border: "1px solid var(--line)",
                    background: "rgba(255,255,255,.03)", color: "#5D6B80", fontSize: 13.5,
                  }}>
                    <LineChart size={15} />
                    Ask Meridian about any instrument…
                    <span className="caret" style={{ marginLeft: "auto", height: 13 }} />
                  </div>
                </div>
              </div>
            </div>
          </Reveal>
        </div>
      </section>

      {/* numbers */}
      <section id="numbers" className="mrd-sec">
        <div className="mrd-wrap">
          <Reveal>
            <span className="eyebrow"><Gauge size={13} /> Performance</span>
            <h2 className="h2" style={{ marginTop: 16, maxWidth: "22ch" }}>
              Measured on live capital, not backtests.
            </h2>
          </Reveal>

          <div className="num-grid" style={{
            display: "grid", gridTemplateColumns: "repeat(4,minmax(0,1fr))", gap: 1,
            marginTop: 44, background: "var(--line)", border: "1px solid var(--line)", borderRadius: 18,
            overflow: "hidden",
          }}>
            {[
              { v: 87.4, d: 1, s: "%", label: "Directional accuracy", note: "rolling 90 days", c: "#3DDC97" },
              { v: 38, d: 0, s: " ms", label: "Median execution latency", note: "signal to fill", c: "#2DE2E6" },
              { v: 41.2, d: 1, s: "M", label: "Events analysed daily", note: "ticks, prints, headlines", c: "#A78BFA" },
              { v: 99.98, d: 2, s: "%", label: "Engine uptime", note: "trailing 12 months", c: "#E8EEF7" },
            ].map((m, i) => (
              <Reveal key={m.label} delay={i * 90}>
                <div style={{ background: "rgba(255,255,255,.025)", padding: "30px 24px", height: "100%" }}>
                  <div className="disp" style={{ fontSize: "clamp(30px,3.4vw,42px)", fontWeight: 600, letterSpacing: "-.03em", color: m.c }}>
                    <Counter to={m.v} decimals={m.d} suffix={m.s} />
                  </div>
                  <div style={{ marginTop: 10, fontSize: 14, color: "#D5DEEA" }}>{m.label}</div>
                  <div className="mono" style={{ marginTop: 5, fontSize: 11, color: "#5D6B80", letterSpacing: ".08em" }}>
                    {m.note}
                  </div>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* access */}
      <section id="access" className="mrd-sec">
        <div className="mrd-wrap">
          <Reveal>
            <div className="glass" style={{ padding: "56px 40px", textAlign: "center", position: "relative", overflow: "hidden" }}>
              <div style={{
                position: "absolute", top: -140, left: "50%", width: 460, height: 300,
                transform: "translateX(-50%)", background: "radial-gradient(closest-side,rgba(45,226,230,.24),transparent)",
                pointerEvents: "none",
              }} />
              <span className="eyebrow"><Sparkles size={13} /> Early access</span>
              <h2 className="h2" style={{ marginTop: 16 }}>Trade with the model on your side.</h2>
              <p className="lead" style={{ margin: "16px auto 0", textAlign: "center" }}>
                Paper trading is open to everyone. Live routing is rolling out to a waitlist,
                in the order requests come in.
              </p>

              <div style={{ maxWidth: 470, margin: "30px auto 0" }}>
                {!sent ? (
                  <div>
                    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                      <input
                        className="field"
                        style={{ flex: "1 1 220px" }}
                        type="email"
                        placeholder="you@desk.com"
                        value={email}
                        onChange={(e) => { setEmail(e.target.value); if (err) setErr(""); }}
                        onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
                        aria-label="Email address"
                      />
                      <button className="btn btn-p" onClick={submit}>
                        Request access <ArrowUpRight size={16} />
                      </button>
                    </div>
                    {err && (
                      <p className="mono" style={{ marginTop: 10, fontSize: 12, color: "#FF5C7A", textAlign: "left" }}>
                        {err}
                      </p>
                    )}
                  </div>
                ) : (
                  <div className="pop" style={{
                    display: "flex", alignItems: "center", justifyContent: "center", gap: 12,
                    padding: "16px 20px", borderRadius: 14,
                    border: "1px solid rgba(61,220,151,.4)", background: "rgba(61,220,151,.09)",
                  }}>
                    <span style={{
                      width: 26, height: 26, borderRadius: 99, display: "grid", placeItems: "center",
                      background: "var(--emerald)", color: "#04060B",
                    }}>
                      <Check size={15} strokeWidth={3} />
                    </span>
                    <span style={{ fontSize: 14.5 }}>
                      Requested. Your access key is on its way to {email}.
                    </span>
                  </div>
                )}
                <p className="mono" style={{ marginTop: 14, fontSize: 11.5, color: "#5D6B80" }}>
                  No card required · Cancel anytime · Keys sent within 24 h
                </p>
              </div>
            </div>
          </Reveal>
        </div>
      </section>

      {/* footer */}
      <footer style={{ borderTop: "1px solid var(--line)", position: "relative", zIndex: 1 }}>
        <div className="mrd-wrap" style={{ padding: "48px 24px 30px" }}>
          <div className="foot-grid" style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr 1fr", gap: 30 }}>
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{
                  width: 26, height: 26, borderRadius: 8, display: "grid", placeItems: "center",
                  background: "linear-gradient(140deg,#2DE2E6,#8B5CF6)", color: "#04060B",
                }}>
                  <BrainCircuit size={15} strokeWidth={2} />
                </div>
                <span className="disp" style={{ fontWeight: 600, fontSize: 16 }}>Meridian</span>
              </div>
              <p style={{ color: "#5D6B80", fontSize: 13.5, marginTop: 14, maxWidth: "34ch", lineHeight: 1.6 }}>
                An AI assistant for people who trade their own book.
              </p>
            </div>
            {[
              ["Product", ["Capabilities", "Live demo", "Pricing", "Changelog"]],
              ["Developers", ["API reference", "Webhooks", "Status", "Latency map"]],
              ["Company", ["About", "Careers", "Risk disclosure", "Contact"]],
            ].map((col) => (
              <div key={col[0]}>
                <div className="mono" style={{ fontSize: 10.5, letterSpacing: ".18em", color: "#5D6B80", marginBottom: 14 }}>
                  {col[0].toUpperCase()}
                </div>
                {col[1].map((l) => (
                  <a key={l} className="lnk" href="#top" onClick={go("top")}
                    style={{ display: "block", marginBottom: 9, fontSize: 13.5 }}>
                    {l}
                  </a>
                ))}
              </div>
            ))}
          </div>
          <div style={{
            marginTop: 40, paddingTop: 20, borderTop: "1px solid var(--line)",
            display: "flex", flexWrap: "wrap", gap: 12, justifyContent: "space-between",
          }}>
            <p className="mono" style={{ fontSize: 11.5, color: "#5D6B80" }}>
              © 2026 Meridian Labs · Demo interface. Figures are illustrative and this is not investment advice.
            </p>
            <p className="mono" style={{ fontSize: 11.5, color: "#5D6B80" }}>
              Trading involves risk of loss.
            </p>
          </div>
        </div>
      </footer>

      <style>{`
        @media(max-width:940px){
          .hero-grid{grid-template-columns:1fr!important;gap:34px!important;}
          .feat-grid{grid-template-columns:1fr!important;}
          .cap-wide{grid-template-columns:1fr!important;}
          .demo-grid{grid-template-columns:1fr!important;}
          .demo-grid>div:first-child{border-right:none!important;border-bottom:1px solid var(--line);}
          .num-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important;}
          .foot-grid{grid-template-columns:1fr 1fr!important;}
        }
        @media(max-width:560px){
          .num-grid{grid-template-columns:1fr!important;}
          .foot-grid{grid-template-columns:1fr!important;}
        }
      `}</style>
    </div>
  );
}
