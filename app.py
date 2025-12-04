import streamlit as st
import google.generativeai as genai
import sys

st.title("🔧 DeskBot Diagnostic Tool")

# 1. Check Python & Library Versions
st.subheader("1. System Check")
try:
    st.write(f"Python Version: `{sys.version}`")
    st.write(f"Google GenAI Library Version: `{genai.__version__}`")
    
    # Check if the library is the new one (0.8.0+) or old
    if genai.__version__ < "0.8.0":
        st.error("❌ Library is too old! It doesn't know about 'Flash'. We need to fix requirements.txt.")
    else:
        st.success("✅ Library version is good.")
except Exception as e:
    st.error(f"Error checking version: {e}")

# 2. Check API Key
st.subheader("2. API Connection")
if "GOOGLE_API_KEY" in st.secrets:
    st.success("✅ API Key found in Secrets!")
    
    try:
        genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
        st.write("Asking Google for available models...")
        
        # 3. List Available Models
        models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                models.append(m.name)
        
        if models:
            st.success(f"✅ Connection Successful! Found {len(models)} models.")
            st.json(models) # Prints the list nicely
        else:
            st.warning("⚠️ Connected, but found 0 models. This usually means the API Key has the wrong permissions.")
            
    except Exception as e:
        st.error(f"❌ Connection Failed: {e}")
else:
    st.error("❌ No API Key found. Go to 'Manage App' > 'Settings' > 'Secrets' on Streamlit Cloud.")
