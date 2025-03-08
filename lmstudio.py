import requests
import json

# Configuration for LM Studio API
LM_STUDIO_API_URL = "http://localhost:1234/v1/chat/completions"

# Function to create a chat completion with a dynamic user prompt
def create_chat_completion(user_input):
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "model": "mistral-7b-instruct-v0.3:2",  # Ensure this matches the model name in LM Studio
        "messages": [
            {"role": "user", "content": user_input}
        ],
        "temperature": 0.5,  # Lower temperature for more formal responses
        "max_tokens": -1,
        "stream": False
    }
    response = requests.post(LM_STUDIO_API_URL, headers=headers, data=json.dumps(data))

    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Error {response.status_code}: {response.text}")

def main():
    # Predefined system message
    system_message = "Always respond in a formal manner. Today is Thursday."

    # Chat loop
    print("Welcome to the Mistral 7B ChatBot! Type 'exit' to quit.")
    while True:
        user_input = input("User: ")
        if user_input.lower() in ['exit', 'bye', 'end']:
            print("Exiting the chat.")
            break

        # Combine system message with user input if needed
        combined_input = f"{system_message} {user_input}"

        try:
            completion = create_chat_completion(combined_input)
            print("Model Response: ", completion['choices'][0]['message']['content'])
        except Exception as e:
            print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()