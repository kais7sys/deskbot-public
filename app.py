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

# ==============================================================================
# 1. SYSTEM CONFIGURATION & STYLING
# ==============================================================================
st.set_page_config(page_title="DeskBot // Workspace", page_icon="⚡", layout="wide")

# Notion/NotebookLLM Hybrid Theme
st.markdown("""
<style>
    /* Global Reset */
    .stApp {background-color: #191919; color: #E3E3E3; font-family: 'Inter', sans-serif;}
    
    /* Sidebar Engineering */
    [data-testid="stSidebar"] {background-color: #202020; border-right: 1px solid #333;}
    
    /* Inputs: Dark, Rounded, Clean */
    .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] {
        background-color: #2B2B2B !important; 
        color: white !important; 
        border: 1px solid #404040 !important; 
        border-radius: 8px;
    }
    
    /* Remove Streamlit Bloat */
    header, footer, #MainMenu {visibility: hidden;}
    
    /* Tabs: Minimalist Text Links */
    .stTabs [data-baseweb="tab-list"] {gap: 20px; border-bottom: 1px solid #333; padding-bottom: 5px;}
    .stTabs [data-baseweb="tab"] {height: 40px; border: none; background: transparent; color: #888;}
    .stTabs [aria-selected="true"] {color: #FFF !important; border-bottom: 2px solid #FFF;}
    
    /* Chat: Invisible Containers, Professional Typography */
    .stChatMessage {background-color: transparent; border: none;}
    [data-testid="stChatMessageAvatarUser"], [data-testid="stChatMessageAvatarAssistant"] {display: none;}
    
    /* Buttons: Subtle Borders */
    .stButton button {border: 1px solid #444; background-color: #2B2B2B; color: #CCC; border-radius: 6px;}
    .stButton button:hover {border-color: #FFF; color: #FFF;}
</style>
""", unsafe_allow_html=True)

# State Management Initialization
if "user" not in st.session_state: st.session_state.user = None
if "active_workspace_id" not in st.session_state: st.session_state.active_workspace_id = None
if "chat_session" not in st.session_state: st.session_state.chat_session = None

# ==============================================================================
# 2. BACKEND INFRASTRUCTURE (DATABASE)
# ==============================================================================
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try: supabase = init_supabase()
except: st.error("⚠️ SYSTEM ERROR: Database Credentials Missing.")

class DB:
    """Database Access Layer - Handles all SQL operations safely."""
    
    @staticmethod
    def get_workspaces(user_id):
        try:
            res = supabase.table("workspaces").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
            return pd.DataFrame(res.data)
        except: return pd.DataFrame()

    @staticmethod
    def create_workspace(user_id, title):
        try:
            res = supabase.table("workspaces").insert({"user_id": user_id, "title": title}).execute()
            return res.data[0]['id']
        except: return None

    @staticmethod
    def get_tasks(workspace_id):
        try:
            res = supabase.table("tasks").select("*").eq("workspace_id", workspace_id).order("id").execute()
            df = pd.DataFrame(res.data)
            if not df.empty:
                # Sanitization pipeline for UI components
                df["id"] = pd.to_numeric(df["id"]).astype(int)
                df["due_date"] = pd.to_datetime(df["due_date"], errors='coerce').dt.date
                df = df.dropna(subset=['due_date'])
            return df
        except: return pd.DataFrame()

    @staticmethod
    def create_task(user_id, workspace_id, title, est, due):
        try:
            supabase.table("tasks").insert({
                "user_id": user_id, "workspace_id": workspace_id,
                "title": title, "est_minutes": est, "due_date": due
            }).execute()
            return True
        except: return False

    @staticmethod
    def update_task(tid, updates):
        try: supabase.table("tasks").update(updates).eq("id", tid).execute()
        except: pass

    @staticmethod
    def delete_task(tid):
        try: supabase.table("tasks").delete().eq("id", tid).execute()
        except: pass

    @staticmethod
    def save_doc(user_id, workspace_id, filename, content):
        try:
            supabase.table("documents").insert({
                "user_id": user_id, "workspace_id": workspace_id,
                "filename": filename, "content": content
            }).execute()
        except: pass

    @staticmethod
    def get_docs(workspace_id):
        try:
            res = supabase.table("documents").select("id, filename, content").eq("workspace_id", workspace_id).execute()
            return res.data
        except: return []

    @staticmethod
    def delete_doc(doc_id):
        try: supabase.table("documents").delete().eq("id", doc_id).execute()
        except: pass

    @staticmethod
    def save_chat(user_id, workspace_id, role, content, image_data=None):
        try:
            data = {"user_id": user_id, "workspace_id": workspace_id, "role": role, "content": content}
            if image_data: data["image_data"] = image_data
            supabase.table("chat_history").insert(data).execute()
        except: pass

    @staticmethod
    def get_chat(workspace_id):
        try:
            res = supabase.table("chat_history").select("*").eq("workspace_id", workspace_id).order("created_at").execute()
            return res.data
        except: return []

