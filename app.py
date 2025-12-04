import streamlit as st
import pandas as pd
import google.generativeai as genai
from PyPDF2 import PdfReader
from PIL import Image
import time
from supabase import create_client, Client

# --- 1. CONFIG ---
st.set_page_config(page_title="DeskBot: SaaS", page_icon="🏢", layout="wide")

if "user" not in st.session_state: st.session_state.user = None

# --- 2. DATABASE ---
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try: supabase = init_supabase()
except: st.error("⚠️ Supabase Keys missing!")

# --- 3. REAL AUTHENTICATION ---
def login_page():
    st.title("☁️ DeskBot: Welcome")
    
    tab1, tab2 = st.tabs(["Log In", "Sign Up"])

    with tab1:
        with st.form("login_form"):
            st.subheader("Welcome Back")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Log In"):
                try:
                    # SUPABASE LOGIN
                    response = supabase.auth.sign_in_with_password({"email": email, "password": password})
                    st.session_state.user = response.user
                    st.success("Logged in!")
                    time.sleep(0.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"Login failed: {e}")

    with tab2:
        with st.form("signup_form"):
            st.subheader("Create Account")
            new_email = st.text_input("Email")
            new_password = st.text_input("Password (min 6 chars)", type="password")
            if st.form_submit_button("Sign Up"):
                try:
                    # SUPABASE SIGN UP
                    response = supabase.auth.sign_up({"email": new_email, "password": new_password})
                    st.session_state.user = response.user
                    st.success("Account created! You are logged in.")
                    time.sleep(0.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"Signup failed: {e}")

def logout():
    supabase.auth.sign_out()
    st.session_state.user = None
    st.rerun()

# --- 4. MAIN APP ---
def main_app():
    # GET CURRENT USER ID (Not just name, but unique ID)
    user_id = st.session_state.user.id
    user_email = st.session_state.user.email

    if "GOOGLE_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        model = genai.GenerativeModel('gemini-2.0-flash')

    # --- DB: TASKS (FILTER BY USER_ID) ---
    def get_tasks():
        # SECURITY: Only show tasks where user_id matches
        res = supabase.table("tasks").select("*").eq("user_id", user_id).order("id").execute()
        df = pd.DataFrame(res.data)
        if not df.empty:
            df["id"] = df["id"].astype(int)
            df["est_minutes"] = df["est_minutes"].astype(int)
            df["due_date"] = pd.to_datetime(df["due_date"]).dt.date
            df["status"] = df["status"].astype(str)
        return df

    def add_task(title, est, due):
        # SECURITY: Save with user_id
        supabase.table("tasks").insert({
            "user_id": user_id, "title": title, "est_minutes": est, "due_date": str(due), "username": user_email
        }).execute()

    def update_task_in_db(tid, updates):
        supabase.table("tasks").update(updates).eq("id", tid).execute()

    def delete_task_in_db(tid):
        supabase.table("tasks").delete().eq("id", tid).execute()

    # --- DB: DOCUMENTS ---
    def save_document(filename, content, task_id):
        data = {"user_id": user_id, "filename": filename, "content": content}
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

    # --- DB: CHAT HISTORY ---
    def save_chat_message(role, content, task_id):
        data = {"user_id": user_id, "role": role, "content": content}
        if task_id: data["task_id"] = int(task_id)
        supabase.table("chat_history").insert(data).execute()

    def get_chat_history(task_id):
        if task_id:
            res = supabase.table("chat_history").select("*").eq("task_id", task_id).eq("user_id", user_id).order("created_at").execute()
        else:
            res = supabase.table("chat_history").select("*").is_("task_id", "null").eq("user_id", user_id).order("created_at").execute()
        return res.data

    # --- AI ---
    def ask_gemini(msg, context_text):
        try:
            sys = f"You are DeskBot.\nCONTEXT:\n{context_text}\nUSER: {msg}"
            return model.generate_content(sys).text
        except Exception as e: return f"AI Error: {e}"

    # --- UI LAYOUT ---
    tasks_df = get_tasks()
    
    with st.sidebar:
        st.caption(f"Logged in as: {user_email}")
        if st.button("Log Out"): logout()
        st.divider()

        # NOTEBOOKS
        st.header("🎯 Notebooks")
        selected_task_id = None
        selected_task_title = "General"
        
        if not tasks_df.empty:
            task_options = {f"{row['id']} - {row['title']}": row['id'] for i, row in tasks_df.iterrows()}
            options_list = ["No Focus (General)"] + list(task_options.keys())
            choice = st.selectbox("Active Notebook", options_list)
            
            if choice != "No Focus (General)":
                selected_task_id = task_options[choice]
                selected_task_title = choice.split(" - ")[1]
        
        st.divider()
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
                        st.success("Saved!"); time.sleep(1); st.rerun()

    st.title(f"📓 {selected_task_title}")
    tab1, tab2 = st.tabs(["📝 Task Grid", "💬 Chat"])

    with tab1:
        with st.expander("➕ Add New Notebook"):
            with st.form("add"):
                c1,c2,c3,c4 = st.columns([3,1,1,1])
                t=c1.text_input("Title"); e=c2.number_input("Min",15,120,60); d=c3.date_input("Due")
                if c4.form_submit_button("Add"): add_task(t,e,d); st.rerun()
        
        if not tasks_df.empty:
            edited = st.data_editor(tasks_df, key="editor", num_rows="dynamic", hide_index=True,
                column_config={"id":st.column_config.NumberColumn(disabled=True),
                               "user_id":st.column_config.TextColumn(disabled=True),
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
        history = get_chat_history(selected_task_id)
        for msg in history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        
        if p := st.chat_input("Chat..."):
            with st.chat_message("user"): st.markdown(p)
            save_chat_message("user", p, selected_task_id)
            
            chat_context = ""
            if selected_task_id:
                row = tasks_df[tasks_df['id'] == selected_task_id].iloc[0]
                chat_context += f"TASK: {row['title']}\n"
                docs = get_task_documents(selected_task_id)
                if docs:
                    for d in docs: chat_context += f"FILE: {d['filename']}\nCONTENT: {d['content'][:15000]}\n\n"
            else:
                chat_context = "User Tasks:\n" + tasks_df.to_string()

            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    reply = ask_gemini(p, chat_context)
                    st.markdown(reply)
            save_chat_message("assistant", reply, selected_task_id)

if st.session_state.user:
    main_app()
else:
    login_page()

