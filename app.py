import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import google.generativeai as genai

# --- 1. SETUP & CONFIG ---
st.set_page_config(page_title="DeskBot: AI Agent", page_icon="🤖", layout="wide")

# Connect to Google Gemini using your Secret Key
if "GOOGLE_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
    model = genai.GenerativeModel('gemini-pro')
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

# --- 3. THE AI BRAIN ---
def ask_gemini(user_message, task_list_text):
    """Sends user message + current tasks to Gemini to get a smart response."""
    try:
        # We give the AI context so it knows it is a Secretary
        system_instruction = f"""
        You are DeskBot, a helpful personal productivity secretary.
        
        Here are the user's current tasks:
        {task_list_text}
        
        User's message: "{user_message}"
        
        Instructions:
        1. If the user asks to add a task, confirm the details (but tell them to use the side panel for now).
        2. If the user asks for advice, prioritize their tasks based on the list above.
        3. Be concise, friendly, and proactive.
        """
        response = model.generate_content(system_instruction)
        return response.text
    except Exception as e:
        return f"⚠️ AI Error: {e}"

# --- 4. THE UI ---
st.title("🤖 DeskBot: AI Agent")
st.caption("Powered by Google Gemini 1.5 Flash")

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
    
    # Task List for Context
    tasks = get_tasks()
    if not tasks.empty:
        st.dataframe(tasks[['title', 'due_date']], hide_index=True, use_container_width=True)
        # Prepare text for AI
        task_context = tasks[['title', 'est_minutes', 'due_date']].to_string(index=False)
        
        # Delete button
        task_del = st.selectbox("Remove:", tasks['id'].astype(str) + " - " + tasks['title'])
        if st.button("Delete"):
            delete_task(task_del.split(" - ")[0])
            st.rerun()
    else:
        task_context = "No tasks currently."
        st.info("No tasks yet.")

# RIGHT: Chat Interface
with col2:
    st.subheader("💬 Chat with your Data")
    
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Display history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat Input
    if prompt := st.chat_input("Ask me to plan your day..."):
        # 1. User Message
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})

        # 2. AI Response
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                # We send the prompt AND the current task list to Gemini
                ai_reply = ask_gemini(prompt, task_context)
                st.markdown(ai_reply)
        st.session_state.messages.append({"role": "assistant", "content": ai_reply})