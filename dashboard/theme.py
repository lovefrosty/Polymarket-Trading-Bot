from __future__ import annotations


PORTFOLIO_TERMINAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap');

:root {
  --terminal-bg: #02070b;
  --terminal-panel: #050d13;
  --terminal-panel-alt: #07131d;
  --terminal-blue: #66c7ff;
  --terminal-blue-bright: #b9e7ff;
  --terminal-blue-muted: #6f91a6;
  --terminal-line: #174968;
  --terminal-line-bright: #2a7ba8;
  --terminal-text: #e7f5ff;
  --terminal-amber: #f5b942;
  --terminal-red: #ff6677;
  --terminal-up: #70d6ff;
  --terminal-down: #ff6677;
}

html, body, [class*="css"], [data-testid="stAppViewContainer"] {
  background: var(--terminal-bg) !important;
  color: var(--terminal-text) !important;
  font-family: "IBM Plex Mono", Menlo, Consolas, monospace !important;
  letter-spacing: 0 !important;
}

[data-testid="stAppViewContainer"]::before {
  background-image: linear-gradient(rgba(102, 199, 255, 0.018) 1px, transparent 1px) !important;
  background-size: 100% 4px !important;
}

body::before {
  background-image: repeating-linear-gradient(
    to bottom,
    rgba(185, 231, 255, 0.012),
    rgba(185, 231, 255, 0.012) 1px,
    transparent 1px,
    transparent 4px
  ) !important;
}

.block-container {
  max-width: 1900px;
  padding: 0.35rem 0.75rem 1.5rem !important;
}

section[data-testid="stSidebar"] {
  background: #030a0f !important;
  border-right: 1px solid var(--terminal-line) !important;
}

h1, h2, h3, h4, p, label, button, input, textarea, select, [data-testid="stCaptionContainer"] {
  font-family: "IBM Plex Mono", Menlo, Consolas, monospace !important;
  letter-spacing: 0 !important;
}

h1 {
  color: var(--terminal-blue-bright) !important;
  font-size: 1.15rem !important;
  line-height: 1.25 !important;
  text-shadow: none !important;
}

h2 {
  color: var(--terminal-blue) !important;
  font-size: 0.95rem !important;
  line-height: 1.25 !important;
  text-shadow: none !important;
  text-transform: uppercase;
}

h3 {
  color: var(--terminal-blue) !important;
  font-size: 0.76rem !important;
  line-height: 1.2 !important;
  text-transform: uppercase;
}

[data-testid="stCaptionContainer"], .stCaption, small, .muted {
  color: var(--terminal-blue-muted) !important;
  font-size: 0.7rem !important;
}

div[data-testid="stMetric"] {
  min-height: 92px;
  background: var(--terminal-panel) !important;
  border: 1px solid var(--terminal-line) !important;
  border-radius: 0 !important;
  padding: 10px 12px !important;
  box-shadow: none !important;
  backdrop-filter: none !important;
}

div[data-testid="stMetric"]:hover {
  background: var(--terminal-panel-alt) !important;
  border-color: var(--terminal-line-bright) !important;
  box-shadow: none !important;
}

div[data-testid="stMetricLabel"] p {
  color: var(--terminal-blue-muted) !important;
  font-size: 0.66rem !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0 !important;
}

div[data-testid="stMetricValue"] {
  color: var(--terminal-text) !important;
  font-family: "IBM Plex Mono", monospace !important;
  font-size: 1.2rem !important;
  font-weight: 600 !important;
}

div[data-testid="stMetricDelta"] > div,
div[data-testid="stMetricDelta"] * {
  font-family: "IBM Plex Mono", monospace !important;
  font-size: 0.68rem !important;
  color: var(--terminal-blue) !important;
}

div[data-testid="stMetricDelta"] svg,
div[data-testid="stMetricDelta"] path {
  fill: var(--terminal-blue) !important;
}

div[data-baseweb="tab-list"] {
  gap: 0 !important;
  border: 1px solid var(--terminal-line) !important;
  background: #030a0f !important;
}

