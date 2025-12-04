import streamlit as st
import pandas as pd
import google.generativeai as genai
from PyPDF2 import PdfReader
from PIL import Image
import time
import base64
from io import BytesIO
from supabase import create_client, Client
from streamlit_calendar import calendar

# --- 1. CONFIG ---
st.set_page_config(page_title="DeskBot: Pro", page_icon="🧠", layout="wide")
if "user" not in st.session_state: st.session_state.user = None

# --- 2. DATABASE ---
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try: supabase = init_supabase()
except: st.error("⚠️ Supabase Keys missing!")

# --- 3. HELPER FUNCTIONS (IMAGE HANDLING) ---
def image_to_base64(image):
    """Converts a PIL Image to a string we can save in the database."""
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

def base64_to_image(base64_str):
    """Converts the database string back to an image."""
    return Image.open(BytesIO(base64.b64decode(base64_str)))

# --- 4. AUTH ---
def login_page():
    st.title("☁️ DeskBot: Login")
    tab1, tab2 = st.tabs(["Log In", "Sign Up"])
    with tab1:
        with st.form("login"):
            e = st.text_input("Email"); p = st.text_input("Pass", type="password")
            if st.form_submit_button("Log In"):
                try:
                    res = supabase.auth.sign_in_with_password({"email":e,"password":p})
                    st.session_state.user = res.user
                    st.success("Success!"); time.sleep(0.5); st.rerun()
                except Exception as err: st.error(f"Error: {err}")
    with tab2:
        with st.form("signup"):
            e = st.text_input("Email"); p = st.text_input("Pass (min 6)", type="password")
            if st.form_submit_button("Sign Up"):
                try:
                    res = supabase.auth.sign_up({"email":e,"password":p})
                    st.session_state.user = res.user
                    st.success("Created!"); time.sleep(0.5); st.rerun()
                except Exception as err: st.error(f"Error: {err}")

def logout():
    supabase.auth.sign_out()
    st.session_state.user = None
    st.rerun()

# --- 5. MAIN APP ---
def main_app():
    user_id = st.session_state.user.id
    email = st.session_state.user.email

    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        model = genai.GenerativeModel('gemini-2.0-flash')

    # --- DB FUNCTIONS ---
    def get_tasks():
        try:
            res = supabase.table("tasks").select
