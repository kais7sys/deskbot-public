import streamlit as st
import pandas as pd
import google.generativeai as genai
from PyPDF2 import PdfReader
from PIL import Image
import time
from supabase import create_client, Client

# --- 1. CONFIG ---
st.set_page_config(page_title="DeskBot: Notebooks", page_icon="📓", layout="wide")

if "authenticated" not in st.session_state: st.session_state.authenticated = False
if "username" not in st.session_state: st.session_state.username = None

# --- 2. DATABASE ---
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try: supabase = init_supabase()
except: st.error("⚠️ Supabase Keys missing!")

# --- 3. AUTH ---
def check_login(u, p):
    return (u == "kais" and p == "deskbot123") or (u == "admin" and p == "admin")

def login_page():
    st.title("☁️ DeskBot Login")
    with st.form("login"):
        u = st.text_input("User"); p = st.text_input("Pass", type="password")
        if st.form_submit_button("Log In"):
            if check_login(u, p):
                st.session_state.authenticated = True
                st.session_state.username = u
                st.rerun()
            else: st.error("Invalid")

def logout():
    st.session_state.authenticated = False; st.rerun()

# --- 4. MAIN APP ---
def main_app():
    current_user = st.session_state.username
    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        model = genai.GenerativeModel('gemini-2.0-flash')

    # --- DB FUNCTIONS ---
    def get_tasks():
        # Get all tasks for user
        res = supabase.table("tasks").select("*").eq("username", current_user).order("id").execute()
        df = pd.DataFrame(res.data)
        if not df.empty:
            df["id"] = df["id"].astype(int)
            df["est_minutes"] = df["est_minutes"].astype(int)
            df["due_date"] = pd.to_datetime(df["due_date"]).dt.date
            df["status"] = df["status"].astype(str)
        return df

    def add_task(title, est, due):
        supabase.table("tasks").insert({
            "username": current_user, "title": title, "est_minutes": est, "due_date": str(due)
        }).execute()

    def update_task_in_db(tid, updates):
        supabase.table("tasks").update(updates).eq("id", tid).execute()

    def delete_task_in_db(tid):
        supabase.table("tasks").delete().eq("id", tid).execute()

    # --- DOCS (LINKED TO TASKS) ---
    def save_document(filename, content, task_id):
        # Save file linked to a specific task
        data = {"username": current_user, "filename": filename, "content": content}
        if task_id: data["task_id"] = int(task_id)
        supabase.table("documents").insert(data).execute()

    def get_task_documents(task_id):
        # Fetch docs ONLY for this task
        res = supabase.table("documents").select("id, filename, content").eq("task_id", task_id).execute()
        return res.data 

    def delete_document(doc_id):
        supabase.table("documents").delete().eq("id", doc_id).execute()

    def extract_pdf(file):
        try:
            reader = PdfReader(file)
            return "".join([p.extract_text() for p in reader.pages])
        except: return None

    # --- AI ---
    def ask_gemini(msg, context_text):
        try:
            sys = f"You are DeskBot. Analyze the following context carefully.\n\nCONTEXT:\n{context_text}\n\nUSER QUESTION: {msg}"
            return model.generate_content(sys).text
        except Exception as e: return f"AI Error: {e}"

    # --- UI LAYOUT ---
    
    # 1. LOAD TASKS FIRST (We need them for the dropdown)
    tasks_df = get_tasks()
    
    with st.sidebar:
        st.header(f"👤 {current_user}")
        if st.button("Log Out"): logout()
        st.divider()

        # --- FOCUS MODE SELECTOR ---
        st.header("🎯 Focus Mode")
        st.caption("Select a Task/Notebook to work on:")
        
        selected_task_id = None
        selected_task_title = "General"
        
        if not tasks_df.empty:
            # Create a dictionary { "ID - Title": ID }
            task_options = {f"{row['id']} - {row['title']}": row['id'] for i, row in tasks_df.iterrows()}
            # Add a "General / All" option
            options_list = ["No Focus (General)"] + list(task_options.keys())
            
            choice = st.selectbox("Active Notebook", options_list)
            
            if choice != "No Focus (General)":
                selected_task_id = task_options[choice]
                selected_task_title = choice.split(" - ")[1]
        
        st.divider()
        
        # --- TASK SPECIFIC UPLOAD ---
        if selected_task_id:
            st.subheader(f"📂 Files for '{selected_task_title}'")
            
            # Show existing files for THIS task
            task_docs = get_task_documents(selected_task_id)
            if task_docs:
                for d in task_docs:
                    c1, c2 = st.columns([4, 1])
                    c1.text(f"📄 {d['filename']}")
                    if c2.button("X", key=f"del_{d['id']}"):
                        delete_document(d['id']); st.rerun()
            else:
                st.caption("No files yet.")
            
            # Upload New
            up_file = st.file_uploader("Add PDF to this Task", type=["pdf"])
            if up_file and st.button("Attach File"):
                with st.spinner("Processing..."):
                    txt = extract_pdf(up_file)
                    if txt:
                        save_document(up_file.name, txt, selected_task_id)
                        st.success("Attached!")
                        time.sleep(1)
                        st.rerun()
        else:
            st.info("Select a Task above to manage its specific files.")

    # --- MAIN PAGE ---
    st.title(f"📓 DeskBot: {selected_task_title}")

    tab1, tab2 = st.tabs(["📝 Task Grid", "💬 Notebook Chat"])

    with tab1:
        # TASK GRID
        with st.expander("➕ Add New Task"):
            with st.form("add"):
                c1,c2,c3,c4 = st.columns([3,1,1,1])
                t=c1.text_input("Title"); e=c2.number_input("Min",15,120,60); d=c3.date_input("Due")
                if c4.form_submit_button("Add"): add_task(t,e,d); st.rerun()
        
        if not tasks_df.empty:
            edited = st.data_editor(tasks_df, key="editor", num_rows="dynamic", hide_index=True,
                column_config={"id":st.column_config.NumberColumn(disabled=True),
                               "status":st.column_config.SelectboxColumn(options=["todo","done"])})
            
            if st.session_state["editor"]["edited_rows"]:
                for idx, updates in st.session_state["editor"]["edited_rows"].items():
                    update_task_in_db(tasks_df.iloc[idx]["id"], updates)
                st.toast("Updated!")
            
            if st.session_state["editor"]["deleted_rows"]:
                for idx in st.session_state["editor"]["deleted_rows"]:
                    delete_task_in_db(tasks_df.iloc[idx]["id"])
                st.rerun()

    with tab2:
        # CONTEXT AWARE CHAT
        # If a task is selected, we gather ALL its text content
        chat_context = ""
        
        if selected_task_id:
            st.success(f"🟢 Context Active: Chatting specifically about **{selected_task_title}**")
            
            # 1. Add Task Details to Context
            # Find the specific task row
            current_task_row = tasks_df[tasks_df['id'] == selected_task_id].iloc[0]
            chat_context += f"CURRENT TASK DETAILS:\nTitle: {current_task_row['title']}\nDue: {current_task_row['due_date']}\n\n"
            
            # 2. Add Document Content to Context
            docs = get_task_documents(selected_task_id)
            if docs:
                chat_context += "ATTACHED DOCUMENTS:\n"
                for d in docs:
                    chat_context += f"--- START OF {d['filename']} ---\n{d['content'][:30000]}\n--- END FILE ---\n\n" # Limit text for speed
            else:
                chat_context += "(No documents attached to this task yet.)"
        else:
            st.info("⚪ General Mode: Chatting about all tasks.")
            chat_context = "ALL USER TASKS:\n" + tasks_df.to_string()

        # Chat UI
        if "messages" not in st.session_state: st.session_state.messages = []
        for m in st.session_state.messages: 
            with st.chat_message(m["role"]): st.markdown(m["content"])
            
        if p := st.chat_input("Ask about this task/notebook..."):
            with st.chat_message("user"): st.markdown(p)
            st.session_state.messages.append({"role":"user", "content":p})
            
            with st.chat_message("assistant"):
                with st.spinner("Analyzing Notebook..."):
                    reply = ask_gemini(p, chat_context)
                    st.markdown(reply)
            st.session_state.messages.append({"role":"assistant", "content":reply})

if st.session_state.authenticated: main_app()
else: login_page()