button[data-baseweb="tab"] {
  min-height: 42px !important;
  background: #030a0f !important;
  color: var(--terminal-blue-muted) !important;
  border: 0 !important;
  border-right: 1px solid var(--terminal-line) !important;
  border-radius: 0 !important;
  margin: 0 !important;
  padding: 8px 18px !important;
  font-family: "IBM Plex Mono", monospace !important;
  font-size: 0.7rem !important;
  font-weight: 500 !important;
  letter-spacing: 0 !important;
  text-transform: uppercase !important;
}

button[data-baseweb="tab"]:hover {
  color: var(--terminal-blue-bright) !important;
  background: var(--terminal-panel-alt) !important;
  border-color: var(--terminal-line-bright) !important;
}

button[data-baseweb="tab"][aria-selected="true"] {
  color: var(--terminal-blue-bright) !important;
  background: #0a2233 !important;
  border: 0 !important;
  border-right: 1px solid var(--terminal-line-bright) !important;
  border-bottom: 2px solid var(--terminal-blue) !important;
  box-shadow: inset 0 0 18px rgba(102, 199, 255, 0.08) !important;
}

button[data-baseweb="tab"] p, button[data-baseweb="tab"] span {
  color: inherit !important;
  font-family: inherit !important;
  font-weight: inherit !important;
  letter-spacing: 0 !important;
}

div[data-testid="stDataFrame"], div[data-testid="stTable"], [data-testid="stJson"] {
  background: var(--terminal-panel) !important;
  border: 1px solid var(--terminal-line) !important;
  border-radius: 0 !important;
  padding: 0 !important;
  box-shadow: none !important;
  backdrop-filter: none !important;
}

details[data-testid="stExpander"] {
  background: var(--terminal-panel) !important;
  border: 1px solid var(--terminal-line) !important;
  border-radius: 0 !important;
}

details[data-testid="stExpander"] summary {
  color: var(--terminal-blue) !important;
  font-family: "IBM Plex Mono", monospace !important;
  font-size: 0.72rem !important;
  letter-spacing: 0 !important;
}

[data-testid="stAlert"] {
  background: var(--terminal-panel) !important;
  border: 1px solid var(--terminal-line) !important;
  border-radius: 0 !important;
  color: var(--terminal-text) !important;
}

.terminal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  min-height: 42px;
  padding: 7px 10px;
  margin: 0 0 7px;
  border: 1px solid var(--terminal-line);
  background: #030a0f;
  color: var(--terminal-blue-muted);
  font-size: 0.72rem;
  text-transform: uppercase;
}

.terminal-header strong { color: var(--terminal-blue-bright); font-weight: 600; }
.terminal-header .live { color: var(--terminal-blue); }
.terminal-header .warn-text { color: var(--terminal-amber); }
.terminal-header .bad-text { color: var(--terminal-red); }

.terminal-live-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 6px;
  background: var(--terminal-blue);
  box-shadow: 0 0 8px rgba(102, 199, 255, 0.75);
  vertical-align: 1px;
  animation: terminal-heartbeat 1s steps(2, end) infinite;
}

.terminal-quote-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(145px, 1fr));
  border-top: 1px solid var(--terminal-line);
  border-left: 1px solid var(--terminal-line);
  margin-bottom: 8px;
}

.terminal-quote {
  position: relative;
  min-height: 88px;
  padding: 9px 11px;
  overflow: hidden;
  border-right: 1px solid var(--terminal-line);
  border-bottom: 1px solid var(--terminal-line);
  background: var(--terminal-panel);
}

.terminal-quote::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0;
}

.terminal-quote.tick-up::after {
  background: rgba(102, 199, 255, 0.2);
  animation: quote-tick-up 620ms steps(3, end);
}

.terminal-quote.tick-down::after {
  background: rgba(255, 102, 119, 0.2);
  animation: quote-tick-down 620ms steps(3, end);
}

.terminal-quote-label {
  display: block;
  color: var(--terminal-blue-muted);
  font-size: 0.62rem;
  text-transform: uppercase;
}

.terminal-quote-value {
  display: block;
  margin-top: 7px;
  color: var(--terminal-text);
  font-size: 1.08rem;
  font-weight: 600;
  line-height: 1.1;
}

.terminal-quote-detail {
  display: block;
  min-height: 1rem;
  margin-top: 6px;
  color: var(--terminal-blue-muted);
  font-size: 0.63rem;
}

