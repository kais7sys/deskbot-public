import streamlit as st
import sqlite3
import pandas as pd
import google.generativeai as genai
from PyPDF2 import PdfReader

# --- 1. SETUP & CONFIG ---
st.set_page_config(page_title="DeskBot: Second Brain", page_icon="🧠", layout="wide")

# Connect to Google Gemini
if "GOOGLE_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    model = genai.GenerativeModel('gemini-2.0-flash')
else:
    st.error("⚠️ Google API Key missing! Please add it to Streamlit Secrets.")

# Database Setup
def init_db():
    conn = sqlite3.connect("deskbot.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    est_minutes INTEGER,
                    due_date TEXT,
                    status TEXT DEFAULT 'todo'
                )''')
    conn.commit()
    conn.close()

init_db()

# --- 2. HELPER FUNCTIONS ---
def get_tasks():
    conn = sqlite3.connect("deskbot.db")
    try:
        df = pd.read_sql("SELECT * FROM tasks", conn)
    except:
        df = pd.DataFrame()
    conn.close()
    return df

def add_task(title, est, due):
    conn = sqlite3.connect("deskbot.db")
    c = conn.cursor()
    c.execute("INSERT INTO tasks (title, est_minutes, due_date) VALUES (?, ?, ?)", 
              (title, est, str(due)))
    conn.commit()
    conn.close()

def delete_task(task_id):
    conn = sqlite3.connect("deskbot.db")
    c = conn.cursor()
    c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()

def extract_text_from_pdf(uploaded_file):
    """Reads a PDF file and returns the text."""
    try:
        pdf_reader = PdfReader(uploaded_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() or ""
        return text
    except Exception as e:
        return f"Error reading PDF: {e}"

# --- 3. THE AI BRAIN ---
def ask_gemini(user_message, task_list_text, document_context=""):
    try:
        # We now give the AI THREE things:
        # 1. The user's tasks
        # 2. The user's uploaded document (Syllabus, Notes, etc.)
        # 3. The user's question
        
        system_instruction = f"""
        You are DeskBot, a smart productivity assistant and study companion.
        
        CONTEXT 1: User's Current Tasks
        {task_list_text}
        
        CONTEXT 2: Uploaded Document Content
        {document_context}
        
        USER MESSAGE: "{user_message}"
        
        INSTRUCTIONS:
        - If the user asks about the document, answer based on Context 2.
        - If the user asks to plan their day, use Context 1 (Tasks).
        - If the user wants to study, use the Document to create a plan or quiz.
        """
        response = model.generate_content(system_instruction)
        return response.text
    except Exception as e:
        return f"⚠️ AI Error: {e}"

# --- 4. THE UI ---
st.title("🧠 DeskBot: Second Brain")
st.caption("Chat with your Tasks AND your Documents (PDF)")

# SIDEBAR: File Upload
with st.sidebar:
    st.header("📂 Knowledge Base")
    uploaded_file = st.file_uploader("Upload a PDF (Syllabus, Notes)", type="pdf")
    
    doc_text = ""
    if uploaded_file is not None:
        with st.spinner("Reading file..."):
            doc_text = extract_text_from_pdf(uploaded_file)
        st.success(f"Loaded {len(doc_text)} characters from {uploaded_file.name}")
        st.info("I can now answer questions about this file!")

col1, col2 = st.columns([1, 2])

# LEFT: Task Management
with col1:
    st.subheader("📝 Quick Add")
    with st.form("task_form"):
        task_title = st.text_input("Task Name")
        est = st.number_input("Minutes", 15, 120, 60, step=15)
        due = st.date_input("Due")
        if st.form_submit_button("Add Task"):
            add_task(task_title, est, due)
            st.success("Added!")
            st.rerun()

    st.divider()
    
    tasks = get_tasks()
    if not tasks.empty:
        st.dataframe(tasks[['title', 'due_date']], hide_index=True, use_container_width=True)
        task_context = tasks[['title', 'est_minutes', 'due_date']].to_string(index=False)
        
        task_del = st.selectbox("Remove:", tasks['id'].astype(str) + " - " + tasks['title'])
        if st.button("Delete"):
            delete_task(task_del.split(" - ")[0])
            st.rerun()
    else:
        task_context = "No tasks currently."
        st.info("No tasks yet.")

# RIGHT: Chat Interface
with col2:
    st.subheader("💬 Chat")
    
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Ask about your PDF or Tasks..."):
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                # Pass the Document Text to the AI
                ai_reply = ask_gemini(prompt, task_context, doc_text)
                st.markdown(ai_reply)
        st.session_state.messages.append({"role": "assistant", "content": ai_reply})

