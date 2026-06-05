"""Shared HUD visual system and transitions-dev primitives for Chorus HTML reports."""

from __future__ import annotations

from html import escape


def document_head(*, title: str, extra_css: str = "") -> str:
    safe = escape(title)
    return f"""<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{safe}</title>
<style>
{chorus_root_css()}
{chorus_hud_css()}
{chorus_modal_css()}
{extra_css}
</style>
</head>"""


def document_close(*, extra_script: str = "", extra_module_script: str = "") -> str:
    module_block = ""
    if extra_module_script:
        module_block = f"""
<script type="module">
{extra_module_script}
</script>"""
    return f"""{chorus_modal_markup()}
<script>
{chorus_ui_js()}
{extra_script}
</script>{module_block}
</body>
</html>"""


def hud_shell_start(*, brand: str, run_line: str, quote: str = "") -> str:
    tagline = (
        f'<p class="hud-quote">{escape(quote)}</p>'
        if quote
        else '<p class="hud-quote hud-quote--dim">reliability instrument · no decoration in the widget</p>'
    )
    return (
        '<div class="hud-scan" aria-hidden="true"></div>'
        '<div class="hud-frame">'
        '<header class="hud-header">'
        '<div class="hud-brand">'
        f'<span class="hud-brand__mark" aria-hidden="true"></span>'
        f'<span class="hud-brand__text">{escape(brand)}</span>'
        "</div>"
        f'<div class="hud-runline">{run_line}</div>'
        '<div class="hud-header__accent" aria-hidden="true"><span class="hud-orb"></span></div>'
        "</header>"
        f"{tagline}"
        '<main class="hud-main">'
    )


def hud_shell_end(*, footer_left: str = "chorus") -> str:
    return (
        "</main>"
        '<footer class="hud-footer">'
        f'<span class="hud-footer__brand">{escape(footer_left)}</span>'
        '<span class="hud-footer__glyph" aria-hidden="true"></span>'
        '<span class="hud-footer__meta">mono · machine values · round numbers</span>'
        "</footer>"
        "</div>"
    )