.terminal-quote.tick-up .terminal-quote-value,
.terminal-quote.tick-up .terminal-quote-detail { color: var(--terminal-up); }
.terminal-quote.tick-down .terminal-quote-value,
.terminal-quote.tick-down .terminal-quote-detail { color: var(--terminal-down); }

@keyframes terminal-heartbeat {
  0%, 42% { opacity: 1; }
  43%, 100% { opacity: 0.25; }
}

@keyframes quote-tick-up {
  0% { opacity: 0.95; transform: translateY(100%); }
  35% { opacity: 0.7; transform: translateY(0); }
  100% { opacity: 0; transform: translateY(-100%); }
}

@keyframes quote-tick-down {
  0% { opacity: 0.95; transform: translateY(-100%); }
  35% { opacity: 0.7; transform: translateY(0); }
  100% { opacity: 0; transform: translateY(100%); }
}

.terminal-section {
  margin: 9px 0 5px;
  padding: 5px 8px;
  border: 1px solid var(--terminal-line);
  border-bottom-color: var(--terminal-line-bright);
  background: #041019;
  color: var(--terminal-blue);
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
}

.terminal-section span {
  display: inline-block;
  max-width: 58%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.terminal-empty {
  min-height: 120px;
  padding: 18px;
  border: 1px solid var(--terminal-line);
  background: var(--terminal-panel);
  color: var(--terminal-blue-muted);
  font-size: 0.75rem;
}

.terminal-warning {
  padding: 8px 10px;
  border: 1px solid #805f23;
  background: rgba(245, 185, 66, 0.05);
  color: var(--terminal-amber);
  font-size: 0.72rem;
}

.terminal-critical {
  padding: 8px 10px;
  border: 1px solid #7d303d;
  background: rgba(255, 102, 119, 0.05);
  color: var(--terminal-red);
  font-size: 0.72rem;
}

.terminal-status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(145px, 1fr));
  border-top: 1px solid var(--terminal-line);
  border-left: 1px solid var(--terminal-line);
  margin-top: 8px;
}

.terminal-status-cell {
  min-height: 62px;
  padding: 8px;
  border-right: 1px solid var(--terminal-line);
  border-bottom: 1px solid var(--terminal-line);
  background: var(--terminal-panel);
}

.terminal-status-cell span {
  display: block;
  color: var(--terminal-blue-muted);
  font-size: 0.62rem;
  text-transform: uppercase;
}

.terminal-status-cell strong {
  display: block;
  margin-top: 7px;
  color: var(--terminal-blue-bright);
  font-size: 0.76rem;
  font-weight: 500;
}

.stButton > button, .stDownloadButton > button {
  border: 1px solid var(--terminal-line-bright) !important;
  border-radius: 0 !important;
  background: #071824 !important;
  color: var(--terminal-blue-bright) !important;
  font-family: "IBM Plex Mono", monospace !important;
  font-size: 0.7rem !important;
  letter-spacing: 0 !important;
}

.stButton > button:hover, .stDownloadButton > button:hover {
  border-color: var(--terminal-blue) !important;
  background: #0a2233 !important;
}

input, textarea, [data-baseweb="select"] > div {
  border-radius: 0 !important;
  border-color: var(--terminal-line) !important;
  background: var(--terminal-panel) !important;
}

hr { border-top: 1px solid var(--terminal-line) !important; }
::-webkit-scrollbar { width: 7px; height: 7px; }
::-webkit-scrollbar-track { background: var(--terminal-bg); }
::-webkit-scrollbar-thumb { background: var(--terminal-line-bright); border-radius: 0; }

@media (max-width: 900px) {
  .block-container { padding-left: 0.35rem !important; padding-right: 0.35rem !important; }
  .terminal-header { align-items: flex-start; flex-direction: column; gap: 3px; }
  .terminal-section span {
    display: block;
    float: none !important;
    max-width: 100%;
    margin-top: 3px;
  }
  button[data-baseweb="tab"] { padding: 7px 10px !important; font-size: 0.62rem !important; }
  div[data-testid="stMetric"] { min-height: 82px; }
}

@media (prefers-reduced-motion: reduce) {
  .terminal-live-dot,
  .terminal-quote.tick-up::after,
  .terminal-quote.tick-down::after {
    animation: none !important;
  }
}
</style>
"""
