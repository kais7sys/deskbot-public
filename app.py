import streamlit as st
import pandas as pd
import google.generativeai as genai
from PyPDF2 import PdfReader
from PIL import Image
import base64
from io import BytesIO
from datetime import datetime
import graphviz

# ---------------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------------
st.set_page_config(
    page_title="DeskBot Workspace",
    page_icon="🧠",
    layout="wide"
)

# ---------------------------------------------------------------
# GLOBAL CSS (Apple-like, minimal, dark, rounded corners)
# ---------------------------------------------------------------
st.markdown("""
<style>

body {
    background-color: #0f1115;
}

.sidebar .sidebar-content {
    background-color: #111216;
    padding-top: 20px;
    border-right: 1px solid #2a2d33;
}

section.main > div {
    padding: 0px;
}

div.stButton > button {
    background-color: #1a73e8 !important;
    color: white !important;
    border-radius: 10px !important;
    padding: 0.6rem 1rem !important;
    border: none !important;
}
div.stButton > button:hover {
    background-color: #0b57cf !important;
}

.chat-box {
    background: #1a1c21;
    padding: 14px;
    border-radius: 14px;
    margin-bottom: 10px;
    color: #d7d8dc;
    font-size: 15px;
}

.chat-prompt {
    background: #1d4ed8;
    color: white;
    border-radius: 14px;
    padding: 10px 16px;
    display: inline-block;
    margin-bottom: 6px;
}

.toolbar-button {
    width: 40px;
    height: 40px;
    background: #1f2126;
    border-radius: 12px;
    display: flex;
    justify-content: center;
    align-items: center;
}
.toolbar-button:hover {
    background: #2a2d33;
}

.mindmap-box {
    background: #111216;
    padding: 20px;
    border-radius: 16px;
}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------
# SIDEBAR UI
# ---------------------------------------------------------------
st.sidebar.markdown("## 🧾 Workspaces")
st.sidebar.button("+ New Workspace")

st.sidebar.markdown("### 📚 Notebooks")
st.sidebar.write("• Physics Project")

st.sidebar.markdown("### 📎 Sources")
uploaded_files = st.sidebar.file_uploader(
    "Upload files", accept_multiple_files=True
)

# ---------------------------------------------------------------
# MAIN APP LAYOUT
# ---------------------------------------------------------------
col1, col2, col3 = st.columns([1.4, 1.4, 1.8])

# ---------------------------------------------------------------
# MIDDLE COLUMN — CHAT
# ---------------------------------------------------------------
with col2:
    st.markdown("## ⚡ Physics Project")

    st.markdown('<div class="chat-prompt">Generate a mind map for Chapter 1</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="chat-box">
    Here is a mind map for Chapter 1. It includes notes on mechanics, thermodynamics,
    kinematics, and related reactions.
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### Actions")
    c1, c2 = st.columns(2)
    with c1:
        st.button("Summarize PDF")
        st.button("Create schedule")
    with c2:
        st.button("Extract key concepts")
        st.button("Generate notes")

# ---------------------------------------------------------------
# RIGHT COLUMN — MIND MAP VIEW
# ---------------------------------------------------------------
with col3:
    st.markdown("## 🧠 Mind Map")

    # Toolbar
    t1, t2 = st.columns([0.15, 0.15])
    with t1:
        st.markdown('<div class="toolbar-button">🔍</div>', unsafe_allow_html=True)
    with t2:
        st.markdown('<div class="toolbar-button">🎨</div>', unsafe_allow_html=True)

    st.markdown("### ")

    st.markdown('<div class="mindmap-box">', unsafe_allow_html=True)

    dot = graphviz.Digraph()
    dot.node("Physics", "Physics", shape="circle", style="filled", color="#1d4ed8", fontcolor="white")

    branches = [
        "Electromagnetic",
        "Physical Optics",
        "Classical Mechanics",
        "Fluid Mechanics",
        "Quantum Mechanics",
        "Statistical Mechanics"
    ]

    for b in branches:
        dot.node(b, b, shape="oval", style="filled", color="white")
        dot.edge("Physics", b)

    st.graphviz_chart(dot)

    st.markdown("</div>", unsafe_allow_html=True)