def chorus_root_css() -> str:
    """transitions-dev universal :root block (import once per document)."""
    return r"""
:root {
  --resize-dur: 300ms;
  --resize-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --digit-dur: 500ms;
  --digit-distance: 8px;
  --digit-stagger: 70ms;
  --digit-blur: 2px;
  --digit-ease: cubic-bezier(0.34, 1.45, 0.64, 1);
  --digit-dir-x: 0;
  --digit-dir-y: 1;
  --badge-slide-dur: 260ms;
  --badge-pop-dur: 500ms;
  --badge-pop-close-dur: 180ms;
  --badge-fade-dur: 400ms;
  --badge-fade-close-dur: 180ms;
  --badge-blur: 2px;
  --badge-offset-x: -8.2px;
  --badge-offset-y: 12.4px;
  --badge-slide-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --badge-pop-ease: cubic-bezier(0.34, 1.36, 0.64, 1);
  --badge-close-ease: cubic-bezier(0.4, 0, 0.2, 1);
  --text-swap-dur: 150ms;
  --text-swap-translate-y: 4px;
  --text-swap-blur: 2px;
  --text-swap-ease: ease-in-out;
  --dropdown-open-dur: 250ms;
  --dropdown-close-dur: 150ms;
  --dropdown-pre-scale: 0.97;
  --dropdown-closing-scale: 0.99;
  --dropdown-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --modal-open-dur: 250ms;
  --modal-close-dur: 150ms;
  --modal-scale: 0.96;
  --modal-scale-close: 0.96;
  --modal-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --panel-open-dur: 400ms;
  --panel-close-dur: 350ms;
  --panel-translate-y: 100px;
  --panel-blur: 2px;
  --panel-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --page-slide-dur: 200ms;
  --page-fade-dur: 200ms;
  --page-slide-distance: 8px;
  --page-blur: 3px;
  --page-stagger: 0ms;
  --page-exit-enabled: 1;
  --page-slide-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --page-fade-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --icon-swap-dur: 200ms;
  --icon-swap-blur: 2px;
  --icon-swap-start-scale: 0.25;
  --icon-swap-ease: ease-in-out;
  --check-opacity-dur: 550ms;
  --check-rotate-dur: 550ms;
  --check-rotate-from: 80deg;
  --check-bob-dur: 450ms;
  --check-y-amount: 40px;
  --check-blur-dur: 500ms;
  --check-blur-from: 10px;
  --check-path-dur: 550ms;
  --check-path-delay: 80ms;
  --check-ease-out: cubic-bezier(0.22, 1, 0.36, 1);
  --check-ease-opacity: cubic-bezier(0.22, 1, 0.36, 1);
  --check-ease-rotate: cubic-bezier(0.22, 1, 0.36, 1);
  --check-ease-bob: cubic-bezier(0.34, 1.35, 0.64, 1);
  --check-ease-path: cubic-bezier(0.22, 1, 0.36, 1);
  --avatar-lift: -4px;
  --avatar-dur: 320ms;
  --avatar-scale: 1.05;
  --avatar-falloff: 0.45;
  --avatar-ease-in: cubic-bezier(0.22, 1, 0.36, 1);
  --avatar-ease-out: cubic-bezier(0.34, 3.85, 0.64, 1);
  --shake-distance: 6px;
  --shake-overshoot: 4px;
  --shake-dur-a: 80ms;
  --shake-dur-b: 60ms;
  --shake-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --revert-hold: 3000ms;
  --revert-dur: 280ms;
  --clear-dur: 1000ms;
  --clear-out-dur: 400ms;
  --clear-in-dur: 400ms;
  --clear-out-fly: 12px;
  --clear-in-fly: 12px;
  --clear-out-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --clear-in-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --clear-blur: 2px;
  --glow-delay: 50ms;
  --glow-peak-at: 0.15;
  --glow-opacity: 0.42;
  --glow-spread: 1.5;
  --pulse-dur: 1000ms;
  --pulse-count: 1;
  --pulse-min: 0.5;
  --reveal-dur: 400ms;
  --reveal-blur: 2px;
  --reveal-ease: ease-in-out;
  --shimmer-dur: 2000ms;
  --shimmer-base: #7c7c7c;
  --shimmer-highlight: #0d0d0d;
  --shimmer-band: 400%;
  --shimmer-ease: linear;
  --tabs-dur: 200ms;
  --tabs-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --tabs-text-muted: rgba(15, 15, 15, 0.6);
  --tabs-text-active: #0f0f0f;
  --tabs-bar-bg: #eeeeee;
  --tabs-pill-bg: #ffffff;
  --tt-in-dur: 150ms;
  --tt-out-dur: 50ms;
  --tt-scale: 0.98;
  --tt-delay: 80ms;
  --tt-in-ease: ease-out;
  --tt-out-ease: ease-out;
  --tt-bg: #ffffff;
  --tt-fg: #2f2f2f;
  --stagger-dur: 600ms;
  --stagger-distance: 12px;
  --stagger-stagger: 40ms;
  --stagger-blur: 3px;
  --stagger-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --bg: #e4e4e0;
  --bg-deep: #cacac4;
  --bg-radial: radial-gradient(ellipse 120% 80% at 50% 0%, #f5f5f2 0%, #e4e4e0 55%, #d8d8d2 100%);
  --panel: rgba(255, 255, 255, 0.52);
  --panel-solid: rgba(248, 248, 245, 0.92);
  --panel2: rgba(255, 255, 255, 0.38);
  --line: rgba(10, 10, 10, 0.14);
  --line-strong: rgba(10, 10, 10, 0.28);
  --txt: #0a0a0a;
  --muted: #5a5a56;
  --dim: #8a8a84;
  --accent: #e8192a;
  --accent-soft: rgba(232, 25, 42, 0.12);
  --accent-glow: rgba(232, 25, 42, 0.55);
  --blue: #1a1a1a;
  --green: #2d2d2a;
  --warn: #c45c00;
  --err: var(--accent);
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --sans: "Segoe UI", ui-sans-serif, system-ui, Roboto, sans-serif;
  --hud-border: 1px solid var(--line);
  --shadow-hud: 0 18px 50px rgba(0, 0, 0, 0.08);
}
"""


