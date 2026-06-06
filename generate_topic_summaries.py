import os
import json
import re
from google import genai
from dotenv import load_dotenv

load_dotenv()

# Prioritize Vertex AI auth
import google.auth
try:
    credentials, project = google.auth.default()
    client = genai.Client(credentials=credentials, project=project, location='us-central1', vertexai=True)
    model_name = 'gemini-2.5-pro'
except:
    api_key = os.getenv("GEMINI_PRO_API_KEY") or os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    model_name = 'gemini-1.5-flash' 

def generate_summaries():
    meetings_data = []
    meetings_dir = 'src/meetings/'
    for filename in os.listdir(meetings_dir):
        if not filename.endswith('.njk'): continue
        with open(os.path.join(meetings_dir, filename), 'r') as f:
            content = f.read()
            match = re.search(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
            if match:
                import yaml
                try:
                    data = yaml.safe_load(match.group(1))
                    data['slug'] = filename.replace('.njk', '')
                    meetings_data.append(data)
                except: pass

    meetings_data.sort(key=lambda x: x.get('date', ''), reverse=True)

    with open('src/_data/topics.json', 'r') as f:
        topics = json.load(f)

    summaries = {}

    for topic in topics:
        print(f"Summarizing topic: {topic}")
        relevant_content = []
        for m in sorted(meetings_data, key=lambda x: x.get('date', '')):
            if topic in m.get('topics', []):
                summary_bullets = m.get('summary', [])
                topic_bullets = [b['text'] for b in summary_bullets if topic.lower() in b['topic'].lower() or topic.lower() in b['text'].lower()]
                if topic_bullets:
                    relevant_content.append(f"Date: {m['date']}\n" + "\n".join(topic_bullets))
        
        if not relevant_content:
            summaries[topic] = "No detailed information available yet."
            continue

        prompt = f"""
        You are a policy analyst for the South Portland School Department. 
        Synthesize the following chronological notes regarding the topic: '{topic}'.
        
        TASK:
        1. Write a 2-3 paragraph 'Current Status & Impact' summary. Start with the most recent developments, votes, or resolutions.
        2. Ensure proper terminology: Kaler (not Caler), Skillin (not Skillen).
        3. Be objective, factual, and concise.

        Notes from meetings:
        {"---".join(relevant_content)}
        """

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={'temperature': 0.1}
            )
            summaries[topic] = response.text
        except Exception as e:
            print(f"Error summarizing {topic}: {e}")
            summaries[topic] = "Summary generation failed."

    with open('src/_data/topic_summaries.json', 'w') as f:
        json.dump(summaries, f, indent=2)

if __name__ == "__main__":
    generate_summaries()
