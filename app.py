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

    # --- DB: TASKS ---
    def get_tasks():
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

    # --- DB: DOCUMENTS ---
    def save_document(filename, content, task_id):
        data = {"username": current_user, "filename": filename, "content": content}
        if task_id: data["task_id"] = int(task_id)
        supabase.table("documents").insert(data).execute()

    def get_task_documents(task_id):
        res = supabase.table("documents").select("id, filename, content").eq("task_id", task_id).execute()
        return res.data 

    def delete_document(doc_id):
        supabase.table("documents").delete().eq("id", doc_id).execute()

    def extract_pdf(file):
        try:
            reader = PdfReader(file)
            return "".join([p.extract_text() for p in reader.pages])
        except: return None

    # --- DB: CHAT HISTORY (NEW!) ---
    def save_chat_message(role, content, task_id):
        data = {
            "username": current_user,
            "role": role,
            "content": content
        }
        # If we are in a specific notebook, link it. Otherwise leave task_id null (General Chat)
        if task_id:
            data["task_id"] = int(task_id)
        
        supabase.table("chat_history").insert(data).execute()

    def get_chat_history(task_id):
        # Fetch messages for THIS specific task
        if task_id:
            res = supabase.table("chat_history").select("*").eq("task_id", task_id).eq("username", current_user).order("created_at").execute()
        else:
            # Fetch "General" messages (where task_id is NULL)
            res = supabase.table("chat_history").select("*").is_("task_id", "null").eq("username", current_user).order("created_at").execute()
        
        return res.data # Returns list of dicts

    # --- AI ---
    def ask_gemini(msg, context_text):
        try:
            sys = f"You are DeskBot. Analyze the following context carefully.\n\nCONTEXT:\n{context_text}\n\nUSER QUESTION: {msg}"
            return model.generate_content(sys).text
        except Exception as e: return f"AI Error: {e}"

    # --- UI LAYOUT ---
    
    # 1. LOAD TASKS
    tasks_df = get_tasks()
    
    with st.sidebar:
        st.header(f"👤 {current_user}")
        if st.button("Log Out"): logout()
        st.divider()

        # --- FOCUS MODE SELECTOR ---
        st.header("🎯 Notebooks")
        st.caption("Select a Task to switch Chat History:")
        
        selected_task_id = None
        selected_task_title = "General"
        
        if not tasks_df.empty:
            task_options = {f"{row['id']} - {row['title']}": row['id'] for i, row in tasks_df.iterrows()}
            options_list = ["No Focus (General)"] + list(task_options.keys())
            
            # Using session_state to track selection helps prevent reset glitches
            choice = st.selectbox("Active Notebook", options_list)
            
            if choice != "No Focus (General)":
                selected_task_id = task_options[choice]
                selected_task_title = choice.split(" - ")[1]
        
        st.divider()
        
        # --- FILES ---
        if selected_task_id:
            st.subheader(f"📂 Files: {selected_task_title}")
            task_docs = get_task_documents(selected_task_id)
            if task_docs:
                for d in task_docs:
                    c1, c2 = st.columns([4, 1])
                    c1.text(f"📄 {d['filename']}")
                    if c2.button("X", key=f"del_{d['id']}"):
                        delete_document(d['id']); st.rerun()
            
            up_file = st.file_uploader("Attach PDF", type=["pdf"])
            if up_file and st.button("Upload"):
                with st.spinner("Processing..."):
                    txt = extract_pdf(up_file)
                    if txt:
                        save_document(up_file.name, txt, selected_task_id)
                        st.success("Saved!")
                        time.sleep(1); st.rerun()

    # --- MAIN PAGE ---
    st.title(f"📓 {selected_task_title}")

    tab1, tab2 = st.tabs(["📝 Task Grid", "💬 Notebook Chat"])

    with tab1:
        with st.expander("➕ Add New Notebook/Task"):
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
        # 1. LOAD HISTORY FOR THIS SPECIFIC NOTEBOOK
        # We fetch from DB every time the app reruns to ensure we see the right chat
        history = get_chat_history(selected_task_id)
        
        # Display History
        for msg in history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
        
        # 2. CHAT INPUT
        if p := st.chat_input(f"Chat about {selected_task_title}..."):
            # A. Display User Message Immediately
            with st.chat_message("user"): st.markdown(p)
            
            # B. Save User Message to DB
            save_chat_message("user", p, selected_task_id)

            # C. Build Context
            chat_context = ""
            if selected_task_id:
                current_task_row = tasks_df[tasks_df['id'] == selected_task_id].iloc[0]
                chat_context += f"TASK: {current_task_row['title']} (Due: {current_task_row['due_date']})\n"
                docs = get_task_documents(selected_task_id)
                if docs:
                    for d in docs: chat_context += f"FILE: {d['filename']}\nCONTENT: {d['content'][:20000]}\n\n"
            else:
                chat_context = "GENERAL CONTEXT. User Tasks:\n" + tasks_df.to_string()

            # D. Generate AI Response
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    reply = ask_gemini(p, chat_context)
                    st.markdown(reply)
            
            # E. Save AI Response to DB
            save_chat_message("assistant", reply, selected_task_id)
            
            # F. Optional: Rerun to make sure everything is clean
            # st.rerun() 

if st.session_state.authenticated: main_app()
else: login_page()