def chorus_hud_css() -> str:
    return r"""
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg-radial);
  color: var(--txt);
  font-family: var(--sans);
  font-size: 13px;
  font-weight: 400;
  letter-spacing: 0.01em;
}
.hud-frame { position: relative; min-height: 100vh; display: flex; flex-direction: column; }
.hud-scan {
  pointer-events: none;
  position: fixed;
  inset: 0;
  z-index: 9998;
  background: linear-gradient(
    180deg,
    transparent 0%,
    rgba(232, 25, 42, 0.03) 48%,
    transparent 52%,
    transparent 100%
  );
  background-size: 100% 8px;
  opacity: 0.35;
  animation: hud-scan 6s linear infinite;
}
@keyframes hud-scan {
  from { background-position: 0 -100%; }
  to { background-position: 0 100%; }
}
.hud-header {
  display: grid;
  grid-template-columns: 1fr auto auto;
  align-items: end;
  gap: 16px;
  padding: 22px 28px 10px;
  border-bottom: var(--hud-border);
  background: var(--panel);
  backdrop-filter: blur(10px);
}
.hud-brand { display: flex; align-items: center; gap: 14px; }
.hud-brand__mark {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 1px solid var(--accent);
  box-shadow: 0 0 18px var(--accent-glow);
}
.hud-brand__text {
  font-size: 28px;
  font-weight: 200;
  letter-spacing: 0.32em;
  text-transform: lowercase;
}
.hud-runline {
  font-family: var(--mono);
  font-size: 11px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  text-align: right;
}
.hud-header__accent { display: flex; align-items: center; }
.hud-orb {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 12px 0 0 -4px var(--accent-glow), 24px 0 0 -6px rgba(232, 25, 42, 0.2);
}
.hud-quote {
  margin: 0;
  padding: 8px 28px 14px;
  font-size: 11px;
  color: var(--muted);
  font-style: italic;
  letter-spacing: 0.04em;
  border-bottom: var(--hud-border);
}
.hud-quote--dim { font-style: normal; text-transform: uppercase; letter-spacing: 0.1em; font-size: 10px; }
.hud-main {
  flex: 1;
  max-width: 1180px;
  width: 100%;
  margin: 0 auto;
  padding: 22px 28px 40px;
}
.hud-footer {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 14px 28px;
  border-top: var(--hud-border);
  background: var(--panel);
  backdrop-filter: blur(8px);
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.hud-footer__brand {
  font-size: 22px;
  font-weight: 200;
  letter-spacing: 0.28em;
  color: var(--txt);
  text-transform: lowercase;
}
.hud-footer__glyph {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 3px solid #1a1a1a;
  box-shadow: inset 0 0 0 2px #8a8a84;
}
.hud-footer__meta { margin-left: auto; font-family: var(--mono); }
.hud-widget {
  background: var(--panel);
  border: var(--hud-border);
  border-radius: 2px;
  backdrop-filter: blur(12px);
  box-shadow: var(--shadow-hud);
}
.hud-widget + .hud-widget { margin-top: 22px; }
.hud-widget__hd {
  padding: 10px 16px;
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
  border-bottom: var(--hud-border);
}
.hud-widget__bd { padding: 16px 18px; }
.cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 18px;
}
.card {
  background: var(--panel-solid);
  border: var(--hud-border);
  border-radius: 2px;
  padding: 18px 20px;
  min-height: 118px;
  position: relative;
  overflow: hidden;
}
.card::before {
  content: "";
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  opacity: 0.35;
}
.card--clickable { cursor: pointer; }
.card--clickable:hover { border-color: var(--line-strong); box-shadow: 0 0 0 1px var(--accent-soft); }
.label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; }
.num { font: 500 32px/1.05 var(--mono); margin-top: 10px; color: var(--txt); }
.sub { color: var(--muted); font: 500 12px/1.35 var(--mono); margin-top: 6px; }
.legend { display: flex; flex-wrap: wrap; gap: 22px; font-size: 11px; letter-spacing: 0.06em;
          text-transform: uppercase; color: var(--muted); margin: 0 0 12px; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 8px;
       border: 1px solid var(--line-strong); }
.dot--accent { background: var(--accent); border-color: var(--accent); box-shadow: 0 0 8px var(--accent-glow); }
.dot--line { background: var(--txt); }
.dot--dash { background: transparent; border-style: dashed; }
svg { width: 100%; height: auto; display: block; }
.axis { fill: var(--muted); font: 11px var(--mono); }
.grid { stroke: var(--line); stroke-width: 1; }
.projected { fill: none; stroke: var(--txt); stroke-width: 2; }
.empirical { fill: none; stroke: var(--accent); stroke-width: 2; stroke-dasharray: 5 5; }
.shade { fill: var(--accent-soft); }
.note { fill: var(--accent); font: 11px var(--mono); }
.overlay-bg { fill: var(--panel-solid); stroke: var(--line); stroke-width: 1; }
.lane-label { fill: var(--muted); font: 12px var(--mono); }
.cell { stroke: var(--line); stroke-width: 1; rx: 2; cursor: pointer; }
.cell:hover { stroke: var(--accent); stroke-width: 1.5; }
.inactive { fill: transparent; stroke: var(--dim); stroke-dasharray: 4 3; }
.div-band { fill: var(--accent-soft); cursor: pointer; }
.div-toggle:hover { fill: rgba(232, 25, 42, 0.22); }
.div-outline { fill: none; stroke: var(--accent); stroke-width: 1.5; }
.split-only .cellwrap:not(.split) { opacity: 0.14; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.bar { height: 8px; background: rgba(0,0,0,0.06); border-radius: 1px; overflow: hidden; display: flex; }
.seg { height: 8px; cursor: pointer; transition: filter 120ms linear; }
.seg:hover { filter: brightness(1.15); }
.kv { display: grid; grid-template-columns: 150px 1fr; gap: 8px; font: 12px var(--mono); margin: 6px 0; }
.k { color: var(--muted); }
a { color: inherit; text-decoration: none; }
a:hover { color: var(--accent); }
.modal-kv { display: grid; grid-template-columns: 140px 1fr; gap: 6px 12px;
            font: 12px var(--mono); margin: 10px 0; }
.modal-kv .k { color: var(--muted); }
.modal-kv span.err { color: var(--accent); }
.modal-list { margin: 12px 0 0; padding: 0; list-style: none; font-family: var(--mono); font-size: 12px; }
.modal-list li { padding: 6px 0; border-bottom: 1px solid var(--line); }
.t-skel { position: relative; min-height: 120px; }
.t-skel-skeleton, .t-skel-content { position: absolute; inset: 0; }
.t-skel-skeleton {
  z-index: 1; opacity: 1; filter: blur(0);
  background: linear-gradient(90deg, #ddd 0%, #eee 50%, #ddd 100%);
  background-size: 200% 100%;
  animation: skel-pulse var(--pulse-dur) ease-in-out var(--pulse-count);
  border: var(--hud-border);
}
.t-skel-content { z-index: 2; opacity: 0; filter: blur(var(--reveal-blur));
  transition: opacity var(--reveal-dur) var(--reveal-ease), filter var(--reveal-dur) var(--reveal-ease); }
.t-skel.is-revealed .t-skel-skeleton { opacity: 0; filter: blur(var(--reveal-blur)); pointer-events: none; }
.t-skel.is-revealed .t-skel-content { opacity: 1; filter: blur(0); position: relative; }
@keyframes skel-pulse {
  0%, 100% { background-position: 0% 50%; opacity: 1; }
  50% { background-position: 100% 50%; opacity: var(--pulse-min); }
}
@media (prefers-reduced-motion: reduce) {
  .hud-scan { animation: none; opacity: 0; }
  .t-skel-skeleton { animation: none; }
}
@media (max-width: 900px) {
  .cards, .cols { grid-template-columns: 1fr; }
  .hud-header { grid-template-columns: 1fr; }
  .hud-runline { text-align: left; }
}
"""


