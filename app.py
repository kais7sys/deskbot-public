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

# 🎨 CUSTOM CSS: NOTION DARK MODE + NOTEBOOKLLM STYLE
st.markdown("""
<style>
    /* Main Background - Notion Dark */
    .stApp {background-color: #191919; color: #ffffff;}
    
    /* Sidebar - Slightly lighter dark */
    [data-testid="stSidebar"] {background-color: #202020; border-right: 1px solid #333;}
    
    /* Inputs - Dark Grey */
    .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] {
        background-color: #2b2b2b !important;
        color: white !important;
        border: 1px solid #333 !important;
        border-radius: 6px;
    }
    
    /* Remove Streamlit Headers */
    header {visibility: hidden;}
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* Clean Tabs (Notion Style) */
    .stTabs [data-baseweb="tab-list"] {gap: 10px; border-bottom: 1px solid #333;}
    .stTabs [data-baseweb="tab"] {
        height: 40px; 
        font-size: 14px; 
        color: #888; 
        background-color: transparent;
        padding: 0px 15px;
    }
    .stTabs [aria-selected="true"] {
        color: #fff !important; 
        border-bottom: 2px solid #fff;
    }

    /* Minimal Chat Messages (No Avatars/Icons) */
    .stChatMessage {
        background-color: transparent; 
        border: none;
    }
    [data-testid="stChatMessageAvatarUser"] {display: none;}
    [data-testid="stChatMessageAvatarAssistant"] {display: none;}
    
    /* Studio/Right Panel Card Styling */
    div.css-1r6slb0 {border: 1px solid #333; border-radius: 8px; padding: 20px;}
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
        st.caption("Notion-style Workspace with AI Brain")
        tab1, tab2 = st.tabs(["Login", "Sign Up"])
        with tab1:
            with st.form("login"):
                e = st.text_input("Email"); p = st.text_input("Password", type="password")
                if st.form_submit_button("Enter Workspace", use_container_width=True):
                    try:
                        res = supabase.auth.sign_in_with_password({"email":e,"password":p})
                        st.session_state.user = res.user
                        st.rerun()
                    except Exception as err: st.error(f"Error: {err}")
        with tab2:
            with st.form("signup"):
                e = st.text_input("Email"); p = st.text_input("Pass (min 6)", type="password")
                if st.form_submit_button("Create Account", use_container_width=True):
                    try:
                        res = supabase.auth.sign_up({"email":e,"password":p})
                        st.session_state.user = res.user
                        st.success("Welcome!"); time.sleep(1); st.rerun()
                    except Exception as err: st.error(f"Error: {err}")

def logout():
    supabase.auth.sign_out()
    st.session_state.user = None
    st.session_state.chat_session = None
    st.rerun()

# --- 5. MAIN APP ---
def main_app():
    user_id = st.session_state.user.id
    email = st.session_state.user.email

    # --- DB TOOLS ---
    def get_tasks():
        try:
            res = supabase.table("tasks").select("*").eq("user_id", user_id).order("id").execute()
            df = pd.DataFrame(res.data)
            if not df.empty:
                df["id"] = pd.to_numeric(df["id"], errors='coerce').fillna(0).astype(int)
                df["est_minutes"] = pd.to_numeric(df["est_minutes"], errors='coerce').fillna(60).astype(int)
                df["due_date"] = pd.to_datetime(df["due_date"], errors='coerce').dt.date
                df = df.dropna(subset=['due_date'])
                df["status"] = df["status"].astype(str)
            return df
        except: return pd.DataFrame()

    def add_task_to_scheduler(task_title: str, duration_minutes: int, due_date: str):
        try:
            if not isinstance(duration_minutes, int): duration_minutes = 60
            supabase.table("tasks").insert({
                "user_id": user_id, "title": task_title, "est_minutes": duration_minutes, "due_date": due_date
            }).execute()
            return f"✅ Scheduled: '{task_title}' on {due_date}"
        except Exception as e: return f"❌ Error: {e}"

    def update_task_in_db(tid, updates):
        try: supabase.table("tasks").update(updates).eq("id", tid).execute()
        except: pass

    def delete_task_in_db(tid):
        try: supabase.table("tasks").delete().eq("id", tid).execute()
        except: pass

    def save_document(filename, content, task_id):
        try:
            data = {"user_id": user_id, "filename": filename, "content": content}
            if task_id: data["task_id"] = int(task_id)
            supabase.table("documents").insert(data).execute()
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

    # --- AI SETUP ---
    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        my_tools = [add_task_to_scheduler]
        model = genai.GenerativeModel('gemini-2.0-flash', tools=my_tools)
        if "chat_session" not in st.session_state:
            st.session_state.chat_session = model.start_chat(enable_automatic_function_calling=True)

    def ask_agent(user_msg, context, image_data=None):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            prompt_parts = [
                f"SYSTEM: You are DeskBot. Today is {today}. If user gives time, put in Title.",
                f"CONTEXT:\n{context}", f"USER: {user_msg}"
            ]
            if image_data: prompt_parts.append(image_data)
            response = st.session_state.chat_session.send_message(prompt_parts)
            return response.text
        except Exception as e: return f"AI Error: {e}"

    # --- MIND MAP GENERATOR (NEW!) ---
    def generate_mindmap(topic, context):
        try:
            prompt = f"""
            Based on this context: '{context[:5000]}'
            Create a Graphviz DOT code for a Mind Map about: '{topic}'.
            ONLY output the DOT code inside ```dot ... ``` blocks.
            Make it colorful and hierarchical.
            """
            response = model.generate_content(prompt)
            # Extract code between ```dot and ```
            match = re.search(r'```dot\n(.*?)\n```', response.text, re.DOTALL)
            if match: return match.group(1)
            else: return None
        except: return None

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
    # UI LAYOUT (3-COLUMN STUDIO)
    # ==========================
    tasks_df = get_tasks()

    # --- 1. LEFT SIDEBAR (Navigation & Sources) ---
    with st.sidebar:
        st.caption("⚡ WORKSPACE")
        
        # Notebook Selector
        selected_task_id = None
        selected_task_title = "General"
        
        if not tasks_df.empty:
            task_options = {f"{row['title']}": row['id'] for i, row in tasks_df.iterrows()}
            options_list = ["General"] + list(task_options.keys())
            choice = st.selectbox("Select Notebook", options_list, label_visibility="collapsed")
            if choice != "General":
                selected_task_id = task_options[choice]
                selected_task_title = choice
        
        st.divider()
        st.caption("SOURCES")
        
        if selected_task_id:
            task_docs = get_task_documents(selected_task_id)
            if task_docs:
                for d in task_docs:
                    c1, c2 = st.columns([5,1])
                    c1.caption(f"📄 {d['filename']}")
                    if c2.button("×", key=f"d{d['id']}"): delete_document(d['id']); st.rerun()
            
            with st.expander("Add Source", expanded=False):
                up_file = st.file_uploader("File", type=["pdf", "png", "jpg"], label_visibility="collapsed")
                if up_file:
                    if up_file.type == "application/pdf":
                        if st.button("Save PDF", use_container_width=True):
                            txt = extract_pdf(up_file)
                            if txt: save_document(up_file.name, txt, selected_task_id); st.success("Saved!"); time.sleep(1); st.rerun()
                    else:
                        st.image(Image.open(up_file), caption="Preview", width=150)
        else:
            st.caption("Select a notebook to manage sources.")

        st.divider()
        if st.button("Log Out"): logout()

    # --- MAIN SPLIT LAYOUT (CHAT | STUDIO) ---
    
    col_chat, col_studio = st.columns([1, 1.2]) # Chat is 45%, Studio is 55%

    # === 2. MIDDLE COLUMN: CHAT ===
    with col_chat:
        st.subheader(f"💬 {selected_task_title}")
        
        # Container for chat history (scrollable area)
        chat_container = st.container(height=550)
        
        # History Logic
        with chat_container:
            history = get_chat_history(selected_task_id)
            if not history: st.caption("Start a new conversation...")
            for msg in history:
                with st.chat_message(msg["role"]):
                    if msg.get("image_data"):
                        try: st.image(base64_to_image(msg["image_data"]), width=300)
                        except: pass
                    st.write(msg["content"]) # Use write for cleaner text

        # Input Area
        if p := st.chat_input("Type a message..."):
            img_to_send = None; img_base64 = None
            if 'up_file' in locals() and up_file and up_file.type != "application/pdf":
                img_to_send = Image.open(up_file); img_base64 = image_to_base64(img_to_send)

            save_chat_message("user", p, selected_task_id, img_base64)
            
            # Context
            ctx = tasks_df.to_string() if not tasks_df.empty else "No tasks."
            if selected_task_id:
                docs = get_task_documents(selected_task_id)
                for d in docs: ctx += f"\nFILE: {d['filename']}\nCONTENT: {d['content'][:15000]}"

            with st.spinner("Thinking..."):
                reply = ask_agent(p, ctx, img_to_send)
            
            save_chat_message("assistant", reply, selected_task_id)
            st.rerun()

    # === 3. RIGHT COLUMN: STUDIO (The "NotebookLLM" Features) ===
    with col_studio:
        st.subheader("🛠️ Studio")
        
        # Tabs for different Tools
        studio_tab1, studio_tab2, studio_tab3 = st.tabs(["Tasks & Cal", "Mind Map", "Summary"])

        # --- TAB A: TASK GRID & CALENDAR ---
        with studio_tab1:
            st.caption("📅 Schedule")
            if not tasks_df.empty:
                # Calendar
                cal_events = []
                for i, row in tasks_df.iterrows():
                    color = "#2ecc71" if row['status'] == 'done' else "#3498db"
                    cal_events.append({"title": row['title'], "start": str(row['due_date']), "allDay": True, "backgroundColor": color})
                calendar(events=cal_events, options={"headerToolbar": {"left": "prev,next", "center": "title", "right": "dayGridMonth"}, "initialView": "dayGridMonth", "height": 350})
                
                st.divider()
                st.caption("📝 Task Database")
                edited = st.data_editor(tasks_df, key="editor", hide_index=True, use_container_width=True,
                    column_config={
                        "id": None, "user_id": None, "est_minutes": None,
                        "title": st.column_config.TextColumn("Task"),
                        "due_date": st.column_config.DateColumn("Due"),
                        "status": st.column_config.SelectboxColumn("Status", options=["todo", "done"])
                    })
                # Auto-Save Edits
                if st.session_state["editor"]["edited_rows"]:
                    for idx, updates in st.session_state["editor"]["edited_rows"].items():
                        update_task_in_db(tasks_df.iloc[idx]["id"], updates)
                    st.rerun()
                if st.session_state["editor"]["deleted_rows"]:
                    for idx in st.session_state["editor"]["deleted_rows"]:
                        delete_task_in_db(tasks_df.iloc[idx]["id"])
                    st.rerun()
            else:
                st.info("No tasks yet. Ask chat to 'Add a task'.")

        # --- TAB B: MIND MAP (NEW FEATURE!) ---
        with studio_tab2:
            st.caption("🧠 Visual Knowledge Graph")
            if st.button("Generate Mind Map for this Notebook"):
                if selected_task_id:
                    with st.spinner("Generating Visuals..."):
                        # Build context from docs
                        doc_ctx = ""
                        docs = get_task_documents(selected_task_id)
                        for d in docs: doc_ctx += d['content'][:10000]
                        
                        if doc_ctx:
                            dot_code = generate_mindmap(selected_task_title, doc_ctx)
                            if dot_code:
                                st.graphviz_chart(dot_code)
                            else: st.error("Could not generate graph.")
                        else: st.warning("No documents to analyze.")
                else:
                    st.warning("Please select a Notebook first.")

        # --- TAB C: SUMMARY ---
        with studio_tab3:
            st.caption("📑 Deep Summary")
            if st.button("Summarize All Sources"):
                if selected_task_id:
                    with st.spinner("Reading..."):
                        doc_ctx = ""
                        docs = get_task_documents(selected_task_id)
                        for d in docs: doc_ctx += d['content'][:20000]
                        
                        if doc_ctx:
                            summary = ask_agent("Provide a detailed study summary of all attached documents.", doc_ctx)
                            st.markdown(summary)
                        else: st.warning("No documents found.")

if st.session_state.user: main_app()
else: login_page()
