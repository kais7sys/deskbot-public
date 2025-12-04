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

# ==========================================
# 1. CONFIGURATION: FIRST PRINCIPLES DESIGN
# ==========================================
st.set_page_config(page_title="DeskBot // Workspace", page_icon="⚡", layout="wide")

# CSS Injection: Force Notion-like Dark Mode & Clean UI
st.markdown("""
<style>
    /* Absolute Dark Mode */
    .stApp {background-color: #191919; color: #e0e0e0;}
    
    /* Sidebar Engineering */
    [data-testid="stSidebar"] {background-color: #202020; border-right: 1px solid #333;}
    
    /* Input Fields: Industrial Grade */
    .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] {
        background-color: #2b2b2b !important; 
        color: white !important;
        border: 1px solid #404040 !important; 
        border-radius: 6px;
    }
    
    /* Remove Bloat */
    header, #MainMenu, footer {visibility: hidden;}
    
    /* Tab Architecture */
    .stTabs [data-baseweb="tab-list"] {gap: 8px; border-bottom: 1px solid #333;}
    .stTabs [data-baseweb="tab"] {height: 40px; font-size: 13px; color: #888; background-color: transparent;}
    .stTabs [aria-selected="true"] {color: #fff !important; border-bottom: 2px solid #fff;}

    /* Chat Aesthetics: Minimalist */
    .stChatMessage {background-color: transparent; border: none; padding: 5px 0px;}
    [data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"] {display: none;}
    
    /* Studio Panel Border */
    div.css-1r6slb0 {border: 1px solid #333; border-radius: 8px; padding: 20px;}
</style>
""", unsafe_allow_html=True)

# Session State Initialization
if "user" not in st.session_state: st.session_state.user = None
if "active_notebook_id" not in st.session_state: st.session_state.active_notebook_id = None
if "show_create_modal" not in st.session_state: st.session_state.show_create_modal = False
if "chat_session" not in st.session_state: st.session_state.chat_session = None

# ==========================================
# 2. THE BACKEND CONNECTION
# ==========================================
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try: supabase = init_supabase()
except: st.error("⚠️ CRITICAL FAILURE: Supabase Keys missing.")

# ==========================================
# 3. CORE LOGIC ENGINE (HELPERS)
# ==========================================
def image_to_base64(image):
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

def base64_to_image(base64_str):
    try: return Image.open(BytesIO(base64.b64decode(base64_str)))
    except: return None

# ==========================================
# 4. AUTHENTICATION MODULE
# ==========================================
def login_page():
    c1, c2, c3 = st.columns([1,2,1])
    with c2:
        st.title("⚡ DeskBot")
        st.caption("Sign in to initialize your neural workspace.")
        tab1, tab2 = st.tabs(["Access", "Initialize"])
        with tab1:
            with st.form("login"):
                e = st.text_input("Email"); p = st.text_input("Password", type="password")
                if st.form_submit_button("Enter System", use_container_width=True):
                    try:
                        res = supabase.auth.sign_in_with_password({"email":e,"password":p})
                        st.session_state.user = res.user
                        st.rerun()
                    except Exception as err: st.error(f"Access Denied: {err}")
        with tab2:
            with st.form("signup"):
                e = st.text_input("Email"); p = st.text_input("Pass (min 6 chars)", type="password")
                if st.form_submit_button("Create ID", use_container_width=True):
                    try:
                        res = supabase.auth.sign_up({"email":e,"password":p})
                        st.session_state.user = res.user
                        st.success("ID Created. Access System."); time.sleep(1); st.rerun()
                    except Exception as err: st.error(f"Creation Failed: {err}")

def logout():
    supabase.auth.sign_out()
    st.session_state.user = None
    st.session_state.active_notebook_id = None
    st.session_state.chat_session = None
    st.rerun()