def chorus_modal_css() -> str:
    """transitions-dev 06-modal + backdrop orchestration."""
    return r"""
.t-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 10000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(8, 8, 8, 0.42);
  backdrop-filter: blur(6px);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--modal-open-dur) var(--modal-ease);
}
.t-modal-backdrop.is-open {
  opacity: 1;
  pointer-events: auto;
}
.t-modal-backdrop.is-closing {
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--modal-close-dur) var(--modal-ease);
}
.t-modal {
  width: min(520px, 100%);
  max-height: min(78vh, 640px);
  overflow: auto;
  background: var(--panel-solid);
  border: 1px solid var(--line-strong);
  border-radius: 2px;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.18), 0 0 0 1px var(--accent-soft);
  transform-origin: center;
  transform: scale(var(--modal-scale));
  opacity: 0;
  pointer-events: none;
  transition:
    transform var(--modal-open-dur) var(--modal-ease),
    opacity var(--modal-open-dur) var(--modal-ease);
  will-change: transform, opacity;
}
.t-modal.is-open {
  transform: scale(1);
  opacity: 1;
  pointer-events: auto;
}
.t-modal.is-closing {
  transform: scale(var(--modal-scale-close));
  opacity: 0;
  pointer-events: none;
  transition:
    transform var(--modal-close-dur) var(--modal-ease),
    opacity var(--modal-close-dur) var(--modal-ease);
}
.t-modal__hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px;
  border-bottom: var(--hud-border);
}
.t-modal__title {
  margin: 0;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--muted);
}
.t-modal__close {
  border: 1px solid var(--line);
  background: transparent;
  color: var(--txt);
  width: 28px;
  height: 28px;
  cursor: pointer;
  font-family: var(--mono);
  line-height: 1;
}
.t-modal__close:hover { border-color: var(--accent); color: var(--accent); }
.t-modal__bd { padding: 16px 18px 20px; font-size: 13px; }
.t-modal__bd .lead { font-family: var(--mono); font-size: 12px; color: var(--dim); margin: 0 0 12px; }

@media (prefers-reduced-motion: reduce) {
  .t-modal, .t-modal-backdrop { transition: none !important; }
}
"""


