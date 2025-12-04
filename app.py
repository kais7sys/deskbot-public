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
            res = supabase.table("tasks").select("*").eq("user_id", user_id).order("id").execute()
            df = pd.DataFrame(res.data)
            if not df.empty:
                df["id"] = df["id"].astype(int)
                df["est_minutes"] = df["est_minutes"].astype(int)
                df["due_date"] = pd.to_datetime(df["due_date"]).dt.date
                df["status"] = df["status"].astype(str)
            return df
        except: return pd.DataFrame()

    def add_task(title, est, due):
        supabase.table("tasks").insert({
            "user_id": user_id, "title": title, "est_minutes": est, "due_date": str(due)
        }).execute()

    def update_task_in_db(tid, updates):
        supabase.table("tasks").update(updates).eq("id", tid).execute()

    def save_document(filename, content, task_id):
        data = {"user_id": user_id, "filename": filename, "content": content}
        if task_id: data["task_id"] = int(task_id)
        supabase.table("documents").insert(data).execute()

    def get_task_documents(task_id):
        try:
            res = supabase.table("documents").select("id, filename, content").eq("task_id", task_id).execute()
            return res.data 
        except: return []

    def delete_document(doc_id):
        supabase.table("documents").delete().eq("id", doc_id).execute()

    def extract_pdf(file):
        try:
            reader = PdfReader(file)
            return "".join([p.extract_text() for p in reader.pages])
        except: return None

    # --- CHAT & HISTORY (UPDATED WITH IMAGES) ---
    def save_chat_message(role, content, task_id, image_data=None):
        data = {"user_id": user_id, "role": role, "content": content}
        if task_id: data["task_id"] = int(task_id)
        if image_data: data["image_data"] = image_data  # <--- SAVE IMAGE CODE
        
        supabase.table("chat_history").insert(data).execute()

    def get_chat_history(task_id):
        try:
            if task_id:
                res = supabase.table("chat_history").select("*").eq("task_id", task_id).eq("user_id", user_id).order("created_at").execute()
            else:
                res = supabase.table("chat_history").select("*").is_("task_id", "null").eq("user_id", user_id).order("created_at").execute()
            return res.data
        except: return []

    def ask_gemini(msg, context, image_data=None):
        try:
            content_package = []
            sys_prompt = f"You are DeskBot.\nCONTEXT:\n{context}\nUSER QUESTION: {msg}"
            content_package.append(sys_prompt)
            if image_data: content_package.append(image_data)
            return model.generate_content(content_package).text
        except Exception as e: return f"AI Error: {e}"

    # --- UI LAYOUT ---
    tasks_df = get_tasks()

    with st.sidebar:
        st.caption(f"User: {email}")
        if st.button("Log Out"): logout()
        st.divider()

        st.header("🎯 Notebooks")
        selected_task_id = None
        selected_task_title = "General"
        
        if not tasks_df.empty:
            task_options = {f"{row['id']} - {row['title']}": row['id'] for i, row in tasks_df.iterrows()}
            options_list = ["No Focus (General)"] + list(task_options.keys())
            choice = st.selectbox("Select Notebook", options_list)
            if choice != "No Focus (General)":
                selected_task_id = task_options[choice]
                selected_task_title = choice.split(" - ")[1]
        
        st.divider()
        if selected_task_id:
            st.subheader(f"📂 Files: {selected_task_title}")
            task_docs = get_task_documents(selected_task_id)
            if task_docs:
                for d in task_docs:
                    c1,c2 = st.columns([4,1])
                    c1.text(f"📄 {d['filename']}")
                    if c2.button("X", key=f"d{d['id']}"): delete_document(d['id']); st.rerun()
            
            # FILE UPLOADER
            up_file = st.file_uploader("Upload File", type=["pdf", "png", "jpg", "jpeg"])
            
            # If PDF, show Save Button
            if up_file and up_file.type == "application/pdf":
                if st.button("Save PDF"):
                    txt = extract_pdf(up_file)
                    if txt: save_document(up_file.name, txt, selected_task_id); st.success("Saved!"); time.sleep(1); st.rerun()

    st.title(f"📓 {selected_task_title}")
    
    # TABS
    tab1, tab2, tab3 = st.tabs(["📝 Grid", "📅 Calendar", "💬 Chat"])

    with tab1:
        with st.expander("➕ Add Task"):
            with st.form("add"):
                c1,c2,c3 = st.columns([3,1,1])
                t=c1.text_input("Title"); e=c2.number_input("Min",15,120,60)
                d_in = c3.date_input("Due")
                if st.form_submit_button("Add"): 
                    add_task(t,e,d_in); st.rerun()
        
        if not tasks_df.empty:
            edited = st.data_editor(tasks_df, key="editor", hide_index=True,
                column_config={"id":st.column_config.NumberColumn(disabled=True), "user_id":None})
            if st.session_state["editor"]["edited_rows"]:
                for idx, updates in st.session_state["editor"]["edited_rows"].items():
                    update_task_in_db(tasks_df.iloc[idx]["id"], updates)
                st.toast("Updated!")

    with tab2:
        if not tasks_df.empty:
            cal_events = []
            for i, row in tasks_df.iterrows():
                color = "#28a745" if row['status'] == 'done' else "#3788d8"
                cal_events.append({
                    "title": f"{row['title']} ({row['est_minutes']}m)",
                    "start": str(row['due_date']), "end": str(row['due_date']),
                    "backgroundColor": color, "borderColor": color
                })
            calendar(events=cal_events, options={"headerToolbar": {"left": "today prev,next", "center": "title", "right": "dayGridMonth"}, "initialView": "dayGridMonth"})
        else: st.info("Add tasks to see Calendar.")

    with tab3:
        # 1. RENDER HISTORY (With Images!)
        history = get_chat_history(selected_task_id)
        for msg in history:
            with st.chat_message(msg["role"]):
                # CHECK IF MESSAGE HAS AN IMAGE
                if msg.get("image_data"):
                    try:
                        # Decode and display the saved image
                        img = base64_to_image(msg["image_data"])
                        st.image(img, width=300)
                    except:
                        st.error("Error loading image")
                
                # Display Text
                st.markdown(msg["content"])
        
        # 2. CHAT INPUT
        if p := st.chat_input("Chat..."):
            # CHECK FOR IMAGE UPLOAD IN SIDEBAR
            img_to_send = None
            img_base64 = None
            
            # If user uploaded an image in sidebar, we include it in this message
            if up_file and up_file.type != "application/pdf":
                img_to_send = Image.open(up_file)
                # Convert to base64 to save in DB
                img_base64 = image_to_base64(img_to_send)

            # A. Display User Message Immediately
            with st.chat_message("user"):
                if img_to_send: st.image(img_to_send, width=300)
                st.markdown(p)
            
            # B. Save User Message (Text + Image Code)
            save_chat_message("user", p, selected_task_id, img_base64)
            
            # C. Build Context
            ctx = ""
            if selected_task_id:
                row = tasks_df[tasks_df['id']==selected_task_id].iloc[0]
                ctx += f"TASK: {row['title']}\n"
                docs = get_task_documents(selected_task_id)
                for d in docs: ctx += f"FILE: {d['filename']}\nCONTENT: {d['content'][:10000]}\n"
            else: ctx = tasks_df.to_string()

            # D. Ask AI
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    reply = ask_gemini(p, ctx, img_to_send)
                    st.markdown(reply)
            
            # E. Save AI Response
            save_chat_message("assistant", reply, selected_task_id)
            
            # F. Clear the uploader by rerunning (optional, keeps UI clean)
            if img_to_send:
                time.sleep(1)
                st.rerun()

if st.session_state.user: main_app()
else: login_page()
