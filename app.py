import html
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
from agent_service import REPORTS_DIR, clear_all_reports, get_stored_reports, run_weather_agent

st.set_page_config(
    page_title="Weather & Storm Prediction Agent",
    page_icon="⛈️",
    layout="wide",
)

# ---------------------------------------------------------------- styling ---
# Sky concept: sunny on the left (sun, rainbow, birds, white clouds),
# stormy on the right (dark clouds, lightning, fine rain). Content sits in glass cards on top.
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&family=Sora:wght@500;600;700&display=swap');

:root {
    --panel: rgba(255, 255, 255, 0.85);
    --line: rgba(120, 160, 210, 0.45);
    --text: #10213a;
    --muted: #41587a;
    --accent: #1d4ed8;
    --accent-2: #0ea5e9;
    --ok: #059669;
    --low: #b45309;
    --mod: #ea580c;
    --high: #dc2626;
}

html, body, .stApp, [class*="css"] { font-family: 'DM Sans', sans-serif; color: var(--text); }
h1, h2, h3, h4 { font-family: 'Sora', sans-serif !important; letter-spacing: -0.01em; color: var(--text) !important; }

.stApp { background: linear-gradient(180deg, #62a0dd 0%, #98c5ee 30%, #cde3f7 64%, #eaf3fc 100%); }
header[data-testid="stHeader"] { background: transparent; }

/* block-container is a stacking context; the sky sits BEHIND all content inside it */
.block-container { position: relative; z-index: 0; padding-top: 2rem; max-width: 1280px; }
.sky { position: fixed; inset: 0; z-index: -1; pointer-events: none; overflow: hidden; }

/* warm light on the left, dark storm tone on the right */
.tone {
    position: absolute; inset: 0;
    background: linear-gradient(90deg, rgba(255,236,160,.42) 0%, rgba(255,236,160,0) 30%, rgba(25,45,85,0) 52%, rgba(25,45,85,.30) 100%);
}

/* ---------- sun ---------- */
.sun { position: absolute; top: -90px; left: -90px; width: 300px; height: 300px; }
.sun .rays {
    position: absolute; inset: -130px; border-radius: 50%;
    background: repeating-conic-gradient(rgba(255,238,150,.60) 0 6deg, transparent 6deg 16deg);
    -webkit-mask-image: radial-gradient(circle, #000 30%, transparent 70%);
            mask-image: radial-gradient(circle, #000 30%, transparent 70%);
    animation: spin 90s linear infinite;
}
.sun .glow {
    position: absolute; inset: -70px; border-radius: 50%;
    background: radial-gradient(circle, rgba(255,243,170,.95) 0%, rgba(255,228,110,.45) 36%, transparent 70%);
    animation: sunPulse 5s ease-in-out infinite;
}
.sun .disc {
    position: absolute; inset: 70px; border-radius: 50%;
    background: radial-gradient(circle at 35% 35%, #fffbd0, #ffd84a 55%, #ffb300);
    box-shadow: 0 0 60px rgba(255,200,40,.85);
}
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes sunPulse { 0%,100% { transform: scale(1); opacity: .9; } 50% { transform: scale(1.08); opacity: 1; } }

/* ---------- rainbow (opposite the sun) ---------- */
.rainbow {
    position: absolute; left: -170px; bottom: -190px; width: 620px; height: 620px; border-radius: 50%;
    background: radial-gradient(circle at 50% 50%, transparent 56%,
        rgba(239,68,68,.40) 57%, rgba(249,115,22,.40) 60%, rgba(250,204,21,.40) 63%,
        rgba(34,197,94,.38) 66%, rgba(59,130,246,.40) 69%, rgba(139,92,246,.38) 72%, transparent 73%);
    animation: rainbowFade 12s ease-in-out infinite;
}
@keyframes rainbowFade { 0%,100% { opacity: .35; } 50% { opacity: .95; } }

/* ---------- clouds ---------- */
.cloud {
    position: absolute; width: 220px; height: 64px; border-radius: 64px; background: #fff; left: -300px;
    filter: drop-shadow(0 12px 16px rgba(40,80,130,.22));
}
.cloud::before, .cloud::after { content: ""; position: absolute; background: inherit; border-radius: 50%; }
.cloud::before { width: 90px;  height: 90px;  top: -44px; left: 30px; }
.cloud::after  { width: 120px; height: 120px; top: -62px; right: 26px; }

/* white clouds drift over the sunny half and fade out before the storm */
.cloud.w { --o: .95; animation: driftSun linear infinite; }
@keyframes driftSun {
    0%   { left: -300px; opacity: 0; }
    10%  { opacity: var(--o); }
    85%  { opacity: var(--o); }
    100% { left: 52vw;  opacity: 0; }
}
.cloud.w1 { top: 3%;  transform: scale(1.2); animation-duration: 70s; }
.cloud.w2 { top: 16%; transform: scale(.8);  animation-duration: 100s; animation-delay: -50s; --o: .9; }
.cloud.w3 { top: 40%; transform: scale(1);   animation-duration: 90s;  animation-delay: -25s; --o: .85; }
.cloud.w4 { top: 58%; transform: scale(.7);  animation-duration: 115s; animation-delay: -80s; --o: .8; }
.cloud.w5 { top: 74%; transform: scale(1.1); animation-duration: 105s; animation-delay: -60s; --o: .75; }

/* dark clouds sway slowly over the storm half */
.cloud.storm { background: #62778f; filter: drop-shadow(0 16px 20px rgba(15,30,55,.40)); animation: sway ease-in-out infinite alternate; }
@keyframes sway { from { margin-left: -40px; } to { margin-left: 60px; } }
.cloud.s1 { top: -1%;  left: 66vw; transform: scale(1.7); animation-duration: 16s; opacity: .96; }
.cloud.s2 { top: 8%;   left: 82vw; transform: scale(1.3); animation-duration: 21s; opacity: .92; background: #55697f; }
.cloud.s3 { top: 30%;  left: 74vw; transform: scale(1.0); animation-duration: 18s; opacity: .8;  background: #6b8099; }
.cloud.s4 { top: 52%;  left: 84vw; transform: scale(.9);  animation-duration: 24s; opacity: .7;  background: #70849c; }

/* ---------- fine rain: tiny short drops, mostly on the storm side ---------- */
.rain {
    position: absolute; inset: 0;
    background-image:
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='90' height='90'%3E%3Cg stroke='%231e50b4' stroke-opacity='.55' stroke-width='1.3' stroke-linecap='round'%3E%3Cline x1='10' y1='6' x2='8' y2='14'/%3E%3Cline x1='47' y1='30' x2='45' y2='38'/%3E%3Cline x1='72' y1='58' x2='70' y2='66'/%3E%3Cline x1='25' y1='70' x2='23' y2='78'/%3E%3C/g%3E%3C/svg%3E"),
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cg stroke='%231e50b4' stroke-opacity='.38' stroke-width='1' stroke-linecap='round'%3E%3Cline x1='20' y1='12' x2='18' y2='19'/%3E%3Cline x1='65' y1='50' x2='63' y2='57'/%3E%3Cline x1='110' y1='24' x2='108' y2='31'/%3E%3Cline x1='40' y1='95' x2='38' y2='102'/%3E%3Cline x1='95' y1='118' x2='93' y2='125'/%3E%3C/g%3E%3C/svg%3E");
    background-size: 90px 90px, 140px 140px;
    animation: rainFall .6s linear infinite;
    -webkit-mask-image: linear-gradient(90deg, rgba(0,0,0,.10) 0%, rgba(0,0,0,.18) 28%, #000 58%, #000 100%);
            mask-image: linear-gradient(90deg, rgba(0,0,0,.10) 0%, rgba(0,0,0,.18) 28%, #000 58%, #000 100%);
}
@keyframes rainFall { from { background-position: 0 0, 0 0; } to { background-position: 0 90px, 0 140px; } }

/* ripples on the ground */
.ripple {
    position: absolute; bottom: 10px; width: 34px; height: 9px; border-radius: 50%;
    border: 1.5px solid rgba(29,78,216,.55); opacity: 0; animation: ripple 2s ease-out infinite;
}
@keyframes ripple { 0% { transform: scale(.2); opacity: .9; } 100% { transform: scale(2.2); opacity: 0; } }

/* ---------- lightning (storm side) ---------- */
.bolt-wrap { position: absolute; top: 0; height: 50vh; width: 120px; opacity: 0; animation: boltFlash 9s infinite;
    filter: drop-shadow(0 0 10px #fff7b0) drop-shadow(0 0 26px #ffe14d); }
.bolt-wrap.b1 { right: 26%; }
.bolt-wrap.b2 { right: 5%; animation-delay: -4.5s; transform: scaleX(-1); }
.bolt-wrap svg { width: 100%; height: 100%; }
@keyframes boltFlash { 0%,86%,100% { opacity: 0; } 87% { opacity: 1; } 88.5% { opacity: .1; } 90% { opacity: 1; } 93% { opacity: 0; } }

.flash { position: absolute; inset: 0; opacity: 0; animation: flash 9s infinite;
    background: radial-gradient(ellipse at 72% 5%, rgba(255,255,255,.7), rgba(255,244,190,.22) 45%, transparent 70%); }
.flash.f2 { animation-delay: -4.5s; background: radial-gradient(ellipse at 94% 5%, rgba(255,255,255,.7), rgba(255,244,190,.22) 45%, transparent 70%); }
@keyframes flash { 0%,86%,100% { opacity: 0; } 87% { opacity: 1; } 88.5% { opacity: .15; } 90% { opacity: .9; } 93% { opacity: 0; } }

/* ---------- birds (sunny side) ---------- */
.bird { position: absolute; left: -40px; width: 26px; height: 12px; animation: fly linear infinite; }
.bird svg { width: 100%; height: 100%; animation: flap .5s ease-in-out infinite alternate; transform-origin: center; }
.bird.k1 { top: 22%; animation-duration: 34s; }
.bird.k2 { top: 27%; animation-duration: 34s; animation-delay: 1.2s; transform: scale(.8); }
.bird.k3 { top: 62%; animation-duration: 48s; animation-delay: -20s; transform: scale(.9); }
@keyframes fly { 0% { left: -40px; opacity: 0; } 8% { opacity: 1; } 85% { opacity: 1; } 100% { left: 46vw; opacity: 0; } }
@keyframes flap { from { transform: scaleY(1); } to { transform: scaleY(.35); } }

/* ---------- hero (glass) ---------- */
.hero {
    display: flex; align-items: center; justify-content: space-between; gap: 24px;
    padding: 26px 34px; margin-bottom: 22px; overflow: hidden;
    border: 1px solid rgba(255,255,255,.7); border-radius: 20px;
    background: linear-gradient(115deg, rgba(255,255,255,.82), rgba(226,239,253,.64));
    backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
    box-shadow: 0 16px 44px rgba(20,60,130,.20);
    animation: heroIn .8s cubic-bezier(.2,.7,.2,1) both;
}
@keyframes heroIn { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: none; } }
.hero h1 { margin: 0 0 8px; font-size: 2.05rem; font-weight: 700; }
.hero p  { margin: 0; color: var(--muted); font-size: 1.02rem; max-width: 520px; }
.hero .tags { margin-top: 16px; display: flex; gap: 8px; flex-wrap: wrap; }
.tag { font-size: .8rem; padding: 4px 12px; border-radius: 999px; background: #fff; color: var(--accent); border: 1px solid #aac6f2; }

.scene { flex: none; width: 290px; height: 180px; }
.scene .drop { animation: dropFall 1s linear infinite; }
.scene .drop:nth-of-type(2) { animation-delay: .25s; } .scene .drop:nth-of-type(3) { animation-delay: .5s; }
.scene .drop:nth-of-type(4) { animation-delay: .75s; } .scene .drop:nth-of-type(5) { animation-delay: .4s; }
.scene .drop:nth-of-type(6) { animation-delay: .1s; }
@keyframes dropFall { 0% { transform: translateY(-14px); opacity: 0; } 25% { opacity: 1; } 100% { transform: translateY(46px); opacity: 0; } }
.scene .bolt { animation: boltFlicker 4s infinite; }
@keyframes boltFlicker { 0%,55%,100% { opacity: .25; } 57% { opacity: 1; } 59% { opacity: .3; } 61% { opacity: 1; } 68% { opacity: .25; } }
.scene .cloudbody { animation: bob 6s ease-in-out infinite; }
@keyframes bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-4px); } }
.scene .sunrays { animation: spin 20s linear infinite; transform-box: fill-box; transform-origin: center; }

/* ---------- page rhythm ---------- */
.block-container { padding-bottom: 3rem; }
div[data-testid="stVerticalBlock"] { gap: .9rem; }

/* ---------- cards ---------- */
.st-key-ask_card, .st-key-reports_card, .st-key-result_card {
    background: var(--panel); border: 1.5px solid rgba(80,125,195,.55); border-radius: 18px;
    padding: 20px 22px 22px; box-shadow: 0 12px 32px rgba(20,60,130,.16);
    backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
}
.st-key-result_card { background: rgba(255,255,255,.93); margin-top: 6px; }

.card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
    padding-bottom: 12px; border-bottom: 1px solid var(--line); margin-bottom: 1.1rem; }
.card-title { font-family: 'Sora', sans-serif; font-weight: 600; font-size: 1.1rem; color: #0b1c33; }
.card-sub { color: var(--muted); font-size: .85rem; margin-top: 2px; }
.count { flex: none; min-width: 28px; text-align: center; font-size: .8rem; font-weight: 600; padding: 3px 10px;
    border-radius: 999px; background: #e4efff; color: var(--accent); border: 1px solid #b9d0f5; }

/* ---------- inputs ---------- */
textarea, .stTextInput input {
    background: #fff !important; color: var(--text) !important;
    border: 1px solid var(--line) !important; border-radius: 12px !important;
    transition: border-color .2s, box-shadow .2s;
}
textarea:focus, .stTextInput input:focus { border-color: var(--accent) !important; box-shadow: 0 0 0 3px rgba(29,78,216,.2) !important; }
label, .stTextArea label p, .stTextInput label p { color: #1d3354 !important; font-weight: 500; }
div[data-baseweb="textarea"], div[data-baseweb="input"], div[data-baseweb="base-input"] { background: #fff !important; border-radius: 12px; }
div[data-testid="stCaptionContainer"] { color: var(--muted); }

/* ---------- buttons ---------- */
.stButton > button, .stDownloadButton > button {
    border-radius: 10px; border: 1px solid var(--line); background: #fff; color: var(--text); font-weight: 500;
    transition: transform .15s ease, border-color .2s, box-shadow .2s;
}
.stButton > button:hover, .stDownloadButton > button:hover {
    border-color: var(--accent); color: var(--accent); transform: translateY(-2px); box-shadow: 0 8px 18px rgba(29,78,216,.22);
}
.stButton > button[kind="primary"] {
    background: linear-gradient(90deg, var(--accent), var(--accent-2)); border: none; color: #fff; font-weight: 600; padding: .6rem 1.6rem;
}
.stButton > button[kind="primary"]:hover { color: #fff; box-shadow: 0 10px 24px rgba(14,165,233,.5); }
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

/* ---------- risk banner ---------- */
.risk {
    display: flex; align-items: center; gap: 16px; padding: 16px 20px; border-radius: 14px;
    border: 1px solid var(--c); background: color-mix(in srgb, var(--c) 12%, #fff);
    animation: heroIn .5s ease both;
}
.risk .dot { width: 14px; height: 14px; border-radius: 50%; background: var(--c); flex: none; }
.risk.high .dot { animation: pulse 1.3s infinite; }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--c) 60%, transparent); } 100% { box-shadow: 0 0 0 16px transparent; } }
.risk .lvl { font-family: 'Sora', sans-serif; font-weight: 600; font-size: 1.1rem; color: var(--c); }
.risk .sub { color: var(--muted); font-size: .9rem; }
.risk.none { --c: var(--ok); } .risk.low { --c: var(--low); } .risk.moderate { --c: var(--mod); } .risk.high { --c: var(--high); }

/* ---------- forecast table ---------- */
.st-key-result_card div[data-testid="stMarkdownContainer"] { overflow-x: auto; }
.st-key-result_card h3 { font-size: 1.2rem; margin: .6rem 0 .4rem; }
.st-key-result_card table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: .9rem;
    border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
.st-key-result_card th { background: #dce9fc; color: var(--accent); text-align: left; padding: 10px 12px; font-weight: 600; white-space: nowrap; }
.st-key-result_card td { padding: 9px 12px; border-top: 1px solid #e0eaf6; }
.st-key-result_card td:first-child { white-space: nowrap; font-variant-numeric: tabular-nums; }
.st-key-result_card tbody tr:nth-child(even) td { background: #f5f9ff; }
.st-key-result_card tbody tr:hover td { background: #e9f2ff; }
.st-key-result_card ul { padding-left: 1.2rem; }
.st-key-result_card li { margin-bottom: .35rem; line-height: 1.55; }
div[data-testid="stAlert"] { border-radius: 12px; }

/* ---------- saved report cards ---------- */
.st-key-reports_card div[data-testid="stVerticalBlockBorderWrapper"] {
    background: #fff; border: 1px solid rgba(80,125,195,.45) !important; border-radius: 12px;
    box-shadow: none; transition: border-color .2s, transform .2s, box-shadow .2s;
}
.st-key-reports_card div[data-testid="stVerticalBlockBorderWrapper"]:hover {
    border-color: var(--accent) !important; transform: translateY(-2px); box-shadow: 0 8px 18px rgba(29,78,216,.16);
}
.rbody { padding-bottom: .9rem; }
.rname { font-weight: 600; font-size: .95rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rmeta { color: var(--muted); font-size: .78rem; margin-top: 4px; display: flex; align-items: center; gap: 8px; }
.chip { display: inline-block; font-size: .72rem; font-weight: 600; padding: 2px 9px; border-radius: 999px; border: 1px solid var(--c); color: var(--c); background: #fff; }
.chip.none { --c: var(--ok); } .chip.low { --c: var(--low); } .chip.moderate { --c: var(--mod); } .chip.high { --c: var(--high); } .chip.na { --c: var(--muted); }

/* ---------- live conditions card ---------- */
.live { display: grid; grid-template-columns: minmax(220px, .85fr) 2fr; gap: 18px; padding: 18px 20px; border-radius: 16px;
    border: 1px solid #b9d0f5; background: linear-gradient(120deg, #ffffff 0%, #e8f2ff 100%);
    box-shadow: 0 8px 22px rgba(20,60,130,.12); animation: heroIn .5s ease both; }
.live-top { display: flex; align-items: center; gap: 8px; font-size: .72rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; color: #dc2626; }
.live-dot { width: 9px; height: 9px; border-radius: 50%; background: #dc2626; animation: pulseRed 1.4s infinite; }
@keyframes pulseRed { 0% { box-shadow: 0 0 0 0 rgba(220,38,38,.55); } 100% { box-shadow: 0 0 0 10px rgba(220,38,38,0); } }
.live-city { font-family: 'Sora', sans-serif; font-weight: 700; font-size: 1.15rem; color: #0b1c33; margin-top: 6px; }
.live-time { color: var(--muted); font-size: .82rem; margin-top: 2px; }
.live-temp { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
.live-ico { font-size: 2.6rem; line-height: 1; }
.live-deg { font-family: 'Sora', sans-serif; font-weight: 700; font-size: 2.3rem; color: #0b1c33; line-height: 1; }
.live-deg small { font-size: .85rem; font-weight: 500; color: var(--muted); }
.live-time .clk { font-family: 'Sora', sans-serif; font-weight: 700; font-size: 1.35rem; color: #1d4ed8; margin-right: 6px; }
.live-tz { color: var(--muted); font-size: .75rem; margin-top: 2px; }
.live-cond { margin-top: 8px; font-weight: 600; color: #1d3354; }
.metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; align-content: center; }
.mt { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; }
.mt .l { font-size: .68rem; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }
.mt .v { font-family: 'Sora', sans-serif; font-weight: 600; font-size: 1.02rem; color: #0b1c33; margin-top: 3px; }
.mt .s { font-size: .75rem; color: var(--muted); margin-top: 1px; }
@media (max-width: 900px) { .live { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .metrics { grid-template-columns: repeat(2, 1fr); } }
@media (prefers-reduced-motion: reduce) { .live, .live-dot { animation: none !important; } }

/* ---------- empty state (before first analysis) ---------- */
.empty-title { font-size: .76rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin: .2rem 0 .6rem; }
.steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.step, .lg { background: #fff; border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; animation: heroIn .6s ease both; }
.step:nth-child(2), .lg:nth-child(2) { animation-delay: .1s; } .step:nth-child(3), .lg:nth-child(3) { animation-delay: .2s; } .lg:nth-child(4) { animation-delay: .3s; }
.step .num { width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: #fff; font-weight: 600; font-size: .85rem; margin-bottom: 8px; }
.step .t { font-family: 'Sora', sans-serif; font-weight: 600; margin-bottom: 4px; color: #0b1c33; }
.step .d, .lg .d { color: var(--muted); font-size: .84rem; line-height: 1.5; }
.legend-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.lg { padding: 12px 14px; border-radius: 12px; }
.lg .d { margin-top: 8px; font-size: .78rem; line-height: 1.4; }
.meta-row { display: flex; flex-wrap: wrap; gap: 8px; }
.pill { font-size: .8rem; padding: 5px 12px; border-radius: 999px; background: #eef5ff; color: #1d3354; border: 1px solid #c9dbf5; }
@media (max-width: 820px) { .steps, .legend-row { grid-template-columns: 1fr; } }

.st-key-clear_wrap .stButton > button { color: #b91c1c; border-color: #f0b4b4; background: rgba(255,255,255,.9); }
.st-key-clear_wrap .stButton > button:hover { color: #fff; background: #dc2626; border-color: #dc2626; box-shadow: 0 8px 18px rgba(220,38,38,.28); }

.footer { text-align: center; color: #264063; font-size: .8rem; margin-top: 18px; text-shadow: 0 1px 6px rgba(255,255,255,.8); }

.st-key-result_card table { font-size: .8rem; }
.st-key-result_card th { padding: 8px; font-size: .76rem; }
.st-key-result_card td { padding: 7px 8px; }
hr { border-color: var(--line) !important; }

@media (max-width: 820px) { .scene { display: none; } .hero { padding: 20px; } .hero h1 { font-size: 1.5rem; } .bolt-wrap, .rainbow { display: none; } }
@media (prefers-reduced-motion: reduce) {
    .cloud, .rain, .flash, .bolt-wrap, .ripple, .sun .rays, .sun .glow, .rainbow, .bird, .bird svg,
    .hero, .risk, .risk .dot, .scene .drop, .scene .bolt, .scene .cloudbody, .scene .sunrays { animation: none !important; }
    .bolt-wrap, .flash { opacity: 0 !important; }
}

/* =================== opening splash (6 seconds, light theme) =================== */
.splash {
    position: fixed; inset: 0; z-index: 99999; display: flex; align-items: center; justify-content: center; text-align: center;
    overflow: hidden; color: #10213a;
    background:
        radial-gradient(circle at 8% 6%, rgba(255,236,160,.95), rgba(255,236,160,0) 28%),
        linear-gradient(180deg, #62a0dd 0%, #98c5ee 35%, #cde3f7 70%, #eaf3fc 100%);
    animation: splashOut 1.2s ease-in-out 4.8s forwards;      /* visible 4.8s, then a slow 1.2s fade = 6s */
}
@keyframes splashOut { to { opacity: 0; visibility: hidden; pointer-events: none; } }

.sp-cloud { position: absolute; width: 220px; height: 64px; border-radius: 64px; background: #fff; opacity: .95; left: -300px;
    filter: drop-shadow(0 12px 16px rgba(40,80,130,.25)); animation: spDrift linear infinite; z-index: 1; }
.sp-cloud::before, .sp-cloud::after { content: ""; position: absolute; background: inherit; border-radius: 50%; }
.sp-cloud::before { width: 90px;  height: 90px;  top: -44px; left: 30px; }
.sp-cloud::after  { width: 120px; height: 120px; top: -62px; right: 26px; }
@keyframes spDrift { from { left: -300px; } to { left: 110vw; } }
.sp-cloud.sc1 { top: 2%;  transform: scale(1.9); animation-duration: 16s; animation-delay: -6s;  background: #7f94ad; }
.sp-cloud.sc2 { top: 14%; transform: scale(1.2); animation-duration: 20s; animation-delay: -14s; }
.sp-cloud.sc3 { top: 62%; transform: scale(1.5); animation-duration: 18s; animation-delay: -3s;  opacity: .85; }
.sp-cloud.sc4 { top: 78%; transform: scale(1);   animation-duration: 22s; animation-delay: -10s; opacity: .8; }
.sp-cloud.sc5 { top: 32%; transform: scale(1.1); animation-duration: 24s; animation-delay: -18s; background: #9fb3c9; opacity: .8; }

.sp-rain { position: absolute; inset: 0; z-index: 1;
    background-image:
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='90' height='90'%3E%3Cg stroke='%231e50b4' stroke-opacity='.5' stroke-width='1.3' stroke-linecap='round'%3E%3Cline x1='10' y1='6' x2='8' y2='14'/%3E%3Cline x1='47' y1='30' x2='45' y2='38'/%3E%3Cline x1='72' y1='58' x2='70' y2='66'/%3E%3Cline x1='25' y1='70' x2='23' y2='78'/%3E%3C/g%3E%3C/svg%3E"),
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cg stroke='%231e50b4' stroke-opacity='.35' stroke-width='1' stroke-linecap='round'%3E%3Cline x1='20' y1='12' x2='18' y2='19'/%3E%3Cline x1='65' y1='50' x2='63' y2='57'/%3E%3Cline x1='110' y1='24' x2='108' y2='31'/%3E%3Cline x1='40' y1='95' x2='38' y2='102'/%3E%3Cline x1='95' y1='118' x2='93' y2='125'/%3E%3C/g%3E%3C/svg%3E");
    background-size: 90px 90px, 140px 140px; animation: rainFall .55s linear infinite; }

.sp-bolt { position: absolute; top: 0; height: 62vh; width: 130px; opacity: 0; z-index: 2;
    filter: drop-shadow(0 0 10px #fff7b0) drop-shadow(0 0 24px #facc15); animation: spBolt 5s linear both; }
.sp-bolt svg { width: 100%; height: 100%; }
.sp-bolt.sb1 { left: 12%; }
.sp-bolt.sb2 { right: 11%; transform: scaleX(-1); animation-delay: .15s; }
@keyframes spBolt {
    0%,17% { opacity: 0; } 18% { opacity: 1; } 19.5% { opacity: .15; } 21% { opacity: 1; } 24% { opacity: 0; }
    41% { opacity: 0; } 42% { opacity: 1; } 43.5% { opacity: .2; } 45% { opacity: .9; } 48%,100% { opacity: 0; }
}
.sp-flash { position: absolute; inset: 0; z-index: 4; pointer-events: none; opacity: 0; background: rgba(255,246,200,.7); animation: spFlash 5s linear both; }
@keyframes spFlash {
    0%,17% { opacity: 0; } 18% { opacity: .6; } 19.5% { opacity: 0; } 21% { opacity: .5; } 24% { opacity: 0; }
    41% { opacity: 0; } 42% { opacity: .55; } 45% { opacity: .4; } 48%,100% { opacity: 0; }
}

/* glass card, same look as the app */
.sp-content {
    position: relative; z-index: 3; max-width: 880px; margin: 0 20px; padding: 34px 40px 32px;
    background: rgba(255,255,255,.62); border: 1px solid rgba(255,255,255,.85); border-radius: 24px;
    backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px); box-shadow: 0 24px 64px rgba(20,60,130,.28);
    animation: spFade .8s ease .3s both;
}
.sp-kicker { font-size: .85rem; font-weight: 600; letter-spacing: .32em; text-transform: uppercase; color: #1d4ed8; animation: spFade .8s ease .6s both; }
.sp-name {
    font-family: 'Sora', sans-serif; font-weight: 700; font-size: clamp(2.3rem, 7.5vw, 4.4rem); line-height: 1.1; margin: 10px 0 14px;
    color: #0b1c33; text-shadow: 0 2px 22px rgba(255,255,255,.95);
    animation: spName 1.1s cubic-bezier(.2,.7,.2,1) 1s both;
}
@keyframes spName {
    from { opacity: 0; transform: translateY(22px) scale(.96); filter: blur(12px); letter-spacing: .18em; }
    to   { opacity: 1; transform: none; filter: blur(0); letter-spacing: .01em; }
}
.sp-line { height: 3px; width: 160px; margin: 0 auto 16px; border-radius: 3px;
    background: linear-gradient(90deg, transparent, #2563eb, #0ea5e9, transparent); transform-origin: center; animation: spLine .8s ease 1.8s both; }
@keyframes spLine { from { transform: scaleX(0); opacity: 0; } to { transform: scaleX(1); opacity: 1; } }
.sp-title { font-family: 'Sora', sans-serif; font-weight: 600; font-size: clamp(1.05rem, 2.6vw, 1.5rem); color: #10213a; animation: spFade .8s ease 2.1s both; }
.sp-desc { max-width: 620px; margin: 12px auto 0; font-size: 1.02rem; line-height: 1.65; color: #2c4566; animation: spFade .8s ease 2.6s both; }
.sp-chips { margin-top: 18px; animation: spFade .8s ease 3.2s both; }
.sp-chips span { display: inline-block; margin: 4px; padding: 5px 13px; border-radius: 999px; font-size: .82rem; color: #1d4ed8;
    border: 1px solid #aac6f2; background: #fff; }
@keyframes spFade { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }

.sp-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 24px; }
.sp-card { background: #fff; border: 1px solid #bcd3f5; border-radius: 16px; padding: 16px 10px 14px;
    box-shadow: 0 8px 20px rgba(20,60,130,.12); animation: spPop .7s cubic-bezier(.2,.8,.2,1) both; }
.sp-card:nth-child(1) { animation-delay: 2.4s; } .sp-card:nth-child(2) { animation-delay: 2.6s; }
.sp-card:nth-child(3) { animation-delay: 2.8s; } .sp-card:nth-child(4) { animation-delay: 3.0s; }
.sp-card .ic { display: inline-block; font-size: 1.9rem; line-height: 1; margin-bottom: 8px; animation: spFloat 3s ease-in-out infinite; }
.sp-card:nth-child(2) .ic { animation-delay: .3s; } .sp-card:nth-child(3) .ic { animation-delay: .6s; } .sp-card:nth-child(4) .ic { animation-delay: .9s; }
.sp-card .ct { font-family: 'Sora', sans-serif; font-weight: 600; font-size: .95rem; color: #0b1c33; }
.sp-card .cs { font-size: .8rem; color: #41587a; margin-top: 3px; }
@keyframes spPop { from { opacity: 0; transform: translateY(16px) scale(.92); } to { opacity: 1; transform: none; } }
@keyframes spFloat { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-4px); } }

.sp-bar { position: absolute; left: 0; right: 0; bottom: 0; height: 4px; background: rgba(20,60,130,.12); z-index: 3; }
.sp-bar span { display: block; height: 100%; background: linear-gradient(90deg, #2563eb, #0ea5e9); animation: spBar 4.8s linear both; }
@keyframes spBar { from { width: 0; } to { width: 100%; } }

@media (max-width: 600px) { .sp-content { padding: 24px 20px; } .sp-bolt { display: none; } .sp-cards { grid-template-columns: repeat(2, 1fr); } }
@media (prefers-reduced-motion: reduce) {
    .splash { animation: splashOut .8s ease 3s forwards; }
    .sp-cloud, .sp-rain, .sp-bolt, .sp-flash, .sp-content, .sp-kicker, .sp-name, .sp-line, .sp-title, .sp-card, .sp-card .ic, .sp-bar span { animation: none !important; }
    .sp-bolt, .sp-flash { opacity: 0 !important; }
    .sp-bar span { width: 100%; }
}
</style>
"""
SKY = """<div class="sky"><div class="tone"></div><div class="sun"><div class="rays"></div><div class="glow"></div><div class="disc"></div></div><div class="rainbow"></div><div class="cloud w w1"></div><div class="cloud w w2"></div><div class="cloud w w3"></div><div class="cloud w w4"></div><div class="cloud w w5"></div><div class="bird k1"><svg viewBox="0 0 26 12"><path d="M1 8 Q7 0 13 7 Q19 0 25 8" fill="none" stroke="#2c4566" stroke-width="2" stroke-linecap="round"/></svg></div><div class="bird k2"><svg viewBox="0 0 26 12"><path d="M1 8 Q7 0 13 7 Q19 0 25 8" fill="none" stroke="#2c4566" stroke-width="2" stroke-linecap="round"/></svg></div><div class="bird k3"><svg viewBox="0 0 26 12"><path d="M1 8 Q7 0 13 7 Q19 0 25 8" fill="none" stroke="#2c4566" stroke-width="2" stroke-linecap="round"/></svg></div><div class="cloud storm s1"></div><div class="cloud storm s2"></div><div class="cloud storm s3"></div><div class="cloud storm s4"></div><div class="rain"></div><div class="bolt-wrap b1"><svg viewBox="0 0 80 200" preserveAspectRatio="none"><polygon points="44,0 18,86 38,86 22,200 66,70 44,70 62,0" fill="#fff6a8" stroke="#fde047" stroke-width="2" stroke-linejoin="round"/></svg></div><div class="bolt-wrap b2"><svg viewBox="0 0 80 200" preserveAspectRatio="none"><polygon points="44,0 18,86 38,86 22,200 66,70 44,70 62,0" fill="#fff6a8" stroke="#fde047" stroke-width="2" stroke-linejoin="round"/></svg></div><div class="flash"></div><div class="flash f2"></div><div class="ripple" style="left:46%; animation-delay:.3s"></div><div class="ripple" style="left:54%; animation-delay:1.1s"></div><div class="ripple" style="left:62%; animation-delay:1.8s"></div><div class="ripple" style="left:70%; animation-delay:.6s"></div><div class="ripple" style="left:78%; animation-delay:1.4s"></div><div class="ripple" style="left:86%; animation-delay:.2s"></div><div class="ripple" style="left:94%; animation-delay:1.6s"></div></div>"""
BOLT_SVG = '<svg viewBox="0 0 80 200" preserveAspectRatio="none"><polygon points="44,0 18,86 38,86 22,200 66,70 44,70 62,0" fill="#fff6a8" stroke="#fde047" stroke-width="2" stroke-linejoin="round"/></svg>'
SPLASH = (
    '<div class="splash">'
    '<div class="sp-cloud sc1"></div><div class="sp-cloud sc2"></div><div class="sp-cloud sc5"></div>'
    '<div class="sp-cloud sc3"></div><div class="sp-cloud sc4"></div>'
    '<div class="sp-rain"></div>'
    '<div class="sp-bolt sb1">__BOLT__</div><div class="sp-bolt sb2">__BOLT__</div>'
    '<div class="sp-flash"></div>'
    '<div class="sp-content">'
    '<div class="sp-kicker">A project by</div>'
    '<div class="sp-name">Meerab Afzal</div>'
    '<div class="sp-line"></div>'
    '<div class="sp-title">Weather Analysis &amp; Storm Prediction Agent</div>'
    '<div class="sp-cards">'
    '<div class="sp-card"><div class="ic">🌍</div><div class="ct">Live Weather</div><div class="cs">Any city worldwide</div></div>'
    '<div class="sp-card"><div class="ic">🌧</div><div class="ct">Rain Prediction</div><div class="cs">Hours ahead</div></div>'
    '<div class="sp-card"><div class="ic">⛈</div><div class="ct">Storm Prediction</div><div class="cs">Risk level per hour</div></div>'
    '<div class="sp-card"><div class="ic">📄</div><div class="ct">News PDF</div><div class="cs">Download from the app</div></div>'
    '</div>'
    '</div>'
    '<div class="sp-bar"><span></span></div>'
    '</div>'
).replace("__BOLT__", BOLT_SVG)
st.markdown(CSS, unsafe_allow_html=True)
st.markdown(SKY, unsafe_allow_html=True)

# Opening splash: only on the first load of a session (a browser refresh shows it again)
if not st.session_state.get("splash_done"):
    st.markdown(SPLASH, unsafe_allow_html=True)
    st.session_state.splash_done = True

# ------------------------------------------------------------------ state ---
st.session_state.setdefault("latest_result", None)
st.session_state.setdefault("query", "")

EXAMPLES = {
    "Lahore – next 6 hours": "Lahore next 6 hours weather, rain and storm forecast",
    "Karachi storm risk": "Karachi next 6 hours rain and storm forecast",
    "Istanbul – next 6 hours": "Istanbul next 6 hours weather, rain and storm forecast",
    "London – 3 days": "London weather for next 3 days",
}


def _set_query(text: str) -> None:
    st.session_state.query = text


def _detect_risk(text: str) -> str:
    order = ["none", "low", "moderate", "high"]
    found = re.findall(r"Storm Risk:\s*\**\s*(None|Low|Moderate|High)", text, flags=re.I)
    if not found:
        return "none"
    return max((f.lower() for f in found), key=order.index)


def _show_error(exc: Exception) -> None:
    msg = str(exc)
    if "429" in msg or "rate_limit" in msg.lower() or "rate limit" in msg.lower():
        wait = re.search(r"try again in ([0-9hms\.]+)", msg)
        when = f" Try again in about {wait.group(1)}." if wait else ""
        st.warning(
            "The AI model has hit its daily usage limit." + when +
            " You can also switch to another model by setting WEATHER_AGENT_MODEL in your .env file."
        )
    else:
        st.error(f"Could not complete the analysis: {msg}")


RISK_TEXT = {
    "none": "No storm risk detected",
    "low": "Low storm risk",
    "moderate": "Moderate storm risk",
    "high": "High storm risk",
}
RISK_SUB = {
    "none": "Conditions look calm for the forecast window.",
    "low": "Keep an eye on gusts and rain chances.",
    "moderate": "Strong gusts or heavy rain possible. Plan ahead.",
    "high": "Thunderstorm or damaging winds likely. Verify with official alerts.",
}

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _clean_response(text: str) -> str:
    """Hide the PDF filename line and the in-text live line (the live card replaces it); shorten ISO timestamps."""
    text = re.sub(r"(?im)^\s*(?:report pdf:?\s*)?\**[\w\-]+_\d{8}_\d{6}\.pdf\**\s*$\n?", "", text)
    text = re.sub(r"(?im)^>\s*\*\*Live (?:Conditions|Weather).*\n?", "", text)
    text = re.sub(
        r"\b\d{4}-(\d{2})-(\d{2})T(\d{2}:\d{2})\b",
        lambda m: f"{int(m.group(2))} {MONTHS[int(m.group(1)) - 1]} {m.group(3)}",
        text,
    )
    return text.strip()


STEPS = [
    ("1", "Ask", "Type any city worldwide or pick an example. Comparing several cities works too."),
    ("2", "Analyze", "The agent pulls live weather and air quality, then scores rain and storm risk hour by hour."),
    ("3", "Download", "Get a news-ready PDF brief, saved automatically to your report archive."),
]


def _empty_state_html() -> str:
    steps = "".join(
        f'<div class="step"><div class="num">{n}</div><div class="t">{t}</div><div class="d">{d}</div></div>'
        for n, t, d in STEPS
    )
    legend = "".join(
        f'<div class="lg"><span class="chip {k}">{k.title()}</span><div class="d">{RISK_SUB[k]}</div></div>'
        for k in ("none", "low", "moderate", "high")
    )
    pills = "".join(
        f'<span class="pill">{p}</span>'
        for p in ("Any city worldwide", "1 hour to 7 days ahead", "Live weather &amp; air quality", "Rain &amp; storm risk per hour")
    )
    return (
        '<div class="empty-title">How it works</div>'
        f'<div class="steps">{steps}</div>'
        '<div class="empty-title" style="margin-top:1.1rem">Storm risk levels in your report</div>'
        f'<div class="legend-row">{legend}</div>'
        '<div class="empty-title" style="margin-top:1.1rem">Coverage</div>'
        f'<div class="meta-row">{pills}</div>'
    )


def _weather_icon(code) -> str:
    try:
        c = int(code)
    except (TypeError, ValueError):
        return "🌡️"
    if c in (0, 1):
        return "☀️"
    if c == 2:
        return "⛅"
    if c == 3:
        return "☁️"
    if c in (45, 48):
        return "🌫️"
    if 51 <= c <= 57:
        return "🌦️"
    if 61 <= c <= 67 or 80 <= c <= 82:
        return "🌧️"
    if 71 <= c <= 77 or c in (85, 86):
        return "❄️"
    if c in (95, 96, 99):
        return "⛈️"
    return "🌡️"


def _aqi_info(v) -> tuple[str, str]:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "", "#41587a"
    for limit, label, color in [
        (50, "Good", "#059669"), (100, "Moderate", "#ca8a04"), (150, "Sensitive groups", "#ea580c"),
        (200, "Unhealthy", "#dc2626"), (300, "Very unhealthy", "#7c3aed"),
    ]:
        if n <= limit:
            return label, color
    return "Hazardous", "#7f1d1d"


def _val(v, unit: str = "") -> str:
    if v is None or v == "N/A":
        return "N/A"
    return f"{html.escape(str(v))}{unit}"


def _city_clock(d: dict) -> tuple[str, str, str]:
    """(date, HH:MM, 'UTC+5') of the city right now, from its UTC offset."""
    off = d.get("utc_offset_seconds")
    if isinstance(off, (int, float)):
        now = datetime.now(timezone.utc) + timedelta(seconds=off)
        sign = "+" if off >= 0 else "-"
        total = abs(int(off))
        hh, mm = total // 3600, (total % 3600) // 60
        utc = f"UTC{sign}{hh}" + (f":{mm:02d}" if mm else "")
        return now.strftime("%a, %d %b %Y"), now.strftime("%H:%M"), utc
    try:
        dt = datetime.fromisoformat(str(d.get("raw_local_time")))
        return dt.strftime("%a, %d %b %Y"), dt.strftime("%H:%M"), ""
    except ValueError:
        return "", "--:--", ""


def _live_card_html(d: dict) -> str:
    aqi_label, aqi_color = _aqi_info(d.get("aqi"))
    aqi_sub = f'<div class="s" style="color:{aqi_color};font-weight:600">{aqi_label}</div>' if aqi_label else ""
    temp_f = f' <small>({_val(d.get("temp_f"), "°F")})</small>' if d.get("temp_f") is not None else ""
    date_txt, clock, utc = _city_clock(d)
    tz_bits = " · ".join(b for b in [html.escape(str(d.get("timezone") or "")), utc] if b)

    def tile(label: str, value: str, sub: str = "") -> str:
        return f'<div class="mt"><div class="l">{label}</div><div class="v">{value}</div>{sub}</div>'

    metrics = "".join([
        tile("Humidity", _val(d.get("humidity_pct"), "%")),
        tile("Wind", _val(d.get("wind_speed_kmh"), " km/h"), f'<div class="s">{_val(d.get("wind_direction"))}</div>'),
        tile("Gusts", _val(d.get("wind_gusts_kmh"), " km/h")),
        tile("Pressure", _val(d.get("pressure_hpa"), " hPa")),
        tile("Cloud cover", _val(d.get("cloud_cover_pct"), "%")),
        tile("Air quality (AQI)", _val(d.get("aqi")), aqi_sub),
    ])
    return (
        '<div class="live"><div>'
        '<div class="live-top"><span class="live-dot"></span>Live now</div>'
        f'<div class="live-city">{html.escape(str(d.get("city", "")))}</div>'
        f'<div class="live-time"><span class="clk">{clock}</span> {html.escape(date_txt)}</div>'
        f'<div class="live-tz">{tz_bits} · weather observed {html.escape(str(d.get("observed_at", "")))}</div>'
        f'<div class="live-temp"><span class="live-ico">{_weather_icon(d.get("weather_code"))}</span>'
        f'<span class="live-deg">{_val(d.get("temp_c"), "°C")}{temp_f}</span></div>'
        f'<div class="live-cond">{html.escape(str(d.get("weather_condition", "")))}</div>'
        f'</div><div class="metrics">{metrics}</div></div>'
    )


@st.fragment(run_every="30s")
def _live_cards_view() -> None:
    """Re-renders every 30 s so each city's clock keeps moving without a full page rerun."""
    data = st.session_state.get("latest_result") or {}
    for live in data.get("live", []):
        st.markdown(_live_card_html(live), unsafe_allow_html=True)


@st.dialog("Delete all saved reports?")
def _confirm_clear(n: int) -> None:
    st.write(
        f"This permanently deletes all **{n}** saved reports and their PDF files. "
        "New analyses will be saved again as usual."
    )
    yes, no = st.columns(2)
    if yes.button("Yes, delete all", type="primary", use_container_width=True):
        clear_all_reports()
        st.session_state.latest_result = None
        st.session_state.show_all = False
        st.rerun()
    if no.button("Cancel", use_container_width=True):
        st.rerun()


# ------------------------------------------------------------------- hero ---
st.markdown(
    """
    <div class="hero">
        <div>
            <h1>Weather & Storm Prediction Agent</h1>
            <p>Live conditions for any city worldwide, with hourly rain and storm outlooks and a downloadable newsroom PDF.</p>
            <div class="tags">
                <span class="tag">Live weather &amp; AQI</span>
                <span class="tag">Hourly rain &amp; storm risk</span>
                <span class="tag">News-ready PDF</span>
                <span class="tag">Report archive</span>
            </div>
        </div>
        <svg class="scene" viewBox="0 0 290 170" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
            <g class="sunrays" stroke="#fbbf24" stroke-width="3" stroke-linecap="round">
                <line x1="52" y1="6"  x2="52" y2="16"/><line x1="52" y1="70" x2="52" y2="80"/>
                <line x1="17" y1="43" x2="27" y2="43"/><line x1="77" y1="43" x2="87" y2="43"/>
                <line x1="27" y1="18" x2="34" y2="25"/><line x1="70" y1="61" x2="77" y2="68"/>
                <line x1="77" y1="18" x2="70" y2="25"/><line x1="34" y1="61" x2="27" y2="68"/>
            </g>
            <circle cx="52" cy="43" r="17" fill="#fcd34d" stroke="#f59e0b" stroke-width="2"/>
            <g class="cloudbody" transform="translate(30,0)">
                <ellipse cx="82" cy="70" rx="46" ry="30" fill="#7d93b0"/>
                <ellipse cx="130" cy="52" rx="52" ry="38" fill="#8ea4c0"/>
                <ellipse cx="176" cy="72" rx="44" ry="29" fill="#7d93b0"/>
                <rect x="60" y="66" width="140" height="36" rx="18" fill="#7d93b0"/>
            </g>
            <polygon class="bolt" transform="translate(30,0)" points="136,84 112,126 130,126 120,160 158,110 138,110 150,84" fill="#facc15" stroke="#eab308" stroke-width="2" stroke-linejoin="round"/>
            <g stroke="#1d4ed8" stroke-width="3" stroke-linecap="round" transform="translate(30,0)">
                <line class="drop" x1="70"  y1="112" x2="64"  y2="124"/>
                <line class="drop" x1="92"  y1="116" x2="86"  y2="128"/>
                <line class="drop" x1="176" y1="114" x2="170" y2="126"/>
                <line class="drop" x1="198" y1="110" x2="192" y2="122"/>
                <line class="drop" x1="54"  y1="106" x2="48"  y2="118"/>
                <line class="drop" x1="214" y1="104" x2="208" y2="116"/>
            </g>
        </svg>
    </div>
    """,
    unsafe_allow_html=True,
)

left_col, right_col = st.columns([2, 1], gap="large")

# ------------------------------------------------------- left: ask + result ---
with left_col:
    with st.container(key="ask_card"):
        st.markdown(
            '<div class="card-head"><div><div class="card-title">Ask about any city</div>'
            '<div class="card-sub">Pick an example or write your own question.</div></div></div>',
            unsafe_allow_html=True,
        )

        ex_cols = st.columns(len(EXAMPLES))
        for col, (label, text) in zip(ex_cols, EXAMPLES.items()):
            col.button(label, key=f"ex_{label}", on_click=_set_query, args=(text,), use_container_width=True)

        query = st.text_area(
            "Your query (any world city, or several cities to compare)",
            key="query",
            height=110,
            placeholder="Example: Istanbul next 6 hours weather, or Karachi rain and storm forecast for next 12 hours",
        )

        if st.button("Analyze & generate news PDF", type="primary"):
            if not query.strip():
                st.warning("Enter a city or question first.")
            else:
                with st.spinner("Fetching live weather, assessing rain and storm risk, building your PDF…"):
                    try:
                        st.session_state.latest_result = run_weather_agent(query.strip())
                    except Exception as exc:
                        _show_error(exc)

    data = st.session_state.latest_result
    with st.container(key="result_card"):
        head_l, head_r = st.columns([2, 1], vertical_alignment="center")
        head_l.markdown(
            '<div class="card-title">Weather analysis &amp; storm prediction</div>'
            '<div class="card-sub">Live forecast, rain and storm outlook.</div>',
            unsafe_allow_html=True,
        )

        if data:
            pdf_name = data.get("pdf_filename")
            if pdf_name:
                pdf_path = REPORTS_DIR / Path(pdf_name).name
                if pdf_path.exists():
                    head_r.download_button(
                        label="Download news PDF",
                        data=pdf_path.read_bytes(),
                        file_name=pdf_path.name,
                        mime="application/pdf",
                        key=f"latest_dl_{pdf_path.name}",
                        use_container_width=True,
                    )

            _live_cards_view()

            risk = _detect_risk(data["response"])
            st.markdown(
                f'<div class="risk {risk}"><div class="dot"></div><div>'
                f'<div class="lvl">{RISK_TEXT[risk]}</div><div class="sub">{RISK_SUB[risk]}</div>'
                '</div></div>',
                unsafe_allow_html=True,
            )
            st.markdown(_clean_response(data["response"]))
        else:
            st.markdown(_empty_state_html(), unsafe_allow_html=True)

# ------------------------------------------------------- right: saved reports ---
filter_text = st.session_state.get("flt", "").strip().lower()
all_reports = list(reversed(get_stored_reports()))   # read after any new analysis was saved
stored = all_reports
if filter_text:
    stored = [
        item
        for item in stored
        if filter_text in f"{item.get('content', '')} {item.get('metadata', '')}".lower()
    ]

PREVIEW = 8
show_all = st.session_state.get("show_all", False)
visible = stored if (filter_text or show_all) else stored[:PREVIEW]

with right_col:
    with st.container(key="reports_card"):
        st.markdown(
            '<div class="card-head"><div><div class="card-title">Saved reports</div>'
            '<div class="card-sub">Download any previous briefing.</div></div>'
            f'<span class="count">{len(stored)}</span></div>',
            unsafe_allow_html=True,
        )
        st.text_input(
            "Filter reports",
            key="flt",
            placeholder="Filter by city, risk or headline…",
            label_visibility="collapsed",
        )

        if not stored:
            st.info("No matching reports yet. Run an analysis to create one.")

        for idx, entry in enumerate(visible):
            meta = entry.get("metadata", {})
            fname = meta.get("pdf_filename")
            prefix = html.escape(str(meta.get("report_prefix", "report")))
            risk_raw = str(meta.get("risk_level", "N/A"))
            risk_cls = risk_raw.lower() if risk_raw.lower() in RISK_TEXT else "na"
            created = html.escape(str(meta.get("created_at", ""))[:16].replace("T", " "))

            with st.container(border=True):
                st.markdown(
                    f'<div class="rbody"><div class="rname" title="{prefix}">{prefix}</div>'
                    f'<div class="rmeta"><span class="chip {risk_cls}">{html.escape(risk_raw)}</span>{created}</div></div>',
                    unsafe_allow_html=True,
                )
                if fname:
                    fpath = REPORTS_DIR / Path(fname).name
                    if fpath.exists():
                        st.download_button(
                            label="Download PDF",
                            data=fpath.read_bytes(),
                            file_name=fpath.name,
                            mime="application/pdf",
                            key=f"dl_{idx}_{fpath.name}",
                            use_container_width=True,
                        )

        if not filter_text and len(stored) > PREVIEW:
            label = "Show less" if show_all else f"Show all {len(stored)} reports"
            if st.button(label, key="toggle_all", use_container_width=True):
                st.session_state.show_all = not show_all
                st.rerun()

        if all_reports:
            with st.container(key="clear_wrap"):
                if st.button("Delete all reports", key="clear_all_btn", use_container_width=True):
                    _confirm_clear(len(all_reports))

st.markdown(
    '<div class="footer">Generated from live weather &amp; air-quality feeds with AI reasoning. '
    'Always verify severe storm alerts with official meteorological authorities.</div>',
    unsafe_allow_html=True,
)