def chorus_modal_markup() -> str:
    return r"""
<div id="chorus-modal-backdrop" class="t-modal-backdrop" hidden>
  <div id="chorus-modal" class="t-modal" role="dialog" aria-modal="true" aria-labelledby="chorus-modal-title">
    <div class="t-modal__hd">
      <h2 id="chorus-modal-title" class="t-modal__title"></h2>
      <button type="button" class="t-modal__close" data-chorus-modal-close aria-label="Close">×</button>
    </div>
    <div id="chorus-modal-body" class="t-modal__bd"></div>
  </div>
</div>
"""


def chorus_ui_js() -> str:
    return r"""
(function () {
  const backdrop = document.getElementById("chorus-modal-backdrop");
  const modal = document.getElementById("chorus-modal");
  const titleEl = document.getElementById("chorus-modal-title");
  const bodyEl = document.getElementById("chorus-modal-body");
  if (!backdrop || !modal) return;

  const closeMs = () => {
    const raw = getComputedStyle(document.documentElement)
      .getPropertyValue("--modal-close-dur")
      .trim();
    return parseFloat(raw) || 150;
  };

  function openModal(title, html) {
    titleEl.textContent = title;
    bodyEl.innerHTML = html;
    backdrop.hidden = false;
    modal.classList.remove("is-closing");
    backdrop.classList.remove("is-closing");
    requestAnimationFrame(() => {
      modal.classList.add("is-open");
      backdrop.classList.add("is-open");
    });
    document.body.style.overflow = "hidden";
  }

  function closeModal() {
    modal.classList.remove("is-open");
    backdrop.classList.remove("is-open");
    modal.classList.add("is-closing");
    backdrop.classList.add("is-closing");
    setTimeout(() => {
      modal.classList.remove("is-closing");
      backdrop.classList.remove("is-closing");
      backdrop.hidden = true;
      bodyEl.innerHTML = "";
      document.body.style.overflow = "";
    }, closeMs());
  }

  window.chorusOpenModal = openModal;
  window.chorusCloseModal = closeModal;

  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) closeModal();
  });
  document.querySelectorAll("[data-chorus-modal-close]").forEach((btn) => {
    btn.addEventListener("click", closeModal);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && backdrop.classList.contains("is-open")) closeModal();
  });

  document.querySelectorAll("[data-chorus-reveal]").forEach((root) => {
    requestAnimationFrame(() => root.classList.add("is-revealed"));
  });
})();
"""
