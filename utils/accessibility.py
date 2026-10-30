"""Trusted local assets for focus, announcements and Windows contrast themes."""
import json
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def notify(message: str, focus: str = "heading") -> None:
    st.session_state["_a11y_notice"] = {
        "id": st.session_state.get("_a11y_serial", 0) + 1,
        "message": message,
        "focus": focus,
    }
    st.session_state["_a11y_serial"] = st.session_state["_a11y_notice"]["id"]


def render_accessibility(*, announce: bool = True) -> None:
    # Escape script delimiters: task titles / AI output are data, never HTML or JS.
    notice = st.session_state.pop("_a11y_notice", None) if announce else None
    payload = json.dumps(notice, ensure_ascii=True)
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    st.html(ASSETS / "accessibility.css")
    script = (ASSETS / "accessibility.js").read_text(encoding="utf-8")
    st.html(f"<script>{script}\nwindow.studyMateA11y.update({payload});</script>", unsafe_allow_javascript=True)
