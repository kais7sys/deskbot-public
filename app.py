import streamlit as st
import pandas as pd
import google.generativeai as genai
from PyPDF2 import PdfReader
from PIL import Image
import time
import base64
import re
from io import BytesIO
from supabase import create_client, Client
from streamlit_calendar import calendar
from datetime import datetime, date
import graphviz

# --- 1. CONFIG & NOTION DARK THEME ---
st.set_page_config(page_title="DeskBot Workspace", page_icon="⚡", layout="wide")

st.markdown("""
<style>
    /* Main Background */
    .stApp {background-color: #191919; color: #ffffff;}
    
    /* Sidebar */
    [data-testid="stSidebar"] {background-color: #202020; border-right: 1px solid #333;}
    
    /* Inputs */
    .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] {
        background-color: #2b2b2b !important; color: white !important;
        border: 1px solid #333 !important; border-radius: 6px;
    }
    
    /* Headers */
    header, #MainMenu, footer {visibility: hidden;}
    
    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {gap: 10px; border-bottom: 1px solid #333;}
    .stTabs [data-baseweb="tab"] {height: 40px; font-size: 14px; color: #888; background-color: transparent;}
    .stTabs [aria-selected="true"] {color: #fff !important; border-bottom: 2px solid #fff;}

    /* Messages */
    .stChatMessage {background-color: transparent; border: none;}
    [data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"] {display: none;}
    
    /* Primary Button (New Chat) */
    .stButton button {
        border-radius: 8px; font-weight: 500;
    }
</style>
""", unsafe_allow_html=True)

if "user" not in st.session_state: st.session_state.user = None

# --- 2. DATABASE ---
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try: supabase = init_supabase()
except: st.error("⚠️ Supabase Keys missing!")

# --- 3. HELPER FUNCTIONS ---
def image_to_base64(image):
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

def base64_to_image(base64_str):
    try: return Image.open(BytesIO(base64.b64decode(base64_str)))
    except: return None

# --- 4. AUTH ---
def login_page():
    c1, c2, c3 = st.columns([1,2,1])
    with c2:
        st.title("⚡ DeskBot")
        tab1, tab2 = st.tabs(["Login", "Sign Up"])
        with tab1:
            with st.form("login"):
                e = st.text_input("Email"); p = st.text_input("Password", type="password")
                if st.form_submit_button("Enter", use_container_width=True):
                    try:
                        res = supabase.auth.sign_in_with_password({"email":e,"password":p})
                        st.session_state.user = res.user; st.rerun()
                    except Exception as err: st.error(f"Error: {err}")
        with tab2:
            with st.form("signup"):
                e = st.text_input("Email"); p = st.text_input("Pass (min 6)", type="password")
                if st.form_submit_button("Create Account", use_container_width=True):
                    try:
                        res = supabase.auth.sign_up({"email":e,"password":p})
                        st.session_state.user = res.user; st.success("Welcome!"); time.sleep(1); st.rerun()
                    except Exception as err: st.error(f"Error: {err}")

def logout():
    supabase.auth.sign_out()
    st.session_state.user = None; st.session_state.chat_session = None; st.rerun()

