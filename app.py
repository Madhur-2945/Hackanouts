from flask import Flask, request, jsonify, render_template
import requests
import json

app = Flask(__name__)

LM_STUDIO_API_URL = "http://localhost:1234/v1/chat/completions"

def create_chat_completion(user_input):
    """
    Creates a chat completion using the LM Studio API
    This function follows the original format from your code
    """
    # For resume-specific guidance, we'll prepend instructions to the user input
    resume_instructions = """
    You are a helpful resume-building assistant. Help the user create or improve their resume
    by providing specific, actionable advice on formatting, content, and wording.
    Be encouraging but honest. Focus on personalized recommendations.
    Use strong action verbs and help quantify achievements. If someone asks anything other than resume building, tell them that you can't help with that.
    
    User query: 
    """
    
    enhanced_input = resume_instructions + user_input
    
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "model": "mistral-7b-instruct-v0.3:2",
        "messages": [
            {"role": "user", "content": enhanced_input}
        ],
        "temperature": 0.7,  # Slightly more creative for resume suggestions
        "max_tokens": -1,
        "stream": False
    }
    response = requests.post(LM_STUDIO_API_URL, headers=headers, data=json.dumps(data))

    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Error {response.status_code}: {response.text}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_response', methods=['POST'])
def get_response():
    user_input = request.json.get('prompt')
    try:
        completion = create_chat_completion(user_input)
        response_text = completion['choices'][0]['message']['content']
        return jsonify({'response': response_text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/export_resume', methods=['POST'])
def export_resume():
    """Generate a formatted resume based on provided information"""
    resume_info = request.json.get('resumeInfo', {})
    
    # Create a structured prompt for generating a complete resume
    resume_prompt = """
    Generate a complete, properly formatted resume using the following information:
    
    """
    
    # Add available resume sections to the prompt
    for section, content in resume_info.items():
        resume_prompt += f"\n{section}:\n{content}\n"
    
    resume_prompt += "\nFormat this as a professional resume with proper sections and formatting."
    
    try:
        # Get response
        completion = create_chat_completion(resume_prompt)
        resume_text = completion['choices'][0]['message']['content']
        
        return jsonify({'resume': resume_text})
    except Exception as e:
        """return jsonify({'error': str(e)}), 500"""
        return "Some Error Occured. Please try again later"

if __name__ == '__main__':
    app.run(debug=True)