# ==========================================
# 5. MAIN APPLICATION CONTROLLER
# ==========================================
def main_app():
    user_id = st.session_state.user.id
    email = st.session_state.user.email

    # --- DATABASE INTERFACE LAYERS ---
    def get_notebooks():
        try:
            # We treat tasks as Notebook containers
            res = supabase.table("tasks").select("*").eq("user_id", user_id).order("id", desc=True).execute()
            df = pd.DataFrame(res.data)
            if not df.empty:
                df["id"] = pd.to_numeric(df["id"]).astype(int)
                # Robust date handling
                df["due_date"] = pd.to_datetime(df["due_date"], errors='coerce').dt.date
                df = df.dropna(subset=['due_date'])
                df["status"] = df["status"].astype(str)
            return df
        except: return pd.DataFrame()

    def create_notebook(title):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            data = supabase.table("tasks").insert({
                "user_id": user_id, "title": title, "est_minutes": 0, "due_date": today
            }).execute()
            return data.data[0]['id']
        except: return None

    # --- AI TOOLS (FUNCTION CALLING) ---
    def add_task_to_scheduler(task_title: str, duration_minutes: int, due_date: str):
        """Adds task to DB. duration_minutes is INT. due_date is YYYY-MM-DD."""
        try:
            if not isinstance(duration_minutes, int): duration_minutes = 60
            supabase.table("tasks").insert({
                "user_id": user_id, "title": task_title, "est_minutes": duration_minutes, "due_date": due_date
            }).execute()
            return f"✅ Scheduled: '{task_title}' on {due_date}"
        except Exception as e: return f"❌ System Error: {e}"

    def update_task_in_db(tid, updates):
        try: supabase.table("tasks").update(updates).eq("id", tid).execute()
        except: pass

    def delete_task_in_db(tid):
        try: supabase.table("tasks").delete().eq("id", tid).execute()
        except: pass

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
            prompt = f"Context: {context[:6000]}\nGenerate Graphviz DOT code for a mindmap on '{topic}'. Style: Dark mode, minimalist. Only output DOT code inside ```dot``` blocks."
            response = model.generate_content(prompt)
            match = re.search(r'```dot\n(.*?)\n```', response.text, re.DOTALL)
            return match.group(1) if match else None
        except: return None

    # --- AI MODEL INITIALIZATION ---
    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        # Bind the scheduler tool to the model
        model = genai.GenerativeModel('gemini-2.0-flash', tools=[add_task_to_scheduler])
        
        # Persistent Chat Session Logic
        if "chat_session" not in st.session_state or st.session_state.chat_session is None:
            st.session_state.chat_session = model.start_chat(enable_automatic_function_calling=True)

    def ask_agent(user_msg, context, image_data=None):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            # System Prompt Engineering
            prompt_parts = [
                f"SYSTEM: You are DeskBot. Date: {today}. If user gives a time (e.g. 7pm), append it to the Task Title.",
                f"CONTEXT:\n{context}", f"USER: {user_msg}"
            ]
            if image_data: prompt_parts.append(image_data)
            response = st.session_state.chat_session.send_message(prompt_parts)
            return response.text
        except Exception as e: return f"AI Error: {e}"

    # --- PERSISTENT HISTORY (IMAGES INCLUDED) ---
    def save_chat_message(role, content, task_id, image_data=None):
        try:
            data = {"user_id": user_id, "role": role, "content": content}
            if task_id: data["task_id"] = int(task_id)
            if image_data: data["image_data"] = image_data # SAVING IMAGE TO CLOUD
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

    # ==========================================
    # 6. UI RENDERER (STUDIO LAYOUT)
    # ==========================================
    notebooks_df = get_notebooks()

    # --- LEFT NAVIGATION (SIDEBAR) ---
    with st.sidebar:
        st.caption("WORKSPACE NAVIGATION")
        
        # Primary Action: New Workspace
        if st.button("➕ New Workspace", use_container_width=True, type="primary"):
            st.session_state.show_create_modal = True

        if st.session_state.show_create_modal:
            with st.form("new_nb"):
                title = st.text_input("Workspace Name")
                if st.form_submit_button("Launch"):
                    if title:
                        new_id = create_notebook(title)
                        st.session_state.active_notebook_id = new_id
                        st.session_state.show_create_modal = False
                        st.rerun()

        # Notebook Selector
        selected_task_id = None
        selected_task_title = "General"
        
        if not notebooks_df.empty:
            options = ["General"] + notebooks_df['title'].tolist()
            ids = [None] + notebooks_df['id'].tolist()
            
            # Logic to keep selection persistent
            curr_id = st.session_state.active_notebook_id
            def_idx = ids.index(curr_id) if curr_id in ids else 0
            
            choice = st.selectbox("Active Notebook", options, index=def_idx, label_visibility="collapsed")
            
            if choice != "General":
                idx = options.index(choice)
                selected_task_id = ids[idx]
                selected_task_title = choice
                st.session_state.active_notebook_id = selected_task_id
            else:
                st.session_state.active_notebook_id = None

        st.divider()
        st.caption("DATA SOURCES")
        if selected_task_id:
            docs = get_task_documents(selected_task_id)
            if docs:
                for d in docs:
                    c1, c2 = st.columns([5,1])
                    c1.caption(f"📄 {d['filename']}")
                    if c2.button("×", key=f"del_{d['id']}"): delete_document(d['id']); st.rerun()
            
            # File Uploader (NotebookLLM Style)
            with st.expander("Add Source (+)", expanded=False):
                up_file = st.file_uploader("Upload", type=["pdf", "png", "jpg", "jpeg"], label_visibility="collapsed")
                if up_file:
                    if up_file.type == "application/pdf":
                        if st.button("Index PDF", use_container_width=True):
                            txt = extract_pdf(up_file)
                            if txt: save_document(up_file.name, txt, selected_task_id); st.success("Indexed"); st.rerun()
                    else:
                        st.image(Image.open(up_file), caption="Preview", width=150)
        else:
            st.caption("Select a workspace to attach data.")

        st.divider()
        c_set, c_log = st.columns([1,1])
        with c_log: 
            if st.button("Log Out"): logout()

    # --- MAIN INTERFACE (SPLIT VIEW) ---
    col_chat, col_studio = st.columns([1, 1.3], gap="medium")

    # === SECTION A: CHAT (The Brain) ===
    with col_chat:
        st.subheader(f"💬 {selected_task_title}")
        chat_box = st.container(height=600)
        
        with chat_box:
            history = get_chat_history(selected_task_id)
            if not history: st.caption("System ready. Awaiting input.")
            for msg in history:
                with st.chat_message(msg["role"]):
                    if msg.get("image_data"): # RENDER HISTORY IMAGES
                        try: st.image(base64_to_image(msg["image_data"]), width=300)
                        except: pass
                    st.write(msg["content"])

        # Input Logic
        if p := st.chat_input("Command the system..."):
            img_to_send = None; img_base64 = None
            
            # Check sidebar for pending image upload
            if 'up_file' in locals() and up_file and up_file.type != "application/pdf":
                img_to_send = Image.open(up_file); img_base64 = image_to_base64(img_to_send)

            save_chat_message("user", p, selected_task_id, img_base64)
            
            # Build Context Window
            ctx = notebooks_df.to_string() if not notebooks_df.empty else "No tasks."
            if selected_task_id:
                docs = get_task_documents(selected_task_id)
                for d in docs: ctx += f"\nFILE: {d['filename']}\nCONTENT: {d['content'][:15000]}"

            with st.spinner("Processing..."):
                reply = ask_agent(p, ctx, img_to_send)
            
            save_chat_message("assistant", reply, selected_task_id)
            st.rerun()

    # === SECTION B: STUDIO (The Dashboard) ===
    with col_studio:
        st.subheader("🛠️ Studio")
        
        tab_sched, tab_mind, tab_sum = st.tabs(["Schedule", "Mind Map", "Deep Dive"])

        # 1. Schedule Tab (Calendar + Grid)
        with tab_sched:
            if not notebooks_df.empty:
                # Calendar Visualization
                cal_events = []
                for i, row in notebooks_df.iterrows():
                    if pd.notnull(row['due_date']):
                        cal_events.append({
                            "title": row['title'], 
                            "start": str(row['due_date']), 
                            "allDay": True, 
                            "backgroundColor": "#2ecc71" if row['status']=='done' else "#3498db"
                        })
                calendar(events=cal_events, options={"headerToolbar": {"left": "prev,next", "center": "title", "right": "dayGridMonth"}, "initialView": "dayGridMonth", "height": 400})
                
                st.divider()
                st.caption("Database View")
                
                # Editable Grid
                edited = st.data_editor(
                    notebooks_df, key="editor", hide_index=True, use_container_width=True,
                    column_config={
                        "id": None, "user_id": None, "est_minutes": None,
                        "title": st.column_config.TextColumn("Task"),
                        "due_date": st.column_config.DateColumn("Due"),
                        "status": st.column_config.SelectboxColumn("Status", options=["todo", "done"])
                    }
                )
                # Auto-Save Logic
                if st.session_state["editor"]["edited_rows"]:
                    for idx, updates in st.session_state["editor"]["edited_rows"].items():
                        update_task_in_db(notebooks_df.iloc[idx]["id"], updates)
                    st.rerun()
                if st.session_state["editor"]["deleted_rows"]:
                    for idx in st.session_state["editor"]["deleted_rows"]:
                        delete_task_in_db(notebooks_df.iloc[idx]["id"])
                    st.rerun()
            else:
                st.info("Schedule is clear.")

        # 2. Mind Map Tab
        with tab_mind:
            st.caption("Knowledge Graph")
            if st.button("Generate Graph"):
                if selected_task_id:
                    with st.spinner("Analyzing Architecture..."):
                        doc_ctx = ""
                        docs = get_task_documents(selected_task_id)
                        for d in docs: doc_ctx += d['content'][:10000]
                        if doc_ctx:
                            dot = generate_mindmap(selected_task_title, doc_ctx)
                            if dot: st.graphviz_chart(dot)
                            else: st.error("Analysis Failed.")
                        else: st.warning("Upload PDF Source first.")
                else: st.warning("Select Workspace.")

        # 3. Summary Tab
        with tab_sum:
            st.caption("Content Synthesis")
            if st.button("Synthesize Data"):
                if selected_task_id:
                    with st.spinner("Computing..."):
                        doc_ctx = ""
                        docs = get_task_documents(selected_task_id)
                        for d in docs: doc_ctx += d['content'][:15000]
                        if doc_ctx:
                            summary = ask_agent("Provide a structured executive summary of all attached documents.", doc_ctx)
                            st.markdown(summary)
                        else: st.warning("No Data Sources.")

# Application Entry Point
if st.session_state.user: main_app()
else: login_page()