# --- 5. MAIN APP ---
def main_app():
    user_id = st.session_state.user.id
    email = st.session_state.user.email

    # --- DB TOOLS ---
    def get_notebooks():
        # We treat "Tasks" as "Notebooks" for now to keep DB simple
        try:
            res = supabase.table("tasks").select("*").eq("user_id", user_id).order("id", desc=True).execute()
            df = pd.DataFrame(res.data)
            if not df.empty:
                df["id"] = pd.to_numeric(df["id"]).astype(int)
                df["due_date"] = pd.to_datetime(df["due_date"], errors='coerce').dt.date
                df = df.dropna(subset=['due_date'])
            return df
        except: return pd.DataFrame()

    def create_notebook(title):
        # Creates a "Task" that acts as a Notebook container
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            data = supabase.table("tasks").insert({
                "user_id": user_id, "title": title, "est_minutes": 0, "due_date": today
            }).execute()
            return data.data[0]['id'] # Return the new ID to switch to it
        except: return None

    # Tools for AI (Scheduling/Docs etc) - Same as before...
    def add_task_to_scheduler(task_title: str, duration_minutes: int, due_date: str):
        try:
            if not isinstance(duration_minutes, int): duration_minutes = 60
            supabase.table("tasks").insert({
                "user_id": user_id, "title": task_title, "est_minutes": duration_minutes, "due_date": due_date
            }).execute()
            return f"✅ Scheduled: '{task_title}' on {due_date}"
        except Exception as e: return f"❌ Error: {e}"
        
    def save_document(filename, content, task_id):
        try:
            supabase.table("documents").insert({"user_id": user_id, "filename": filename, "content": content, "task_id": int(task_id)}).execute()
        except: pass

    def get_task_documents(task_id):
        try:
            res = supabase.table("documents").select("id, filename, content").eq("task_id", task_id).execute()
            return res.data 
        except: return []

    def delete_document(doc_id):
        try: supabase.table("documents").delete().eq("id", doc_id).execute()
        except: pass

    def extract_pdf(file):
        try:
            reader = PdfReader(file)
            return "".join([p.extract_text() for p in reader.pages])
        except: return None

    def generate_mindmap(topic, context):
        try:
            prompt = f"Context: {context[:4000]}\nCreate Graphviz DOT code for a mindmap about '{topic}'. Only output DOT code."
            response = model.generate_content(prompt)
            match = re.search(r'```dot\n(.*?)\n```', response.text, re.DOTALL)
            return match.group(1) if match else None
        except: return None

    # --- AI SETUP ---
    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        model = genai.GenerativeModel('gemini-2.0-flash', tools=[add_task_to_scheduler])
        if "chat_session" not in st.session_state:
            st.session_state.chat_session = model.start_chat(enable_automatic_function_calling=True)

    def ask_agent(user_msg, context, image_data=None):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            prompt_parts = [f"SYSTEM: You are DeskBot. Today is {today}.", f"CONTEXT:\n{context}", f"USER: {user_msg}"]
            if image_data: prompt_parts.append(image_data)
            response = st.session_state.chat_session.send_message(prompt_parts)
            return response.text
        except Exception as e: return f"AI Error: {e}"

    # --- CHAT HISTORY ---
    def save_chat_message(role, content, task_id, image_data=None):
        try:
            data = {"user_id": user_id, "role": role, "content": content}
            if task_id: data["task_id"] = int(task_id)
            if image_data: data["image_data"] = image_data
            supabase.table("chat_history").insert(data).execute()
        except: pass

    def get_chat_history(task_id):
        try:
            if task_id:
                res = supabase.table("chat_history").select("*").eq("task_id", task_id).eq("user_id", user_id).order("created_at").execute()
            else:
                res = supabase.table("chat_history").select("*").is_("task_id", "null").eq("user_id", user_id).order("created_at").execute()
            return res.data
        except: return []

    # ==========================
    # UI LAYOUT
    # ==========================
    notebooks_df = get_notebooks()

    # --- SIDEBAR: NAVIGATION ---
    with st.sidebar:
        st.caption("⚡ WORKSPACE")
        
        # 1. NEW WORKSPACE BUTTON (Gemini Style)
        if st.button("➕ New Workspace", use_container_width=True, type="primary"):
            st.session_state.show_create_modal = True

        # Modal Logic (Simple Input)
        if st.session_state.get("show_create_modal"):
            with st.form("create_nb"):
                new_title = st.text_input("Name your workspace", placeholder="e.g. Physics Project")
                c1, c2 = st.columns(2)
                if c1.form_submit_button("Create"):
                    if new_title:
                        new_id = create_notebook(new_title)
                        st.session_state.active_notebook_id = new_id # Switch to new
                        st.session_state.show_create_modal = False
                        st.rerun()
                if c2.form_submit_button("Cancel"):
                    st.session_state.show_create_modal = False
                    st.rerun()
            st.divider()

        # 2. NOTEBOOK LIST
        selected_task_id = None
        selected_task_title = "General"
        
        # Default to session state selection if available
        default_index = 0
        if not notebooks_df.empty:
            options = ["General"] + notebooks_df['title'].tolist()
            ids = [None] + notebooks_df['id'].tolist()
            
            # Find index of active notebook
            if st.session_state.get("active_notebook_id") in ids:
                default_index = ids.index(st.session_state.active_notebook_id)
            
            choice = st.selectbox("Select Notebook", options, index=default_index, label_visibility="collapsed")
            
            # Update Active ID based on selection
            if choice != "General":
                selected_index = options.index(choice)
                selected_task_id = ids[selected_index]
                selected_task_title = choice
                st.session_state.active_notebook_id = selected_task_id
            else:
                st.session_state.active_notebook_id = None

        st.divider()
        
        # 3. SOURCES (Files for THIS notebook)
        st.caption("SOURCES")
        if selected_task_id:
            task_docs = get_task_documents(selected_task_id)
            if task_docs:
                for d in task_docs:
                    c1, c2 = st.columns([5,1])
                    c1.caption(f"📄 {d['filename']}")
                    if c2.button("×", key=f"d{d['id']}"): delete_document(d['id']); st.rerun()
            
            with st.expander("Add Source", expanded=False):
                up_file = st.file_uploader("Upload", type=["pdf", "png", "jpg"], label_visibility="collapsed")
                if up_file:
                    if up_file.type == "application/pdf":
                        if st.button("Save PDF", use_container_width=True):
                            txt = extract_pdf(up_file)
                            if txt: save_document(up_file.name, txt, selected_task_id); st.success("Saved!"); time.sleep(1); st.rerun()
                    else:
                        st.image(Image.open(up_file), width=150)
        else:
            st.caption("Select a notebook above to add sources.")

        st.divider()
        if st.button("Log Out"): logout()

    # --- MAIN SPLIT LAYOUT ---
    col_chat, col_studio = st.columns([1, 1.2])

    # === CHAT ===
    with col_chat:
        st.subheader(f"💬 {selected_task_title}")
        chat_container = st.container(height=550)
        
        with chat_container:
            history = get_chat_history(selected_task_id)
            if not history: st.caption("Start a new conversation...")
            for msg in history:
                with st.chat_message(msg["role"]):
                    if msg.get("image_data"):
                        try: st.image(base64_to_image(msg["image_data"]), width=300)
                        except: pass
                    st.write(msg["content"])

        if p := st.chat_input("Type a message..."):
            img_to_send = None; img_base64 = None
            if 'up_file' in locals() and up_file and up_file.type != "application/pdf":
                img_to_send = Image.open(up_file); img_base64 = image_to_base64(img_to_send)

            save_chat_message("user", p, selected_task_id, img_base64)
            ctx = notebooks_df.to_string() if not notebooks_df.empty else "No tasks."
            if selected_task_id:
                docs = get_task_documents(selected_task_id)
                for d in docs: ctx += f"\nFILE: {d['filename']}\nCONTENT: {d['content'][:15000]}"

            with st.spinner("Thinking..."):
                reply = ask_agent(p, ctx, img_to_send)
            
            save_chat_message("assistant", reply, selected_task_id)
            st.rerun()

    # === STUDIO ===
    with col_studio:
        st.subheader("🛠️ Studio")
        studio_tab1, studio_tab2, studio_tab3 = st.tabs(["Tasks & Cal", "Mind Map", "Summary"])

        with studio_tab1:
            st.caption("📅 Schedule")
            if not notebooks_df.empty:
                cal_events = []
                for i, row in notebooks_df.iterrows():
                    # Check for valid date before adding to calendar
                    if pd.notnull(row['due_date']):
                        cal_events.append({"title": row['title'], "start": str(row['due_date']), "allDay": True, "backgroundColor": "#3498db"})
                calendar(events=cal_events, options={"headerToolbar": {"left": "prev,next", "center": "title", "right": "dayGridMonth"}, "initialView": "dayGridMonth", "height": 350})
            else: st.info("No tasks.")

        with studio_tab2:
            st.caption("🧠 Visuals")
            if st.button("Generate Mind Map"):
                if selected_task_id:
                    with st.spinner("Generating..."):
                        doc_ctx = ""
                        docs = get_task_documents(selected_task_id)
                        for d in docs: doc_ctx += d['content'][:10000]
                        if doc_ctx:
                            dot_code = generate_mindmap(selected_task_title, doc_ctx)
                            if dot_code: st.graphviz_chart(dot_code)
                            else: st.error("Failed to generate.")
                        else: st.warning("No docs.")
                else: st.warning("Select Notebook.")

        with studio_tab3:
            st.caption("📑 Summary")
            if st.button("Summarize"):
                if selected_task_id:
                    with st.spinner("Reading..."):
                        doc_ctx = ""
                        docs = get_task_documents(selected_task_id)
                        for d in docs: doc_ctx += d['content'][:20000]
                        if doc_ctx:
                            summary = ask_agent("Summarize all documents.", doc_ctx)
                            st.markdown(summary)
                        else: st.warning("No docs.")

if st.session_state.user: main_app()
else: login_page()
    