# ==============================================================================
# 3. UTILITIES & I/O
# ==============================================================================
def image_to_base64(image):
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

def base64_to_image(base64_str):
    try: return Image.open(BytesIO(base64.b64decode(base64_str)))
    except: return None

def extract_pdf(file):
    try:
        reader = PdfReader(file)
        return "".join([p.extract_text() for p in reader.pages])
    except: return None

# ==============================================================================
# 4. INTELLIGENCE LAYER (GEMINI AGENT)
# ==============================================================================
if "GOOGLE_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

    # Tool Definition
    def add_task_tool(task_title: str, duration_minutes: int, due_date: str):
        """Adds a task. Duration must be INT. Date YYYY-MM-DD."""
        if not st.session_state.active_workspace_id: return "Error: No workspace selected."
        try:
            if not isinstance(duration_minutes, int): duration_minutes = 60
            DB.create_task(st.session_state.user.id, st.session_state.active_workspace_id, task_title, duration_minutes, due_date)
            return f"✅ Scheduled: '{task_title}' on {due_date}"
        except Exception as e: return f"❌ Error: {e}"

    model = genai.GenerativeModel('gemini-2.0-flash', tools=[add_task_tool])
    
    if "chat_session" not in st.session_state or st.session_state.chat_session is None:
        st.session_state.chat_session = model.start_chat(enable_automatic_function_calling=True)

def generate_mindmap_code(topic, context):
    try:
        prompt = f"Context: {context[:5000]}\nGenerate Graphviz DOT code for a mindmap on '{topic}'. Only output the DOT code inside ```dot``` blocks."
        response = model.generate_content(prompt)
        match = re.search(r'```dot\n(.*?)\n```', response.text, re.DOTALL)
        return match.group(1) if match else None
    except: return None

def ask_agent(user_msg, context, image_data=None):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        prompt_parts = [
            f"SYSTEM: You are DeskBot. Date: {today}. If user gives a time (e.g. 7pm), include it in the Title.",
            f"CONTEXT:\n{context}", f"USER: {user_msg}"
        ]
        if image_data: prompt_parts.append(image_data)
        response = st.session_state.chat_session.send_message(prompt_parts)
        return response.text
    except Exception as e: return f"AI Error: {e}"

# ==============================================================================
# 5. UI COMPONENTS
# ==============================================================================
def auth_screen():
    c1, c2, c3 = st.columns([1,2,1])
    with c2:
        st.title("⚡ DeskBot // Access")
        tab1, tab2 = st.tabs(["Login", "Register"])
        with tab1:
            with st.form("login"):
                e = st.text_input("Email"); p = st.text_input("Password", type="password")
                if st.form_submit_button("Enter System", use_container_width=True):
                    try:
                        res = supabase.auth.sign_in_with_password({"email":e,"password":p})
                        st.session_state.user = res.user; st.rerun()
                    except Exception as err: st.error(f"Error: {err}")
        with tab2:
            with st.form("signup"):
                e = st.text_input("Email"); p = st.text_input("Pass (min 6)", type="password")
                if st.form_submit_button("Create Identity", use_container_width=True):
                    try:
                        res = supabase.auth.sign_up({"email":e,"password":p})
                        st.session_state.user = res.user; st.success("Created! Login now."); st.rerun()
                    except Exception as err: st.error(f"Error: {err}")

