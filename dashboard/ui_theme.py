from __future__ import annotations

from html import escape
from typing import Mapping

THEME_TOKENS: Mapping[str, str] = {
    "bg": "#05060A",
    "surface": "#10121A",
    "border": "#1C1F2A",
    "text": "#E5E7EB",
    "muted": "#9CA3AF",
    "accent": "#3B82F6",
    "success": "#22C55E",
    "warning": "#FACC15",
    "danger": "#F97373",
}


def build_theme_css() -> str:
    return f"""
<style>
:root {{
  --bg:{THEME_TOKENS["bg"]};
  --surface:{THEME_TOKENS["surface"]};
  --border:{THEME_TOKENS["border"]};
  --text:{THEME_TOKENS["text"]};
  --muted:{THEME_TOKENS["muted"]};
  --accent:{THEME_TOKENS["accent"]};
  --success:{THEME_TOKENS["success"]};
  --warning:{THEME_TOKENS["warning"]};
  --danger:{THEME_TOKENS["danger"]};
}}
html, body, [class*="css"], [data-testid="stAppViewContainer"] {{
  background-color: var(--bg) !important;
  color: var(--text) !important;
  font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif !important;
}}
.block-container {{
  padding-top: 12px;
  padding-bottom: 16px;
}}
div[data-testid="stMetric"],
div[data-testid="stContainer"],
div[data-testid="stVerticalBlock"],
div[data-testid="stHorizontalBlock"],
div[data-testid="stDataFrame"],
div[data-testid="stMarkdownContainer"],
section[data-testid="stSidebar"],
[data-testid="stAppViewBlockContainer"] {{
  background: transparent !important;
  box-shadow: none !important;
}}
section[data-testid="stSidebar"] {{
  background: var(--surface) !important;
  border-right: 1px solid var(--border) !important;
}}
div[data-testid="stMetric"] {{
  border: 1px solid var(--border) !important;
  background: var(--surface) !important;
  border-radius: 8px !important;
  padding: 8px 12px !important;
}}
div[data-testid="stMetricLabel"] p,
div[data-testid="stMetricValue"] {{
  color: var(--text) !important;
}}
div[data-testid="stDataFrame"] {{
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: 8px !important;
  padding: 4px !important;
}}
div[data-testid="stDataFrame"] * {{
  background: transparent !important;
  color: var(--text) !important;
}}
div[data-baseweb="tab-list"] {{
  background: transparent !important;
}}
button[data-baseweb="tab"] {{
  border: 1px solid var(--border) !important;
  border-radius: 8px !important;
  margin-right: 6px !important;
  background: var(--surface) !important;
}}
h1, h2, h3 {{
  color: var(--text) !important;
}}
small, .muted {{
  color: var(--muted) !important;
}}
.pm-card {{
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}}
.pm-title {{
  font-size: 20px;
  font-weight: 600;
}}
.pm-toolbar {{
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: 8px;
  padding: 8px;
  margin-bottom: 12px;
}}
.pm-pill {{
  display: inline-block;
  border-radius: 8px;
  padding: 4px 8px;
  border: 1px solid var(--border);
  background: rgba(255,255,255,0.02);
  color: var(--text);
  font-size: 12px;
  margin-right: 8px;
}}
.pm-pill.ok {{
  border-color: color-mix(in srgb, var(--success) 55%, var(--border));
  color: var(--success);
}}
.pm-pill.warn {{
  border-color: color-mix(in srgb, var(--warning) 55%, var(--border));
  color: var(--warning);
}}
.pm-pill.alert {{
  border-color: color-mix(in srgb, var(--danger) 60%, var(--border));
  color: var(--danger);
}}
.pm-status {{
  color: var(--muted);
  font-size: 14px;
}}
.pm-status.alert {{
  color: var(--danger);
  font-weight: 600;
}}
.ok {{
  border: 1px solid color-mix(in srgb, var(--success) 55%, var(--border));
  color: var(--text);
  background: color-mix(in srgb, var(--success) 10%, transparent);
  border-radius: 8px;
  padding: 8px 10px;
}}
.warn {{
  border: 1px solid color-mix(in srgb, var(--warning) 55%, var(--border));
  color: var(--text);
  background: color-mix(in srgb, var(--warning) 10%, transparent);
  border-radius: 8px;
  padding: 8px 10px;
}}
.alert {{
  border: 1px solid color-mix(in srgb, var(--danger) 55%, var(--border));
  color: var(--text);
  background: color-mix(in srgb, var(--danger) 10%, transparent);
  border-radius: 8px;
  padding: 8px 10px;
}}
.readonly-btn {{
  border:1px dashed var(--warning);
  background: color-mix(in srgb, var(--warning) 10%, transparent);
  color:var(--text);
  padding:8px;
  border-radius:8px;
}}
.pm-meta {{
  font-size: 12px;
  color: var(--muted);
}}
hr {{
  border: none;
  border-top: 1px solid var(--border);
}}
</style>
"""


def inject_global_css(st_module) -> None:
    st_module.markdown(build_theme_css(), unsafe_allow_html=True)


def pill(text: str, kind: str = "neutral") -> str:
    klass = "pm-pill"
    if kind in {"ok", "warn", "alert"}:
        klass = f"pm-pill {kind}"
    return f'<span class="{klass}">{escape(text)}</span>'