def workspace_interface():
    user = st.session_state.user
    
    # --- SIDEBAR NAVIGATION ---
    with st.sidebar:
        st.caption("WORKSPACE")
        
        # 1. Workspace Manager
        if st.button("➕ New Workspace", use_container_width=True, type="primary"):
            st.session_state.show_create_modal = True
            
        if st.session_state.get("show_create_modal"):
            with st.form("new_ws"):
                title = st.text_input("Name")
                if st.form_submit_button("Create"):
                    if title:
                        new_id = DB.create_workspace(user.id, title)
                        st.session_state.active_workspace_id = new_id
                        st.session_state.show_create_modal = False
                        st.rerun()

        # 2. Selector
        workspaces = DB.get_workspaces(user.id)
        selected_ws_id = None
        selected_ws_title = "General"
        
        if not workspaces.empty:
            options = workspaces['title'].tolist()
            ids = workspaces['id'].tolist()
            
            # Logic to maintain persistence
            curr_id = st.session_state.active_workspace_id
            def_idx = ids.index(curr_id) if curr_id in ids else 0
            
            choice = st.selectbox("Active Notebook", options, index=def_idx, label_visibility="collapsed")
            
            idx = options.index(choice)
            selected_ws_id = ids[idx]
            selected_ws_title = choice
            st.session_state.active_workspace_id = selected_ws_id
        
        st.divider()
        st.caption("SOURCES")
        
        if selected_ws_id:
            docs = DB.get_docs(selected_ws_id)
            if docs:
                for d in docs:
                    c1, c2 = st.columns([5,1])
                    c1.caption(f"📄 {d['filename'][:15]}...")
                    if c2.button("×", key=f"del_{d['id']}"): DB.delete_doc(d['id']); st.rerun()
            else: st.caption("No sources.")
            
            with st.expander("Add Source (+)", expanded=False):
                up_file = st.file_uploader("Upload", type=["pdf", "png", "jpg"], label_visibility="collapsed")
                if up_file:
                    if up_file.type == "application/pdf":
                        if st.button("Index PDF", use_container_width=True):
                            txt = extract_pdf(up_file)
                            if txt: DB.save_doc(user.id, selected_ws_id, up_file.name, txt); st.success("Indexed"); st.rerun()
                    else: st.image(Image.open(up_file), width=150)
        else: st.caption("Select workspace first.")

        st.divider()
        if st.button("Log Out"): 
            supabase.auth.sign_out()
            st.session_state.user = None
            st.session_state.active_workspace_id = None
            st.rerun()

    # --- MAIN CANVAS (Split View) ---
    col_chat, col_studio = st.columns([1, 1.4], gap="medium")

    # === LEFT: CHAT ===
    with col_chat:
        st.subheader(f"💬 {selected_ws_title}")
        chat_container = st.container(height=650)
        
        with chat_container:
            if selected_ws_id:
                history = DB.get_chat(selected_ws_id)
                if not history: st.caption("System online.")
                for msg in history:
                    with st.chat_message(msg["role"]):
                        if msg.get("image_data"):
                            try: st.image(base64_to_image(msg["image_data"]), width=300)
                            except: pass
                        st.write(msg["content"])
            else: st.info("Please select or create a workspace.")

        if p := st.chat_input("Command..."):
            if not selected_ws_id: st.error("Select a workspace."); st.stop()
            
            img_to_send = None; img_base64 = None
            if 'up_file' in locals() and up_file and up_file.type != "application/pdf":
                img_to_send = Image.open(up_file); img_base64 = image_to_base64(img_to_send)

            DB.save_chat(user.id, selected_ws_id, "user", p, img_base64)
            
            # Context Building
            tasks_df = DB.get_tasks(selected_ws_id)
            ctx = tasks_df.to_string() if not tasks_df.empty else "No tasks."
            docs = DB.get_docs(selected_ws_id)
            for d in docs: ctx += f"\nFILE: {d['filename']}\nCONTENT: {d['content'][:15000]}"

            with st.spinner("Processing..."):
                reply = ask_agent(p, ctx, img_to_send)
            
            DB.save_chat(user.id, selected_ws_id, "assistant", reply)
            st.rerun()

    # === RIGHT: STUDIO ===
    with col_studio:
        st.subheader("🛠️ Studio")
        tab_cal, tab_mind, tab_sum = st.tabs(["Plan & Calendar", "Mind Map", "Summary"])

        # 1. Calendar Tab
        with tab_cal:
            if selected_ws_id:
                tasks_df = DB.get_tasks(selected_ws_id)
                if not tasks_df.empty:
                    cal_events = []
                    for i, row in tasks_df.iterrows():
                        if pd.notnull(row['due_date']):
                            color = "#2ecc71" if row['status']=='done' else "#3498db"
                            cal_events.append({"title": row['title'], "start": str(row['due_date']), "allDay": True, "backgroundColor": color})
                    
                    calendar(events=cal_events, options={"headerToolbar": {"left": "prev,next", "center": "title", "right": "dayGridMonth"}, "initialView": "dayGridMonth", "height": 450})
                    
                    st.divider()
                    edited = st.data_editor(
                        tasks_df, key="editor", hide_index=True, use_container_width=True,
                        column_config={
                            "id": None, "user_id": None, "workspace_id": None, "created_at": None, "est_minutes": None,
                            "title": st.column_config.TextColumn("Task"),
                            "due_date": st.column_config.DateColumn("Due"),
                            "status": st.column_config.SelectboxColumn("Status", options=["todo", "done"])
                        }
                    )
                    # Auto-Save
                    if st.session_state["editor"]["edited_rows"]:
                        for idx, updates in st.session_state["editor"]["edited_rows"].items():
                            DB.update_task(tasks_df.iloc[idx]["id"], updates)
                        st.rerun()
                    if st.session_state["editor"]["deleted_rows"]:
                        for idx in st.session_state["editor"]["deleted_rows"]:
                            DB.delete_task(tasks_df.iloc[idx]["id"])
                        st.rerun()
                else: st.info("No tasks in this workspace.")
            else: st.caption("Select a workspace.")

        # 2. Mind Map Tab
        with tab_mind:
            if st.button("Generate Graph"):
                if selected_ws_id:
                    with st.spinner("Analyzing..."):
                        doc_ctx = ""
                        docs = DB.get_docs(selected_ws_id)
                        for d in docs: doc_ctx += d['content'][:10000]
                        if doc_ctx:
                            dot = generate_mindmap_code(selected_ws_title, doc_ctx)
                            if dot: st.graphviz_chart(dot)
                            else: st.error("Failed to visualize.")
                        else: st.warning("Upload PDF source first.")
                else: st.warning("Select Workspace.")

        # 3. Summary Tab
        with tab_sum:
            if st.button("Synthesize Data"):
                if selected_ws_id:
                    with st.spinner("Computing..."):
                        doc_ctx = ""
                        docs = DB.get_docs(selected_ws_id)
                        for d in docs: doc_ctx += d['content'][:15000]
                        if doc_ctx:
                            summary = ask_agent("Provide an executive summary of attached documents.", doc_ctx)
                            st.markdown(summary)
                        else: st.warning("No data sources.")

# ==============================================================================
# 6. RUN
# ==============================================================================
if st.session_state.user: workspace_interface()
else: auth_screen()
